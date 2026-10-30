"""Resolve the single isolated MySQL 8.0.16+ target for exchange integration tests and refuse every other destination.

Opt-in only: a connection is opened only when the isolation switch, every TEST_ISOLATED_DB_* key and the server identity pin are present.
Port 3306 belongs to the existing environment and is refused by name; no value is read from the repository .env file.
"""

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pymysql

from bot.config import ConfigurationError, DatabaseSettings, required_environment_value
from bot.database.connection import open_connection

ISOLATION_SWITCH = "JUKEBOX_RUN_EXCHANGE_MYSQL_ISOLATED"  # [explicit opt-in]: Only this switch allows any isolated connection.
ISOLATED_HOST = "127.0.0.1"  # [loopback only]: The isolated server must be reachable from this machine alone.
ISOLATED_PORT = 3307  # [isolated port]: The only port this module will ever connect to.
PROTECTED_PORTS = frozenset({3306})  # [protected environment]: The existing MySQL80 port is never a test destination.
ISOLATED_DATABASE = "jukebox101_test"  # [disposable database]: Fixed name inside the isolated instance only.
MINIMUM_SERVER_VERSION = (8, 0, 16)  # [CHECK enforcement]: Earlier servers do not enforce the migration 003 constraints.
PROTECTED_SOCKET_NAME = "MySQL"  # [existing pipe name]: The default pipe of the protected MySQL80 service.
WINDOW_SWITCH = "JUKEBOX_RUN_WINDOW_N12_SCHEMA"  # [separate opt-in]: Needed in addition to the isolation switch; never implied by it.
WINDOW_DATABASE = "jukebox101_window_n12"  # [dedicated schema]: The only extra schema, created for one approved window run.
WINDOW_ACCOUNT = "jukebox101_test_exchange"  # [application account]: The exchange account only; administrative accounts are never accepted here.
ENVIRONMENT_KEYS = ("TEST_ISOLATED_DB_HOST", "TEST_ISOLATED_DB_PORT", "TEST_ISOLATED_DB_USER", "TEST_ISOLATED_DB_PASSWORD",
                    "TEST_ISOLATED_DB_NAME", "TEST_ISOLATED_SERVER_UUID", "TEST_ISOLATED_RUN_DIR")  # [names only]: Values are never printed.


@dataclass(frozen=True)
class IsolatedPin:
    """Hold the identity that the isolated instance must prove before any write."""

    server_uuid: str  # [instance pin]: Recorded when the isolated instance was initialised; loopback and port alone do not prove identity.
    run_dir: Path  # [run folder]: The only directory that may hold the isolated data, log and pid files.
    database: str = ISOLATED_DATABASE  # [expected schema]: The schema the identity check must name; the default keeps existing callers on jukebox101_test.


def normalized_path(value: str) -> str:
    """@description: Compare Windows and POSIX-style paths the same way so a trailing separator or case never hides a mismatch.
    @param value: Path text from the environment or from the server.
    @return: Normalised path text for equality and prefix checks.
    """
    return os.path.normcase(os.path.normpath(str(value)))


def window_settings_from_environment() -> tuple[DatabaseSettings, IsolatedPin]:
    """@description: Resolve the dedicated window schema only when both switches, the exact host and port, that schema and the exchange account are all present.
    @param: none
    @return: Validated settings and an identity pin naming the window schema; any other value raises before a socket is opened.
    """
    if os.environ.get(ISOLATION_SWITCH) != "1":
        raise ConfigurationError("ISOLATION_NOT_AUTHORIZED")
    if os.environ.get(WINDOW_SWITCH) != "1":
        raise ConfigurationError("WINDOW_SCHEMA_NOT_AUTHORIZED")
    values = {name: required_environment_value(name) for name in ENVIRONMENT_KEYS}
    port = int(values["TEST_ISOLATED_DB_PORT"]) if values["TEST_ISOLATED_DB_PORT"].isdecimal() else -1
    if port in PROTECTED_PORTS:
        raise ConfigurationError("PROTECTED_PORT_REFUSED")
    if (values["TEST_ISOLATED_DB_HOST"], port, values["TEST_ISOLATED_DB_NAME"]) != (ISOLATED_HOST, ISOLATED_PORT, WINDOW_DATABASE):
        raise ConfigurationError("ISOLATED_DESTINATION_MISMATCH")
    if values["TEST_ISOLATED_DB_USER"] != WINDOW_ACCOUNT:
        raise ConfigurationError("WINDOW_ACCOUNT_REFUSED")
    settings = DatabaseSettings(ISOLATED_HOST, port, WINDOW_ACCOUNT, values["TEST_ISOLATED_DB_PASSWORD"], WINDOW_DATABASE)
    pin = IsolatedPin(values["TEST_ISOLATED_SERVER_UUID"].lower(), Path(values["TEST_ISOLATED_RUN_DIR"]), WINDOW_DATABASE)
    return settings, pin


def isolated_settings_from_environment() -> tuple[DatabaseSettings, IsolatedPin]:
    """@description: Build the isolated destination only when every explicit key names port 3307 and the fixed disposable database.
    @param: none
    @return: Validated settings and the identity pin; any other destination raises before a socket is opened.
    """
    if os.environ.get(ISOLATION_SWITCH) != "1":
        raise ConfigurationError("ISOLATION_NOT_AUTHORIZED")
    values = {name: required_environment_value(name) for name in ENVIRONMENT_KEYS}
    port = int(values["TEST_ISOLATED_DB_PORT"]) if values["TEST_ISOLATED_DB_PORT"].isdecimal() else -1
    if port in PROTECTED_PORTS:
        raise ConfigurationError("PROTECTED_PORT_REFUSED")
    if (values["TEST_ISOLATED_DB_HOST"], port, values["TEST_ISOLATED_DB_NAME"]) != (ISOLATED_HOST, ISOLATED_PORT, ISOLATED_DATABASE):
        raise ConfigurationError("ISOLATED_DESTINATION_MISMATCH")
    settings = DatabaseSettings(ISOLATED_HOST, port, values["TEST_ISOLATED_DB_USER"], values["TEST_ISOLATED_DB_PASSWORD"], ISOLATED_DATABASE)
    pin = IsolatedPin(values["TEST_ISOLATED_SERVER_UUID"].lower(), Path(values["TEST_ISOLATED_RUN_DIR"]))
    return settings, pin


def read_server_identity(connection: pymysql.Connection) -> dict:
    """@description: Read read-only server facts that prove which instance, data directory and log a connection reached.
    @param connection: Open connection to the candidate destination.
    @return: Server identity facts without credentials.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@port AS port, @@datadir AS datadir, @@version AS version, @@server_uuid AS server_uuid, "
                       "@@log_error AS log_error, @@socket AS socket, DATABASE() AS database_name")
        return cursor.fetchone()


def check_server_identity(identity: dict, pin: IsolatedPin) -> None:
    """@description: Refuse any server whose port, version, UUID, data directory, log or pipe is not the isolated instance's.
    @param identity: Facts read from the connected server.
    @param pin: Expected isolated identity from the explicit environment.
    @return: None; any mismatch raises a code-only configuration error.
    """
    if identity["port"] != ISOLATED_PORT:
        raise ConfigurationError("SERVER_PORT_MISMATCH")
    version = tuple(int(part) for part in str(identity["version"]).split("-")[0].split(".")[:3])
    if version < MINIMUM_SERVER_VERSION:
        raise ConfigurationError("SERVER_VERSION_TOO_OLD")
    if str(identity["server_uuid"]).lower() != pin.server_uuid:
        raise ConfigurationError("SERVER_UUID_MISMATCH")
    if identity["database_name"] != pin.database:
        raise ConfigurationError("DATABASE_MISMATCH")
    if normalized_path(identity["datadir"]) != normalized_path(pin.run_dir / "data"):
        raise ConfigurationError("DATADIR_NOT_ISOLATED")
    if not normalized_path(identity["log_error"]).startswith(normalized_path(pin.run_dir)):
        raise ConfigurationError("LOG_NOT_ISOLATED")
    if str(identity["socket"]) == PROTECTED_SOCKET_NAME:
        raise ConfigurationError("PIPE_NAME_IS_PROTECTED")


def open_verified_connection(settings: DatabaseSettings, pin: IsolatedPin) -> pymysql.Connection:
    """@description: Open one connection to the isolated target and close it again if the server identity does not match.
    @param settings: Validated isolated destination.
    @param pin: Expected isolated identity.
    @return: Connection whose server identity has been checked.
    """
    connection = open_connection(settings)
    try:
        check_server_identity(read_server_identity(connection), pin)
    except BaseException:
        connection.close()
        raise
    return connection


def main(argv: list[str] | None = None) -> int:
    """@description: Print the isolated server identity for the operator's read-only target check before any DDL or test write.
    @param argv: Optional argument list for tests.
    @return: Process exit code; the only supported mode is verify.
    """
    parser = argparse.ArgumentParser(description="Read-only identity check of the isolated MySQL test target")
    parser.add_argument("mode", choices=("verify",))
    parser.parse_args(argv)
    settings, pin = isolated_settings_from_environment()
    connection = open_verified_connection(settings, pin)
    try:
        identity = read_server_identity(connection)
    finally:
        connection.close()
    print(json.dumps({key: str(value) for key, value in identity.items()}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
