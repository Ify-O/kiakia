"""Alembic environment: connects to the database from our settings and runs migrations."""

from alembic import context
from sqlalchemy import create_engine

from kiakia.config import load_settings

url = load_settings(require_telegram=False).database_url


def run_migrations_offline() -> None:
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
