"""Offline tests for HTTP interaction configuration: public-key validation, closed formal switch and secret-safe representation.

Only the monkeypatched process environment is read; no dotenv file, database or Discord connection is touched.
"""

import pytest
from nacl.signing import SigningKey

from bot.config import ConfigurationError
from bot.http_config import PUBLIC_KEY_ENV, load_http_interaction_settings, load_verify_key

TOKEN = "unit-test-token-value"
DB_PASSWORD = "unit-test-password-value"


@pytest.fixture
def complete_environment(monkeypatch: pytest.MonkeyPatch) -> str:
    """@description: Set every required name to a test value and clear the formal switch so each test states its own change.
    @param monkeypatch: Pytest environment patcher.
    @return: Valid public key hex for the test.
    """
    public_key_hex = SigningKey.generate().verify_key.encode().hex()
    values = {PUBLIC_KEY_ENV: public_key_hex, "DISCORD_BOT_TOKEN": TOKEN, "DISCORD_DEV_GUILD_ID": "1559749813011087392",
              "DISCORD_EXPECTED_APPLICATION_ID": "1559751168723521576", "DISCORD_OWNER_USER_ID": "456708412697215018",
              "DB_HOST": "10.0.0.5", "DB_PORT": "3306", "DB_USER": "jukebox101_app", "DB_PASSWORD": DB_PASSWORD,
              "DB_NAME": "jukebox101"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("JUKEBOX_FORMAL_EXCHANGE", raising=False)
    return public_key_hex


def test_complete_environment_loads_with_formal_exchange_closed(complete_environment: str) -> None:
    settings = load_http_interaction_settings()
    assert settings.formal_exchange is False
    assert settings.guild_id == 1559749813011087392
    assert settings.listen_host == "127.0.0.1"


def test_secrets_never_appear_in_representation(complete_environment: str) -> None:
    settings_text = repr(load_http_interaction_settings())
    assert TOKEN not in settings_text
    assert DB_PASSWORD not in settings_text


def test_formal_switch_opens_only_when_explicitly_enabled(complete_environment: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUKEBOX_FORMAL_EXCHANGE", "1")
    assert load_http_interaction_settings().formal_exchange is True


def test_unrecognised_formal_switch_value_refuses_startup(complete_environment: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JUKEBOX_FORMAL_EXCHANGE", "maybe")
    with pytest.raises(ConfigurationError):
        load_http_interaction_settings()


def test_missing_public_key_is_refused_with_name_only(complete_environment: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PUBLIC_KEY_ENV)
    with pytest.raises(ConfigurationError) as raised:
        load_verify_key()
    assert PUBLIC_KEY_ENV in str(raised.value)


@pytest.mark.parametrize("malformed_key", ["ab" * 31, "zz" * 32, "ab" * 33])
def test_malformed_public_key_is_refused(monkeypatch: pytest.MonkeyPatch, malformed_key: str) -> None:
    monkeypatch.setenv(PUBLIC_KEY_ENV, malformed_key)
    with pytest.raises(ConfigurationError):
        load_verify_key()


def test_non_owner_database_user_is_still_enforced(complete_environment: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_USER", "root")
    with pytest.raises(ConfigurationError):
        load_http_interaction_settings()
