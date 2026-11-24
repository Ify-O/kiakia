"""Integration tests: PostgresStore against a real PostgreSQL (in Docker)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from kiakia.adapters.postgres.repository import PostgresStore
from kiakia.domain.models import Channel, Direction, Message, MessageKind, Period, Reject, RunStatus, Sender

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
ADA = Sender("9001", "Ada Test", "ada_test")
BAYO = Sender("9002", "Bayo", None)


def msg(chat, mid, minutes, sender, body="hi", direction=Direction.IN, kind=MessageKind.TEXT):
    return Message(
        Channel.TELEGRAM, chat, str(mid), direction, kind, T0 + timedelta(minutes=minutes), sender, body
    )


def count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def test_saving_twice_creates_no_duplicates(conn):
    store = PostgresStore(conn)
    batch = [msg("9001", 1, 0, ADA), msg("9001", 2, 1, ADA), msg("9002", 1, 2, BAYO)]
    store.save_messages(batch)
    store.save_messages(batch)  # the same import again
    assert count(conn, "messages") == 3
    assert count(conn, "contacts") == 2
    assert count(conn, "conversations") == 2


def test_edited_message_updates_the_row(conn):
    store = PostgresStore(conn)
    original = msg("9001", 1, 0, ADA, body="blue bag?")
    store.save_messages([original])
    store.save_messages([replace(original, body="blue bag, size L?")])
    with conn.cursor() as cur:
        cur.execute("SELECT body FROM messages")
        assert cur.fetchall() == [("blue bag, size L?",)]


def test_times_are_stored_in_utc(conn):
    PostgresStore(conn).save_messages([msg("9001", 1, 0, ADA)])
    with conn.cursor() as cur:
        cur.execute("SELECT sent_at FROM messages")
        assert cur.fetchone()[0] == T0


def test_waiting_customers_oldest_first_and_replied_chats_excluded(conn):
    store = PostgresStore(conn)
    store.save_messages(
        [
            msg("9001", 1, 0, ADA, "first"),
            msg("9001", 2, 5, ADA, "second"),  # Ada: waiting since 0
            msg("9002", 1, 2, BAYO, "price?"),  # Bayo: waiting since 2
        ]
    )
    waiting = store.waiting_customers()
    assert [w.contact_name for w in waiting] == ["Ada Test", "Bayo"]
    assert waiting[0].waiting_since == T0  # since her FIRST unanswered message
    assert waiting[0].last_message_preview == "second"

    # The business replies to Bayo -> he is no longer waiting.
    store.save_messages([msg("9002", 2, 3, BAYO, "₦15,000", direction=Direction.OUT)])
    assert [w.contact_name for w in store.waiting_customers()] == ["Ada Test"]


def test_voice_note_preview_says_what_it_is(conn):
    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA, body=None, kind=MessageKind.VOICE)])
    assert store.waiting_customers()[0].last_message_preview == "[voice]"


def test_cursor_round_trip(conn):
    store = PostgresStore(conn)
    assert store.get_cursor("telegram") is None
    store.set_cursor("telegram", "500009")
    store.set_cursor("telegram", "500010")
    assert store.get_cursor("telegram") == "500010"


def test_import_run_log(conn):
    store = PostgresStore(conn)
    run_id = store.start_run("telegram")
    store.finish_run(run_id, status=RunStatus.SUCCEEDED, seen=8, saved=5, bad=1)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, records_seen, records_saved, records_bad, finished_at IS NOT NULL "
            "FROM import_runs"
        )
        assert cur.fetchone() == ("succeeded", 8, 5, 1, True)


def test_failed_batch_saves_nothing(conn):
    store = PostgresStore(conn)
    good = msg("9001", 1, 0, ADA)
    bad = replace(msg("9001", 2, 1, ADA), external_message_id=None)  # violates NOT NULL in the DB
    with pytest.raises(psycopg.IntegrityError):
        store.save_messages([good, bad])
    assert count(conn, "messages") == 0  # all or nothing


def test_inbox_stats(conn):
    store = PostgresStore(conn)
    store.save_messages(
        [
            msg("9001", 1, 0, ADA),
            msg("9001", 2, 1, ADA, body=None, kind=MessageKind.VOICE),
            msg("9002", 1, 2, BAYO),
        ]
    )
    st = store.inbox_stats(Period())
    assert (st.contacts, st.conversations, st.messages) == (2, 2, 3)
    assert st.by_kind == {MessageKind.TEXT: 2, MessageKind.VOICE: 1}


def test_database_accepts_every_channel_the_domain_knows(conn):
    # Guards against the domain enum and the DB CHECK constraint drifting apart (W15).
    store = PostgresStore(conn)
    for i, channel in enumerate(Channel):
        store.save_messages([replace(msg(f"c{i}", 1, i, Sender(f"s{i}", "X")), channel=channel)])
    assert count(conn, "messages") == len(Channel)


def test_database_rejects_an_unknown_channel(conn):
    with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
        cur.execute("INSERT INTO contacts (display_name) VALUES ('x') RETURNING id")
        cur.execute(
            "INSERT INTO contact_identities (contact_id, channel, external_id) VALUES (%s, 'myspace', '1')",
            (cur.fetchone()[0],),
        )


def test_rejects_are_kept_with_their_raw_json_and_not_duplicated(conn):
    store = PostgresStore(conn)
    bad = [
        Reject("missing message.date", "500007", raw={"update_id": 500007, "message": {"text": "hi"}}),
        Reject("update is not an object", None, raw="garbage"),
    ]
    assert store.save_rejects("telegram", bad) == 2
    assert store.save_rejects("telegram", bad[:1]) == 0  # same update again: stored once
    with conn.cursor() as cur:
        cur.execute("SELECT ref, reason, raw FROM rejected_updates ORDER BY id")
        rows = cur.fetchall()
    assert rows[0] == ("500007", "missing message.date", {"update_id": 500007, "message": {"text": "hi"}})
    assert rows[1][2] == "garbage"


# ---------- replies + idempotency keys ----------


def _conversation_id(conn, chat):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM conversations WHERE external_chat_id = %s", (chat,))
        return cur.fetchone()[0]


def test_reply_is_stored_as_outgoing_and_ends_the_wait(conn):
    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA, "price?")])
    cid = _conversation_id(conn, "9001")
    assert [w.contact_name for w in store.waiting_customers()] == ["Ada Test"]

    stored = store.record_reply(cid, "500", "₦15,000", T0 + timedelta(minutes=5))
    assert stored.direction is Direction.OUT and stored.body == "₦15,000"
    assert store.get_message(stored.id) == stored
    assert store.waiting_customers() == []  # answered: no longer waiting
    assert count(conn, "contacts") == 1  # the business didn't become a "contact"


def test_idempotency_claim_lifecycle(conn):
    from kiakia.domain.models import ClaimOutcome

    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA)])
    reply = store.record_reply(_conversation_id(conn, "9001"), "500", "hi", T0)

    assert store.claim("replies", "k1", "hash-a").outcome is ClaimOutcome.NEW
    assert store.claim("replies", "k1", "hash-a").outcome is ClaimOutcome.IN_PROGRESS
    assert store.claim("replies", "k1", "hash-b").outcome is ClaimOutcome.MISMATCH
    store.complete("replies", "k1", reply.id)
    replay = store.claim("replies", "k1", "hash-a")
    assert replay.outcome is ClaimOutcome.REPLAY and replay.message_id == reply.id
    assert store.claim("other-scope", "k1", "hash-a").outcome is ClaimOutcome.NEW  # keys are per scope


def test_released_key_can_be_claimed_again(conn):
    from kiakia.domain.models import ClaimOutcome

    store = PostgresStore(conn)
    store.claim("replies", "k2", "h")
    store.release("replies", "k2")
    assert store.claim("replies", "k2", "h").outcome is ClaimOutcome.NEW


def test_expired_key_can_be_reused(conn):
    from kiakia.domain.models import ClaimOutcome

    store = PostgresStore(conn)
    store.claim("replies", "k3", "old-request")
    with conn.cursor() as cur:
        cur.execute("UPDATE idempotency_keys SET created_at = now() - interval '25 hours' WHERE key = 'k3'")
    assert store.claim("replies", "k3", "new-request").outcome is ClaimOutcome.NEW


# ---------- ADR 0006: resolved status, filters, feed, stats ----------


def _status(store, chat):
    from kiakia.domain.models import ConversationFilter

    [c] = [c for c in store.list_conversations(ConversationFilter(), 50, None) if c.external_chat_id == chat]
    return c


def test_resolve_hides_from_waiting_and_a_new_message_reopens_it(conn):
    from kiakia.domain.models import ConversationChanges, ConversationStatus

    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA, "thanks!")])
    cid = _conversation_id(conn, "9001")
    assert _status(store, "9001").status is ConversationStatus.WAITING

    store.update_conversation(
        cid, ConversationChanges(resolved_at=T0 + timedelta(minutes=1), resolved_set=True)
    )
    resolved = _status(store, "9001")
    assert resolved.status is ConversationStatus.RESOLVED and resolved.waiting_since is None
    assert store.waiting_customers() == []

    store.save_messages([msg("9001", 2, 5, ADA, "one more question…")])  # customer writes again
    again = _status(store, "9001")
    assert again.status is ConversationStatus.WAITING and again.unanswered_count == 1
    assert again.resolved_at is not None  # kept, but no longer hides the new message


def test_reply_after_resolve_counts_as_answered(conn):
    from kiakia.domain.models import ConversationChanges, ConversationStatus

    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA)])
    cid = _conversation_id(conn, "9001")
    store.update_conversation(
        cid, ConversationChanges(resolved_at=T0 + timedelta(minutes=1), resolved_set=True)
    )
    store.record_reply(cid, "500", "anything else?", T0 + timedelta(minutes=2))
    assert _status(store, "9001").status is ConversationStatus.ANSWERED


def test_filters_by_status_category_and_period(conn):
    from kiakia.domain.models import Category, ConversationChanges, ConversationFilter, ConversationStatus

    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA), msg("9002", 1, 60 * 24 * 3, BAYO)])  # Bayo: 3 days later
    store.update_conversation(
        _conversation_id(conn, "9001"), ConversationChanges(category=Category.ORDER, category_set=True)
    )
    names = lambda f: [c.contact_name for c in store.list_conversations(f, 50, None)]  # noqa: E731
    assert names(ConversationFilter(category=Category.ORDER)) == ["Ada Test"]
    assert names(ConversationFilter(uncategorized=True)) == ["Bayo"]
    assert names(ConversationFilter(period=Period(T0 + timedelta(days=1), None))) == ["Bayo"]
    assert names(ConversationFilter(period=Period(None, T0 + timedelta(days=1)))) == ["Ada Test"]
    assert names(ConversationFilter(status=ConversationStatus.RESOLVED)) == []


def test_feed_lists_all_messages_newest_first_with_filters(conn):
    from kiakia.domain.models import FeedFilter

    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA, "a"), msg("9002", 1, 1, BAYO, "b")])
    store.record_reply(_conversation_id(conn, "9001"), "500", "c", T0 + timedelta(minutes=2))
    feed = store.list_feed(FeedFilter(), 10, None)
    assert [(f.message.body, f.contact_name) for f in feed] == [
        ("c", "Ada Test"),
        ("b", "Bayo"),
        ("a", "Ada Test"),
    ]
    assert all(f.message.channel is Channel.TELEGRAM for f in feed)
    assert [f.message.body for f in store.list_feed(FeedFilter(direction=Direction.IN), 10, None)] == [
        "b",
        "a",
    ]
    older = store.list_feed(FeedFilter(), 10, (feed[0].message.sent_at, feed[0].message.id))
    assert [f.message.body for f in older] == ["b", "a"]


def test_stats_definitions_from_adr_0006(conn):
    from kiakia.domain.models import Category, ConversationChanges

    store = PostgresStore(conn)
    store.save_messages(
        [
            msg("9001", 1, 0, ADA, "q1"),
            msg("9001", 2, 2, ADA, "q2"),  # Ada: first unanswered at +0
            msg("9002", 1, 10, BAYO, "hi"),  # Bayo: will stay waiting
        ]
    )
    ada = _conversation_id(conn, "9001")
    store.record_reply(ada, "500", "answer", T0 + timedelta(minutes=6))  # first response after 6 min
    store.update_conversation(ada, ConversationChanges(category=Category.ORDER, category_set=True))

    st = store.inbox_stats(Period())
    assert (st.conversations, st.messages, st.messages_received) == (2, 4, 3)
    assert st.waiting == 1 and st.response_rate == 0.5  # Ada answered, Bayo waiting
    assert st.median_first_response_seconds == 6 * 60
    assert st.by_category == {"enquiry": 0, "order": 1, "complaint": 0, "other": 0, "none": 1}
    [tg] = st.by_channel
    assert (tg.channel, tg.messages_received, tg.waiting, tg.response_rate) == (Channel.TELEGRAM, 3, 1, 0.5)

    later = store.inbox_stats(Period(T0 + timedelta(minutes=5), None))  # only Bayo's message + the reply
    assert (later.conversations, later.messages, later.waiting) == (1, 2, 1)  # waiting = right now
    assert later.median_first_response_seconds == 6 * 60  # the reply was sent in the period


# ---------- ADR 0007: customers + recorded consent ----------


def _consent(store, customer_id, kind, status, new_current=None, details=None, evidence=None, wording=None):
    from kiakia.domain.models import ConsentChange, ConsentKind, ConsentStatus

    store.record_consent(
        ConsentChange(
            customer_id=customer_id,
            kind=ConsentKind(kind),
            status=ConsentStatus(status),
            new_current=ConsentStatus(new_current or status),
            channel=Channel.TELEGRAM,
            wording=wording,
            evidence_message_id=evidence,
            details=details,
        )
    )


def test_customer_list_shows_platforms_counts_and_consent(conn):
    from kiakia.domain.models import Category, ConsentStatus, ConversationChanges, CustomerFilter

    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA), msg("9002", 1, 5, BAYO)])
    store.update_conversation(
        _conversation_id(conn, "9001"), ConversationChanges(category=Category.ORDER, category_set=True)
    )
    customers = store.list_customers(CustomerFilter(), 10, None)
    assert [c.display_name for c in customers] == ["Bayo", "Ada Test"]  # most recent first
    ada = customers[1]
    assert ada.first_channel is Channel.TELEGRAM and ada.channels[0].username == "ada_test"
    assert (ada.conversation_count, ada.order_count) == (1, 1)
    assert ada.details_consent is ConsentStatus.UNKNOWN and ada.phone is None
    assert [c.display_name for c in store.list_customers(CustomerFilter(search="ada_t"), 10, None)] == [
        "Ada Test"
    ]


def test_granted_details_are_saved_and_declining_erases_them(conn):
    from kiakia.domain.models import ConsentStatus, CustomerDetailsInput

    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA, "YES")])
    from kiakia.domain.models import CustomerFilter

    ada_id = store.list_customers(CustomerFilter(), 1, None)[0].id
    _consent(store, ada_id, "details", "asked", wording="Can we save your details?")
    _consent(
        store,
        ada_id,
        "details",
        "granted",
        details=CustomerDetailsInput(name="Ada Okafor", phone="+2348031234567", email="ada@example.com"),
    )
    detail = store.get_customer(ada_id)
    c = detail.customer
    assert (c.display_name, c.phone, c.email, c.details_consent) == (
        "Ada Okafor",
        "+2348031234567",
        "ada@example.com",
        ConsentStatus.GRANTED,
    )
    assert [e.status for e in detail.history] == [ConsentStatus.ASKED, ConsentStatus.GRANTED]
    assert detail.history[0].wording == "Can we save your details?"

    store.save_messages(
        [msg("9001", 2, 1, ADA, "hi again")]
    )  # platform name must not overwrite the saved one
    assert store.get_customer(ada_id).customer.display_name == "Ada Okafor"

    _consent(store, ada_id, "details", "withdrawn")
    c = store.get_customer(ada_id).customer
    assert (c.phone, c.email, c.details_consent) == (None, None, ConsentStatus.WITHDRAWN)


def test_database_refuses_details_without_consent(conn):
    # Defence in depth: even a buggy app can't store a phone number without consent.
    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA)])
    with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
        cur.execute("UPDATE contacts SET phone = '+2348031234567'")


def test_stop_reply_withdraws_offers_once(conn):
    from kiakia.domain.models import ConsentStatus, CustomerFilter

    store = PostgresStore(conn)
    stop = msg("9001", 2, 1, ADA, "STOP")
    store.save_messages([msg("9001", 1, 0, ADA), stop])
    ada_id = store.list_customers(CustomerFilter(), 1, None)[0].id
    _consent(store, ada_id, "marketing", "granted")
    assert store.withdraw_marketing(stop) is True
    assert store.withdraw_marketing(stop) is False  # same STOP imported again: no duplicate event
    detail = store.get_customer(ada_id)
    assert detail.customer.marketing_consent is ConsentStatus.WITHDRAWN
    assert detail.history[-1].evidence_message_id is not None
    assert [
        c.display_name
        for c in store.list_customers(CustomerFilter(marketing_consent=ConsentStatus.WITHDRAWN), 10, None)
    ] == ["Ada Test"]


def test_conversation_evidence_only_for_own_conversations(conn):
    from kiakia.domain.models import CustomerFilter

    store = PostgresStore(conn)
    store.save_messages([msg("9001", 1, 0, ADA, "yes please"), msg("9002", 1, 1, BAYO)])
    ada = next(c for c in store.list_customers(CustomerFilter(), 10, None) if c.display_name == "Ada Test")
    channel, evidence = store.conversation_evidence(ada.id, _conversation_id(conn, "9001"))
    assert channel is Channel.TELEGRAM and evidence is not None
    assert store.conversation_evidence(ada.id, _conversation_id(conn, "9002")) is None  # Bayo's chat
