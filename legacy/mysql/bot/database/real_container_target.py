"""Verify the named real-isolated container before the migrator session connects to it.

This is a separate named profile beside the migration container mode and the fixed test guards. It has its own opt-in switch, its own container,
volume, published loopback port, fixed schema and migrator account, and the server UUID pinned at creation. It reuses the container fact checks in
bot.isolated_container_target and never relaxes any existing guard constant.
"""

from functools import partial
from typing import Any, Callable, Mapping

from bot.config import ConfigurationError, DatabaseSettings
from bot.database.submission_migration import MigrationGuards
from bot.isolated_container_target import verify_container_target

REAL_SWITCH = "JUKEBOX_RUN_REAL_CONTAINER_TARGET"  # [explicit opt-in]: Exactly "1" enables this profile and no other switch does.
REAL_CONTAINER_NAME = "jb101-real-mysql"  # [named container]: Created for this profile only.
REAL_VOLUME_NAME = "jb101-real-data"  # [named volume]: Backs this container's data directory.
REAL_PUBLISHED_PORT = 3309  # [published binding]: Host loopback port of this container, checked as a container fact and never connected to.
REAL_CONNECT_HOST = "127.0.0.1"  # [in-namespace endpoint]: Migrations connect inside the container's network namespace.
REAL_CONNECT_PORT = 3306  # [in-namespace server port]: The server port seen from inside the container.
REAL_DATABASE = "jukebox101_test"  # [fixed schema]: The only schema this profile accepts.
REAL_MIGRATOR_USER = "jukebox101_test_migrator"  # [DDL identity]: The only account this profile accepts for migration.
REAL_MIGRATOR_ACCOUNT = f"{REAL_MIGRATOR_USER}@{REAL_CONNECT_HOST}"  # [account identity]: Compared with the server's CURRENT_USER().
REAL_NO_ACTIVE_ROLES = "NONE"  # [role-free session]: CURRENT_ROLE() must report this value before any DDL.


def _check_opt_in(environ: Mapping[str, str]) -> None:
    """@description: Keep this profile closed unless its own switch is exactly enabled.
    @param environ: Environment mapping supplied by the trusted caller.
    @return: None; raises ConfigurationError when the switch is absent or not "1".
    """
    if environ.get(REAL_SWITCH) != "1":
        raise ConfigurationError("REAL_CONTAINER_NOT_AUTHORIZED")


def _check_settings(settings: DatabaseSettings) -> None:
    """@description: Require the in-namespace endpoint, the fixed schema and the migrator user before any socket opens.
    @param settings: Explicit migration destination the caller intends to connect to.
    @return: None; raises ConfigurationError with a code on any mismatch.
    """
    if (settings.host, settings.port) != (REAL_CONNECT_HOST, REAL_CONNECT_PORT):
        raise ConfigurationError("ENDPOINT_MISMATCH")
    if settings.database != REAL_DATABASE:
        raise ConfigurationError("DATABASE_MISMATCH")
    if settings.user != REAL_MIGRATOR_USER:
        raise ConfigurationError("MIGRATOR_ACCOUNT_MISMATCH")


def _check_identity(identity: dict) -> None:
    """@description: Confirm on the live session that the server is on the fixed schema, as the migrator, with no active role.
    @param identity: Live server facts including CURRENT_USER() and CURRENT_ROLE().
    @return: None; raises ConfigurationError with a code on any mismatch.
    """
    if identity.get("database_name") != REAL_DATABASE:
        raise ConfigurationError("DATABASE_MISMATCH")
    if identity.get("account") != REAL_MIGRATOR_ACCOUNT:
        raise ConfigurationError("MIGRATOR_ACCOUNT_MISMATCH")
    if identity.get("active_roles") != REAL_NO_ACTIVE_ROLES:
        raise ConfigurationError("ACTIVE_ROLE_PRESENT")


def verify_real_container_target(environ: Mapping[str, str], settings: DatabaseSettings, inspect: dict, identity: dict,
                                 pinned_server_uuid: str) -> None:
    """@description: Refuse the real container unless the opt-in, endpoint, container facts, schema, account and pinned server identity all agree.
    @param environ: Environment mapping supplied by the trusted caller; only the real opt-in switch is read.
    @param settings: Explicit migration destination the caller intends to connect to.
    @param inspect: Selected docker inspect fields only (name, mounts, port bindings); never the environment.
    @param identity: Live server and account facts read through the migrator connection.
    @param pinned_server_uuid: UUID recorded when jb101-real-mysql was created.
    @return: None when every check passes; raises ConfigurationError carrying only a refusal code otherwise.
    """
    _check_opt_in(environ)
    _check_settings(settings)
    _check_identity(identity)
    verify_container_target(inspect, identity, pinned_server_uuid, container_name=REAL_CONTAINER_NAME, volume_name=REAL_VOLUME_NAME,
                            loopback_port=REAL_PUBLISHED_PORT, database=REAL_DATABASE)


def real_session_guard(environ: Mapping[str, str], settings: DatabaseSettings, inspect: dict, pinned_server_uuid: str) -> Callable[[Any], None]:
    """@description: Build the post-connect check that reads the live identity once and refuses before any DDL.
    @param environ: Environment mapping supplied by the trusted caller.
    @param settings: Explicit migration destination the connection was opened with.
    @param inspect: Selected docker inspect fields for the real container.
    @param pinned_server_uuid: UUID recorded when jb101-real-mysql was created.
    @return: Callable that takes a live cursor and raises ConfigurationError on any mismatch.
    """
    def guard(cursor: Any) -> None:
        """@description: Read the live identity with one SELECT, then verify every real-container fact against it.
        @param cursor: Live cursor of the migrator session, before any DDL.
        @return: None; raises ConfigurationError on mismatch.
        """
        cursor.execute("SELECT DATABASE() AS database_name, CURRENT_USER() AS account, CURRENT_ROLE() AS active_roles, "
                       "@@server_uuid AS server_uuid, VERSION() AS version")
        verify_real_container_target(environ, settings, inspect, cursor.fetchone(), pinned_server_uuid)
    return guard


def real_guards(environ: Mapping[str, str], settings: DatabaseSettings, inspect: dict, pinned_server_uuid: str) -> MigrationGuards:
    """@description: Pair the real-profile pre-connect and post-connect checks for an explicit real-container migration run.
    @param environ: Environment mapping supplied by the trusted caller.
    @param settings: Explicit migration destination.
    @param inspect: Selected docker inspect fields for the real container.
    @param pinned_server_uuid: UUID recorded when jb101-real-mysql was created.
    @return: Guard pair to pass to apply_migration; the local fixed guards are never substituted.
    """
    settings_guard = partial(_pre_connect, environ)
    session_guard = real_session_guard(environ, settings, inspect, pinned_server_uuid)
    return MigrationGuards(settings_guard, session_guard)


def _pre_connect(environ: Mapping[str, str], settings: DatabaseSettings) -> None:
    """@description: Refuse before any socket opens unless the opt-in, endpoint, schema and migrator user are this profile's.
    @param environ: Environment mapping supplied by the trusted caller.
    @param settings: Explicit migration destination.
    @return: None; raises ConfigurationError with a code on any mismatch.
    """
    _check_opt_in(environ)
    _check_settings(settings)
