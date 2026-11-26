"""A small, safe wrapper around one IMAP mailbox (Python's imaplib), docs/adr/0010.

- Encrypted only (IMAPS, port 993) with certificate checks, and a timeout on every network call.
- The inbox is opened READ-ONLY and bodies are fetched with BODY.PEEK: Kiakia never marks mail as
  read, moves or deletes it.
- imaplib/socket errors become Kiakia errors here, so nothing outside this package knows imaplib:
  a refused sign-in is permanent (SourceRejectedRequest); network trouble is temporary
  (SourceUnavailable). The app password is never part of any message.
"""

import contextlib
import imaplib
import re
import ssl
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from kiakia.domain.errors import SourceRejectedRequest, SourceUnavailable

_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")
_INTERNALDATE = re.compile(rb'INTERNALDATE "([^"]+)"')
_STATUS = re.compile(rb"(UIDVALIDITY|UIDNEXT) (\d+)")


@dataclass(frozen=True)
class FetchedEmail:
    raw: bytes
    received_at: datetime  # when the mailbox received it (the sender can't fake this)
    headers_only: bool  # True = too big, only the headers were downloaded


class Mailbox(Protocol):
    """What EmailSource needs from a mailbox (a fake one is used in tests)."""

    uid_validity: int

    def uid_next(self) -> int: ...

    def uids_after(self, uid: int) -> list[int]: ...

    def fetch(self, uid: int, max_bytes: int) -> FetchedEmail | None: ...

    def close(self) -> None: ...


@contextmanager
def imap_errors(action: str) -> Iterator[None]:
    try:
        yield
    except imaplib.IMAP4.abort as exc:  # the connection broke: try again later
        raise SourceUnavailable(f"email: connection lost while trying to {action}") from exc
    except imaplib.IMAP4.error as exc:
        raise SourceRejectedRequest(
            f"email: the mail server refused to {action} ({_first_line(exc)})"
        ) from exc
    except (OSError, ssl.SSLError, TimeoutError) as exc:
        raise SourceUnavailable(f"email: could not {action} ({type(exc).__name__})") from exc


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return (text.splitlines() or [type(exc).__name__])[0][:200]


def _ok(typ: str, data: Sequence[object], action: str) -> Sequence[object]:
    if typ != "OK":
        raise imaplib.IMAP4.error(f"{action}: {typ}")
    return data


def parse_internaldate(meta: bytes) -> datetime:
    match = _INTERNALDATE.search(meta)
    if match:
        try:
            parsed = datetime.strptime(match.group(1).decode().strip(), "%d-%b-%Y %H:%M:%S %z")
            return parsed.astimezone(UTC)
        except ValueError:
            pass
    return datetime.now(UTC)  # a server that omits it: "now" is the best we know


class ImapMailbox:
    def __init__(self, host: str, port: int, address: str, password: str, timeout: float = 30.0):
        self.host = host
        with imap_errors("connect"):
            self._imap = imaplib.IMAP4_SSL(
                host, port, ssl_context=ssl.create_default_context(), timeout=timeout
            )
        try:
            with imap_errors("sign in (check EMAIL_ADDRESS and EMAIL_APP_PASSWORD)"):
                _ok(*self._imap.login(address, password), "login")
            with imap_errors("open the inbox"):
                _ok(*self._imap.select("INBOX", readonly=True), "select")
            self.uid_validity = self._status()["UIDVALIDITY"]
        except BaseException:
            self.close()
            raise

    def __repr__(self) -> str:
        return f"ImapMailbox(host={self.host!r})"  # never the password

    def _status(self) -> dict[str, int]:
        with imap_errors("read the inbox status"):
            data = _ok(*self._imap.status("INBOX", "(UIDVALIDITY UIDNEXT)"), "status")
        found = {
            k.decode(): int(v) for k, v in _STATUS.findall(data[0] if isinstance(data[0], bytes) else b"")
        }
        if "UIDVALIDITY" not in found or "UIDNEXT" not in found:
            raise SourceUnavailable("email: the mail server did not report the inbox status")
        return found

    def uid_next(self) -> int:
        return self._status()["UIDNEXT"]

    def uids_after(self, uid: int) -> list[int]:
        with imap_errors("check for new mail"):
            _ok(*self._imap.noop(), "noop")  # lets the server tell us about mail that just arrived
            data = _ok(*self._imap.uid("SEARCH", "UID", f"{uid + 1}:*"), "search")
        raw = data[0] if data and isinstance(data[0], bytes) else b""
        # "n:*" always includes the newest message, even when it is older than n: filter.
        return sorted(u for u in (int(x) for x in raw.split()) if u > uid)

    def fetch(self, uid: int, max_bytes: int) -> FetchedEmail | None:
        with imap_errors("read an email"):
            meta = _ok(*self._imap.uid("FETCH", str(uid), "(RFC822.SIZE INTERNALDATE)"), "fetch")
            first = meta[0] if meta else None
            if not isinstance(first, bytes):
                return None  # deleted in the meantime
            size_match = _SIZE.search(first)
            headers_only = size_match is not None and int(size_match.group(1)) > max_bytes
            part = "BODY.PEEK[HEADER]" if headers_only else "BODY.PEEK[]"
            data = _ok(*self._imap.uid("FETCH", str(uid), f"({part})"), "fetch")
        raw = next((item[1] for item in data if isinstance(item, tuple) and len(item) == 2), None)
        if not isinstance(raw, bytes):
            return None
        return FetchedEmail(raw=raw, received_at=parse_internaldate(first), headers_only=headers_only)

    def close(self) -> None:
        with contextlib.suppress(Exception):  # closing must never raise; the socket may be gone anyway
            self._imap.logout()
