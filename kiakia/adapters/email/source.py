"""EmailSource: implements the MessageSource port for one IMAP mailbox (docs/adr/0010).

The cursor is "UIDVALIDITY:last UID". IMAP gives every message in a mailbox a growing number (UID);
UIDVALIDITY changes only if the mailbox is rebuilt, which makes all old UIDs meaningless.
- No cursor yet (first connection) → remember where the inbox ends and import NOTHING old:
  only mail that arrives from now on. The owner's past mail is never read.
- UIDVALIDITY changed → same: start again from "now" (saving the same email twice is harmless
  anyway, thanks to the unique Message-ID).
One connection is kept open between fetches; after a network problem it is dropped and the next
fetch opens a new one, so the live watcher heals itself.
"""

import logging
import time
from collections.abc import Callable

from kiakia.domain.errors import SourceUnavailable
from kiakia.domain.models import FetchResult, Reject

from .mailbox import Mailbox
from .parser import BadEmail, parse_email

log = logging.getLogger(__name__)

MAX_DOWNLOAD = 5 * 1024 * 1024  # bigger emails: headers only (ADR 0010)


def parse_cursor(cursor: str | None) -> tuple[int, int] | None:
    if not cursor:
        return None
    validity, sep, last = cursor.partition(":")
    if not sep or not validity.isdigit() or not last.isdigit():
        return None  # not ours (or damaged): start from "now" rather than crash
    return int(validity), int(last)


class EmailSource:
    name = "email"

    def __init__(
        self,
        open_mailbox: Callable[[], Mailbox],
        own_address: str,
        wait_seconds: int = 0,
        poll_seconds: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._open_mailbox = open_mailbox
        self._own_address = own_address
        self._wait = wait_seconds  # > 0 = live (`kiakia watch`): pause when there's nothing new
        self._poll = min(poll_seconds, wait_seconds) if wait_seconds else 0
        self._sleep = sleep
        self._box: Mailbox | None = None

    def _mailbox(self) -> Mailbox:
        if self._box is None:
            self._box = self._open_mailbox()
        return self._box

    def close(self) -> None:
        if self._box is not None:
            self._box.close()
            self._box = None

    def fetch(self, cursor: str | None, limit: int) -> FetchResult:
        try:
            return self._fetch(parse_cursor(cursor), limit)
        except SourceUnavailable:
            self.close()  # the connection may be dead: open a fresh one next time
            raise

    def _fetch(self, position: tuple[int, int] | None, limit: int) -> FetchResult:
        box = self._mailbox()
        if position is None or position[0] != box.uid_validity:
            if position is not None:
                log.warning("The mailbox was rebuilt (new UIDVALIDITY); importing new mail from now on")
            return FetchResult(next_cursor=f"{box.uid_validity}:{box.uid_next() - 1}")

        last = position[1]
        uids = box.uids_after(last)[:limit]
        if not uids:
            if self._poll:
                self._sleep(self._poll)
            return FetchResult()  # nothing new

        result = FetchResult(next_cursor=f"{box.uid_validity}:{uids[-1]}")
        for uid in uids:
            fetched = box.fetch(uid, MAX_DOWNLOAD)
            if fetched is None:
                result.ignored += 1  # deleted before we could read it
                continue
            ref = f"{box.uid_validity}:{uid}"
            try:
                message = parse_email(
                    fetched.raw,
                    received_at=fetched.received_at,
                    fallback_id=f"uid-{ref}",
                    own_address=self._own_address,
                    headers_only=fetched.headers_only,
                )
            except (BadEmail, ValueError, LookupError, TypeError) as exc:
                # One broken email never stops the others. Only the reason and the mailbox position
                # are kept, not the email itself (it may be spam or contain personal data).
                result.rejects.append(Reject(str(exc), ref, raw={"uid": uid, "size": len(fetched.raw)}))
                continue
            if message is None:
                result.ignored += 1
            else:
                result.messages.append(message)
        return result
