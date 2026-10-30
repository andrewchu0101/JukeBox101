"""Verify offline that the window schema is reachable only through its own explicit switch, its exact name and the exchange account, and that the default isolated target still refuses it.

No connection is made: pymysql.connect is replaced with a function that fails the test if it is ever called.
"""

import pytest

from bot.config import ConfigurationError
from tests import isolated_mysql_target as target
from tests.test_isolated_mysql_target import GOOD_IDENTITY, RUN_DIR, VALID_ENVIRONMENT, no_socket  # noqa: F401  (fixtures imported by name)

WINDOW_ENVIRONMENT = {**VALID_ENVIRONMENT, "TEST_ISOLATED_DB_NAME": "jukebox101_window_n12",
                      "TEST_ISOLATED_DB_USER": "jukebox101_test_exchange"}  # [synthetic environment]: Same instance, the window schema, the exchange account.
WINDOW_IDENTITY = {**GOOD_IDENTITY, "database_name": "jukebox101_window_n12"}  # [synthetic identity]: The same instance reporting the window schema.


def set_window_environment(monkeypatch, overrides: dict, isolation: str = "1", window: str = "1") -> None:
    """@description: Replace the environment for one window test, with optional single-key overrides and both switches set explicitly.
    @param monkeypatch: Pytest environment facility.
    @param overrides: Key/value changes applied on top of the window environment.
    @param isolation: Value of the general isolation switch.
    @param window: Value of the window schema switch.
    @return: None.
    """
    monkeypatch.setenv(target.ISOLATION_SWITCH, isolation)
    monkeypatch.setenv(target.WINDOW_SWITCH, window)
    for name, value in {**WINDOW_ENVIRONMENT, **overrides}.items():
        monkeypatch.setenv(name, value)


def test_the_window_schema_needs_its_own_switch_on_top_of_the_isolation_switch(monkeypatch, no_socket) -> None:  # noqa: F811
    """@description: Without the window switch the window schema resolves to nothing, even with every other value correct.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @return: None.
    """
    set_window_environment(monkeypatch, {}, window="0")
    with pytest.raises(ConfigurationError, match="WINDOW_SCHEMA_NOT_AUTHORIZED"):
        target.window_settings_from_environment()


def test_the_window_schema_is_refused_without_the_general_isolation_switch(monkeypatch, no_socket) -> None:  # noqa: F811
    """@description: The window switch alone never authorises a destination.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @return: None.
    """
    set_window_environment(monkeypatch, {}, isolation="0")
    with pytest.raises(ConfigurationError, match="ISOLATION_NOT_AUTHORIZED"):
        target.window_settings_from_environment()


@pytest.mark.parametrize("overrides", [
    {"TEST_ISOLATED_DB_PORT": "3306"},
    {"TEST_ISOLATED_DB_PORT": "33060"},
    {"TEST_ISOLATED_DB_HOST": "localhost"},
    {"TEST_ISOLATED_DB_HOST": "10.0.0.5"},
    {"TEST_ISOLATED_DB_PORT": "3308"},
    {"TEST_ISOLATED_DB_NAME": "jukebox101_test"},
    {"TEST_ISOLATED_DB_NAME": "mysql"},
    {"TEST_ISOLATED_DB_NAME": "jukebox101"},
    {"TEST_ISOLATED_DB_USER": "root"},
    {"TEST_ISOLATED_DB_USER": "jukebox101_test_migrator"},
])
def test_any_other_host_port_schema_or_account_is_refused_before_any_socket(monkeypatch, no_socket, overrides) -> None:  # noqa: F811
    """@description: Only 127.0.0.1, port 3307, the window schema and the exchange account resolve; every other destination or account is refused by name.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @param overrides: One changed value.
    @return: None.
    """
    set_window_environment(monkeypatch, overrides)
    with pytest.raises(ConfigurationError):
        target.window_settings_from_environment()


def test_the_exact_window_environment_resolves_only_the_window_schema(monkeypatch, no_socket) -> None:  # noqa: F811
    """@description: The exact values resolve to the window schema, the exchange account and an identity pin that names that schema.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @return: None.
    """
    set_window_environment(monkeypatch, {})
    settings, pin = target.window_settings_from_environment()
    assert (settings.host, settings.port, settings.database, settings.user) == ("127.0.0.1", 3307, "jukebox101_window_n12", "jukebox101_test_exchange")
    assert pin.database == "jukebox101_window_n12"


def test_the_default_isolated_target_still_refuses_the_window_schema_with_both_switches_on(monkeypatch, no_socket) -> None:  # noqa: F811
    """@description: Setting the window switch does not widen the default target, so every existing test still resolves only jukebox101_test.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @return: None.
    """
    set_window_environment(monkeypatch, {})
    with pytest.raises(ConfigurationError, match="ISOLATED_DESTINATION_MISMATCH"):
        target.isolated_settings_from_environment()


def test_the_default_pin_names_jukebox101_test_and_refuses_the_window_identity() -> None:
    """@description: Existing callers that build a pin without a schema still expect jukebox101_test, so the window identity is refused for them.
    @return: None.
    """
    default_pin = target.IsolatedPin("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", RUN_DIR)
    assert default_pin.database == "jukebox101_test"
    with pytest.raises(ConfigurationError, match="DATABASE_MISMATCH"):
        target.check_server_identity(WINDOW_IDENTITY, default_pin)


def test_the_window_identity_passes_only_for_the_window_pin() -> None:
    """@description: The same instance and data directory pass the check when the pin names the window schema.
    @return: None.
    """
    window_pin = target.IsolatedPin("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", RUN_DIR, "jukebox101_window_n12")
    target.check_server_identity(WINDOW_IDENTITY, window_pin)
