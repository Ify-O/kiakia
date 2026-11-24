"""Integration tests: PostgresStore against a real PostgreSQL (in Docker)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from kiakia.adapters.postgres.repository import PostgresStore
from kiakia.domain.models import Channel, Direction, Message, MessageKind, Reject, RunStatus, Sender

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
    st = store.inbox_stats()
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
