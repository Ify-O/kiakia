"""PostgreSQL adapter: the ONLY place in Kiakia that reads or writes the database.

Implements the MessageRepository, RejectStore, CursorStore, ImportRunLog, StatsQuery,
ConversationQuery and HealthCheck ports.
Uses psycopg 3, the current PostgreSQL driver for Python.

- All SQL is parameterised (%s placeholders), never built from strings.
- The connection is passed in (dependency injection), so tests can use their own.
- Connections run in autocommit mode; every write that must be all-or-nothing
  is wrapped in an explicit `with conn.transaction():` block.
"""

import json
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from kiakia.domain.errors import StorageUnavailable
from kiakia.domain.models import (
    Channel,
    ConversationDetail,
    ConversationFilter,
    ConversationSort,
    ConversationStatus,
    ConversationSummary,
    Direction,
    InboxStats,
    Message,
    MessageKind,
    Reject,
    RunStatus,
    StoredMessage,
    WaitingCustomer,
)
from kiakia.domain.ports import Keyset


def connect(host: str, port: int, user: str, password: str, dbname: str) -> psycopg.Connection:
    try:
        return psycopg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=dbname,
            connect_timeout=5,
            autocommit=True,
        )
    except psycopg.OperationalError as exc:
        # Keep the message short: it can contain host/port but never the password.
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        raise StorageUnavailable(f"cannot connect to PostgreSQL at {host}:{port}: {first_line}") from exc


def _json_dumps(obj: Any) -> str:
    """JSON for the dead-letter table; anything exotic is stored as text rather than lost."""
    return json.dumps(obj, default=str)


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


# One row per conversation with everything the inbox shows. A constant: no user input in it.
# "waiting" = there are customer messages after the business's last reply.
_CONVERSATIONS_CTE = """
WITH conv AS (
    SELECT cv.id, cv.channel, cv.created_at,
           ct.id AS contact_id, ct.display_name, ident.username,
           GREATEST(cv.last_inbound_at, cv.last_outbound_at) AS last_message_at,
           cv.last_outbound_at,
           unanswered.waiting_since,
           unanswered.unanswered_count,
           unanswered.unanswered_count > 0 AS waiting,
           latest.preview
    FROM conversations cv
    JOIN contacts ct ON ct.id = cv.contact_id
    LEFT JOIN LATERAL (
        SELECT ci.username FROM contact_identities ci
        WHERE ci.contact_id = ct.id AND ci.channel = cv.channel
        ORDER BY ci.id LIMIT 1
    ) ident ON true
    LEFT JOIN LATERAL (
        SELECT min(m.sent_at) AS waiting_since, count(*) AS unanswered_count
        FROM messages m
        WHERE m.conversation_id = cv.id AND m.direction = 'in'
          AND m.sent_at > coalesce(cv.last_outbound_at, '-infinity')
    ) unanswered ON true
    LEFT JOIN LATERAL (
        SELECT left(coalesce(m.body, '[' || m.kind || ']'), 120) AS preview
        FROM messages m
        WHERE m.conversation_id = cv.id AND m.direction = 'in'
        ORDER BY m.sent_at DESC, m.id DESC LIMIT 1
    ) latest ON true
)
"""

_CONVERSATION_COLUMNS = """
SELECT id, channel, contact_id, display_name, username, waiting, waiting_since,
       last_message_at, last_outbound_at, preview, unanswered_count, created_at
FROM conv
"""


# Fixed queries built once from constants (no input ever goes into the SQL text).
_CONVERSATION_BY_ID = _CONVERSATIONS_CTE + _CONVERSATION_COLUMNS + " WHERE id = %s"
_WAITING_SELECT = (
    "SELECT count(*) FILTER (WHERE waiting), min(waiting_since) FILTER (WHERE waiting) FROM conv"
)
_WAITING_STATS = _CONVERSATIONS_CTE + _WAITING_SELECT
_MESSAGES_PAGE = sql.SQL(
    "SELECT id, conversation_id, direction, kind, body, media_ref IS NOT NULL, sent_at "
    "FROM messages WHERE conversation_id = %s{keyset} ORDER BY sent_at DESC, id DESC LIMIT %s"
)
_MESSAGES_KEYSET = sql.SQL(" AND (sent_at, id) < (%s, %s)")


def _like_pattern(text: str) -> str:
    """Search text as a LIKE pattern, with LIKE's own wildcards escaped (so '100%' is literal)."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _summary(row: tuple[Any, ...]) -> ConversationSummary:
    (
        cid,
        channel,
        contact_id,
        name,
        username,
        waiting,
        waiting_since,
        last_message_at,
        last_outbound_at,
        preview,
        unanswered,
        _created,
    ) = row
    return ConversationSummary(
        id=cid,
        channel=Channel(channel),
        contact_id=contact_id,
        contact_name=name,
        contact_username=username,
        status=ConversationStatus.WAITING if waiting else ConversationStatus.ANSWERED,
        waiting_since=waiting_since if waiting else None,
        last_message_at=last_message_at,
        last_reply_at=last_outbound_at,
        last_message_preview=preview,
        unanswered_count=unanswered,
    )


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
        cur.execute("INSERT INTO contacts (display_name) VALUES (%s) RETURNING id", (m.sender.display_name,))
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
            (
                conversation_id,
                m.channel.value,
                m.external_chat_id,
                m.external_message_id,
                m.direction.value,
                m.kind.value,
                m.body,
                m.media_ref,
                m.sent_at,
            ),
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

    # ---------- ConversationQuery ----------

    def list_conversations(
        self, filters: ConversationFilter, limit: int, after: Keyset | None
    ) -> list[ConversationSummary]:
        conditions: list[sql.Composable] = []
        params: list[Any] = []
        longest = filters.sort is ConversationSort.LONGEST_WAITING
        if filters.status is ConversationStatus.WAITING or longest:
            conditions.append(sql.SQL("waiting"))
        elif filters.status is ConversationStatus.ANSWERED:
            conditions.append(sql.SQL("NOT waiting"))
        if filters.channel is not None:
            conditions.append(sql.SQL("channel = %s"))
            params.append(filters.channel.value)
        if filters.search:
            conditions.append(sql.SQL("(display_name ILIKE %s OR username ILIKE %s)"))
            pattern = _like_pattern(filters.search)
            params += [pattern, pattern]
        if after is not None:
            if longest:
                conditions.append(sql.SQL("(waiting_since, id) > (%s, %s)"))
            else:
                conditions.append(sql.SQL("(last_message_at, id) < (%s, %s)"))
            params += [after[0], after[1]]
        order = sql.SQL("waiting_since ASC, id ASC" if longest else "last_message_at DESC, id DESC")
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")
        query = sql.Composed(
            [
                sql.SQL(_CONVERSATIONS_CTE + _CONVERSATION_COLUMNS),
                where,
                sql.SQL(" ORDER BY "),
                order,
                sql.SQL(" LIMIT %s"),
            ]
        )
        params.append(limit)
        with self._conn.cursor() as cur:
            cur.execute(query, params)
            return [_summary(row) for row in cur.fetchall()]

    def get_conversation(self, conversation_id: int) -> ConversationDetail | None:
        with self._conn.cursor() as cur:
            cur.execute(_CONVERSATION_BY_ID, (conversation_id,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("SELECT count(*) FROM messages WHERE conversation_id = %s", (conversation_id,))
            message_count: int = _one(cur)[0]
        return ConversationDetail(summary=_summary(row), created_at=row[-1], message_count=message_count)

    def list_messages(self, conversation_id: int, limit: int, before: Keyset | None) -> list[StoredMessage]:
        params: list[Any] = [conversation_id]
        keyset: sql.Composable = sql.SQL("")
        if before is not None:
            keyset = _MESSAGES_KEYSET
            params += [before[0], before[1]]
        params.append(limit)
        with self._conn.cursor() as cur:
            cur.execute(_MESSAGES_PAGE.format(keyset=keyset), params)
            return [
                StoredMessage(
                    id=i,
                    conversation_id=c,
                    direction=Direction(d),
                    kind=MessageKind(k),
                    body=b,
                    has_media=m,
                    sent_at=t,
                )
                for i, c, d, k, b, m, t in cur.fetchall()
            ]

    # ---------- HealthCheck ----------

    def ping(self) -> None:
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT 1")
        except psycopg.Error as exc:
            raise StorageUnavailable("The database is not reachable.") from exc

    # ---------- RejectStore (dead letter) ----------

    def save_rejects(self, source: str, rejects: list[Reject]) -> int:
        """Keep bad records with their original JSON. Same (source, ref) is stored once."""
        if not rejects:
            return 0
        new = 0
        try:
            with self._conn.transaction(), self._conn.cursor() as cur:
                for r in rejects:
                    cur.execute(
                        """INSERT INTO rejected_updates (source, ref, reason, raw)
                           VALUES (%s, %s, %s, %s)
                           ON CONFLICT (source, ref) DO NOTHING""",
                        (source, r.ref, r.reason, Jsonb(r.raw, dumps=_json_dumps)),
                    )
                    new += cur.rowcount
        except psycopg.OperationalError as exc:
            raise StorageUnavailable(f"saving rejected records failed: {exc}") from exc
        return new

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
        self, run_id: int, *, status: RunStatus, seen: int, saved: int, bad: int, error: str | None = None
    ) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE import_runs SET finished_at = now(), status = %s, records_seen = %s,
                          records_saved = %s, records_bad = %s, error = %s
                   WHERE id = %s""",
                (status.value, seen, saved, bad, error, run_id),
            )

    # ---------- StatsQuery ----------

    def inbox_stats(self) -> InboxStats:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT (SELECT count(*) FROM contacts), (SELECT count(*) FROM conversations),
                          (SELECT count(*) FROM messages)"""
            )
            contacts, conversations, messages = _one(cur)
            cur.execute("SELECT kind, count(*) FROM messages GROUP BY kind ORDER BY 2 DESC")
            by_kind = {MessageKind(kind): count for kind, count in cur.fetchall()}
            cur.execute(_WAITING_STATS)
            waiting, oldest = _one(cur)
        return InboxStats(
            contacts=contacts,
            conversations=conversations,
            messages=messages,
            by_kind=by_kind,
            waiting=waiting,
            oldest_waiting_since=oldest,
        )
