"""Settings, read from environment variables (and .env during development).

One place for configuration, so no other module reads os.environ directly.

Validated with pydantic-settings:
- Every setting is checked for type and range when the app starts, and ALL problems are
  reported at once in one clear ConfigError (never a raw Python crash).
- Secrets are SecretStr: they print as '**********' in logs, errors and tracebacks,
  and code must call .get_secret_value() on purpose to use them.
- Environment variables win over the .env file (so CI and tests can override).
"""

import re
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class ConfigError(Exception):
    pass


Port = Annotated[int, Field(ge=1, le=65535)]
_EMAIL = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
NonEmpty = Annotated[str, Field(min_length=1)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env may hold settings for other tools (e.g. docker compose)
        frozen=True,
        str_strip_whitespace=True,
    )

    db_user: NonEmpty = Field(validation_alias="POSTGRES_USER")
    db_password: SecretStr = Field(validation_alias="POSTGRES_PASSWORD")
    db_name: NonEmpty = Field(validation_alias="POSTGRES_DB")
    db_host: NonEmpty = Field(default="localhost", validation_alias="POSTGRES_HOST")
    db_port: Port = Field(default=55432, validation_alias="POSTGRES_PORT")
    telegram_bot_token: SecretStr = Field(default=SecretStr(""), validation_alias="TELEGRAM_BOT_TOKEN")
    business_timezone: str = Field(default="Africa/Lagos", validation_alias="BUSINESS_TIMEZONE")
    demo_mode: bool = Field(default=False, validation_alias="KIAKIA_DEMO")  # docs/adr/0008
    # Email channel (docs/adr/0010): a dedicated mailbox + an app password, never a personal password.
    email_address: str = Field(default="", validation_alias="EMAIL_ADDRESS")
    email_app_password: SecretStr = Field(default=SecretStr(""), validation_alias="EMAIL_APP_PASSWORD")
    email_display_name: NonEmpty = Field(
        default="Kiakia", max_length=80, validation_alias="EMAIL_DISPLAY_NAME"
    )
    email_imap_host: NonEmpty = Field(default="imap.gmail.com", validation_alias="EMAIL_IMAP_HOST")
    email_imap_port: Port = Field(default=993, validation_alias="EMAIL_IMAP_PORT")
    email_smtp_host: NonEmpty = Field(default="smtp.gmail.com", validation_alias="EMAIL_SMTP_HOST")
    email_smtp_port: Port = Field(default=465, validation_alias="EMAIL_SMTP_PORT")

    @field_validator("db_password")
    @classmethod
    def _password_not_empty(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("business_timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone {value!r} (example: Africa/Lagos)") from exc
        return value

    @field_validator("email_address")
    @classmethod
    def _valid_email_address(cls, value: str) -> str:
        value = value.lower()
        if value and not _EMAIL.fullmatch(value):
            raise ValueError("doesn't look like an email address (e.g. shop@gmail.com)")
        return value

    @field_validator("email_display_name")
    @classmethod
    def _one_line_name(cls, value: str) -> str:
        if any(ch in value for ch in '\r\n<>"@'):
            raise ValueError("must be a plain name on one line (e.g. Ada's Bags)")
        return value

    @model_validator(mode="after")
    def _email_needs_both_address_and_password(self) -> "Settings":
        if bool(self.email_address) != bool(self.email_app_password.get_secret_value().strip()):
            raise ValueError("set both EMAIL_ADDRESS and EMAIL_APP_PASSWORD, or neither")
        return self

    @property
    def email_enabled(self) -> bool:
        return bool(self.email_address)

    @model_validator(mode="after")
    def _demo_only_on_a_demo_database(self) -> "Settings":
        # Safety: demo mode wipes and re-seeds its database, so it must never point at real data.
        if self.demo_mode and not self.db_name.endswith("demo"):
            raise ValueError(
                f"KIAKIA_DEMO=1 needs a demo database (POSTGRES_DB ending in 'demo', not {self.db_name!r})"
            )
        return self

    @property
    def business_tz(self) -> ZoneInfo:
        return ZoneInfo(self.business_timezone)

    @property
    def database_url(self) -> str:
        """SQLAlchemy URL for Alembic. Built with URL.create so special characters in the
        password (@ / : #) are escaped correctly instead of corrupting the URL."""
        url = URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )
        return url.render_as_string(hide_password=False)


# Map field names back to the environment variable names people actually type.
_ENV_NAMES = {name: str(f.validation_alias) for name, f in Settings.model_fields.items()}


def load_settings(require_telegram: bool = True) -> Settings:
    try:
        settings = Settings()  # type: ignore[call-arg]  # values come from env / .env
    except ValidationError as exc:
        problems = []
        for err in exc.errors():
            field = str(err["loc"][0]) if err["loc"] else ""
            name = _ENV_NAMES.get(field, field) or "settings"
            # Never echo the bad VALUE: it could be a secret.
            message = (
                "is missing" if err["type"] == "missing" else str(err["msg"]).removeprefix("Value error, ")
            )
            problems.append(f"  - {name}: {message}")
        raise ConfigError(
            "Invalid settings (check your .env; see .env.example):\n" + "\n".join(problems)
        ) from None
    if require_telegram and not settings.telegram_bot_token.get_secret_value():
        raise ConfigError("Missing setting TELEGRAM_BOT_TOKEN. Copy .env.example to .env and fill it in.")
    return settings
