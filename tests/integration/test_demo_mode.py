"""Demo mode safety (ADR 0008): it can never touch real data, and seeding works."""

import pytest

from kiakia.adapters.demo.sender import DemoSender
from kiakia.adapters.postgres.repository import PostgresStore
from kiakia.bootstrap import default_senders
from kiakia.config import ConfigError, load_settings
from kiakia.demo_seed import CUSTOMERS, seed
from kiakia.domain.errors import StorageUnavailable
from kiakia.domain.models import Channel, ConsentStatus, CustomerFilter, Period

pytestmark = pytest.mark.integration


def test_reset_refuses_a_non_demo_database(conn):
    # The test database is called kiakia_test, so it must NOT be wiped.
    with pytest.raises(StorageUnavailable, match="refusing"):
        PostgresStore(conn).reset_demo_data()


def test_demo_mode_refuses_the_real_database(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for k, v in {
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "POSTGRES_DB": "kiakia",
        "KIAKIA_DEMO": "1",
    }.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(ConfigError, match="demo database"):
        load_settings(require_telegram=False)


def test_demo_replies_never_leave_the_laptop(test_db_settings):
    demo = test_db_settings.model_copy(update={"db_name": "kiakia_demo", "demo_mode": True})
    senders = default_senders(demo)
    assert set(senders) == set(Channel) and all(isinstance(s, DemoSender) for s in senders.values())


def test_seed_fills_every_platform_and_consent_state(test_db_settings, conn):
    # Run the seed on the test DB by temporarily allowing the reset (only for this test).
    store = PostgresStore(conn)
    store.reset_demo_data = lambda: None  # the test DB is wiped by the fixture instead
    assert seed(store) == len(CUSTOMERS)
    customers = store.list_customers(CustomerFilter(), 50, None)
    assert {c.first_channel for c in customers} == set(Channel)
    assert {c.details_consent for c in customers} == set(ConsentStatus)
    assert {c.marketing_consent for c in customers} >= {
        ConsentStatus.GRANTED,
        ConsentStatus.DECLINED,
        ConsentStatus.WITHDRAWN,
        ConsentStatus.UNKNOWN,
    }
    stats = store.inbox_stats(Period())
    assert stats.waiting == 5 and stats.response_rate is not None and stats.median_first_response_seconds
