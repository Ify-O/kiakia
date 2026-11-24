"""Domain models: Kiakia's own language for messages and customers.

This module has NO dependencies on Telegram, Meta, HTTP or the database.
Every channel adapter converts its own format into these classes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from .errors import InvalidQuery


class Channel(StrEnum):
    TELEGRAM = "telegram"
    MESSENGER = "messenger"
    INSTAGRAM = "instagram"
    WHATSAPP = "whatsapp"
    TIKTOK = "tiktok"
    EMAIL = "email"


class Direction(StrEnum):
    IN = "in"  # from the customer
    OUT = "out"  # from the business


class RunStatus(StrEnum):
    """Outcome of one import run (matches the CHECK constraint on import_runs.status)."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MessageKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    STICKER = "sticker"
    OTHER = "other"


@dataclass(frozen=True)
class Sender:
    """Who sent a message, as known on one channel."""

    external_id: str
    display_name: str
    username: str | None = None


@dataclass(frozen=True)
class Message:
    channel: Channel
    external_chat_id: str
    external_message_id: str
    direction: Direction
    kind: MessageKind
    sent_at: datetime  # must be timezone-aware (we store UTC)
    sender: Sender
    body: str | None = None  # text or caption
    media_ref: str | None = None  # platform file id (e.g. a voice note to download later)

    def __post_init__(self) -> None:
        # Guard rule: a naive datetime would silently shift by the server's time zone.
        if self.sent_at.tzinfo is None or self.sent_at.utcoffset() is None:
            raise ValueError("Message.sent_at must be timezone-aware")


@dataclass(frozen=True)
class Reject:
    """A record we could not use, and why. Counted, logged and kept (dead letter), never fatal."""

    reason: str
    ref: str | None = None  # e.g. the update id, to find it again
    raw: Any = field(default=None, compare=False)  # the original payload, for inspection/replay


@dataclass
class FetchResult:
    """What a MessageSource returns for one fetch."""

    messages: list[Message] = field(default_factory=list)
    rejects: list[Reject] = field(default_factory=list)
    ignored: int = 0  # valid updates we deliberately skip (e.g. group chats)
    next_cursor: str | None = None  # where to continue next time; None = nothing new


@dataclass(frozen=True)
class WaitingCustomer:
    """A customer whose last message has no reply yet (problem #1)."""

    contact_name: str
    channel: Channel
    external_chat_id: str
    waiting_since: datetime
    last_message_preview: str | None

    def waited(self, now: datetime) -> timedelta:
        return now - self.waiting_since


@dataclass(frozen=True)
class InboxStats:
    """A quick overview of what Kiakia holds."""

    contacts: int
    conversations: int
    messages: int
    by_kind: dict[MessageKind, int]
    waiting: int = 0
    oldest_waiting_since: datetime | None = None
    messages_received: int = 0
    response_rate: float | None = None
    median_first_response_seconds: int | None = None
    by_channel: list["ChannelStats"] = field(default_factory=list)
    by_category: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ChannelStats:
    channel: Channel
    conversations: int
    messages: int
    messages_received: int
    waiting: int
    response_rate: float | None


# ---------- Read models for browsing the inbox (API / UI) ----------


class ConversationStatus(StrEnum):
    WAITING = "waiting"  # the customer has messages with no reply (and not resolved after them)
    ANSWERED = "answered"  # the business replied last
    RESOLVED = "resolved"  # marked done; a new customer message makes it WAITING again (ADR 0006)


class ConsentStatus(StrEnum):
    """Customer consent (ADR 0007). UNKNOWN = never asked (a new customer)."""

    UNKNOWN = "unknown"
    ASKED = "asked"
    GRANTED = "granted"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"


class ConsentKind(StrEnum):
    DETAILS = "details"  # save my name / phone / email
    MARKETING = "marketing"  # send me offers and sales


class Category(StrEnum):
    """A light label staff give a conversation (ADR 0006: not a CRM)."""

    ENQUIRY = "enquiry"
    ORDER = "order"
    COMPLAINT = "complaint"
    OTHER = "other"


@dataclass(frozen=True)
class Period:
    """A time window: start inclusive, end exclusive; None = open-ended."""

    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (("from", self.start), ("to", self.end)):
            if value is not None and value.utcoffset() is None:
                raise InvalidQuery(name, "must include a time zone, e.g. 2026-11-24T00:00:00Z")
        if self.start is not None and self.end is not None and self.start >= self.end:
            raise InvalidQuery("from", "must be before 'to'")


class ConversationSort(StrEnum):
    RECENT = "recent"  # most recent message first
    LONGEST_WAITING = "longestWaiting"  # longest-waiting customer first (waiting only)


@dataclass(frozen=True)
class ConversationFilter:
    status: ConversationStatus | None = None  # None = all
    channel: Channel | None = None
    search: str | None = None  # in the customer's name or username
    sort: ConversationSort = ConversationSort.RECENT
    category: Category | None = None
    uncategorized: bool = False  # only conversations without a category
    period: Period = field(default_factory=Period)  # on the last message time


@dataclass(frozen=True)
class ConversationSummary:
    id: int
    channel: Channel
    external_chat_id: str  # the chat on the channel (needed to reply); internal, not in the API
    contact_id: int
    contact_name: str
    contact_username: str | None
    status: ConversationStatus
    waiting_since: datetime | None
    last_message_at: datetime
    last_reply_at: datetime | None
    last_message_preview: str | None
    unanswered_count: int
    category: Category | None = None
    resolved_at: datetime | None = None
    contact_details_consent: ConsentStatus = ConsentStatus.UNKNOWN
    contact_marketing_consent: ConsentStatus = ConsentStatus.UNKNOWN


@dataclass(frozen=True)
class ConversationDetail:
    summary: ConversationSummary
    created_at: datetime
    message_count: int


@dataclass(frozen=True)
class StoredMessage:
    """A message as stored by Kiakia (with our own id), for reading."""

    id: int
    conversation_id: int
    direction: Direction
    kind: MessageKind
    body: str | None
    has_media: bool
    sent_at: datetime
    channel: Channel


@dataclass(frozen=True)
class FeedItem:
    """A message plus the customer it belongs to (for the all-messages view)."""

    message: StoredMessage
    contact_id: int
    contact_name: str
    contact_username: str | None
    contact_details_consent: ConsentStatus = ConsentStatus.UNKNOWN
    contact_marketing_consent: ConsentStatus = ConsentStatus.UNKNOWN


@dataclass(frozen=True)
class FeedFilter:
    channel: Channel | None = None
    direction: Direction | None = None
    period: Period = field(default_factory=Period)


@dataclass(frozen=True)
class ConversationChanges:
    """What PATCH /conversations changes. *_set says whether the field was given at all."""

    category: Category | None = None
    category_set: bool = False
    resolved_at: datetime | None = None
    resolved_set: bool = False


@dataclass(frozen=True)
class Page[T]:
    """One page of results. next_cursor is None on the last page."""

    items: list[T]
    next_cursor: str | None


@dataclass(frozen=True)
class SentMessage:
    """What a channel tells us after it accepted an outgoing message."""

    external_message_id: str
    sent_at: datetime


class ClaimOutcome(StrEnum):
    NEW = "new"  # first time we see this key: go ahead
    REPLAY = "replay"  # same key + same request, already done: return the stored result
    MISMATCH = "mismatch"  # same key, different request: refuse (409)
    IN_PROGRESS = "in_progress"  # same key, first request still running: refuse (409)


@dataclass(frozen=True)
class IdempotencyClaim:
    outcome: ClaimOutcome
    message_id: int | None = None  # for REPLAY: the stored reply to return again


# ---------- Customers + recorded consent (ADR 0007) ----------


@dataclass(frozen=True)
class CustomerChannel:
    channel: Channel
    username: str | None


@dataclass(frozen=True)
class Customer:
    id: int
    display_name: str
    phone: str | None  # only while details consent is granted
    email: str | None
    details_consent: ConsentStatus
    marketing_consent: ConsentStatus
    first_channel: Channel
    channels: list[CustomerChannel]
    first_contact_at: datetime
    last_contact_at: datetime
    conversation_count: int
    order_count: int


@dataclass(frozen=True)
class CustomerConversation:
    id: int
    channel: Channel
    status: ConversationStatus
    last_message_at: datetime


@dataclass(frozen=True)
class ConsentEvent:
    id: int
    kind: ConsentKind
    status: ConsentStatus
    channel: Channel | None
    wording: str | None
    evidence_message_id: int | None
    recorded_at: datetime


@dataclass(frozen=True)
class CustomerDetail:
    customer: Customer
    conversations: list[CustomerConversation]
    history: list[ConsentEvent]


@dataclass(frozen=True)
class CustomerFilter:
    search: str | None = None
    details_consent: ConsentStatus | None = None
    marketing_consent: ConsentStatus | None = None
    channel: Channel | None = None


@dataclass(frozen=True)
class CustomerDetailsInput:
    """What the customer chose to share. None = not shared."""

    name: str | None = None
    phone: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class ConsentChange:
    """One consent event to record, plus the resulting current status."""

    customer_id: int
    kind: ConsentKind
    status: ConsentStatus  # the event (asked / granted / declined / withdrawn)
    new_current: ConsentStatus  # what the customer's current status becomes
    channel: Channel | None = None
    wording: str | None = None
    evidence_message_id: int | None = None
    details: CustomerDetailsInput | None = None
