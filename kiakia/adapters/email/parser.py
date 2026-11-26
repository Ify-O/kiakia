"""Turn one raw email into a Kiakia domain Message (docs/adr/0010).

Email is untrusted input from anyone on the internet, so:
- it is parsed with the standard `email` package (modern policy), never by hand;
- HTML is turned into plain text and never rendered; NUL characters are removed;
- quoted earlier messages and signatures are cut, and the body is capped;
- robots (newsletters, auto-replies, bounces, no-reply) are IGNORED, and mail the provider marked
  as spoofed (dmarc=fail) is REJECTED, kept in the dead-letter table for inspection.

`parse_email` returns a Message, None (deliberately ignored) or raises BadEmail (rejected).
"""

import re
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import ClassVar

from kiakia.domain.models import Channel, Direction, Message, MessageKind, Sender

MAX_BODY = 4000
# The same practical check as the contract's email field (openapi.yaml).
ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
_ROBOT_NAMES = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster"}
_QUOTE_STARTS = (
    re.compile(r"^On .{0,300}wrote:\s*$", re.MULTILINE),  # Gmail, Apple Mail, Thunderbird
    re.compile(r"^On .{0,300}\n.{0,300}wrote:\s*$", re.MULTILINE),  # same, wrapped onto two lines
    re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.MULTILINE | re.IGNORECASE),  # Outlook
    re.compile(r"^_{10,}\s*$", re.MULTILINE),  # Outlook web
    re.compile(r"^-- ?$", re.MULTILINE),  # standard signature separator
    re.compile(r"^Sent from my \w+", re.MULTILINE),  # phone signatures
)
_REPLY_PREFIX = re.compile(r"^\s*((re|fw|fwd|aw|sv)\s*:\s*)+", re.IGNORECASE)


class BadEmail(ValueError):
    """This email can't be used (becomes a Reject)."""


def is_address(value: str) -> bool:
    return len(value) <= 254 and ADDRESS.fullmatch(value) is not None


class _HtmlText(HTMLParser):
    """HTML to plain text: keeps the words, drops tags, scripts and styles."""

    _BREAKS: ClassVar[set[str]] = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag == "blockquote":
            self._skip += 1  # a quoted earlier message
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "head", "blockquote") and self._skip:
            self._skip -= 1
        if tag in self._BREAKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    reader = _HtmlText()
    reader.feed(html)
    reader.close()
    return "".join(reader.parts)


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def strip_quoted(text: str) -> str:
    """Keep only what the customer wrote now: cut the earlier messages and the signature."""
    cut = len(text)
    for pattern in _QUOTE_STARTS:
        match = pattern.search(text)
        if match and match.start() > 0:
            cut = min(cut, match.start())
    kept = [line for line in text[:cut].split("\n") if not line.lstrip().startswith(">")]
    return "\n".join(kept)


def _header(msg: EmailMessage, name: str) -> str:
    try:
        value = msg.get(name)
    except (ValueError, TypeError, IndexError):  # a header so broken the parser gives up on it
        raise BadEmail(f"unreadable {name} header") from None
    return str(value).strip() if value is not None else ""


def _robot_reason(msg: EmailMessage, address: str) -> str | None:
    """Why this is an automatic email rather than a person writing, or None for a person."""
    if _header(msg, "List-Id") or _header(msg, "List-Unsubscribe"):
        return "mailing list"
    if _header(msg, "Precedence").lower() in ("bulk", "list", "junk"):
        return "bulk"
    auto = _header(msg, "Auto-Submitted").lower()
    if auto and auto != "no":
        return "auto-submitted"
    if _header(msg, "X-Autoreply") or _header(msg, "X-Autorespond"):
        return "auto-reply"
    local = address.split("@", 1)[0].lower()
    if local in _ROBOT_NAMES or local.startswith(("noreply", "no-reply", "bounce")):
        return "no-reply sender"
    return None


def _failed_dmarc(msg: EmailMessage) -> bool:
    try:
        results = msg.get_all("Authentication-Results") or []
    except (ValueError, TypeError, IndexError):
        return False
    return any(re.search(r"\bdmarc\s*=\s*fail\b", str(r), re.IGNORECASE) for r in results)


def _sender(msg: EmailMessage) -> Sender:
    raw_from = _header(msg, "From")
    if not raw_from:
        raise BadEmail("missing From")
    addresses = getaddresses([raw_from])
    if len(addresses) != 1:
        raise BadEmail("From must name exactly one sender")
    name, address = addresses[0]
    address = address.strip().lower()
    if not is_address(address):
        raise BadEmail("From is not a valid email address")
    display = _clean(name.replace("\n", " ")) or address
    return Sender(external_id=address, display_name=display[:200], username=None)


def _body_text(msg: EmailMessage) -> str | None:
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return None
        content = part.get_content()
    except (LookupError, ValueError, TypeError, AttributeError, UnicodeError):
        raise BadEmail("unreadable body") from None
    if not isinstance(content, str):
        return None
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return _clean(strip_quoted(content.replace("\r\n", "\n")))


def _attachment_kind(msg: EmailMessage) -> MessageKind | None:
    try:
        for part in msg.iter_attachments():
            main = part.get_content_maintype()
            return {
                "image": MessageKind.IMAGE,
                "audio": MessageKind.AUDIO,
                "video": MessageKind.VIDEO,
            }.get(main, MessageKind.DOCUMENT)
    except (ValueError, TypeError, AttributeError):
        return MessageKind.DOCUMENT
    return None


INBOUND_ID_PREFIX = "in:"


def _message_id(msg: EmailMessage, fallback: str) -> str:
    """The sender chooses an email's Message-ID, so it is only trusted to spot the SAME email twice.
    The "in:" prefix keeps it apart from the ids of our own replies (which have no prefix): a
    customer copying a reply's Message-ID into a new email can never match, let alone rewrite, it."""
    value = _header(msg, "Message-ID").strip("<> \t")
    if not value or len(value) > 250 or not value.isprintable():
        value = fallback
    return INBOUND_ID_PREFIX + value


def _with_subject(subject: str, body: str | None) -> str | None:
    """A first email's subject often IS the question ("Price of the blue bag?"): keep it, unless
    it's a reply ("Re: …") or the body already says it."""
    subject = _clean(subject.replace("\n", " ")) or ""
    if not subject or _REPLY_PREFIX.match(subject):
        return body
    if body is None:
        return subject
    if subject.lower() in body.lower():
        return body
    return f"{subject}\n\n{body}"


def parse_email(
    raw: bytes,
    *,
    received_at: datetime,
    fallback_id: str,
    own_address: str,
    headers_only: bool = False,
) -> Message | None:
    """`received_at` is the mailbox's arrival time (INTERNALDATE). `headers_only` = the email was
    too big to download, so only its headers were fetched."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    if not isinstance(msg, EmailMessage):  # pragma: no cover - policy.default always gives one
        raise BadEmail("not an email")

    sender = _sender(msg)
    if sender.external_id == own_address.lower():
        return None  # our own mail (e.g. a copy of a reply)
    if _robot_reason(msg, sender.external_id):
        return None
    if _failed_dmarc(msg):
        raise BadEmail("failed the sender check (dmarc=fail): the From address may be forged")

    subject = _header(msg, "Subject")
    if headers_only:
        kind = MessageKind.DOCUMENT
        body = _with_subject(subject, "📎 A large email: open it in the mailbox to see everything.")
    else:
        text = _body_text(msg)
        attachment = _attachment_kind(msg)
        body = _with_subject(subject, text)
        kind = MessageKind.TEXT if text or not attachment else attachment
        if body is None and kind is MessageKind.TEXT:
            kind = MessageKind.OTHER  # nothing readable at all
    if body is not None and len(body) > MAX_BODY:
        body = body[: MAX_BODY - 1] + "…"

    return Message(
        channel=Channel.EMAIL,
        external_chat_id=sender.external_id,  # one conversation per sender address
        external_message_id=_message_id(msg, fallback_id),
        direction=Direction.IN,
        kind=kind,
        sent_at=received_at,
        sender=sender,
        body=body,
        media_ref=None,  # attachments are never stored
    )
