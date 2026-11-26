"""The web API's connection pool (kiakia/adapters/postgres/pool.py, review finding M4b)."""

import pytest

from kiakia.adapters.postgres.pool import borrow, open_pool
from kiakia.bootstrap import api_pool, pooled_app
from kiakia.domain.errors import StorageUnavailable
from tests.conftest import TEST_DB

pytestmark = pytest.mark.integration


@pytest.fixture
def settings(test_db_settings):
    return test_db_settings.model_copy(update={"db_name": TEST_DB})


def test_requests_reuse_the_same_open_connection(settings):
    pool = api_pool(settings)
    pool.wait()  # the first connection opens in the background; wait so only one exists
    try:
        with pooled_app(pool, settings, {}) as services:
            first = services.store._conn
            services.list_waiting_customers().run()
        with pooled_app(pool, settings, {}) as services:
            assert services.store._conn is first  # borrowed again, not opened again
            assert not first.closed
    finally:
        pool.close()


def test_connection_is_given_back_even_when_the_request_fails(settings):
    pool = api_pool(settings)
    try:
        with pytest.raises(RuntimeError), pooled_app(pool, settings, {}):
            raise RuntimeError("boom")
        stats = pool.get_stats()
        assert stats["pool_available"] == stats["pool_size"]  # every connection is back
    finally:
        pool.close()


def test_pooled_connections_keep_the_query_time_limit(settings):
    pool = api_pool(settings)
    try:
        with borrow(pool) as conn:
            assert conn.autocommit
            assert conn.execute("SHOW statement_timeout").fetchone() == ("10s",)
    finally:
        pool.close()


def test_no_database_means_storage_unavailable_not_a_crash(settings):
    pool = open_pool(
        settings.db_host, 1, settings.db_user, "x", settings.db_name, wait_seconds=0.5
    )  # port 1: nothing listens there
    try:
        with pytest.raises(StorageUnavailable), borrow(pool):
            pass
    finally:
        pool.close()
