"""Unit tests for settings validation (W6, W7). Never reads the real .env."""

import pytest
from sqlalchemy.engine import make_url

from kiakia.config import ConfigError, load_settings

GOOD = {
    "POSTGRES_USER": "kiakia",
    "POSTGRES_PASSWORD": "secret",
    "POSTGRES_DB": "kiakia",
    "TELEGRAM_BOT_TOKEN": "123:ABC",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A clean environment in an empty folder (so no real .env is picked up)."""
    monkeypatch.chdir(tmp_path)
    for name in [
        *GOOD,
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "BUSINESS_TIMEZONE",
        "EMAIL_ADDRESS",
        "EMAIL_APP_PASSWORD",
    ]:
        monkeypatch.delenv(name, raising=False)

    def set_env(**overrides):
        for name, value in {**GOOD, **overrides}.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)

    return set_env


def test_good_settings_and_defaults(env):
    env()
    s = load_settings()
    assert s.db_port == 55432 and s.db_host == "localhost"
    assert str(s.business_tz) == "Africa/Lagos"


def test_secrets_are_masked_everywhere(env):
    env(POSTGRES_PASSWORD="super-secret", TELEGRAM_BOT_TOKEN="999:TOKEN")
    s = load_settings()
    text = repr(s) + str(s)
    assert "super-secret" not in text and "999:TOKEN" not in text


def test_bad_port_gives_a_clear_config_error_not_a_crash(env):
    # W7: POSTGRES_PORT=abc used to raise a raw ValueError with a stack trace.
    env(POSTGRES_PORT="abc")
    with pytest.raises(ConfigError, match="POSTGRES_PORT"):
        load_settings()


def test_port_out_of_range_is_rejected(env):
    env(POSTGRES_PORT="70000")
    with pytest.raises(ConfigError, match="POSTGRES_PORT"):
        load_settings()


def test_all_problems_are_reported_at_once(env):
    env(POSTGRES_USER=None, POSTGRES_DB=None, BUSINESS_TIMEZONE="Mars/Base")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    message = str(exc.value)
    assert "POSTGRES_USER: is missing" in message
    assert "POSTGRES_DB: is missing" in message
    assert "BUSINESS_TIMEZONE" in message


def test_error_never_echoes_the_bad_value(env):
    env(POSTGRES_PORT="my-secret-ish-value")
    with pytest.raises(ConfigError) as exc:
        load_settings()
    assert "my-secret-ish-value" not in str(exc.value)


def test_empty_password_is_rejected(env):
    env(POSTGRES_PASSWORD="   ")
    with pytest.raises(ConfigError, match="POSTGRES_PASSWORD"):
        load_settings()


def test_password_with_special_characters_survives_the_url(env):
    # W6: 'p@ss/word' used to corrupt the URL (host read as 'ss').
    env(POSTGRES_PASSWORD="p@ss/word:#1")
    url = make_url(load_settings().database_url)
    assert url.host == "localhost"
    assert url.password == "p@ss/word:#1"


def test_telegram_token_required_only_when_needed(env):
    env(TELEGRAM_BOT_TOKEN=None)
    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN"):
        load_settings()
    assert load_settings(require_telegram=False).db_user == "kiakia"


# ---------- Email channel (docs/adr/0010) ----------


def test_email_is_off_unless_configured(env, monkeypatch):
    for name in ("EMAIL_ADDRESS", "EMAIL_APP_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    env()
    assert not load_settings().email_enabled


def test_email_settings_are_validated_and_the_password_masked(env):
    env(EMAIL_ADDRESS="Shop@Gmail.com", EMAIL_APP_PASSWORD="abcd efgh ijkl mnop")
    s = load_settings()
    assert s.email_enabled and s.email_address == "shop@gmail.com"
    assert s.email_imap_host == "imap.gmail.com" and s.email_smtp_port == 465
    assert "abcd" not in repr(s)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"EMAIL_ADDRESS": "shop@gmail.com"}, "set both EMAIL_ADDRESS and EMAIL_APP_PASSWORD"),
        ({"EMAIL_APP_PASSWORD": "secret-pw"}, "set both EMAIL_ADDRESS and EMAIL_APP_PASSWORD"),
        ({"EMAIL_ADDRESS": "not-an-address", "EMAIL_APP_PASSWORD": "pw"}, "EMAIL_ADDRESS"),
        (
            {
                "EMAIL_ADDRESS": "a@gmail.com",
                "EMAIL_APP_PASSWORD": "pw",
                "EMAIL_DISPLAY_NAME": "Shop\nBcc: x",
            },
            "EMAIL_DISPLAY_NAME",
        ),
    ],
)
def test_bad_email_settings_are_one_clear_error_without_the_secret(env, overrides, message):
    env(**overrides)
    with pytest.raises(ConfigError, match=message) as caught:
        load_settings()
    assert "secret-pw" not in str(caught.value)
