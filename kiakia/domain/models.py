"""Domain models: Kiakia's own language for messages and customers.

This module has NO dependencies on Telegram, Meta, HTTP or the database.
Every channel adapter converts its own format into these classes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional


class Channel(str, Enum):
    TELEGRAM = "telegram"
    MESSENGER = "messenger"
    INSTAGRAM = "instagram"
    WHATSAPP = "whatsapp"
    TIKTOK = "tiktok"
    EMAIL = "email"


class Direction(str, Enum):
    IN = "in"     # from the customer
    OUT = "out"   # from the business


class MessageKind(str, Enum):
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
    username: Optional[str] = None


@dataclass(frozen=True)
class Message:
    channel: Channel
    external_chat_id: str
    external_message_id: str
    direction: Direction
    kind: MessageKind
    sent_at: datetime            # must be timezone-aware (we store UTC)
    sender: Sender
    body: Optional[str] = None   # text or caption
    media_ref: Optional[str] = None  # platform file id (e.g. a voice note to download later)

    def __post_init__(self) -> None:
        # Guard rule: a naive datetime would silently shift by the server's time zone.
        if self.sent_at.tzinfo is None or self.sent_at.utcoffset() is None:
            raise ValueError("Message.sent_at must be timezone-aware")


@dataclass(frozen=True)
class Reject:
    """A record we could not use, and why. Counted and logged, never fatal."""
    reason: str
    ref: Optional[str] = None    # e.g. the update id, to find it again


@dataclass
class FetchResult:
    """What a MessageSource returns for one fetch."""
    messages: List[Message] = field(default_factory=list)
    rejects: List[Reject] = field(default_factory=list)
    ignored: int = 0                   # valid updates we deliberately skip (e.g. group chats)
    next_cursor: Optional[str] = None  # where to continue next time; None = nothing new


@dataclass(frozen=True)
class WaitingCustomer:
    """A customer whose last message has no reply yet (problem #1)."""
    contact_name: str
    channel: Channel
    external_chat_id: str
    waiting_since: datetime
    last_message_preview: Optional[str]

    def waited(self, now: datetime) -> timedelta:
        return now - self.waiting_since
