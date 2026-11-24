"""Contract-test fixtures: the API wired to the kiakia_test database, seeded with fake data."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from kiakia.adapters.postgres.repository import PostgresStore
from kiakia.api.app import create_app
from kiakia.domain.models import Channel, Direction, Message, MessageKind, Sender
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


@pytest.fixture
def client(test_db_settings, seeded):
    settings = test_db_settings.model_copy(update={"db_name": TEST_DB})
    with TestClient(create_app(settings), raise_server_exceptions=False) as c:
        yield c
