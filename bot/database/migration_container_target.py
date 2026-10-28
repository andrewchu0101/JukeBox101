"""Verify the dedicated Migration container before any migration session connects to it.

This is a separate local-only mode beside the fixed test guards. It consumes its own opt-in switch, the in-namespace loopback endpoint of its own
container, the dated disposable schema for one run, the migrator identity and the server UUID pinned at setup. It reuses the container
fact checks in bot.isolated_container_target and never relaxes the protected-port, account or role rules of the existing guards.
"""

import re
from functools import partial
from typing import Any, Callable, Mapping

from bot.config import ConfigurationError, DatabaseSettings
from bot.database.submission_migration import MigrationGuards
from bot.isolated_container_target import verify_container_target

MIGRATION_CONTAINER_SWITCH = "JUKEBOX_RUN_MIGRATION_CONTAINER_TARGET"  # [explicit opt-in]: Exactly "1" enables this mode and nothing else does.
CONTAINER_NAME = "jb101-mig-mysql"  # [dedicated container]: Created for migration verification only; never the isolated test container.
VOLUME_NAME = "jb101-mig-data"  # [dedicated volume]: The named volume that backs this container's data directory.
LOOPBACK_HOST = "127.0.0.1"  # [loopback only]: The endpoint must be this exact host address.
CONNECT_PORT = 3306  # [in-namespace endpoint]: The server port seen from inside the dedicated container's network namespace only; no host port is used.
PUBLISHED_PORT = 3308  # [published binding]: The host port the container was created with; checked as a container fact, never connected to.
MIGRATOR_USER = "jukebox101_test_migrator"  # [DDL identity]: The only account this mode accepts for migration.
MIGRATOR_ACCOUNT = f"{MIGRATOR_USER}@{LOOPBACK_HOST}"  # [account identity]: Compared with the server's CURRENT_USER().
NO_ACTIVE_ROLES = "NONE"  # [role-free session]: CURRENT_ROLE() must report this value before any DDL.
RUN_DATABASE_PATTERN = re.compile(r"jukebox101_mig_\d{12}")  # [dated schema]: One name per run, YYYYMMDDHHMM, never the shared test schema.


def _check_opt_in(environ: Mapping[str, str]) -> None:
    """@description: Keep the container mode closed unless its own switch is exactly enabled.
    @param environ: Environment mapping supplied by the trusted caller.
    @return: None; raises ConfigurationError when the switch is absent or not "1".
    """
    if environ.get(MIGRATION_CONTAINER_SWITCH) != "1":
        raise ConfigurationError("MIGRATION_CONTAINER_NOT_AUTHORIZED")


def _check_endpoint(settings: DatabaseSettings) -> None:
    """@description: Require the migrator to connect to the in-namespace server port on loopback and no host port.
    @param settings: Explicit migration destination.
    @return: None; raises ConfigurationError when host or port differ.
    """
    if (settings.host, settings.port) != (LOOPBACK_HOST, CONNECT_PORT):
        raise ConfigurationError("ENDPOINT_MISMATCH")


def _check_settings(environ: Mapping[str, str], settings: DatabaseSettings, database: str) -> None:
    """@description: Refuse before any socket opens unless the opt-in, endpoint, dated schema and migrator user are the ones this run names.
    @param environ: Environment mapping supplied by the trusted caller.
    @param settings: Explicit migration destination the caller intends to connect to.
    @param database: Dated disposable schema for this run.
    @return: None; raises ConfigurationError with a code on any mismatch.
    """
    _check_opt_in(environ)
    _check_endpoint(settings)
    if not RUN_DATABASE_PATTERN.fullmatch(database) or settings.database != database:
        raise ConfigurationError("DATABASE_MISMATCH")
    if settings.user != MIGRATOR_USER:
        raise ConfigurationError("MIGRATOR_ACCOUNT_MISMATCH")


def _check_identity(identity: dict, database: str) -> None:
    """@description: Confirm on the live session that the server is on the run schema, as the migrator, with no active role.
    @param identity: Live server facts including CURRENT_USER() and CURRENT_ROLE().
    @param database: Dated disposable schema for this run.
    @return: None; raises ConfigurationError with a code on any mismatch.
    """
    if identity.get("database_name") != database:
        raise ConfigurationError("DATABASE_MISMATCH")
    if identity.get("account") != MIGRATOR_ACCOUNT:
        raise ConfigurationError("MIGRATOR_ACCOUNT_MISMATCH")
    if identity.get("active_roles") != NO_ACTIVE_ROLES:
        raise ConfigurationError("ACTIVE_ROLE_PRESENT")


def verify_migration_container_target(environ: Mapping[str, str], settings: DatabaseSettings, inspect: dict, identity: dict,
                                      expected_server_uuid: str, database: str) -> None:
    """@description: Refuse any migration container target unless the opt-in, endpoint, container facts, schema, account and server identity all agree.
    @param environ: Environment mapping supplied by the trusted caller; only the opt-in switch is read.
    @param settings: Explicit migration destination the caller intends to connect to.
    @param inspect: Selected docker inspect fields only (name, mounts, port bindings); never the environment.
    @param identity: Live server and account facts read through the migrator connection.
    @param expected_server_uuid: UUID pinned for this dedicated container at setup.
    @param database: Dated disposable schema for this run.
    @return: None when every check passes; raises ConfigurationError carrying only a refusal code otherwise.
    """
    _check_settings(environ, settings, database)
    _check_identity(identity, database)
    verify_container_target(inspect, identity, expected_server_uuid, container_name=CONTAINER_NAME, volume_name=VOLUME_NAME,
                            loopback_port=PUBLISHED_PORT, database=database)


def container_session_guard(environ: Mapping[str, str], settings: DatabaseSettings, inspect: dict, expected_server_uuid: str,
                            database: str) -> Callable[[Any], None]:
    """@description: Build the post-connect check that reads the live identity once and refuses before any DDL.
    @param environ: Environment mapping supplied by the trusted caller.
    @param settings: Explicit migration destination the connection was opened with.
    @param inspect: Selected docker inspect fields for the dedicated container.
    @param expected_server_uuid: UUID pinned for this dedicated container at setup.
    @param database: Dated disposable schema for this run.
    @return: Callable that takes a live cursor and raises ConfigurationError on any mismatch.
    """
    def guard(cursor: Any) -> None:
        """@description: Read the live identity with one SELECT, then verify every container fact against it.
        @param cursor: Live cursor of the migrator session, before any DDL.
        @return: None; raises ConfigurationError on mismatch.
        """
        cursor.execute("SELECT DATABASE() AS database_name, CURRENT_USER() AS account, CURRENT_ROLE() AS active_roles, "
                       "@@server_uuid AS server_uuid, VERSION() AS version")
        verify_migration_container_target(environ, settings, inspect, cursor.fetchone(), expected_server_uuid, database)
    return guard


def container_guards(environ: Mapping[str, str], settings: DatabaseSettings, inspect: dict, expected_server_uuid: str,
                     database: str) -> MigrationGuards:
    """@description: Pair the container pre-connect and post-connect checks for an explicit container migration run.
    @param environ: Environment mapping supplied by the trusted caller.
    @param settings: Explicit migration destination.
    @param inspect: Selected docker inspect fields for the dedicated container.
    @param expected_server_uuid: UUID pinned for this dedicated container at setup.
    @param database: Dated disposable schema for this run.
    @return: Guard pair to pass to apply_migration; the local fixed guards are never substituted.
    """
    settings_guard = partial(_check_settings, environ, database=database)
    session_guard = container_session_guard(environ, settings, inspect, expected_server_uuid, database)
    return MigrationGuards(settings_guard, session_guard)
