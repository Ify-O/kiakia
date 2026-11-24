"""Composition root: the ONE place that builds concrete objects and wires them together.

Entry points (the CLI and the web API) ask this module for ready-made use
cases and never import adapters themselves. Swapping Telegram for Meta, or adding
a new channel, means changing wiring here only.

Connections are opened with `with app(settings) as services:` and are ALWAYS
closed when the block ends, even if an error happens inside it.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

from kiakia.adapters.postgres.repository import PostgresStore, connect
from kiakia.adapters.telegram.client import TelegramClient
from kiakia.adapters.telegram.sender import TelegramSender
from kiakia.adapters.telegram.source import TelegramSource
from kiakia.application.conversations import (
    GetConversation,
    ListConversations,
    ListFeed,
    ListMessages,
    UpdateConversation,
)
from kiakia.application.customers import GetCustomer, ListCustomers, RecordConsent
from kiakia.application.import_messages import ImportMessages
from kiakia.application.replies import SendReply
from kiakia.application.stats import GetInboxStats
from kiakia.application.waiting import ListWaitingCustomers
from kiakia.config import ConfigError, Settings
from kiakia.domain.models import Channel
from kiakia.domain.ports import MessageSender


@dataclass(frozen=True)
class Services:
    """Use cases ready to run, sharing one database connection."""

    store: PostgresStore
    settings: Settings
    senders: Mapping[Channel, MessageSender] = field(default_factory=dict)

    def import_messages(self) -> ImportMessages:
        token = self.settings.telegram_bot_token.get_secret_value()
        if not token:
            raise ConfigError("Missing setting TELEGRAM_BOT_TOKEN. Copy .env.example to .env and fill it in.")
        source = TelegramSource(TelegramClient(token))
        return ImportMessages(
            source=source,
            messages=self.store,
            rejects=self.store,
            cursors=self.store,
            runs=self.store,
            opt_outs=self.store,  # customers replying STOP leave the offers list (ADR 0007)
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

    def list_feed(self) -> ListFeed:
        return ListFeed(self.store)

    def update_conversation(self) -> UpdateConversation:
        return UpdateConversation(self.store, self.store)

    def send_reply(self) -> SendReply:
        return SendReply(conversations=self.store, replies=self.store, keys=self.store, senders=self.senders)

    def list_customers(self) -> ListCustomers:
        return ListCustomers(self.store)

    def get_customer(self) -> GetCustomer:
        return GetCustomer(self.store)

    def record_consent(self) -> RecordConsent:
        return RecordConsent(self.store)

    def check_health(self) -> None:
        """Raises StorageUnavailable if the database can't be reached."""
        self.store.ping()


def default_senders(settings: Settings) -> dict[Channel, MessageSender]:
    """One sender per channel Kiakia can reply on. Telegram needs its bot token."""
    senders: dict[Channel, MessageSender] = {}
    token = settings.telegram_bot_token.get_secret_value()
    if token:
        senders[Channel.TELEGRAM] = TelegramSender(TelegramClient(token))
    return senders


@contextmanager
def app(settings: Settings, senders: Mapping[Channel, MessageSender] | None = None) -> Iterator[Services]:
    """`senders` can be replaced (e.g. by fakes in tests, so tests never message real customers)."""
    conn = connect(
        settings.db_host,
        settings.db_port,
        settings.db_user,
        settings.db_password.get_secret_value(),
        settings.db_name,
    )
    try:
        yield Services(
            store=PostgresStore(conn),
            settings=settings,
            senders=default_senders(settings) if senders is None else senders,
        )
    finally:
        conn.close()  # W10: never leak a connection
