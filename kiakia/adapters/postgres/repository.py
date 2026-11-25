"""PostgreSQL adapter: the ONLY place in Kiakia that reads or writes the database.

Implements the MessageRepository, RejectStore, CursorStore, ImportRunLog, StatsQuery,
ConversationQuery, HealthCheck, ReplyStore and IdempotencyStore ports.
Uses psycopg 3, the current PostgreSQL driver for Python.

- All SQL is parameterised (%s placeholders), never built from strings.
- The connection is passed in (dependency injection), so tests can use their own.
- Connections run in autocommit mode; every write that must be all-or-nothing
  is wrapped in an explicit `with conn.transaction():` block.
"""

import json
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from kiakia.domain.errors import StorageUnavailable
from kiakia.domain.models import (
    Category,
    Channel,
    ChannelStats,
    ClaimOutcome,
    ConsentChange,
    ConsentEvent,
    ConsentKind,
    ConsentStatus,
    ConversationChanges,
    ConversationDetail,
    ConversationFilter,
    ConversationSort,
    ConversationStatus,
    ConversationSummary,
    Customer,
    CustomerChannel,
    CustomerConversation,
    CustomerDetail,
    CustomerFilter,
    Direction,
    FeedFilter,
    FeedItem,
    IdempotencyClaim,
    InboxStats,
    Message,
    MessageKind,
    Period,
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

# One row per conversation with everything the inbox shows. A constant: no user input in it.
# "Fence" = the later of our last reply and the time it was marked resolved. Customer messages
# after the fence are unanswered, so a new message re-opens a resolved conversation (ADR 0006).
_CONVERSATIONS_CTE = """
WITH conv0 AS (
    SELECT cv.id, cv.channel, cv.external_chat_id, cv.created_at, cv.category, cv.resolved_at,
           ct.id AS contact_id, ct.display_name, ident.username,
           ct.details_consent, ct.marketing_consent,
           GREATEST(cv.last_inbound_at, cv.last_outbound_at) AS last_message_at,
           cv.last_outbound_at,
           unanswered.waiting_since,
           unanswered.unanswered_count,
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
          AND m.sent_at > coalesce(GREATEST(cv.last_outbound_at, cv.resolved_at), '-infinity')
    ) unanswered ON true
    LEFT JOIN LATERAL (
        SELECT left(coalesce(m.body, '[' || m.kind || ']'), 120) AS preview
        FROM messages m
        WHERE m.conversation_id = cv.id AND m.direction = 'in'
        ORDER BY m.sent_at DESC, m.id DESC LIMIT 1
    ) latest ON true
),
conv AS (
    SELECT *,
           unanswered_count > 0 AS waiting,
           unanswered_count = 0 AND resolved_at IS NOT NULL
               AND (last_outbound_at IS NULL OR resolved_at >= last_outbound_at) AS resolved
    FROM conv0
)
"""

_CONVERSATION_COLUMNS = """
SELECT id, channel, external_chat_id, contact_id, display_name, username, waiting, resolved,
       waiting_since, last_message_at, last_outbound_at, preview, unanswered_count, category,
       resolved_at, details_consent, marketing_consent, created_at
FROM conv
"""

# Fixed queries built once from constants (no input ever goes into the SQL text).
_CONVERSATION_BY_ID = _CONVERSATIONS_CTE + _CONVERSATION_COLUMNS + " WHERE id = %s"
_WAITING_LIST = (
    "SELECT display_name, channel, external_chat_id, waiting_since, preview FROM conv "
    "WHERE waiting ORDER BY waiting_since ASC, id ASC"
)
_WAITING_SQL = _CONVERSATIONS_CTE + _WAITING_LIST

_STORED_MESSAGE_COLUMNS = sql.SQL(
    "id, conversation_id, direction, kind, body, media_ref IS NOT NULL, sent_at, channel"
)
_MESSAGES_PAGE = sql.SQL(
    "SELECT {cols} FROM messages WHERE conversation_id = %s{keyset} ORDER BY sent_at DESC, id DESC LIMIT %s"
)
_MESSAGES_KEYSET = sql.SQL(" AND (sent_at, id) < (%s, %s)")
_MESSAGE_BY_ID = sql.SQL("SELECT {cols} FROM messages WHERE id = %s").format(cols=_STORED_MESSAGE_COLUMNS)
_INSERT_REPLY = sql.SQL(
    """INSERT INTO messages (conversation_id, channel, external_chat_id, external_message_id,
                             direction, kind, body, sent_at)
       VALUES (%s, %s, %s, %s, 'out', 'text', %s, %s)
       ON CONFLICT (channel, external_chat_id, external_message_id)
       DO UPDATE SET body = EXCLUDED.body
       RETURNING {cols}"""
).format(cols=_STORED_MESSAGE_COLUMNS)

# All messages with their customer (the feed). Columns are qualified: m = messages.
_FEED_SELECT = sql.SQL(
    """SELECT m.id, m.conversation_id, m.direction, m.kind, m.body, m.media_ref IS NOT NULL,
              m.sent_at, m.channel, ct.id, ct.display_name, ident.username,
              ct.details_consent, ct.marketing_consent
       FROM messages m
       JOIN conversations cv ON cv.id = m.conversation_id
       JOIN contacts ct ON ct.id = cv.contact_id
       LEFT JOIN LATERAL (
           SELECT ci.username FROM contact_identities ci
           WHERE ci.contact_id = ct.id AND ci.channel = cv.channel ORDER BY ci.id LIMIT 1
       ) ident ON true"""
)

# ---- Stats for a period (ADR 0006). %(start)s / %(end)s may be NULL = open-ended. ----
_PERIOD_ON_SENT_AT = (
    "(%(start)s::timestamptz IS NULL OR sent_at >= %(start)s) "
    "AND (%(end)s::timestamptz IS NULL OR sent_at < %(end)s)"
)
_STATS_MESSAGES_SELECT = "SELECT channel, direction, kind, count(*) FROM messages WHERE "
_STATS_MESSAGES_GROUP = " GROUP BY channel, direction, kind"
_STATS_MESSAGES = _STATS_MESSAGES_SELECT + _PERIOD_ON_SENT_AT + _STATS_MESSAGES_GROUP

_STATS_CONV_SELECT = (
    "SELECT conv.channel, conv.waiting, coalesce(conv.category, 'none') FROM conv "
    "WHERE EXISTS (SELECT 1 FROM messages WHERE messages.conversation_id = conv.id "
    "AND direction = 'in' AND "
)
_CLOSE_PAREN = ")"
_STATS_CONVERSATIONS = _CONVERSATIONS_CTE + _STATS_CONV_SELECT + _PERIOD_ON_SENT_AT + _CLOSE_PAREN

_STATS_WAITING_SELECT = (
    "SELECT channel, count(*), min(waiting_since) FROM conv WHERE waiting GROUP BY channel"
)
_STATS_WAITING_NOW = _CONVERSATIONS_CTE + _STATS_WAITING_SELECT
# Median first response: messages after our k-th reply form group k; group k's first customer
# message is answered by our (k+1)-th reply. Only replies sent in the period count.
_STATS_MEDIAN_FIRST_RESPONSE = """WITH m AS (
        SELECT conversation_id, direction, sent_at,
               count(*) FILTER (WHERE direction = 'out')
                   OVER (PARTITION BY conversation_id ORDER BY sent_at, id) AS outs_so_far
        FROM messages
    ), g AS (
        SELECT conversation_id, direction, sent_at,
               CASE WHEN direction = 'out' THEN outs_so_far - 1 ELSE outs_so_far END AS grp
        FROM m
    ), turns AS (
        SELECT min(sent_at) FILTER (WHERE direction = 'in') AS first_in,
               min(sent_at) FILTER (WHERE direction = 'out') AS reply_at
        FROM g GROUP BY conversation_id, grp
    )
    SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM reply_at - first_in))
    FROM turns
    WHERE first_in IS NOT NULL AND reply_at IS NOT NULL
      AND (%(start)s::timestamptz IS NULL OR reply_at >= %(start)s)
      AND (%(end)s::timestamptz IS NULL OR reply_at < %(end)s)"""
IDEMPOTENCY_TTL = "24 hours"


# ---- Customers (ADR 0007). One row per contact with what the customer list shows. ----
_CUSTOMERS_CTE = """
WITH cust AS (
    SELECT ct.id, ct.display_name, ct.phone, ct.email, ct.details_consent, ct.marketing_consent,
           (SELECT cv.channel FROM conversations cv WHERE cv.contact_id = ct.id
             ORDER BY cv.created_at, cv.id LIMIT 1) AS first_channel,
           (SELECT min(m.sent_at) FROM messages m JOIN conversations cv ON cv.id = m.conversation_id
             WHERE cv.contact_id = ct.id) AS first_contact_at,
           (SELECT max(GREATEST(cv.last_inbound_at, cv.last_outbound_at)) FROM conversations cv
             WHERE cv.contact_id = ct.id) AS last_contact_at,
           (SELECT count(*) FROM conversations cv WHERE cv.contact_id = ct.id) AS conversation_count,
           (SELECT count(*) FROM conversations cv WHERE cv.contact_id = ct.id AND cv.category = 'order')
               AS order_count,
           (SELECT coalesce(json_agg(json_build_object('channel', ci.channel, 'username', ci.username)
                                     ORDER BY ci.id), '[]'::json)
              FROM contact_identities ci WHERE ci.contact_id = ct.id) AS channels,
           (SELECT string_agg(coalesce(ci.username, ''), ' ') FROM contact_identities ci
             WHERE ci.contact_id = ct.id) AS usernames
    FROM contacts ct
)
"""
_CUSTOMER_COLUMNS = """
SELECT id, display_name, phone, email, details_consent, marketing_consent, first_channel, channels,
       first_contact_at, last_contact_at, conversation_count, order_count
FROM cust WHERE last_contact_at IS NOT NULL
"""
_CUSTOMER_BY_ID = _CUSTOMERS_CTE + _CUSTOMER_COLUMNS + " AND id = %s"
_CUSTOMER_CONVERSATIONS_SELECT = (
    "SELECT id, channel, waiting, resolved, last_message_at FROM conv WHERE contact_id = %s "
    "ORDER BY last_message_at DESC, id DESC"
)
_CUSTOMER_CONVERSATIONS = _CONVERSATIONS_CTE + _CUSTOMER_CONVERSATIONS_SELECT
_CONSENT_HISTORY = (
    "SELECT id, kind, status, channel, wording, evidence_message_id, recorded_at FROM consent_events "
    "WHERE contact_id = %s ORDER BY recorded_at, id"
)
_INSERT_CONSENT_EVENT = (
    "INSERT INTO consent_events (contact_id, kind, status, channel, wording, evidence_message_id) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)
# Two fixed statements per kind: no column names are ever built from input.
_SET_DETAILS_CONSENT = "UPDATE contacts SET details_consent = %s WHERE id = %s"
_SET_DETAILS_GRANTED = (
    "UPDATE contacts SET details_consent = 'granted', display_name = coalesce(%s, display_name), "
    "phone = coalesce(%s, phone), email = coalesce(%s, email) WHERE id = %s"
)
_ERASE_DETAILS = "UPDATE contacts SET details_consent = %s, phone = NULL, email = NULL WHERE id = %s"
_SET_MARKETING_CONSENT = "UPDATE contacts SET marketing_consent = %s WHERE id = %s"


def _customer(row: tuple[Any, ...]) -> Customer:
    (
        cid,
        name,
        phone,
        email,
        details,
        marketing,
        first_channel,
        channels,
        first_at,
        last_at,
        convs,
        orders,
    ) = row
    return Customer(
        id=cid,
        display_name=name,
        phone=phone,
        email=email,
        details_consent=ConsentStatus(details),
        marketing_consent=ConsentStatus(marketing),
        first_channel=Channel(first_channel),
        channels=[CustomerChannel(Channel(c["channel"]), c["username"]) for c in channels],
        first_contact_at=first_at,
        last_contact_at=last_at,
        conversation_count=convs,
        order_count=orders,
    )


def _stored(row: tuple[Any, ...]) -> StoredMessage:
    i, c, d, k, b, m, t, ch = row
    return StoredMessage(
        id=i,
        conversation_id=c,
        direction=Direction(d),
        kind=MessageKind(k),
        body=b,
        has_media=m,
        sent_at=t,
        channel=Channel(ch),
    )


def _summary(row: tuple[Any, ...]) -> ConversationSummary:
    (
        cid,
        channel,
        external_chat_id,
        contact_id,
        name,
        username,
        waiting,
        resolved,
        waiting_since,
        last_message_at,
        last_outbound_at,
        preview,
        unanswered,
        category,
        resolved_at,
        details_consent,
        marketing_consent,
        _created,
    ) = row
    if waiting:
        status = ConversationStatus.WAITING
    elif resolved:
        status = ConversationStatus.RESOLVED
    else:
        status = ConversationStatus.ANSWERED
    return ConversationSummary(
        id=cid,
        channel=Channel(channel),
        external_chat_id=external_chat_id,
        contact_id=contact_id,
        contact_name=name,
        contact_username=username,
        status=status,
        waiting_since=waiting_since if waiting else None,
        last_message_at=last_message_at,
        last_reply_at=last_outbound_at,
        last_message_preview=preview,
        unanswered_count=unanswered,
        category=Category(category) if category else None,
        resolved_at=resolved_at,
        contact_details_consent=ConsentStatus(details_consent),
        contact_marketing_consent=ConsentStatus(marketing_consent),
    )


def _text(value: str | None) -> str | None:
    """PostgreSQL can't store the NUL character in text; drop it from anything we store."""
    return value.replace("\x00", "") if value is not None else None


def _like_pattern(text: str) -> str:
    """Search text as a LIKE pattern, with LIKE's own wildcards escaped (so '100%' is literal)."""
    text = text.replace("\x00", "")
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


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
            # Keep the latest name/username the customer uses, unless they saved their own name
            # with consent (ADR 0007): then their chosen name wins over the platform's.
            cur.execute(
                "UPDATE contacts SET display_name = %s WHERE id = %s AND details_consent <> 'granted'",
                (_text(m.sender.display_name), contact_id),
            )
            cur.execute(
                "UPDATE contact_identities SET username = %s WHERE channel = %s AND external_id = %s",
                (_text(m.sender.username), m.channel.value, m.sender.external_id),
            )
            return contact_id
        cur.execute(
            "INSERT INTO contacts (display_name) VALUES (%s) RETURNING id", (_text(m.sender.display_name),)
        )
        new_id: int = _one(cur)[0]
        cur.execute(
            "INSERT INTO contact_identities (contact_id, channel, external_id, username) "
            "VALUES (%s, %s, %s, %s)",
            (new_id, m.channel.value, m.sender.external_id, _text(m.sender.username)),
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
                _text(m.body),
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
            conditions.append(sql.SQL("NOT waiting AND NOT resolved"))
        elif filters.status is ConversationStatus.RESOLVED:
            conditions.append(sql.SQL("resolved"))
        if filters.category is not None:
            conditions.append(sql.SQL("category = %s"))
            params.append(filters.category.value)
        elif filters.uncategorized:
            conditions.append(sql.SQL("category IS NULL"))
        if filters.period.start is not None:
            conditions.append(sql.SQL("last_message_at >= %s"))
            params.append(filters.period.start)
        if filters.period.end is not None:
            conditions.append(sql.SQL("last_message_at < %s"))
            params.append(filters.period.end)
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
            cur.execute(_MESSAGES_PAGE.format(cols=_STORED_MESSAGE_COLUMNS, keyset=keyset), params)
            return [_stored(row) for row in cur.fetchall()]

    def list_feed(self, filters: FeedFilter, limit: int, before: Keyset | None) -> list[FeedItem]:
        conditions: list[sql.Composable] = []
        params: list[Any] = []
        if filters.channel is not None:
            conditions.append(sql.SQL("m.channel = %s"))
            params.append(filters.channel.value)
        if filters.direction is not None:
            conditions.append(sql.SQL("m.direction = %s"))
            params.append(filters.direction.value)
        if filters.period.start is not None:
            conditions.append(sql.SQL("m.sent_at >= %s"))
            params.append(filters.period.start)
        if filters.period.end is not None:
            conditions.append(sql.SQL("m.sent_at < %s"))
            params.append(filters.period.end)
        if before is not None:
            conditions.append(sql.SQL("(m.sent_at, m.id) < (%s, %s)"))
            params += [before[0], before[1]]
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")
        query = sql.Composed([_FEED_SELECT, where, sql.SQL(" ORDER BY m.sent_at DESC, m.id DESC LIMIT %s")])
        params.append(limit)
        with self._conn.cursor() as cur:
            cur.execute(query, params)
            return [
                FeedItem(
                    message=_stored(row[:8]),
                    contact_id=row[8],
                    contact_name=row[9],
                    contact_username=row[10],
                    contact_details_consent=ConsentStatus(row[11]),
                    contact_marketing_consent=ConsentStatus(row[12]),
                )
                for row in cur.fetchall()
            ]

    # ---------- CustomerStore + OptOutRecorder (ADR 0007) ----------

    def list_customers(self, filters: CustomerFilter, limit: int, after: Keyset | None) -> list[Customer]:
        conditions: list[sql.Composable] = []
        params: list[Any] = []
        if filters.search:
            pattern = _like_pattern(filters.search)
            conditions.append(
                sql.SQL("(display_name ILIKE %s OR usernames ILIKE %s OR phone ILIKE %s OR email ILIKE %s)")
            )
            params += [pattern] * 4
        if filters.details_consent is not None:
            conditions.append(sql.SQL("details_consent = %s"))
            params.append(filters.details_consent.value)
        if filters.marketing_consent is not None:
            conditions.append(sql.SQL("marketing_consent = %s"))
            params.append(filters.marketing_consent.value)
        if filters.channel is not None:
            conditions.append(
                sql.SQL(
                    "EXISTS (SELECT 1 FROM contact_identities ci "
                    "WHERE ci.contact_id = cust.id AND ci.channel = %s)"
                )
            )
            params.append(filters.channel.value)
        if after is not None:
            conditions.append(sql.SQL("(last_contact_at, id) < (%s, %s)"))
            params += [after[0], after[1]]
        extra = sql.SQL(" AND ") + sql.SQL(" AND ").join(conditions) if conditions else sql.SQL("")
        query = sql.Composed(
            [
                sql.SQL(_CUSTOMERS_CTE + _CUSTOMER_COLUMNS),
                extra,
                sql.SQL(" ORDER BY last_contact_at DESC, id DESC LIMIT %s"),
            ]
        )
        params.append(limit)
        with self._conn.cursor() as cur:
            cur.execute(query, params)
            return [_customer(row) for row in cur.fetchall()]

    def get_customer(self, customer_id: int) -> CustomerDetail | None:
        with self._conn.cursor() as cur:
            cur.execute(_CUSTOMER_BY_ID, (customer_id,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(_CUSTOMER_CONVERSATIONS, (customer_id,))
            conversations = [
                CustomerConversation(
                    id=i,
                    channel=Channel(ch),
                    status=ConversationStatus.WAITING
                    if waiting
                    else ConversationStatus.RESOLVED
                    if resolved
                    else ConversationStatus.ANSWERED,
                    last_message_at=last,
                )
                for i, ch, waiting, resolved, last in cur.fetchall()
            ]
            cur.execute(_CONSENT_HISTORY, (customer_id,))
            history = [
                ConsentEvent(
                    id=i,
                    kind=ConsentKind(k),
                    status=ConsentStatus(st),
                    channel=Channel(ch) if ch else None,
                    wording=w,
                    evidence_message_id=ev,
                    recorded_at=at,
                )
                for i, k, st, ch, w, ev, at in cur.fetchall()
            ]
        return CustomerDetail(customer=_customer(row), conversations=conversations, history=history)

    def conversation_evidence(
        self, customer_id: int, conversation_id: int
    ) -> tuple[Channel, int | None] | None:
        with self._conn.cursor() as cur:
            cur.execute(
                """SELECT cv.channel,
                          (SELECT m.id FROM messages m WHERE m.conversation_id = cv.id AND m.direction = 'in'
                            ORDER BY m.sent_at DESC, m.id DESC LIMIT 1)
                   FROM conversations cv WHERE cv.id = %s AND cv.contact_id = %s""",
                (conversation_id, customer_id),
            )
            row = cur.fetchone()
        return (Channel(row[0]), row[1]) if row else None

    def record_consent(self, change: ConsentChange) -> None:
        details = change.details
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("SELECT id FROM contacts WHERE id = %s FOR UPDATE", (change.customer_id,))
            _one(cur)
            cur.execute(
                _INSERT_CONSENT_EVENT,
                (
                    change.customer_id,
                    change.kind.value,
                    change.status.value,
                    change.channel.value if change.channel else None,
                    _text(change.wording),
                    change.evidence_message_id,
                ),
            )
            if change.kind is ConsentKind.MARKETING:
                cur.execute(_SET_MARKETING_CONSENT, (change.new_current.value, change.customer_id))
            elif change.new_current is ConsentStatus.GRANTED:
                cur.execute(
                    _SET_DETAILS_GRANTED,
                    (
                        _text(details.name) if details else None,
                        details.phone if details else None,
                        details.email if details else None,
                        change.customer_id,
                    ),
                )
            elif change.new_current in (ConsentStatus.DECLINED, ConsentStatus.WITHDRAWN):
                cur.execute(
                    _ERASE_DETAILS, (change.new_current.value, change.customer_id)
                )  # erase phone/email
            else:
                cur.execute(_SET_DETAILS_CONSENT, (change.new_current.value, change.customer_id))

    def withdraw_marketing(self, message: Message) -> bool:
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "SELECT contact_id FROM contact_identities WHERE channel = %s AND external_id = %s",
                (message.channel.value, message.sender.external_id),
            )
            row = cur.fetchone()
            if row is None:
                return False
            contact_id = row[0]
            cur.execute(
                "SELECT id FROM messages "
                "WHERE channel = %s AND external_chat_id = %s AND external_message_id = %s",
                (message.channel.value, message.external_chat_id, message.external_message_id),
            )
            evidence = cur.fetchone()
            cur.execute(
                "SELECT 1 FROM consent_events WHERE contact_id = %s AND kind = 'marketing' "
                "AND status = 'withdrawn' AND evidence_message_id IS NOT DISTINCT FROM %s",
                (contact_id, evidence[0] if evidence else None),
            )
            if cur.fetchone() is not None:
                return False  # same STOP imported twice: already recorded (idempotent)
            cur.execute(
                _INSERT_CONSENT_EVENT,
                (
                    contact_id,
                    "marketing",
                    "withdrawn",
                    message.channel.value,
                    None,
                    evidence[0] if evidence else None,
                ),
            )
            cur.execute(_SET_MARKETING_CONSENT, ("withdrawn", contact_id))
        return True

    # ---------- ConversationUpdater ----------

    def update_conversation(self, conversation_id: int, changes: ConversationChanges) -> None:
        sets: list[sql.Composable] = []
        params: list[Any] = []
        if changes.category_set:
            sets.append(sql.SQL("category = %s"))
            params.append(changes.category.value if changes.category else None)
        if changes.resolved_set:
            sets.append(sql.SQL("resolved_at = %s"))
            params.append(changes.resolved_at)
        if not sets:
            return
        params.append(conversation_id)
        query = sql.Composed(
            [sql.SQL("UPDATE conversations SET "), sql.SQL(", ").join(sets), sql.SQL(" WHERE id = %s")]
        )
        with self._conn.cursor() as cur:
            cur.execute(query, params)

    # ---------- ReplyStore ----------

    def record_reply(
        self, conversation_id: int, external_message_id: str, text: str, sent_at: datetime
    ) -> StoredMessage:
        """Store an outgoing reply and mark the conversation as answered, in one transaction."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute(
                "SELECT channel, external_chat_id FROM conversations WHERE id = %s", (conversation_id,)
            )
            channel, chat_id = _one(cur)
            cur.execute(
                _INSERT_REPLY, (conversation_id, channel, chat_id, external_message_id, _text(text), sent_at)
            )
            stored = _stored(_one(cur))
            cur.execute(_TOUCH_OUTBOUND, (sent_at, conversation_id))
        return stored

    def get_message(self, message_id: int) -> StoredMessage | None:
        with self._conn.cursor() as cur:
            cur.execute(_MESSAGE_BY_ID, (message_id,))
            row = cur.fetchone()
        return _stored(row) if row else None

    # ---------- IdempotencyStore ----------

    def claim(self, scope: str, key: str, request_hash: str) -> IdempotencyClaim:
        with self._conn.transaction(), self._conn.cursor() as cur:
            # An expired key is forgotten, so it can be used again.
            cur.execute(
                "DELETE FROM idempotency_keys WHERE scope = %s AND key = %s "
                "AND created_at < now() - %s::interval",
                (scope, key, IDEMPOTENCY_TTL),
            )
            # Atomic "first one wins": a concurrent identical claim waits on the primary key.
            cur.execute(
                """INSERT INTO idempotency_keys (scope, key, request_hash, status)
                   VALUES (%s, %s, %s, 'in_progress')
                   ON CONFLICT (scope, key) DO NOTHING
                   RETURNING key""",
                (scope, key, request_hash),
            )
            if cur.fetchone() is not None:
                return IdempotencyClaim(ClaimOutcome.NEW)
            cur.execute(
                "SELECT request_hash, status, message_id FROM idempotency_keys WHERE scope = %s AND key = %s",
                (scope, key),
            )
            stored_hash, status, message_id = _one(cur)
        if stored_hash != request_hash:
            return IdempotencyClaim(ClaimOutcome.MISMATCH)
        if status == "in_progress":
            return IdempotencyClaim(ClaimOutcome.IN_PROGRESS)
        return IdempotencyClaim(ClaimOutcome.REPLAY, message_id=message_id)

    def complete(self, scope: str, key: str, message_id: int) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """UPDATE idempotency_keys SET status = 'completed', message_id = %s, completed_at = now()
                   WHERE scope = %s AND key = %s""",
                (message_id, scope, key),
            )

    def release(self, scope: str, key: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "DELETE FROM idempotency_keys WHERE scope = %s AND key = %s AND status = 'in_progress'",
                (scope, key),
            )

    # ---------- Demo mode (ADR 0008) ----------

    def reset_demo_data(self) -> None:
        """Delete ALL data, for re-seeding the demo. Refuses unless this is a *demo* database."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            name: str = _one(cur)[0]
            if not name.endswith("demo"):
                raise StorageUnavailable(f"refusing to wipe {name!r}: only a demo database may be reset")
            cur.execute(
                "TRUNCATE messages, conversations, contact_identities, contacts, sync_cursors, import_runs, "
                "rejected_updates, idempotency_keys, consent_events RESTART IDENTITY CASCADE"
            )

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

    def inbox_stats(self, period: Period) -> InboxStats:
        """Numbers as defined in docs/adr/0006 (period counts + the situation right now)."""
        p = {"start": period.start, "end": period.end}
        msgs: dict[Channel, list[int]] = {}  # channel -> [messages, received]
        by_kind: dict[MessageKind, int] = {}
        convs: dict[Channel, list[int]] = {}  # channel -> [conversations, responded]
        by_category = {"enquiry": 0, "order": 0, "complaint": 0, "other": 0, "none": 0}
        waiting_now: dict[Channel, int] = {}
        oldest = None
        with self._conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM contacts")
            contacts: int = _one(cur)[0]
            cur.execute(_STATS_MESSAGES, p)
            for channel, direction, kind, n in cur.fetchall():
                row = msgs.setdefault(Channel(channel), [0, 0])
                row[0] += n
                row[1] += n if direction == "in" else 0
                by_kind[MessageKind(kind)] = by_kind.get(MessageKind(kind), 0) + n
            cur.execute(_STATS_CONVERSATIONS, p)
            for channel, waiting, category in cur.fetchall():
                row = convs.setdefault(Channel(channel), [0, 0])
                row[0] += 1
                row[1] += 0 if waiting else 1
                by_category[category] += 1
            cur.execute(_STATS_WAITING_NOW)
            for channel, n, since in cur.fetchall():
                waiting_now[Channel(channel)] = n
                oldest = since if oldest is None or since < oldest else oldest
            cur.execute(_STATS_MEDIAN_FIRST_RESPONSE, p)
            median = _one(cur)[0]

        def rate(c: list[int] | None) -> float | None:
            return c[1] / c[0] if c and c[0] else None

        total_convs = [sum(c[0] for c in convs.values()), sum(c[1] for c in convs.values())]
        by_channel = [
            ChannelStats(
                channel=ch,
                conversations=convs.get(ch, [0, 0])[0],
                messages=msgs.get(ch, [0, 0])[0],
                messages_received=msgs.get(ch, [0, 0])[1],
                waiting=waiting_now.get(ch, 0),
                response_rate=rate(convs.get(ch)),
            )
            for ch in Channel
            if ch in msgs or ch in convs or ch in waiting_now
        ]
        return InboxStats(
            contacts=contacts,
            conversations=total_convs[0],
            messages=sum(m[0] for m in msgs.values()),
            by_kind=by_kind,
            waiting=sum(waiting_now.values()),
            oldest_waiting_since=oldest,
            messages_received=sum(m[1] for m in msgs.values()),
            response_rate=rate(total_convs),
            median_first_response_seconds=round(median) if median is not None else None,
            by_channel=by_channel,
            by_category=by_category,
        )
