"""Unit tests for EmailSender (docs/adr/0010): never sends twice, never lets a header be injected."""

import smtplib
from datetime import UTC, datetime

import pytest

from kiakia.adapters.email.sender import EmailSender
from kiakia.domain.errors import DeliveryUnknown, SourceRejectedRequest, SourceUnavailable

NOW = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)


class FakeSmtp:
    def __init__(self, fail_login=None, fail_send=None, fail_quit=None, refused=None):
        self.fail_login, self.fail_send, self.fail_quit = fail_login, fail_send, fail_quit
        self.refused = refused or {}
        self.sent = []
        self.quit_called = False

    def login(self, user, password):
        if self.fail_login:
            raise self.fail_login

    def send_message(self, msg, from_addr=None, to_addrs=None):
        if self.fail_send:
            raise self.fail_send
        self.sent.append((msg, from_addr, to_addrs))
        return self.refused

    def quit(self):
        self.quit_called = True
        if self.fail_quit:
            raise self.fail_quit


def make_sender(smtp=None, connect_error=None):
    connections = []

    def connect():
        if connect_error:
            raise connect_error
        connections.append(smtp)
        return smtp

    sender = EmailSender(
        "smtp.example.com",
        465,
        "shop@example.com",
        "app-pw",
        "Ada's Bags",
        smtp_factory=connect,
        clock=lambda: NOW,
    )
    return sender, connections


def test_sends_a_plain_reply_and_reports_its_message_id():
    smtp = FakeSmtp()
    sender, _ = make_sender(smtp)
    sent = sender.send_text("Ada@Example.com", "Yes, we have it in red!")
    msg, from_addr, to_addrs = smtp.sent[0]
    assert from_addr == "shop@example.com" and to_addrs == ["ada@example.com"]
    assert msg["To"] == "ada@example.com"
    assert msg["From"] == "Ada's Bags <shop@example.com>"
    assert msg["Subject"] == "Message from Ada's Bags"
    assert msg["Auto-Submitted"] == "no"
    assert msg.get_content().strip() == "Yes, we have it in red!"
    assert sent.external_message_id == msg["Message-ID"].strip("<>") and sent.sent_at == NOW
    assert smtp.quit_called


@pytest.mark.parametrize(
    "bad_address",
    ["ada@example.com\r\nBcc: everyone@example.com", "not-an-address", "", "a@b@example.com"],
)
def test_a_bad_address_is_refused_before_connecting(bad_address):
    sender, connections = make_sender(FakeSmtp())
    with pytest.raises(SourceRejectedRequest):
        sender.send_text(bad_address, "hi")
    assert connections == []  # nothing was even attempted


def test_cannot_reach_the_server_means_not_sent():
    sender, _ = make_sender(connect_error=ConnectionRefusedError())
    with pytest.raises(SourceUnavailable):
        sender.send_text("ada@example.com", "hi")


def test_wrong_app_password_is_permanent_and_never_shown():
    smtp = FakeSmtp(
        fail_login=smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    )
    sender, _ = make_sender(smtp)
    with pytest.raises(SourceRejectedRequest, match="EMAIL_APP_PASSWORD") as caught:
        sender.send_text("ada@example.com", "hi")
    assert "app-pw" not in str(caught.value) and "app-pw" not in repr(sender)
    assert smtp.quit_called


@pytest.mark.parametrize(
    "error",
    [
        smtplib.SMTPRecipientsRefused({"ada@example.com": (550, b"no such user")}),
        smtplib.SMTPDataError(554, b"message refused"),
    ],
)
def test_the_server_saying_no_means_not_sent(error):
    sender, _ = make_sender(FakeSmtp(fail_send=error))
    with pytest.raises(SourceRejectedRequest):
        sender.send_text("ada@example.com", "hi")


@pytest.mark.parametrize("error", [smtplib.SMTPServerDisconnected("gone"), TimeoutError()])
def test_a_break_while_handing_over_means_maybe_sent(error):
    sender, _ = make_sender(FakeSmtp(fail_send=error))
    with pytest.raises(DeliveryUnknown):
        sender.send_text("ada@example.com", "hi")


def test_a_refused_recipient_in_the_answer_means_not_sent():
    sender, _ = make_sender(FakeSmtp(refused={"ada@example.com": (550, b"no")}))
    with pytest.raises(SourceRejectedRequest):
        sender.send_text("ada@example.com", "hi")


def test_a_problem_while_closing_does_not_undo_a_sent_message():
    sender, _ = make_sender(FakeSmtp(fail_quit=smtplib.SMTPServerDisconnected("bye")))
    assert sender.send_text("ada@example.com", "hi").sent_at == NOW
