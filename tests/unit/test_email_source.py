"""Unit tests for EmailSource and the IMAP wrapper (docs/adr/0010). No network: fakes only."""

import imaplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import ClassVar

import pytest

from kiakia.adapters.email import mailbox as mailbox_module
from kiakia.adapters.email.mailbox import FetchedEmail, ImapMailbox, parse_internaldate
from kiakia.adapters.email.source import EmailSource, parse_cursor
from kiakia.domain.errors import SourceRejectedRequest, SourceUnavailable

SHOP = "shop@example.com"
WHEN = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)


def raw_email(sender="ada@example.com", text="hi", message_id=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = SHOP
    msg["Subject"] = "Re: hello"
    if message_id:
        msg["Message-ID"] = message_id
    msg.set_content(text)
    return msg.as_bytes()


class FakeMailbox:
    def __init__(self, emails=None, uid_validity=100, uid_next=50):
        self.emails = dict(emails or {})  # uid -> raw bytes (or None = deleted)
        self.uid_validity = uid_validity
        self._uid_next = uid_next
        self.closed = False
        self.fail_with = None

    def uid_next(self):
        return self._uid_next

    def uids_after(self, uid):
        if self.fail_with:
            raise self.fail_with
        return sorted(u for u in self.emails if u > uid)

    def fetch(self, uid, max_bytes):
        raw = self.emails[uid]
        return None if raw is None else FetchedEmail(raw, WHEN, headers_only=False)

    def close(self):
        self.closed = True


def make_source(*boxes, wait=0):
    opened, sleeps = [], []
    queue = list(boxes)

    def open_mailbox():
        box = queue.pop(0)
        opened.append(box)
        return box

    source = EmailSource(open_mailbox, own_address=SHOP, wait_seconds=wait, sleep=sleeps.append)
    return source, opened, sleeps


def test_first_connection_imports_nothing_old_and_starts_from_now():
    box = FakeMailbox({10: raw_email(), 49: raw_email()}, uid_next=50)
    source, _, _ = make_source(box)
    result = source.fetch(None, 100)
    assert result.messages == [] and result.next_cursor == "100:49"


def test_new_mail_after_the_cursor_is_imported_in_order():
    box = FakeMailbox(
        {49: raw_email(), 50: raw_email("bayo@example.com"), 51: raw_email("chidi@example.com")}
    )
    source, _, _ = make_source(box)
    result = source.fetch("100:49", 100)
    assert [m.external_chat_id for m in result.messages] == ["bayo@example.com", "chidi@example.com"]
    assert result.next_cursor == "100:51"


def test_limit_is_respected_and_the_cursor_stops_at_the_last_one_read():
    box = FakeMailbox({u: raw_email() for u in range(50, 60)})
    source, _, _ = make_source(box)
    result = source.fetch("100:49", 3)
    assert len(result.messages) == 3 and result.next_cursor == "100:52"


def test_rebuilt_mailbox_starts_again_from_now():
    box = FakeMailbox({50: raw_email()}, uid_validity=200, uid_next=51)
    source, _, _ = make_source(box)
    result = source.fetch("100:49", 100)
    assert result.messages == [] and result.next_cursor == "200:50"


@pytest.mark.parametrize("cursor", ["garbage", "1:x", ":5", "12"])
def test_a_damaged_cursor_starts_from_now_instead_of_crashing(cursor):
    assert parse_cursor(cursor) is None


def test_bad_robot_and_deleted_emails_are_counted_never_fatal():
    box = FakeMailbox(
        {
            50: b"From: not an address\r\n\r\nhello",  # rejected
            51: raw_email("noreply@bank.example"),  # robot: ignored
            52: None,  # deleted before we read it: ignored
            53: raw_email("ada@example.com"),  # fine
        }
    )
    source, _, _ = make_source(box)
    result = source.fetch("100:49", 100)
    assert len(result.messages) == 1 and result.ignored == 2 and len(result.rejects) == 1
    reject = result.rejects[0]
    assert reject.ref == "100:50"
    assert reject.raw == {"uid": 50, "size": len(box.emails[50])}  # never the email itself


def test_quiet_inbox_waits_before_saying_nothing_new_when_live():
    source, _, sleeps = make_source(FakeMailbox({}), wait=25)
    result = source.fetch("100:49", 100)
    assert result.next_cursor is None and sleeps == [10.0]


def test_quiet_inbox_returns_at_once_for_a_one_off_import():
    source, _, sleeps = make_source(FakeMailbox({}))
    assert source.fetch("100:49", 100).next_cursor is None and sleeps == []


def test_connection_is_kept_between_fetches():
    source, opened, _ = make_source(FakeMailbox({}), FakeMailbox({}))
    source.fetch("100:49", 100)
    source.fetch("100:49", 100)
    assert len(opened) == 1


def test_after_a_network_problem_the_next_fetch_reconnects():
    broken = FakeMailbox({})
    broken.fail_with = SourceUnavailable("email: connection lost")
    healthy = FakeMailbox({50: raw_email()})
    source, opened, _ = make_source(broken, healthy)
    with pytest.raises(SourceUnavailable):
        source.fetch("100:49", 100)
    assert broken.closed
    assert len(source.fetch("100:49", 100).messages) == 1
    assert opened == [broken, healthy]


# ---------- ImapMailbox: imaplib answers as real servers send them ----------


class FakeImap:
    instances: ClassVar[list["FakeImap"]] = []
    login_ok = True

    def __init__(self, host, port, ssl_context=None, timeout=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.ssl_context = ssl_context
        self.calls = []
        self.logged_out = False
        FakeImap.instances.append(self)

    def login(self, user, password):
        if not FakeImap.login_ok:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        return "OK", [b"LOGIN completed"]

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def status(self, mailbox, names):
        return "OK", [b'"INBOX" (UIDVALIDITY 777 UIDNEXT 15)']

    def noop(self):
        return "OK", [b"NOOP completed"]

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "SEARCH":
            return "OK", [b"14"]  # "15:*" still returns the newest (14), which must be filtered out
        if args[1] == "(RFC822.SIZE INTERNALDATE)":
            return "OK", [b'3 (UID 14 RFC822.SIZE 9000000 INTERNALDATE "28-Nov-2026 11:00:00 +0100")']
        return "OK", [(b"3 (UID 14 BODY[HEADER] {20}", b"From: a@example.com\r\n\r\n"), b")"]

    def logout(self):
        self.logged_out = True
        return "BYE", [b""]


@pytest.fixture
def fake_imap(monkeypatch):
    FakeImap.instances, FakeImap.login_ok = [], True
    monkeypatch.setattr(mailbox_module.imaplib, "IMAP4_SSL", FakeImap)
    return FakeImap


def test_imap_mailbox_is_encrypted_read_only_and_never_marks_mail_read(fake_imap):
    box = ImapMailbox("imap.example.com", 993, SHOP, "app-password")
    imap = fake_imap.instances[0]
    assert imap.ssl_context is not None and imap.timeout == 30.0
    assert ("select", "INBOX", True) in imap.calls  # read-only
    assert box.uid_validity == 777 and box.uid_next() == 15
    assert box.uids_after(14) == []  # the "n:*" quirk is filtered
    fetched = box.fetch(14, max_bytes=5_000_000)
    assert fetched.headers_only  # 9 MB > 5 MB: headers only
    assert fetched.received_at == datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
    assert ("uid", "FETCH", "14", "(BODY.PEEK[HEADER])") in imap.calls  # PEEK = not marked as read
    assert "app-password" not in repr(box)
    box.close()
    assert imap.logged_out


def test_wrong_app_password_is_a_permanent_error_and_closes_the_connection(fake_imap):
    fake_imap.login_ok = False
    with pytest.raises(SourceRejectedRequest, match="EMAIL_APP_PASSWORD") as caught:
        ImapMailbox("imap.example.com", 993, SHOP, "wrong-password")
    assert "wrong-password" not in str(caught.value)
    assert fake_imap.instances[0].logged_out


def test_cannot_connect_is_temporary(monkeypatch):
    def refuse(*args, **kwargs):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(mailbox_module.imaplib, "IMAP4_SSL", refuse)
    with pytest.raises(SourceUnavailable):
        ImapMailbox("imap.example.com", 993, SHOP, "pw")


def test_internaldate_falls_back_to_now_when_missing():
    assert parse_internaldate(b"3 (UID 14)").tzinfo is not None
