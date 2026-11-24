"""Ports: the interfaces the application depends on.

Use cases talk to these, never to Telegram or PostgreSQL directly. Adapters
implement them. That is the decoupling: swap or add an adapter and the use
cases don't change. Each port is small (interface segregation).
"""

from datetime import datetime
from typing import Protocol

from .models import (
    ConversationDetail,
    ConversationFilter,
    ConversationSummary,
    FetchResult,
    InboxStats,
    Message,
    Reject,
    RunStatus,
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

    def inbox_stats(self) -> InboxStats: ...


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


class HealthCheck(Protocol):
    def ping(self) -> None:
        """Raise StorageUnavailable if the storage can't be reached."""
        ...
