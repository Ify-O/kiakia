"""PostgreSQL adapter: the ONLY place in Kiakia that reads or writes the database.

Implements the MessageRepository, CursorStore and ImportRunLog ports.
Uses psycopg 3, the current PostgreSQL driver for Python.

- All SQL is parameterised (%s placeholders), never built from strings.
- The connection is passed in (dependency injection), so tests can use their own.
- Connections run in autocommit mode; every write that must be all-or-nothing
  is wrapped in an explicit `with conn.transaction():` block.
"""

from typing import Any

import psycopg

from kiakia.domain.errors import StorageUnavailable
from kiakia.domain.models import Channel, Direction, Message, WaitingCustomer


def connect(host: str, port: int, user: str, password: str, dbname: str) -> psycopg.Connection:
    try:
        return psycopg.connect(
            host=host, port=port, user=user, password=password, dbname=dbname,
            connect_timeout=5, autocommit=True,
        )
    except psycopg.OperationalError as exc:
        # Keep the message short: it can contain host/port but never the password.
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise StorageUnavailable(f"cannot connect to PostgreSQL at {host}:{port}: {first_line}") from exc


def _one(cur: psycopg.Cursor) -> tuple[Any, ...]:
    """fetchone() for queries that must return a row (e.g. INSERT ... RETURNING)."""
    row = cur.fetchone()
    if row is None:
        raise StorageUnavailable("expected a row from the database but got none")
    return row


_TOUCH_INBOUND = "UPDATE conversations SET last_inbound_at = GREATEST(last_inbound_at, %s) WHERE id = %s"
_TOUCH_OUTBOUND = "UPDATE conversations SET last_outbound_at = GREATEST(last_outbound_at, %s) WHERE id = %s"

_WAITING_SQL = """
    SELECT ct.display_name, cv.channel, cv.external_chat_id,
           (SELECT min(m.sent_at) FROM messages m
             WHERE m.conversation_id = cv.id AND m.direction = 'in'
               AND m.sent_at > coalesce(cv.last_outbound_at, '-infinity')) AS waiting_since,
           (SELECT coalesce(m.body, '[' || m.kind || ']') FROM messages m
             WHERE m.conversation_id = cv.id AND m.direction = 'in'
             ORDER BY m.sent_at DESC, m.id DESC LIMIT 1) AS preview
    FROM conversations cv
    JOIN contacts ct ON ct.id = cv.contact_id
    WHERE cv.last_inbound_at IS NOT NULL
      AND (cv.last_outbound_at IS NULL OR cv.last_inbound_at > cv.last_outbound_at)
    ORDER BY waiting_since ASC
"""


class PostgresStore:
    def __init__(self, conn: psycopg.Connection):
        self._conn = conn

    # ---------- MessageRepository ----------

    def save_messages(self, messages: list[Message]) -> int:
        """Save a batch in ONE transaction: all or nothing. Idempotent."""
        if not messages:
            return 0
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                for m in messages:
                    contact_id = self._contact_id(cur, m)
                    conversation_id = self._conversation_id(cur, m, contact_id)
                    self._upsert_message(cur, m, conversation_id)
                    self._touch_conversation(cur, m, conversation_id)
        except psycopg.OperationalError as exc:
            raise StorageUnavailable(f"saving messages failed: {exc}") from exc
        return len(messages)

    def _contact_id(self, cur: psycopg.Cursor, m: Message) -> int:
        cur.execute(
            "SELECT contact_id FROM contact_identities WHERE channel = %s AND external_id = %s",
            (m.channel.value, m.sender.external_id),
        )
        row = cur.fetchone()
        if row:
            contact_id: int = row[0]
            # Keep the latest name/username the customer uses.
            cur.execute(
                "UPDATE contacts SET display_name = %s WHERE id = %s",
                (m.sender.display_name, contact_id),
            )
            cur.execute(
                "UPDATE contact_identities SET username = %s WHERE channel = %s AND external_id = %s",
                (m.sender.username, m.channel.value, m.sender.external_id),
            )
            return contact_id
        cur.execute(
            "INSERT INTO contacts (display_name) VALUES (%s) RETURNING id", (m.sender.display_name,)
        )
        new_id: int = _one(cur)[0]
        cur.execute(
            "INSERT INTO contact_identities (contact_id, channel, external_id, username) "
            "VALUES (%s, %s, %s, %s)",
            (new_id, m.channel.value, m.sender.external_id, m.sender.username),
        )
        return new_id

    def _conversation_id(self, cur: psycopg.Cursor, m: Message, contact_id: int) -> int:
        cur.execute(
            """INSERT INTO conversations (channel, external_chat_id, contact_id)
               VALUES (%s, %s, %s)
               ON CONFLICT (channel, external_chat_id) DO UPDATE SET channel = EXCLUDED.channel
               RETURNING id""",
            (m.channel.value, m.external_chat_id, contact_id),
        )
        conversation_id: int = _one(cur)[0]
        return conversation_id

    def _upsert_message(self, cur: psycopg.Cursor, m: Message, conversation_id: int) -> None:
        # Same (channel, chat, message id) = same message: update it (e.g. an edit), never duplicate.
        cur.execute(
            """INSERT INTO messages (conversation_id, channel, external_chat_id, external_message_id,
                                     direction, kind, body, media_ref, sent_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (channel, external_chat_id, external_message_id) DO UPDATE SET
                   kind = EXCLUDED.kind,
                   body = EXCLUDED.body,
                   media_ref = EXCLUDED.media_ref""",
            (conversation_id, m.channel.value, m.external_chat_id, m.external_message_id,
             m.direction.value, m.kind.value, m.body, m.media_ref, m.sent_at),
        )

    def _touch_conversation(self, cur: psycopg.Cursor, m: Message, conversation_id: int) -> None:
        # GREATEST ignores NULL, so the first message sets it and older ones never move it back.
        # Two fixed statements: no SQL is ever built from strings.
        sql = _TOUCH_INBOUND if m.direction is Direction.IN else _TOUCH_OUTBOUND
        cur.execute(sql, (m.sent_at, conversation_id))

    def waiting_customers(self) -> list[WaitingCustomer]:
        with self._conn.cursor() as cur:
            cur.execute(_WAITING_SQL)
            rows = cur.fetchall()
        return [
            WaitingCustomer(name, Channel(channel), chat_id, since, preview)
            for name, channel, chat_id, since, preview in rows
        ]

    # ---------- CursorStore ----------

    def get_cursor(self, source: str) -> str | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT cursor FROM sync_cursors WHERE source = %s", (source,))
            row = cur.fetchone()
        return row[0] if row else None

    def set_cursor(self, source: str, cursor: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """INSERT INTO sync_cursors (source, cursor) VALUES (%s, %s)
                   ON CONFLICT (source) DO UPDATE SET cursor = EXCLUDED.cursor, updated_at = now()""",
                (source, cursor),
            )

    # ---------- ImportRunLog ----------

    def start_run(self, source: str) -> int:
        with self._conn.cursor() as cur:
            cur.execute("INSERT INTO import_runs (source) VALUES (%s) RETURNING id", (source,))
            run_id: int = _one(cur)[0]
            return run_id

    def finish_run(
        self, run_id: int, *, status: str, seen: int, saved: int, bad: int, error: str | None = None
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE import_runs SET finished_at = now(), status = %s, records_seen = %s,
                          records_saved = %s, records_bad = %s, error = %s
                   WHERE id = %s""",
                (status, seen, saved, bad, error, run_id),
            )

    # ---------- small read helpers for the CLI ----------

    def counts(self) -> dict[str, Any]:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT (SELECT count(*) FROM contacts), (SELECT count(*) FROM conversations),
                          (SELECT count(*) FROM messages)"""
            )
            contacts, conversations, messages = _one(cur)
            cur.execute("SELECT kind, count(*) FROM messages GROUP BY kind ORDER BY 2 DESC")
            by_kind = dict(cur.fetchall())
        return {"contacts": contacts, "conversations": conversations, "messages": messages,
                "by_kind": by_kind}
