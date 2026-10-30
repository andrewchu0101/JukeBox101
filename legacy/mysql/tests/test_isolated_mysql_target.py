"""Verify offline that the isolated MySQL target refuses port 3306 and every identity mismatch before any socket is opened.

No connection is made: pymysql.connect is replaced with a function that fails the test if it is ever called.
"""

import os
from pathlib import Path

import pymysql
import pytest

from bot.config import ConfigurationError
from tests import isolated_mysql_target as target

RUN_DIR = Path("D:/Project/MusicBased/discordBot_JukeBox101/.local/mysql-test/run-test")
VALID_ENVIRONMENT = {
    "TEST_ISOLATED_DB_HOST": "127.0.0.1", "TEST_ISOLATED_DB_PORT": "3307", "TEST_ISOLATED_DB_USER": "synthetic_user",
    "TEST_ISOLATED_DB_PASSWORD": "synthetic-secret", "TEST_ISOLATED_DB_NAME": "jukebox101_test",
    "TEST_ISOLATED_SERVER_UUID": "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE", "TEST_ISOLATED_RUN_DIR": str(RUN_DIR),
}
GOOD_IDENTITY = {
    # [platform separator]: The trailing separator uses the host's own form, so normalisation sees the same directory on Windows and POSIX.
    "port": 3307, "datadir": os.path.join(str(RUN_DIR), "data") + os.sep, "version": "8.0.46", "server_uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    "log_error": str(RUN_DIR / "error.log"), "socket": "jb101_test_3307", "database_name": "jukebox101_test",
}


@pytest.fixture
def no_socket(monkeypatch):
    """@description: Fail the test if any code path reaches pymysql.connect.
    @param monkeypatch: Pytest patching facility.
    @return: None.
    """
    def refuse(*args, **kwargs):
        raise AssertionError("no socket may be opened by the isolated target guard")
    monkeypatch.setattr(pymysql, "connect", refuse)


def set_environment(monkeypatch, overrides: dict, switch: str = "1") -> None:
    """@description: Replace the isolated environment for one test, with optional single-key overrides.
    @param monkeypatch: Pytest environment facility.
    @param overrides: Key/value changes applied on top of the valid environment.
    @param switch: Value of the isolation switch.
    @return: None.
    """
    monkeypatch.setenv(target.ISOLATION_SWITCH, switch)
    for name, value in {**VALID_ENVIRONMENT, **overrides}.items():
        monkeypatch.setenv(name, value)


def test_without_the_isolation_switch_nothing_is_resolved(monkeypatch, no_socket) -> None:
    """@description: A valid-looking environment alone never authorises a destination.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @return: None.
    """
    set_environment(monkeypatch, {}, switch="0")
    with pytest.raises(ConfigurationError, match="ISOLATION_NOT_AUTHORIZED"):
        target.isolated_settings_from_environment()


@pytest.mark.parametrize("port", ["3306", "33060"])
def test_protected_port_is_refused_even_with_the_switch_on(monkeypatch, no_socket, port) -> None:
    """@description: The existing MySQL80 port is refused by name before any connection, whatever else the environment says.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @param port: Port text under test.
    @return: None.
    """
    set_environment(monkeypatch, {"TEST_ISOLATED_DB_PORT": port})
    with pytest.raises(ConfigurationError, match="PROTECTED_PORT_REFUSED|ISOLATED_DESTINATION_MISMATCH"):
        target.isolated_settings_from_environment()


@pytest.mark.parametrize("overrides", [
    {"TEST_ISOLATED_DB_NAME": "jukebox101"},
    {"TEST_ISOLATED_DB_NAME": "jukebox101_test_app"},
    {"TEST_ISOLATED_DB_HOST": "localhost"},
    {"TEST_ISOLATED_DB_PORT": "3308"},
    {"TEST_ISOLATED_DB_PORT": "not-a-port"},
])
def test_any_other_destination_is_refused(monkeypatch, no_socket, overrides) -> None:
    """@description: Only loopback port 3307 and the fixed disposable database name are accepted.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @param overrides: One destination field changed away from the isolated target.
    @return: None.
    """
    set_environment(monkeypatch, overrides)
    with pytest.raises(ConfigurationError):
        target.isolated_settings_from_environment()


def test_missing_pin_is_refused(monkeypatch, no_socket) -> None:
    """@description: Without the instance UUID pin the target is not resolved.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @return: None.
    """
    set_environment(monkeypatch, {})
    monkeypatch.delenv("TEST_ISOLATED_SERVER_UUID")
    with pytest.raises(ConfigurationError, match="TEST_ISOLATED_SERVER_UUID"):
        target.isolated_settings_from_environment()


def test_valid_environment_resolves_only_the_isolated_target(monkeypatch, no_socket) -> None:
    """@description: The complete isolated environment resolves to loopback 3307 and the fixed database, still without a socket.
    @param monkeypatch: Pytest environment facility.
    @param no_socket: Socket guard.
    @return: None.
    """
    set_environment(monkeypatch, {})
    settings, pin = target.isolated_settings_from_environment()
    assert (settings.host, settings.port, settings.database) == ("127.0.0.1", 3307, "jukebox101_test")
    assert pin.server_uuid == "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee" and pin.run_dir == RUN_DIR


def test_matching_identity_passes_and_each_mismatch_is_named() -> None:
    """@description: The identity check accepts the isolated server and names the first field that does not match.
    @return: None.
    """
    pin = target.IsolatedPin("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", RUN_DIR)
    target.check_server_identity(dict(GOOD_IDENTITY), pin)
    cases = {
        "SERVER_PORT_MISMATCH": {"port": 3306},
        "SERVER_VERSION_TOO_OLD": {"version": "8.0.15"},
        "SERVER_UUID_MISMATCH": {"server_uuid": "11111111-2222-4333-8444-555555555555"},
        "DATABASE_MISMATCH": {"database_name": "jukebox101"},
        "DATADIR_NOT_ISOLATED": {"datadir": "C:\\ProgramData\\MySQL\\MySQL Server 8.0\\Data\\"},
        "LOG_NOT_ISOLATED": {"log_error": "C:\\ProgramData\\MySQL\\MySQL Server 8.0\\Data\\error.log"},
        "PIPE_NAME_IS_PROTECTED": {"socket": "MySQL"},
    }
    for code, change in cases.items():
        with pytest.raises(ConfigurationError, match=code):
            target.check_server_identity({**GOOD_IDENTITY, **change}, pin)
