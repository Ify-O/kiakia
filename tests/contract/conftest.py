"""Contract-test fixtures: the API wired to the kiakia_test database, seeded with fake data."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kiakia.adapters.postgres.repository import PostgresStore
from kiakia.api.app import create_app
from kiakia.domain.models import Channel, Direction, Message, MessageKind, Sender, SentMessage
from tests.conftest import TEST_DB

NOW = datetime.now(UTC).replace(microsecond=0)


def _msg(chat, mid, minutes_ago, sender, body="hi", direction=Direction.IN, kind=MessageKind.TEXT):
    return Message(
        Channel.TELEGRAM, chat, str(mid), direction, kind, NOW - timedelta(minutes=minutes_ago), sender, body
    )


ADA = Sender("9001", "Ada Test", "ada_test")
BAYO = Sender("9002", "Bayo", None)
CHIDI = Sender("9003", "Chidi", "chidi")
PERCENT = Sender("9004", "100% Real_Deal", None)  # proves LIKE wildcards are escaped


@pytest.fixture
def seeded(conn):
    """4 conversations: Ada waiting (2 msgs), Bayo answered, Chidi waiting (voice note), 100% waiting."""
    PostgresStore(conn).save_messages(
        [
            _msg("9001", 1, 60, ADA, "Hello, how much is the blue bag?"),
            _msg("9001", 2, 55, ADA, "Do you have it in red?"),
            _msg("9002", 1, 50, BAYO, "Are you open today?"),
            _msg("9002", 2, 45, BAYO, "Yes, until 6pm!", direction=Direction.OUT),
            _msg("9003", 1, 30, CHIDI, None, kind=MessageKind.VOICE),
            _msg("9004", 1, 10, PERCENT, "price?"),
        ]
    )
    return conn


class FakeSender:
    """Stands in for Telegram: tests must never message real people."""

    def __init__(self):
        self.sent = []
        self.error = None

    def send_text(self, external_chat_id, text):
        if self.error:
            raise self.error
        self.sent.append((external_chat_id, text))
        return SentMessage(external_message_id=f"out-{len(self.sent)}", sent_at=datetime.now(UTC))


@pytest.fixture
def sender():
    return FakeSender()


@pytest.fixture
def client(test_db_settings, seeded, sender):
    settings = test_db_settings.model_copy(update={"db_name": TEST_DB})
    app = create_app(settings, senders={Channel.TELEGRAM: sender})
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
