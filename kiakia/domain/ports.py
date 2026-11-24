"""Ports: the interfaces the application depends on.

Use cases talk to these, never to Telegram or PostgreSQL directly. Adapters
implement them. That is the decoupling: swap or add an adapter and the use
cases don't change. Each port is small (interface segregation).
"""

from datetime import datetime
from typing import Protocol

from .models import (
    Channel,
    ConsentChange,
    ConversationChanges,
    ConversationDetail,
    ConversationFilter,
    ConversationSummary,
    Customer,
    CustomerDetail,
    CustomerFilter,
    FeedFilter,
    FeedItem,
    FetchResult,
    IdempotencyClaim,
    InboxStats,
    Message,
    Period,
    Reject,
    RunStatus,
    SentMessage,
    StoredMessage,
    WaitingCustomer,
)


class MessageSource(Protocol):
    """Somewhere messages come from (Telegram now; Meta, WhatsApp later)."""

    name: str  # unique, e.g. "telegram"; also the key for its cursor

    def fetch(self, cursor: str | None, limit: int) -> FetchResult:
        """Return messages after `cursor`. Must NOT lose data if we crash before saving."""
        ...


class MessageRepository(Protocol):
    def save_messages(self, messages: list[Message]) -> int:
        """Store messages idempotently (saving the same message twice changes nothing).
        Returns how many messages were processed."""
        ...

    def waiting_customers(self) -> list[WaitingCustomer]:
        """Conversations whose last message is from the customer, oldest wait first."""
        ...


class RejectStore(Protocol):
    """Dead-letter storage: keeps records we couldn't parse, so they can be inspected or replayed."""

    def save_rejects(self, source: str, rejects: list[Reject]) -> int:
        """Store rejects idempotently (same source + ref is stored once). Returns how many were new."""
        ...


class CursorStore(Protocol):
    def get_cursor(self, source: str) -> str | None: ...

    def set_cursor(self, source: str, cursor: str) -> None: ...


class ImportRunLog(Protocol):
    def start_run(self, source: str) -> int: ...

    def finish_run(
        self, run_id: int, *, status: RunStatus, seen: int, saved: int, bad: int, error: str | None = None
    ) -> None: ...


class StatsQuery(Protocol):
    """Read-only overview numbers (kept separate from MessageRepository: interface segregation)."""

    def inbox_stats(self, period: Period) -> InboxStats: ...


# A position in a sorted list: (sort time, id). Used for keyset pagination (docs/adr/0003).
Keyset = tuple[datetime, int]


class ConversationQuery(Protocol):
    """Read side for browsing conversations (used by the API)."""

    def list_conversations(
        self, filters: ConversationFilter, limit: int, after: Keyset | None
    ) -> list[ConversationSummary]:
        """Up to `limit` conversations in `filters.sort` order, strictly after `after`."""
        ...

    def get_conversation(self, conversation_id: int) -> ConversationDetail | None: ...

    def list_messages(self, conversation_id: int, limit: int, before: Keyset | None) -> list[StoredMessage]:
        """Up to `limit` messages, newest first, strictly older than `before`."""
        ...

    def list_feed(self, filters: FeedFilter, limit: int, before: Keyset | None) -> list[FeedItem]:
        """Messages across all conversations, newest first, strictly older than `before`."""
        ...


class ConversationUpdater(Protocol):
    def update_conversation(self, conversation_id: int, changes: ConversationChanges) -> None: ...


class HealthCheck(Protocol):
    def ping(self) -> None:
        """Raise StorageUnavailable if the storage can't be reached."""
        ...


class MessageSender(Protocol):
    """Sends a text to a customer on one channel (Telegram now; WhatsApp, Meta later)."""

    def send_text(self, external_chat_id: str, text: str) -> SentMessage:
        """Must NOT retry when it can't tell whether the message was delivered (no duplicates)."""
        ...


class ReplyStore(Protocol):
    def record_reply(
        self, conversation_id: int, external_message_id: str, text: str, sent_at: datetime
    ) -> StoredMessage:
        """Store an outgoing reply; the conversation stops being 'waiting'."""
        ...

    def get_message(self, message_id: int) -> StoredMessage | None: ...


class IdempotencyStore(Protocol):
    """Remembers Idempotency-Keys (docs/adr/0004)."""

    def claim(self, scope: str, key: str, request_hash: str) -> IdempotencyClaim:
        """Atomically: NEW if unseen (or expired), REPLAY if done with the same request,
        MISMATCH if used with a different request, IN_PROGRESS if still running."""
        ...

    def complete(self, scope: str, key: str, message_id: int) -> None: ...

    def release(self, scope: str, key: str) -> None:
        """Forget a key whose request failed before anything was sent, so it can be retried."""
        ...


class CustomerStore(Protocol):
    """The light customer list with recorded consent (ADR 0007)."""

    def list_customers(self, filters: CustomerFilter, limit: int, after: Keyset | None) -> list[Customer]:
        """Most recent contact first, strictly after `after` = (last contact time, id)."""
        ...

    def get_customer(self, customer_id: int) -> CustomerDetail | None: ...

    def conversation_evidence(
        self, customer_id: int, conversation_id: int
    ) -> tuple[Channel, int | None] | None:
        """The conversation's channel and latest customer message id, or None if it isn't this customer's."""
        ...

    def record_consent(self, change: ConsentChange) -> None:
        """Append the event and update the current status atomically. Declining/withdrawing
        details erases phone and email."""
        ...


class OptOutRecorder(Protocol):
    def withdraw_marketing(self, message: Message) -> bool:
        """The customer who sent `message` (e.g. "STOP") opts out of offers. True if recorded."""
        ...
