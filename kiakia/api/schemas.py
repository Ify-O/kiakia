"""HTTP response shapes, matching openapi.yaml exactly (camelCase JSON, nullable fields present).

These are the API's "view models": they translate domain objects into the contract.
Contract tests check that what these produce validates against openapi.yaml.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from kiakia.domain.models import (
    Channel,
    ConversationDetail,
    ConversationStatus,
    ConversationSummary,
    Direction,
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


class InboxStatsOut(ApiModel):
    contacts: int
    conversations: int
    messages: int
    waiting: int
    oldest_waiting_since: datetime | None
    messages_by_kind: dict[MessageKind, int]

    @classmethod
    def from_domain(cls, s: InboxStats) -> "InboxStatsOut":
        return cls(
            contacts=s.contacts,
            conversations=s.conversations,
            messages=s.messages,
            waiting=s.waiting,
            oldest_waiting_since=_utc_or_none(s.oldest_waiting_since),
            messages_by_kind=s.by_kind,
        )


class HealthOut(ApiModel):
    status: str = "ok"
    database: str = "ok"
