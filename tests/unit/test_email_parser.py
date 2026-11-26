"""Unit tests for the email parser (docs/adr/0010). Emails are untrusted input from anyone."""

from datetime import UTC, datetime
from email.message import EmailMessage

import pytest

from kiakia.adapters.email.parser import BadEmail, html_to_text, parse_email, strip_quoted
from kiakia.domain.models import Channel, Direction, MessageKind

RECEIVED = datetime(2026, 11, 28, 10, 0, tzinfo=UTC)
SHOP = "shop@example.com"


def email(body="Hello, how much is the blue bag?", *, html=None, subject="Blue bag", **headers):
    msg = EmailMessage()
    msg["From"] = headers.pop("From", "Ada Obi <Ada@Example.com>")
    msg["To"] = SHOP
    if subject is not None:
        msg["Subject"] = subject
    msg["Message-ID"] = headers.pop("Message_ID", "<abc123@mail.example.com>")
    for name, value in headers.items():
        msg[name.replace("_", "-")] = value
    if body is not None:
        msg.set_content(body)
    if html is not None:
        if body is None:
            msg.set_content(html, subtype="html")
        else:
            msg.add_alternative(html, subtype="html")
    return msg


def parse(msg, **kw):
    raw = msg if isinstance(msg, bytes) else msg.as_bytes()
    defaults = {"received_at": RECEIVED, "fallback_id": "uid-1:7", "own_address": SHOP}
    return parse_email(raw, **{**defaults, **kw})


def test_a_customer_email_becomes_one_message():
    m = parse(email(subject="Question"))
    assert m.channel is Channel.EMAIL and m.direction is Direction.IN and m.kind is MessageKind.TEXT
    assert m.external_chat_id == "ada@example.com"  # one conversation per sender, lower case
    assert m.sender.external_id == "ada@example.com" and m.sender.display_name == "Ada Obi"
    assert m.sender.username is None
    assert m.external_message_id == "in:abc123@mail.example.com"  # "in:" = chosen by the sender
    assert m.sent_at == RECEIVED  # the mailbox's arrival time, not the sender's Date header
    assert m.body == "Question\n\nHello, how much is the blue bag?"  # a first email's subject is kept
    assert m.media_ref is None


def test_subject_is_skipped_for_replies_or_when_the_body_already_says_it():
    assert parse(email(subject="Re: Blue bag")).body == "Hello, how much is the blue bag?"
    assert (
        parse(email(subject="blue bag?", body="Blue bag? Still available")).body
        == "Blue bag? Still available"
    )
    assert parse(email(subject="Price please", body=None, html=None)).body == "Price please"


def test_name_in_encoded_words_is_decoded():
    m = parse(email(From="=?utf-8?q?Ng=C3=B3zi_Eze?= <ngozi@example.com>"))
    assert m.sender.display_name == "Ngózi Eze"


def test_without_a_name_the_address_is_shown():
    assert parse(email(From="bayo@example.com")).sender.display_name == "bayo@example.com"


def test_html_only_email_becomes_plain_text_and_scripts_are_dropped():
    html = "<html><head><style>p{}</style></head><body><p>Do you have it in <b>red</b>?</p>"
    html += "<script>alert('x')</script><blockquote>old message</blockquote></body></html>"
    m = parse(email(body=None, html=html, subject="Re: bag"))
    assert m.body == "Do you have it in red?"


def test_plain_text_is_preferred_over_html():
    m = parse(email(body="plain words", html="<p>html words</p>", subject=None))
    assert m.body == "plain words"


def test_quoted_earlier_messages_and_signatures_are_cut():
    text = (
        "Yes, I'll take two.\n\nOn Wed, Nov 25, 2026 at 10:00 AM Shop <shop@example.com>\nwrote:\n"
        "> How many would you like?\n"
    )
    assert strip_quoted(text).strip() == "Yes, I'll take two."
    assert strip_quoted("Thanks!\n-- \nAda\n+234 802").strip() == "Thanks!"
    assert strip_quoted("ok\n> quoted line\nmore").strip() == "ok\nmore"
    assert strip_quoted("Sounds good\n\nSent from my iPhone").strip() == "Sounds good"


@pytest.mark.parametrize(
    "headers",
    [
        {"List_Id": "<news.example.com>"},
        {"List_Unsubscribe": "<mailto:unsub@example.com>"},
        {"Precedence": "bulk"},
        {"Auto_Submitted": "auto-replied"},
        {"X_Autoreply": "yes"},
        {"From": "No Reply <noreply@bank.example>"},
        {"From": "MAILER-DAEMON@mx.example.com"},
        {"From": f"Me <{SHOP}>"},  # a copy of our own mail
    ],
)
def test_robots_newsletters_and_our_own_mail_are_ignored(headers):
    assert parse(email(**headers)) is None


def test_auto_submitted_no_means_a_person_wrote_it():
    assert parse(email(Auto_Submitted="no")) is not None


def test_spoofed_sender_is_rejected():
    with pytest.raises(BadEmail, match="dmarc=fail"):
        parse(
            email(
                Authentication_Results="mx.google.com; spf=fail; dmarc=fail (p=REJECT) header.from=bank.com"
            )
        )


def test_passing_sender_checks_are_fine():
    assert parse(email(Authentication_Results="mx.google.com; dkim=pass; dmarc=pass")) is not None


@pytest.mark.parametrize(
    ("from_", "reason"),
    [
        (None, "missing From"),
        ("not an address", "not a valid email address"),
        ("a@example.com, b@example.com", "exactly one sender"),
        ("evil\x00@example.com", "not a valid"),
    ],
)
def test_unusable_senders_are_rejected_never_a_crash(from_, reason):
    msg = email()
    del msg["From"]
    raw = msg.as_bytes()
    if from_ is not None:
        raw = f"From: {from_}\r\n".encode() + raw
    with pytest.raises(BadEmail, match=reason):
        parse(raw)


def test_attachment_without_text_keeps_its_kind_but_never_the_file():
    msg = email(body=None, subject="Re: photo")
    msg.set_content("")
    msg.add_attachment(b"\x89PNG...", maintype="image", subtype="png", filename="bag.png")
    m = parse(msg)
    assert m.kind is MessageKind.IMAGE and m.body is None and m.media_ref is None


def test_a_large_email_only_gets_a_note():
    m = parse(email(), headers_only=True)
    assert m.kind is MessageKind.DOCUMENT
    assert m.body.startswith("Blue bag\n\n📎 A large email")  # the subject is all we know


def test_long_bodies_are_capped_and_nul_removed():
    m = parse(email(body="a\x00b" + "x" * 10_000, subject=None))
    assert len(m.body) == 4000 and m.body.endswith("…") and "\x00" not in m.body


def test_missing_or_odd_message_id_uses_the_mailbox_position():
    msg = email()
    del msg["Message-ID"]
    assert parse(msg).external_message_id == "in:uid-1:7"


def test_garbage_bytes_never_crash():
    with pytest.raises(BadEmail):
        parse(b"\xff\xfe\x00 this is not an email at all")


def test_html_to_text_keeps_line_breaks():
    assert html_to_text("one<br>two<p>three</p>").split() == ["one", "two", "three"]


def test_a_reused_reply_message_id_can_never_match_our_reply():
    # Our replies are stored under their bare Message-ID; a customer copying it into a new email
    # gets the "in:" prefix, so the two can never be the same message (security review 2026-11-26).
    reply_id = "173262.1.42@example.com"
    m = parse(email(Message_ID=f"<{reply_id}>"))
    assert m.external_message_id == f"in:{reply_id}" != reply_id
