"""Integration test setup: a fresh `kiakia_test` database, built by our migrations.

Needs PostgreSQL running (docker compose up -d). If it isn't reachable,
the integration tests are skipped instead of failing.
"""

import os

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from kiakia.config import load_settings

TEST_DB = "kiakia_test"


@pytest.fixture(scope="session")
def test_db_settings():
    settings = load_settings(require_telegram=False)
    try:
        admin = psycopg.connect(
            host=settings.db_host,
            port=settings.db_port,
            user=settings.db_user,
            password=settings.db_password.get_secret_value(),
            dbname=settings.db_name,
            connect_timeout=3,
            autocommit=True,
        )
    except psycopg.OperationalError:
        pytest.skip("PostgreSQL is not running (start it with: docker compose up -d)")
    with admin.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        cur.execute(f"CREATE DATABASE {TEST_DB}")
    admin.close()

    # Build the schema exactly as production would: by running the migrations.
    old = os.environ.get("POSTGRES_DB")
    os.environ["POSTGRES_DB"] = TEST_DB
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if old is None:
            os.environ.pop("POSTGRES_DB", None)
        else:
            os.environ["POSTGRES_DB"] = old
    return settings


@pytest.fixture
def conn(test_db_settings):
    s = test_db_settings
    c = psycopg.connect(
        host=s.db_host,
        port=s.db_port,
        user=s.db_user,
        password=s.db_password.get_secret_value(),
        dbname=TEST_DB,
        autocommit=True,
    )
    with c.cursor() as cur:  # every test starts with empty tables
        cur.execute(
            "TRUNCATE messages, conversations, contact_identities, contacts, "
            "sync_cursors, import_runs, rejected_updates RESTART IDENTITY CASCADE"
        )
    yield c
    c.close()
