"""Integration tests: PostgresStore against a real PostgreSQL (in Docker)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from kiakia.adapters.postgres.repository import PostgresStore
from kiakia.domain.models import Channel, Direction, Message, MessageKind, Sender

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
    store.finish_run(run_id, status="succeeded", seen=8, saved=5, bad=1)
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
