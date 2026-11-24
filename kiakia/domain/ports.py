"""Ports: the interfaces the application depends on.

Use cases talk to these, never to Telegram or PostgreSQL directly. Adapters
implement them. That is the decoupling: swap or add an adapter and the use
cases don't change. Each port is small (interface segregation).
"""

from typing import Protocol

from .models import FetchResult, Message, WaitingCustomer


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


class CursorStore(Protocol):
    def get_cursor(self, source: str) -> str | None: ...

    def set_cursor(self, source: str, cursor: str) -> None: ...


class ImportRunLog(Protocol):
    def start_run(self, source: str) -> int: ...

    def finish_run(
        self, run_id: int, *, status: str, seen: int, saved: int, bad: int, error: str | None = None
    ) -> None: ...
