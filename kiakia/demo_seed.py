"""Fake, realistic demo data for demo mode (docs/adr/0008). Never run against real data.

Times are relative to "now", so waiting timers look live during a presentation.
Everything goes through the normal repository methods, so the data obeys the same rules
(idempotent messages, consent with history, the details-need-consent constraint).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from kiakia.adapters.postgres.repository import PostgresStore
from kiakia.domain.models import (
    Category,
    Channel,
    ConsentChange,
    ConsentKind,
    ConsentStatus,
    ConversationChanges,
    CustomerDetailsInput,
    CustomerFilter,
    Direction,
    Message,
    MessageKind,
    Sender,
)

M, H, D = timedelta(minutes=1), timedelta(hours=1), timedelta(days=1)
DETAILS_Q = (
    "Hi! 👋 Can we save your contact details (name, phone, email) "
    "so we can serve you better? Reply YES or NO."
)
OFFERS_Q = (
    "Would you like to hear about our offers and sales? Reply YES or NO. "
    "You can reply STOP anytime to opt out."
)

# (channel, external id, name, username, category, resolved_ago, [(direction, ago, body, kind)], consent)
CUSTOMERS: list[tuple[Any, ...]] = [
    (
        Channel.TELEGRAM,
        "d-ada",
        "Ada Test",
        "ada_test",
        Category.ENQUIRY,
        None,
        [
            ("in", 65 * M, "Hello, how much is the blue bag?", "text"),
            ("in", 60 * M, "Do you have it in red?", "text"),
        ],
        {},
    ),
    (Channel.TELEGRAM, "d-chidi", "Chidi", "chidi", None, None, [("in", 12 * M, None, "voice")], {}),
    (
        Channel.WHATSAPP,
        "+2348030000001",
        "Tunde",
        None,
        Category.ORDER,
        None,
        [("in", 7 * M, "Can you deliver 2 bags to Ikeja tomorrow?", "text")],
        {},
    ),
    (
        Channel.TIKTOK,
        "d-musa",
        "Musa",
        "musa.styles",
        Category.ENQUIRY,
        None,
        [("in", 25 * M, "Saw your video! Is the gold one still available?", "text")],
        {},
    ),
    (Channel.INSTAGRAM, "d-amaka", "Amaka", "amaka.k", None, None, [("in", 2 * M, "price?", "image")], {}),
    (
        Channel.TELEGRAM,
        "d-bayo",
        "Bayo",
        None,
        Category.ENQUIRY,
        None,
        [("in", 50 * M, "Are you open today?", "text"), ("out", 44 * M, "Yes, until 6pm!", "text")],
        {
            "details": ("granted", CustomerDetailsInput(phone="+234 803 555 0101")),
            "marketing": ("granted", None),
        },
    ),
    (
        Channel.INSTAGRAM,
        "d-ngozi",
        "Ngozi",
        "ngozi.styles",
        Category.ORDER,
        None,
        [
            ("in", 3 * H + 9 * M, "I'll take the black one 🖤", "text"),
            ("out", 3 * H, "Lovely! Sending the payment details now.", "text"),
        ],
        {
            "details": ("granted", CustomerDetailsInput(email="ngozi@example.com")),
            "marketing": ("declined", None),
        },
    ),
    (
        Channel.MESSENGER,
        "d-kemi",
        "Kemi",
        None,
        Category.COMPLAINT,
        23 * H,
        [
            ("in", 26 * H, "My order arrived damaged 😞", "text"),
            ("out", 25 * H, "So sorry! We'll replace it today.", "text"),
            ("in", 24 * H, "Thank you!", "text"),
        ],
        {"details": ("declined", None)},
    ),
    (
        Channel.EMAIL,
        "funke@example.com",
        "Funke Ade",
        None,
        Category.ORDER,
        None,
        [
            ("in", 3 * D + 2 * H, "Can I get an invoice for my order #1042?", "text"),
            ("out", 3 * D + H, "Attached, thank you!", "text"),
        ],
        {
            "details": ("granted", CustomerDetailsInput(email="funke@example.com")),
            "marketing": ("withdrawn", None),
        },
    ),
    (
        Channel.WHATSAPP,
        "+2348030000002",
        "Emeka",
        None,
        Category.COMPLAINT,
        None,
        [
            ("in", 6 * D + 3 * H, "Delivery was late again.", "text"),
            ("out", 6 * D + 2 * H + 40 * M, "Apologies, we've changed courier.", "text"),
        ],
        {"details": ("withdrawn", None)},  # said yes once, later withdrew: phone/email erased
    ),
    (
        Channel.INSTAGRAM,
        "d-zainab",
        "Zainab",
        "zainab.k",
        Category.ENQUIRY,
        12 * D - 20 * M,
        [
            ("in", 12 * D, "Do you ship to Abuja?", "text"),
            ("out", 12 * D - 30 * M, "Yes, 2–3 days!", "text"),
            ("in", 12 * D - 25 * M, "Great, thanks", "text"),
        ],
        {"details": ("asked", None)},
    ),
    (
        Channel.WHATSAPP,
        "+2348030000003",
        "Sade",
        None,
        Category.ORDER,
        None,
        [
            ("in", 2 * D, "Do you have the sandals in size 40?", "text"),
            ("out", 2 * D - 6 * M, "Yes we have size 40!", "text"),
        ],
        {
            "details": ("granted", CustomerDetailsInput(name="Sade Bello", phone="+234 805 555 0199")),
            "marketing": ("granted", None),
        },
    ),
]


def seed(store: PostgresStore) -> int:
    """Reset the demo database and fill it. Returns the number of customers created."""
    store.reset_demo_data()
    now = datetime.now(UTC)
    for channel, ext_id, name, username, category, resolved_ago, msgs, consent in CUSTOMERS:
        sender = Sender(external_id=ext_id, display_name=name, username=username)
        store.save_messages(
            [
                Message(
                    channel,
                    ext_id,
                    f"{ext_id}-{i}",
                    Direction(direction),
                    MessageKind(kind),
                    now - ago,
                    sender,
                    body,
                )
                for i, (direction, ago, body, kind) in enumerate(msgs, start=1)
            ]
        )
        [customer] = [
            c for c in store.list_customers(CustomerFilter(search=name), 5, None) if c.display_name == name
        ]
        detail = store.get_customer(customer.id)
        if detail is None or not detail.conversations:
            raise RuntimeError(f"seeding {name}: expected the messages above to create a conversation")
        conversation_id = detail.conversations[0].id
        store.update_conversation(
            conversation_id,
            ConversationChanges(
                category=category,
                category_set=category is not None,
                resolved_at=(now - resolved_ago) if resolved_ago else None,
                resolved_set=resolved_ago is not None,
            ),
        )
        for kind_name, (status_name, details) in consent.items():
            kind = ConsentKind(kind_name)
            question = DETAILS_Q if kind is ConsentKind.DETAILS else OFFERS_Q
            store.record_consent(
                ConsentChange(
                    customer.id, kind, ConsentStatus.ASKED, ConsentStatus.ASKED, channel, wording=question
                )
            )
            if status_name != "asked":
                status = ConsentStatus(status_name)
                if status is ConsentStatus.WITHDRAWN:  # granted first, then they replied STOP
                    store.record_consent(
                        ConsentChange(
                            customer.id, kind, ConsentStatus.GRANTED, ConsentStatus.GRANTED, channel
                        )
                    )
                store.record_consent(
                    ConsentChange(customer.id, kind, status, status, channel, details=details)
                )
    return len(CUSTOMERS)
