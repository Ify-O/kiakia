"""HTTP response shapes, matching openapi.yaml exactly (camelCase JSON, nullable fields present).

These are the API's "view models": they translate domain objects into the contract.
Contract tests check that what these produce validates against openapi.yaml.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pydantic.alias_generators import to_camel

from kiakia.domain.models import (
    Category,
    Channel,
    ChannelStats,
    ConversationDetail,
    ConversationStatus,
    ConversationSummary,
    Direction,
    FeedItem,
    InboxStats,
    MessageKind,
    Page,
    StoredMessage,
)


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def _utc_or_none(dt: datetime | None) -> datetime | None:
    return dt.astimezone(UTC) if dt is not None else None


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ContactOut(ApiModel):
    id: int
    display_name: str
    username: str | None


class ConversationSummaryOut(ApiModel):
    id: int
    channel: Channel
    contact: ContactOut
    status: ConversationStatus
    waiting_since: datetime | None
    last_message_at: datetime
    last_reply_at: datetime | None
    last_message_preview: str | None
    unanswered_count: int
    category: Category | None
    resolved_at: datetime | None

    @classmethod
    def from_domain(cls, c: ConversationSummary) -> "ConversationSummaryOut":
        return cls(
            id=c.id,
            channel=c.channel,
            contact=ContactOut(id=c.contact_id, display_name=c.contact_name, username=c.contact_username),
            status=c.status,
            waiting_since=_utc_or_none(c.waiting_since),
            last_message_at=_utc(c.last_message_at),
            last_reply_at=_utc_or_none(c.last_reply_at),
            last_message_preview=c.last_message_preview,
            unanswered_count=c.unanswered_count,
            category=c.category,
            resolved_at=_utc_or_none(c.resolved_at),
        )


class ConversationDetailOut(ConversationSummaryOut):
    created_at: datetime
    message_count: int

    @classmethod
    def from_detail(cls, d: ConversationDetail) -> "ConversationDetailOut":
        base = ConversationSummaryOut.from_domain(d.summary).model_dump()
        return cls(**base, created_at=_utc(d.created_at), message_count=d.message_count)


class MessageOut(ApiModel):
    id: int
    conversation_id: int
    channel: Channel
    direction: Direction
    kind: MessageKind
    body: str | None
    has_media: bool
    sent_at: datetime

    @classmethod
    def from_domain(cls, m: StoredMessage) -> "MessageOut":
        return cls(
            id=m.id,
            conversation_id=m.conversation_id,
            channel=m.channel,
            direction=m.direction,
            kind=m.kind,
            body=m.body,
            has_media=m.has_media,
            sent_at=_utc(m.sent_at),
        )


class PageInfoOut(ApiModel):
    limit: int
    next_cursor: str | None


class ConversationPageOut(ApiModel):
    data: list[ConversationSummaryOut]
    page: PageInfoOut

    @classmethod
    def from_domain(cls, page: Page[ConversationSummary], limit: int) -> "ConversationPageOut":
        return cls(
            data=[ConversationSummaryOut.from_domain(c) for c in page.items],
            page=PageInfoOut(limit=limit, next_cursor=page.next_cursor),
        )


class MessagePageOut(ApiModel):
    data: list[MessageOut]
    page: PageInfoOut

    @classmethod
    def from_domain(cls, page: Page[StoredMessage], limit: int) -> "MessagePageOut":
        return cls(
            data=[MessageOut.from_domain(m) for m in page.items],
            page=PageInfoOut(limit=limit, next_cursor=page.next_cursor),
        )


class ChannelStatsOut(ApiModel):
    channel: Channel
    conversations: int
    messages: int
    messages_received: int
    waiting: int
    response_rate: float | None

    @classmethod
    def from_domain(cls, c: ChannelStats) -> "ChannelStatsOut":
        return cls(
            channel=c.channel,
            conversations=c.conversations,
            messages=c.messages,
            messages_received=c.messages_received,
            waiting=c.waiting,
            response_rate=c.response_rate,
        )


class InboxStatsOut(ApiModel):
    contacts: int
    conversations: int
    messages: int
    messages_received: int
    waiting: int
    oldest_waiting_since: datetime | None
    response_rate: float | None
    median_first_response_seconds: int | None
    messages_by_kind: dict[MessageKind, int]
    by_channel: list[ChannelStatsOut]
    by_category: dict[str, int]

    @classmethod
    def from_domain(cls, s: InboxStats) -> "InboxStatsOut":
        return cls(
            contacts=s.contacts,
            conversations=s.conversations,
            messages=s.messages,
            messages_received=s.messages_received,
            waiting=s.waiting,
            oldest_waiting_since=_utc_or_none(s.oldest_waiting_since),
            response_rate=s.response_rate,
            median_first_response_seconds=s.median_first_response_seconds,
            messages_by_kind=s.by_kind,
            by_channel=[ChannelStatsOut.from_domain(c) for c in s.by_channel],
            by_category=s.by_category,
        )


class FeedItemOut(ApiModel):
    message: MessageOut
    contact: ContactOut

    @classmethod
    def from_domain(cls, f: FeedItem) -> "FeedItemOut":
        return cls(
            message=MessageOut.from_domain(f.message),
            contact=ContactOut(id=f.contact_id, display_name=f.contact_name, username=f.contact_username),
        )


class FeedPageOut(ApiModel):
    data: list[FeedItemOut]
    page: PageInfoOut

    @classmethod
    def from_domain(cls, page: Page[FeedItem], limit: int) -> "FeedPageOut":
        return cls(
            data=[FeedItemOut.from_domain(f) for f in page.items],
            page=PageInfoOut(limit=limit, next_cursor=page.next_cursor),
        )


class ConversationUpdateIn(ApiModel):
    """PATCH body. Same rules as ConversationUpdate in openapi.yaml. Strict types: "true" is not a bool."""

    resolved: StrictBool = False  # only used if the client sent it (see model_fields_set)
    category: Category | None = None


class ReplyIn(ApiModel):
    """Request body of POST /replies. Same rules as ReplyCreate in openapi.yaml."""

    text: str = Field(min_length=1, max_length=4096, pattern=r"\S")  # \S: not only spaces


class HealthOut(ApiModel):
    status: str = "ok"
    database: str = "ok"
