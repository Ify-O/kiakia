"""Domain models: Kiakia's own language for messages and customers.

This module has NO dependencies on Telegram, Meta, HTTP or the database.
Every channel adapter converts its own format into these classes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


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
