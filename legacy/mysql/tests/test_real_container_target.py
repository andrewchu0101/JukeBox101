"""Pin the named real-isolated container profile: every fact must agree before the migrator may connect.

The profile is opt-in with its own switch, names jb101-real-mysql, volume jb101-real-data, the published loopback port 3309 (checked as a container fact),
the schema jukebox101_test and the migrator account, and pins the server UUID recorded at creation. Refusals carry only a code.
"""

from unittest.mock import MagicMock

import pytest

from bot.config import ConfigurationError, DatabaseSettings
from bot.database.real_container_target import (
    REAL_CONTAINER_NAME,
    REAL_DATABASE,
    REAL_PUBLISHED_PORT,
    REAL_SWITCH,
    REAL_VOLUME_NAME,
    real_guards,
    real_session_guard,
    verify_real_container_target,
)
from bot.database.migration_container_target import MIGRATION_CONTAINER_SWITCH
from bot.database.submission_migration import MIGRATION_AUTHORIZATION, apply_migration

PIN = "a9216e09-1111-2222-3333-444444444444"
MIGRATOR_ACCOUNT = "jukebox101_test_migrator@127.0.0.1"
ENVIRON = {REAL_SWITCH: "1"}


def _settings(**overrides) -> DatabaseSettings:
    """@description: Build the migrator destination for the real profile, with every field matching by default.
    @param overrides: Fields to change for a single refusal case.
    @return: Explicit settings: the in-namespace endpoint and the migrator user.
    """
    values = {"host": "127.0.0.1", "port": 3306, "user": "jukebox101_test_migrator", "password": "sentinel-secret", "database": REAL_DATABASE}
    values.update(overrides)
    return DatabaseSettings(**values)


def _inspect(**overrides) -> dict:
    """@description: Build the docker inspect subset for the real container, with every field matching by default.
    @param overrides: Fields to change for a single refusal case.
    @return: Mapping shaped like the selected docker inspect fields.
    """
    inspect = {
        "Name": f"/{REAL_CONTAINER_NAME}",
        "Mounts": [{"Type": "volume", "Name": REAL_VOLUME_NAME, "Destination": "/var/lib/mysql"}],
        "HostConfig": {"PortBindings": {"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(REAL_PUBLISHED_PORT)}]}},
    }
    inspect.update(overrides)
    return inspect


def _identity(**overrides) -> dict:
    """@description: Build the live identity the real profile compares, with every field matching by default.
    @param overrides: Fields to change for a single refusal case.
    @return: Mapping of server and account facts read with read-only SELECT statements.
    """
    identity = {"server_uuid": PIN, "version": "8.0.46", "database_name": REAL_DATABASE, "account": MIGRATOR_ACCOUNT, "active_roles": "NONE"}
    identity.update(overrides)
    return identity


def _verify(environ=None, settings=None, inspect=None, identity=None, pin=PIN):
    """@description: Run the real-profile verification with defaults for every argument not under test.
    @param environ: Environment mapping for the opt-in check.
    @param settings: Migrator destination.
    @param inspect: Docker inspect subset.
    @param identity: Live server identity.
    @param pin: Server UUID recorded at creation.
    @return: None; raises ConfigurationError on refusal.
    """
    verify_real_container_target(ENVIRON if environ is None else environ, _settings() if settings is None else settings,
                                 _inspect() if inspect is None else inspect, _identity() if identity is None else identity, pin)


def test_all_facts_agreeing_is_accepted():
    _verify()


def test_the_real_profile_has_its_own_switch_and_the_migration_switch_does_not_enable_it():
    assert REAL_SWITCH != MIGRATION_CONTAINER_SWITCH
    with pytest.raises(ConfigurationError, match="REAL_CONTAINER_NOT_AUTHORIZED"):
        _verify(environ={MIGRATION_CONTAINER_SWITCH: "1"})


@pytest.mark.parametrize("value", ["0", "true", ""])
def test_a_real_switch_value_other_than_exactly_one_is_refused(value):
    with pytest.raises(ConfigurationError, match="REAL_CONTAINER_NOT_AUTHORIZED"):
        _verify(environ={REAL_SWITCH: value})


@pytest.mark.parametrize(("host", "port"), [("127.0.0.1", 3309), ("127.0.0.1", 3307), ("127.0.0.1", 3308), ("localhost", 3306), ("10.0.0.5", 3306)])
def test_an_endpoint_other_than_the_in_namespace_port_is_refused(host, port):
    with pytest.raises(ConfigurationError, match="ENDPOINT_MISMATCH"):
        _verify(settings=_settings(host=host, port=port))


def test_the_protected_mysql80_host_port_is_refused_as_a_container_fact():
    bindings = {"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3306"}]}
    with pytest.raises(ConfigurationError, match="PORT_PROTECTED"):
        _verify(inspect=_inspect(HostConfig={"PortBindings": bindings}))


@pytest.mark.parametrize("port", ["3307", "3308"])
def test_the_other_isolated_published_ports_are_refused_as_container_facts(port):
    bindings = {"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]}
    with pytest.raises(ConfigurationError, match="PORT_NOT_ISOLATED"):
        _verify(inspect=_inspect(HostConfig={"PortBindings": bindings}))


def test_the_isolated_test_container_name_is_refused():
    with pytest.raises(ConfigurationError, match="CONTAINER_NAME_MISMATCH"):
        _verify(inspect=_inspect(Name="/jb101-iso-mysql"))


def test_the_migration_container_name_is_refused():
    with pytest.raises(ConfigurationError, match="CONTAINER_NAME_MISMATCH"):
        _verify(inspect=_inspect(Name="/jb101-mig-mysql"))


def test_the_isolated_volume_is_refused():
    mounts = [{"Type": "volume", "Name": "jb101-iso-data", "Destination": "/var/lib/mysql"}]
    with pytest.raises(ConfigurationError, match="VOLUME_MISMATCH"):
        _verify(inspect=_inspect(Mounts=mounts))


def test_a_bind_mount_is_refused():
    mounts = [{"Type": "bind", "Source": "/tmp", "Destination": "/var/lib/mysql"}]
    with pytest.raises(ConfigurationError, match="VOLUME_MISMATCH"):
        _verify(inspect=_inspect(Mounts=mounts))


def test_a_schema_other_than_jukebox101_test_is_refused():
    with pytest.raises(ConfigurationError, match="DATABASE_MISMATCH"):
        _verify(settings=_settings(database="jukebox101_mig_202610281706"), identity=_identity(database_name="jukebox101_mig_202610281706"))


def test_an_application_account_is_refused_as_migrator():
    with pytest.raises(ConfigurationError, match="MIGRATOR_ACCOUNT_MISMATCH"):
        _verify(settings=_settings(user="jukebox101_test_app"), identity=_identity(account="jukebox101_test_app@127.0.0.1"))


def test_a_live_account_outside_loopback_is_refused():
    with pytest.raises(ConfigurationError, match="MIGRATOR_ACCOUNT_MISMATCH"):
        _verify(identity=_identity(account="jukebox101_test_migrator@172.17.0.1"))


def test_an_active_role_is_refused():
    with pytest.raises(ConfigurationError, match="ACTIVE_ROLE_PRESENT"):
        _verify(identity=_identity(active_roles="`role_admin`@`%`"))


def test_a_live_uuid_differing_from_the_creation_pin_is_refused():
    with pytest.raises(ConfigurationError, match="SERVER_UUID_MISMATCH"):
        _verify(identity=_identity(server_uuid="99999999-0000-0000-0000-000000000000"))


def test_a_pin_differing_from_the_live_server_is_refused():
    with pytest.raises(ConfigurationError, match="SERVER_UUID_MISMATCH"):
        _verify(pin="99999999-0000-0000-0000-000000000000")


def test_refusals_never_echo_the_password_or_the_uuid():
    with pytest.raises(ConfigurationError) as refusal:
        _verify(identity=_identity(server_uuid="99999999-0000-0000-0000-000000000000"), settings=_settings(password="sentinel-secret"))
    assert "sentinel-secret" not in str(refusal.value)
    assert "99999999" not in str(refusal.value)


def test_the_session_guard_issues_only_the_identity_read_and_refuses_before_ddl():
    cursor = MagicMock()
    cursor.fetchone.return_value = _identity(server_uuid="99999999-0000-0000-0000-000000000000")
    with pytest.raises(ConfigurationError, match="SERVER_UUID_MISMATCH"):
        real_session_guard(ENVIRON, _settings(), _inspect(), PIN)(cursor)
    assert cursor.execute.call_count == 1


def test_apply_migration_with_real_guards_refuses_without_opt_in_before_connecting():
    factory = MagicMock()
    guards = real_guards({}, _settings(), _inspect(), PIN)
    with pytest.raises(ConfigurationError, match="REAL_CONTAINER_NOT_AUTHORIZED"):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=_settings(), guards=guards)
    factory.assert_not_called()
