"""Composition root: the ONE place that builds concrete objects and wires them together.

Entry points (the CLI and the web API) ask this module for ready-made use
cases and never import adapters themselves. Swapping Telegram for Meta, or adding
a new channel, means changing wiring here only.

Connections are opened with `with app(settings) as services:` and are ALWAYS
closed when the block ends, even if an error happens inside it. The web API instead
borrows connections from a pool (`api_pool` + `pooled_app`), so a request doesn't pay
for opening a new connection (about 55 ms) every time.
"""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field

import psycopg
from psycopg_pool import ConnectionPool

from kiakia.adapters.demo.sender import DemoSender
from kiakia.adapters.email.mailbox import ImapMailbox
from kiakia.adapters.email.sender import EmailSender
from kiakia.adapters.email.source import EmailSource
from kiakia.adapters.postgres.pool import borrow, open_pool
from kiakia.adapters.postgres.repository import PostgresStore, connect
from kiakia.adapters.spreadsheet.writers import CsvWriter, XlsxWriter
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
from kiakia.application.privacy import ExportCustomers, ForgetCustomer
from kiakia.application.replies import SendReply
from kiakia.application.stats import GetInboxStats
from kiakia.application.waiting import ListWaitingCustomers
from kiakia.config import ConfigError, Settings
from kiakia.domain.models import Channel, ExportFormat
from kiakia.domain.ports import MessageSender, MessageSource, SpreadsheetWriter


@dataclass(frozen=True)
class Services:
    """Use cases ready to run, sharing one database connection."""

    store: PostgresStore
    settings: Settings
    senders: Mapping[Channel, MessageSender] = field(default_factory=dict)

    def import_messages(self, wait_seconds: int = 0, channel: Channel = Channel.TELEGRAM) -> ImportMessages:
        if self.settings.demo_mode:
            raise ConfigError(
                "import/watch are off in demo mode: the demo database only holds fake customers (ADR 0008)."
            )
        source = message_source(self.settings, channel, wait_seconds)
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

    def forget_customer(self) -> ForgetCustomer:
        return ForgetCustomer(self.store)

    def export_customers(self) -> ExportCustomers:
        return ExportCustomers(customers=self.store, log=self.store, writers=SPREADSHEET_WRITERS)

    def check_health(self) -> None:
        """Raises StorageUnavailable if the database can't be reached."""
        self.store.ping()


def message_source(settings: Settings, channel: Channel, wait_seconds: int = 0) -> MessageSource:
    """The adapter that brings in messages from `channel`. Adding a channel = one more branch here."""
    if channel is Channel.TELEGRAM:
        token = settings.telegram_bot_token.get_secret_value()
        if not token:
            raise ConfigError("Missing setting TELEGRAM_BOT_TOKEN. Copy .env.example to .env and fill it in.")
        return TelegramSource(TelegramClient(token), wait_seconds=wait_seconds)
    if channel is Channel.EMAIL:
        if not settings.email_enabled:
            raise ConfigError("Email is not set up: fill in EMAIL_ADDRESS and EMAIL_APP_PASSWORD in .env.")
        password = settings.email_app_password.get_secret_value()
        return EmailSource(
            lambda: ImapMailbox(
                settings.email_imap_host, settings.email_imap_port, settings.email_address, password
            ),
            own_address=settings.email_address,
            wait_seconds=wait_seconds,
        )
    raise ConfigError(f"Importing from {channel.value} is not supported yet.")


SPREADSHEET_WRITERS: Mapping[ExportFormat, SpreadsheetWriter] = {
    ExportFormat.CSV: CsvWriter(),
    ExportFormat.XLSX: XlsxWriter(),
}


def default_senders(settings: Settings) -> dict[Channel, MessageSender]:
    """One sender per channel Kiakia can reply on. Telegram needs its bot token."""
    if settings.demo_mode:  # offline: nothing is ever sent to a real person (ADR 0008)
        return dict.fromkeys(Channel, DemoSender())
    senders: dict[Channel, MessageSender] = {}
    token = settings.telegram_bot_token.get_secret_value()
    if token:
        senders[Channel.TELEGRAM] = TelegramSender(TelegramClient(token))
    if settings.email_enabled:
        senders[Channel.EMAIL] = EmailSender(
            settings.email_smtp_host,
            settings.email_smtp_port,
            settings.email_address,
            settings.email_app_password.get_secret_value(),
            settings.email_display_name,
        )
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


def api_pool(settings: Settings) -> ConnectionPool[psycopg.Connection]:
    """The web API's connection pool. Close it when the API stops."""
    return open_pool(
        settings.db_host,
        settings.db_port,
        settings.db_user,
        settings.db_password.get_secret_value(),
        settings.db_name,
    )


@contextmanager
def pooled_app(
    pool: ConnectionPool[psycopg.Connection], settings: Settings, senders: Mapping[Channel, MessageSender]
) -> Iterator[Services]:
    """Like `app`, but with a connection borrowed from `pool` and given back afterwards."""
    with borrow(pool) as conn:
        yield Services(store=PostgresStore(conn), settings=settings, senders=senders)
