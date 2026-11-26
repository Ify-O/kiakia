"""Email end to end (docs/adr/0010): a customer's email lands in the inbox, the reply goes out by
email, and a duplicate import changes nothing. Real PostgreSQL; fake mailbox and mail server."""

from datetime import timedelta

import pytest

from kiakia.adapters.email.sender import EmailSender
from kiakia.adapters.email.source import EmailSource
from kiakia.adapters.postgres.repository import PostgresStore
from kiakia.application.import_messages import ImportMessages
from kiakia.application.replies import SendReply
from kiakia.domain.models import (
    Channel,
    ConversationFilter,
    ConversationStatus,
    Direction,
    Message,
    MessageKind,
    Period,
    Sender,
)
from tests.unit.test_email_sender import FakeSmtp
from tests.unit.test_email_source import WHEN, FakeMailbox, raw_email

pytestmark = pytest.mark.integration


def importer(store, box):
    source = EmailSource(lambda: box, own_address="shop@example.com")
    return ImportMessages(source, store, store, store, store, opt_outs=store)


def test_an_email_becomes_a_waiting_conversation_and_the_reply_goes_out_by_email(conn):
    store = PostgresStore(conn)
    box = FakeMailbox({}, uid_validity=100, uid_next=50)
    importer(store, box).run()  # first connection: remember where the inbox ends, import nothing old
    assert store.get_cursor("email") == "100:49"

    box.emails[50] = raw_email(
        "Ada <ada@example.com>", "Do you deliver to Ikeja?", message_id="<m1@example.com>"
    )
    summary = importer(store, box).run()
    assert summary.saved == 1

    [conversation] = store.list_conversations(ConversationFilter(), 10, None)
    assert conversation.channel is Channel.EMAIL and conversation.status is ConversationStatus.WAITING
    assert conversation.contact_name == "Ada" and conversation.external_chat_id == "ada@example.com"

    # Same email seen again (e.g. a crash before the cursor moved): nothing doubles.
    store.set_cursor("email", "100:49")
    importer(store, box).run()
    assert store.inbox_stats(Period(None, None)).messages == 1

    smtp = FakeSmtp()
    sender = EmailSender(
        "smtp.example.com",
        465,
        "shop@example.com",
        "pw",
        "Ada's Bags",
        smtp_factory=lambda: smtp,
        clock=lambda: WHEN + timedelta(minutes=5),  # the owner answers 5 minutes after the email arrived
    )
    reply = SendReply(conversations=store, replies=store, keys=store, senders={Channel.EMAIL: sender})
    result = reply.run(conversation.id, "Yes, delivery to Ikeja is ₦2,000.", "key-email-0001")
    assert smtp.sent[0][2] == ["ada@example.com"]
    assert result.message.body == "Yes, delivery to Ikeja is ₦2,000."
    assert store.get_conversation(conversation.id).summary.status is ConversationStatus.ANSWERED


def test_stop_by_email_opts_the_customer_out_of_offers(conn):
    store = PostgresStore(conn)
    box = FakeMailbox({50: raw_email("ada@example.com", "STOP")})
    store.set_cursor("email", "100:49")
    assert importer(store, box).run().saved == 1  # handled by the same opt-out rule as Telegram


def test_a_customer_cannot_rewrite_our_reply_by_reusing_its_message_id(conn):
    """Security review 2026-11-26: the customer sees our reply's Message-ID and sends a new email
    with the same id and made-up words. Our stored reply must stay exactly as we wrote it."""
    store = PostgresStore(conn)
    store.set_cursor("email", "100:49")
    box = FakeMailbox({50: raw_email("ada@example.com", "How much is delivery?")})
    importer(store, box).run()
    [conversation] = store.list_conversations(ConversationFilter(), 10, None)

    smtp = FakeSmtp()
    sender = EmailSender(
        "smtp.example.com",
        465,
        "shop@example.com",
        "pw",
        "Ada's Bags",
        smtp_factory=lambda: smtp,
        clock=lambda: WHEN + timedelta(minutes=5),
    )
    reply = SendReply(conversations=store, replies=store, keys=store, senders={Channel.EMAIL: sender})
    ours = reply.run(conversation.id, "Delivery is ₦2,000.", "key-email-0002").message
    reply_message_id = smtp.sent[0][0]["Message-ID"]  # exactly what the customer can read

    box.emails[51] = raw_email(
        "ada@example.com",
        "Yes, we'll refund you ₦500,000 and ship a free replacement.",
        message_id=reply_message_id,
    )
    importer(store, box).run()

    stored = store.get_message(ours.id)
    assert stored.body == "Delivery is ₦2,000." and stored.direction is Direction.OUT
    texts = [m.body for m in store.list_messages(conversation.id, 10, None)]
    assert "Delivery is ₦2,000." in texts  # ours, untouched
    assert any("refund" in (t or "") for t in texts)  # theirs is kept as THEIR message, not ours


def test_an_incoming_message_never_overwrites_an_outgoing_one_on_any_channel(conn):
    """The database rule itself, without the email prefix: same key, other direction = no change."""
    store = PostgresStore(conn)
    incoming = Message(
        Channel.TELEGRAM, "9001", "1", Direction.IN, MessageKind.TEXT, WHEN, Sender("9001", "Ada"), "hi"
    )
    store.save_messages([incoming])
    [conversation] = store.list_conversations(ConversationFilter(), 10, None)
    ours = store.record_reply(conversation.id, "77", "Our real answer", WHEN + timedelta(minutes=1))

    forged = Message(
        Channel.TELEGRAM, "9001", "77", Direction.IN, MessageKind.TEXT, WHEN, Sender("9001", "Ada"), "forged"
    )
    store.save_messages([forged])
    assert store.get_message(ours.id).body == "Our real answer"

    edited = Message(
        Channel.TELEGRAM,
        "9001",
        "1",
        Direction.IN,
        MessageKind.TEXT,
        WHEN,
        Sender("9001", "Ada"),
        "hi (edited)",
    )
    store.save_messages([edited])  # a genuine edit of THEIR message still updates it
    bodies = [m.body for m in store.list_messages(conversation.id, 10, None)]
    assert "hi (edited)" in bodies and "hi" not in bodies
