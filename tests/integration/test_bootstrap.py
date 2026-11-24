"""Integration tests for the composition root (W8) and connection lifecycle (W10)."""

import pytest

from kiakia.application.import_messages import ImportMessages
from kiakia.application.waiting import ListWaitingCustomers
from kiakia.bootstrap import app
from kiakia.config import ConfigError

from .conftest import TEST_DB

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(test_db_settings):
    return test_db_settings.model_copy(update={"db_name": TEST_DB})


def test_connection_is_closed_after_the_block(settings):
    with app(settings) as services:
        conn = services.store._conn
        assert not conn.closed
        services.list_waiting_customers().run()  # a real query works
    assert conn.closed


def test_connection_is_closed_even_when_an_error_happens(settings):
    with pytest.raises(RuntimeError), app(settings) as services:
        conn = services.store._conn
        raise RuntimeError("boom")
    assert conn.closed


def test_builds_use_cases_wired_to_the_same_store(settings):
    with app(settings) as services:
        assert isinstance(services.list_waiting_customers(), ListWaitingCustomers)
        if settings.telegram_bot_token.get_secret_value():
            assert isinstance(services.import_messages(), ImportMessages)


def test_import_without_token_is_a_clear_config_error(settings):
    no_token = settings.model_copy(update={"telegram_bot_token": type(settings.telegram_bot_token)("")})
    with app(no_token) as services, pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        services.import_messages()
