"""Settings, read from environment variables (and .env during development).

One place for configuration, so no other module reads os.environ directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    db_user: str
    db_password: str
    db_name: str
    db_host: str
    db_port: int
    telegram_bot_token: str

    @property
    def database_url(self) -> str:
        """SQLAlchemy-style URL, used by Alembic for migrations."""
        return (f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}")

    def __repr__(self) -> str:  # never print secrets by accident
        return f"Settings(db={self.db_user}@{self.db_host}:{self.db_port}/{self.db_name}, telegram_token=***)"


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing setting {name}. Copy .env.example to .env and fill it in.")
    return value


def load_settings(require_telegram: bool = True) -> Settings:
    load_dotenv()
    return Settings(
        db_user=_required("POSTGRES_USER"),
        db_password=_required("POSTGRES_PASSWORD"),
        db_name=_required("POSTGRES_DB"),
        db_host=os.getenv("POSTGRES_HOST", "localhost"),
        db_port=int(os.getenv("POSTGRES_PORT", "55432")),
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN") if require_telegram
        else os.getenv("TELEGRAM_BOT_TOKEN", ""),
    )
