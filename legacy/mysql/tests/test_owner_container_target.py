"""Pin the Migration container target mode: every fact must agree before a migration may connect.

The mode is opt-in, names its own container, volume, loopback port and dated disposable schema, and reads the migrator identity.
Any mismatch refuses by code only; no protected port, no shared container and no shared volume is ever accepted.
"""

from unittest.mock import MagicMock

import pytest

from bot.config import ConfigurationError, DatabaseSettings
from bot.database.migration_container_target import (
    CONNECT_PORT,
    CONTAINER_NAME,
    PUBLISHED_PORT,
    MIGRATION_CONTAINER_SWITCH,
    MIGRATOR_ACCOUNT,
    VOLUME_NAME,
    container_guards,
    container_session_guard,
    verify_migration_container_target,
)
from bot.database.submission_migration import MIGRATION_AUTHORIZATION, apply_migration

SERVER_UUID = "11111111-2222-3333-4444-555555555555"
DATABASE = "jukebox101_mig_202610291530"
ENVIRON = {MIGRATION_CONTAINER_SWITCH: "1"}


def _settings(**overrides) -> DatabaseSettings:
    """@description: Build the migrator destination the container mode is asked to verify, with every field matching by default.
    @param overrides: Fields to change for a single refusal case.
    @return: Explicit settings shaped like the migration profile.
    """
    values = {"host": "127.0.0.1", "port": CONNECT_PORT, "user": "jukebox101_test_migrator", "password": "not-a-real-secret", "database": DATABASE}
    values.update(overrides)
    return DatabaseSettings(**values)


def _inspect(**overrides) -> dict:
    """@description: Build the docker inspect subset the container mode reads, with every field matching by default.
    @param overrides: Fields to change for a single refusal case.
    @return: Mapping shaped like the selected docker inspect fields.
    """
    inspect = {
        "Name": f"/{CONTAINER_NAME}",
        "Mounts": [{"Type": "volume", "Name": VOLUME_NAME, "Destination": "/var/lib/mysql"}],
        "HostConfig": {"PortBindings": {"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(PUBLISHED_PORT)}]}},
    }
    inspect.update(overrides)
    return inspect


def _identity(**overrides) -> dict:
    """@description: Build the live identity the container mode compares, with every field matching by default.
    @param overrides: Fields to change for a single refusal case.
    @return: Mapping of server and account facts read with read-only SELECT statements.
    """
    identity = {
        "server_uuid": SERVER_UUID,
        "version": "8.0.46",
        "database_name": DATABASE,
        "account": MIGRATOR_ACCOUNT,
        "active_roles": "NONE",
    }
    identity.update(overrides)
    return identity


def _verify(environ=None, settings=None, inspect=None, identity=None):
    """@description: Run the container verification with defaults for every argument not under test.
    @param environ: Environment mapping for the opt-in check.
    @param settings: Migrator destination.
    @param inspect: Docker inspect subset.
    @param identity: Live server identity.
    @return: None; raises ConfigurationError on refusal.
    """
    verify_migration_container_target(
        ENVIRON if environ is None else environ,
        _settings() if settings is None else settings,
        _inspect() if inspect is None else inspect,
        _identity() if identity is None else identity,
        SERVER_UUID,
        DATABASE,
    )


def test_all_facts_agreeing_is_accepted():
    _verify()


def test_missing_opt_in_switch_is_refused():
    with pytest.raises(ConfigurationError, match="MIGRATION_CONTAINER_NOT_AUTHORIZED"):
        _verify(environ={})


@pytest.mark.parametrize("value", ["0", "true", "yes", ""])
def test_a_switch_value_other_than_exactly_one_is_refused(value):
    with pytest.raises(ConfigurationError, match="MIGRATION_CONTAINER_NOT_AUTHORIZED"):
        _verify(environ={MIGRATION_CONTAINER_SWITCH: value})


@pytest.mark.parametrize(("host", "port"), [("127.0.0.1", 3307), ("127.0.0.1", 3308), ("localhost", CONNECT_PORT), ("10.0.0.5", CONNECT_PORT)])
def test_an_endpoint_other_than_the_in_namespace_container_port_is_refused(host, port):
    with pytest.raises(ConfigurationError, match="ENDPOINT_MISMATCH"):
        _verify(settings=_settings(host=host, port=port))


def test_a_different_container_name_is_refused():
    with pytest.raises(ConfigurationError, match="CONTAINER_NAME_MISMATCH"):
        _verify(inspect=_inspect(Name="/jb101-iso-mysql"))


def test_a_volume_other_than_the_dedicated_migration_volume_is_refused():
    mounts = [{"Type": "volume", "Name": "jb101-iso-data", "Destination": "/var/lib/mysql"}]
    with pytest.raises(ConfigurationError, match="VOLUME_MISMATCH"):
        _verify(inspect=_inspect(Mounts=mounts))


def test_a_bind_mount_from_the_host_is_refused():
    mounts = [{"Type": "bind", "Source": "/home/gaston/mig-data", "Destination": "/var/lib/mysql"}]
    with pytest.raises(ConfigurationError, match="VOLUME_MISMATCH"):
        _verify(inspect=_inspect(Mounts=mounts))


def test_a_database_name_outside_the_dated_pattern_is_refused():
    with pytest.raises(ConfigurationError, match="DATABASE_MISMATCH"):
        _verify(settings=_settings(database="jukebox101_test"), identity=_identity(database_name="jukebox101_test"))


def test_a_database_name_that_differs_from_the_requested_run_is_refused():
    with pytest.raises(ConfigurationError, match="DATABASE_MISMATCH"):
        _verify(identity=_identity(database_name="jukebox101_mig_202610291531"))


def test_a_server_uuid_that_differs_from_the_pin_is_refused():
    with pytest.raises(ConfigurationError, match="SERVER_UUID_MISMATCH"):
        _verify(identity=_identity(server_uuid="99999999-0000-0000-0000-000000000000"))


def test_an_application_account_is_refused_for_migration():
    with pytest.raises(ConfigurationError, match="MIGRATOR_ACCOUNT_MISMATCH"):
        _verify(settings=_settings(user="jukebox101_test_app"), identity=_identity(account="jukebox101_test_app@127.0.0.1"))


def test_an_active_role_on_the_migrator_session_is_refused():
    with pytest.raises(ConfigurationError, match="ACTIVE_ROLE_PRESENT"):
        _verify(identity=_identity(active_roles="`role_admin`@`%`"))


def test_refusal_messages_carry_only_the_code_not_the_secret_like_values():
    with pytest.raises(ConfigurationError) as refusal:
        _verify(identity=_identity(server_uuid="99999999-0000-0000-0000-000000000000"), settings=_settings(password="sentinel-secret"))
    assert "99999999" not in str(refusal.value)
    assert "sentinel-secret" not in str(refusal.value)


def _session_cursor(**identity_overrides):
    """@description: Build a cursor whose only identity read returns the live migrator facts, so no DDL can be mistaken for a check.
    @param identity_overrides: Live fields to change for a single refusal case.
    @return: Mock cursor with fetchone returning the live identity mapping.
    """
    cursor = MagicMock()
    cursor.fetchone.return_value = _identity(**identity_overrides)
    return cursor


def test_container_guards_refuse_before_any_connection_without_opt_in():
    factory = MagicMock()
    guards = container_guards({}, _settings(), _inspect(), SERVER_UUID, DATABASE)
    with pytest.raises(ConfigurationError, match="MIGRATION_CONTAINER_NOT_AUTHORIZED"):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=_settings(), guards=guards)
    factory.assert_not_called()


def test_container_guards_refuse_a_settings_endpoint_outside_the_container_before_connecting():
    factory = MagicMock()
    guards = container_guards(ENVIRON, _settings(port=PUBLISHED_PORT), _inspect(), SERVER_UUID, DATABASE)
    with pytest.raises(ConfigurationError, match="ENDPOINT_MISMATCH"):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=_settings(port=PUBLISHED_PORT), guards=guards)
    factory.assert_not_called()


def test_session_guard_accepts_the_live_migrator_and_issues_only_the_identity_read():
    cursor = _session_cursor()
    container_session_guard(ENVIRON, _settings(), _inspect(), SERVER_UUID, DATABASE)(cursor)
    assert cursor.execute.call_count == 1


def test_session_guard_refuses_an_application_account_before_any_ddl():
    cursor = _session_cursor(account="jukebox101_app@127.0.0.1")
    with pytest.raises(ConfigurationError, match="MIGRATOR_ACCOUNT_MISMATCH"):
        container_session_guard(ENVIRON, _settings(), _inspect(), SERVER_UUID, DATABASE)(cursor)
    assert cursor.execute.call_count == 1


def test_session_guard_refuses_a_live_server_uuid_that_differs_from_the_pin():
    cursor = _session_cursor(server_uuid="99999999-0000-0000-0000-000000000000")
    with pytest.raises(ConfigurationError, match="SERVER_UUID_MISMATCH"):
        container_session_guard(ENVIRON, _settings(), _inspect(), SERVER_UUID, DATABASE)(cursor)
