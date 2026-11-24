"""Composition root: the ONE place that builds concrete objects and wires them together.

Entry points (the CLI and the web API) ask this module for ready-made use
cases and never import adapters themselves. Swapping Telegram for Meta, or adding
a new channel, means changing wiring here only.

Connections are opened with `with app(settings) as services:` and are ALWAYS
closed when the block ends, even if an error happens inside it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from kiakia.adapters.postgres.repository import PostgresStore, connect
from kiakia.adapters.telegram.client import TelegramClient
from kiakia.adapters.telegram.source import TelegramSource
from kiakia.application.conversations import GetConversation, ListConversations, ListMessages
from kiakia.application.import_messages import ImportMessages
from kiakia.application.stats import GetInboxStats
from kiakia.application.waiting import ListWaitingCustomers
from kiakia.config import ConfigError, Settings


@dataclass(frozen=True)
class Services:
    """Use cases ready to run, sharing one database connection."""

    store: PostgresStore
    settings: Settings

    def import_messages(self) -> ImportMessages:
        token = self.settings.telegram_bot_token.get_secret_value()
        if not token:
            raise ConfigError("Missing setting TELEGRAM_BOT_TOKEN. Copy .env.example to .env and fill it in.")
        source = TelegramSource(TelegramClient(token))
        return ImportMessages(
            source=source, messages=self.store, rejects=self.store, cursors=self.store, runs=self.store
        )

    def list_waiting_customers(self) -> ListWaitingCustomers:
        return ListWaitingCustomers(self.store)

    def get_inbox_stats(self) -> GetInboxStats:
        return GetInboxStats(self.store)

    def list_conversations(self) -> ListConversations:
        return ListConversations(self.store)

    def get_conversation(self) -> GetConversation:
        return GetConversation(self.store)

    def list_messages(self) -> ListMessages:
        return ListMessages(self.store)

    def check_health(self) -> None:
        """Raises StorageUnavailable if the database can't be reached."""
        self.store.ping()


@contextmanager
def app(settings: Settings) -> Iterator[Services]:
    conn = connect(
        settings.db_host,
        settings.db_port,
        settings.db_user,
        settings.db_password.get_secret_value(),
        settings.db_name,
    )
    try:
        yield Services(store=PostgresStore(conn), settings=settings)
    finally:
        conn.close()  # W10: never leak a connection
