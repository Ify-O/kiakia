"""Turn Telegram Bot API updates into Kiakia domain Messages.

Telegram format: https://core.telegram.org/bots/api#update
Each update has an `update_id` and at most one payload, e.g. `message` or
`edited_message`. Other payloads (callback_query, my_chat_member, ...) are
not customer messages, so we ignore them.

Decisions:
- Only PRIVATE chats are customer conversations. Group/channel posts are ignored.
- Required: update_id, message_id, chat.id, date. Missing -> Reject (never crash).
- `date` is a unix timestamp in seconds -> timezone-aware UTC datetime.
- An edited message keeps its message_id, so saving it again just updates the row.
- Kind is decided by which media field is present; caption counts as body.
- next_cursor = highest update_id + 1, counting rejected and ignored updates
  too, so one broken update can't block the queue forever.
"""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, TypeGuard

from kiakia.domain.models import Channel, Direction, FetchResult, Message, MessageKind, Reject, Sender

MESSAGE_KEYS = ("message", "edited_message")

# (Telegram field, our kind). Checked in order; first match wins.
_MEDIA_KINDS = (
    ("voice", MessageKind.VOICE),
    ("audio", MessageKind.AUDIO),
    ("photo", MessageKind.IMAGE),
    ("video", MessageKind.VIDEO),
    ("video_note", MessageKind.VIDEO),
    ("document", MessageKind.DOCUMENT),
    ("sticker", MessageKind.STICKER),
)


class _Bad(ValueError):
    """Internal: this update can't be used (becomes a Reject)."""


def _is_int(value: Any) -> TypeGuard[int]:
    """True for real integers only. In Python, True/False are also ints (True == 1)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required(obj: dict[str, Any], key: str, where: str) -> Any:
    value = obj.get(key)
    if value is None or value == "":
        raise _Bad(f"missing {where}.{key}")
    return value


def _to_utc(unix_seconds: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(unix_seconds), tz=UTC)
    except (TypeError, ValueError, OverflowError, OSError) as exc:
        raise _Bad(f"bad date {unix_seconds!r}") from exc


def _sender(user: dict[str, Any] | None, chat: dict[str, Any]) -> Sender:
    """Prefer `from`; fall back to the private chat's own name fields."""
    src = user or chat
    user_id = _clean(src.get("id")) or _clean(chat.get("id"))
    name = " ".join(p for p in (_clean(src.get("first_name")), _clean(src.get("last_name"))) if p)
    username = _clean(src.get("username"))
    display = name or (f"@{username}" if username else f"Telegram user {user_id}")
    return Sender(external_id=str(user_id), display_name=display, username=username)


def _kind_and_media(msg: dict[str, Any]) -> tuple[MessageKind, str | None]:
    if _clean(msg.get("text")):
        return MessageKind.TEXT, None
    for field, kind in _MEDIA_KINDS:
        media = msg.get(field)
        if not media:
            continue
        if isinstance(media, list):  # photo = list of sizes; the last one is the largest
            media = media[-1]
        file_id = _clean(media.get("file_id")) if isinstance(media, dict) else None
        return kind, file_id
    return MessageKind.OTHER, None


def parse_update(update: dict[str, Any]) -> Message | None:
    """Return a Message, None if the update is deliberately ignored, or raise _Bad."""
    msg = next((update[k] for k in MESSAGE_KEYS if isinstance(update.get(k), dict)), None)
    if msg is None:
        return None  # not a message update (e.g. callback_query)

    chat = _required(msg, "chat", "message")
    if not isinstance(chat, dict):
        raise _Bad("message.chat is not an object")
    if chat.get("type") != "private":
        return None  # groups/channels aren't customer chats

    kind, media_ref = _kind_and_media(msg)
    return Message(
        channel=Channel.TELEGRAM,
        external_chat_id=str(_required(chat, "id", "chat")),
        external_message_id=str(_required(msg, "message_id", "message")),
        direction=Direction.IN,  # getUpdates only delivers messages sent TO the bot
        kind=kind,
        sent_at=_to_utc(_required(msg, "date", "message")),
        sender=_sender(msg.get("from"), chat),
        body=_clean(msg.get("text")) or _clean(msg.get("caption")),
        media_ref=media_ref,
    )


def parse_updates(updates: Iterable[Any]) -> FetchResult:
    result = FetchResult()
    max_update_id: int | None = None

    for update in updates:
        if not isinstance(update, dict):
            result.rejects.append(Reject("update is not an object"))
            continue
        update_id = update.get("update_id")
        if _is_int(update_id):
            max_update_id = update_id if max_update_id is None else max(max_update_id, update_id)
        ref = str(update_id) if update_id is not None else None
        try:
            if not _is_int(update_id):
                raise _Bad("missing or invalid update_id")
            message = parse_update(update)
        except (_Bad, ValueError) as exc:
            result.rejects.append(Reject(str(exc), ref))
            continue
        if message is None:
            result.ignored += 1
        else:
            result.messages.append(message)

    if max_update_id is not None:
        result.next_cursor = str(max_update_id + 1)
    return result
