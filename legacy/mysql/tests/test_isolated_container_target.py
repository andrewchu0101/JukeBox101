"""Pin the container test-mode target check: every identity fact must agree before a test may connect.

The check cross-examines the dedicated container, its named volume mount, its loopback-only port binding,
the test database and the live server identity. Any mismatch refuses the target by code only.
"""

import pytest

from bot.config import ConfigurationError
from bot.isolated_container_target import (
    CONTAINER_NAME,
    LOOPBACK_HOST,
    LOOPBACK_PORT,
    MINIMUM_SERVER_VERSION,
    VOLUME_NAME,
    verify_container_target,
)

SERVER_UUID = "11111111-2222-3333-4444-555555555555"


def _inspect(**overrides) -> dict:
    """@description: Build the minimal docker inspect subset the guard reads, with every field matching by default.
    @param overrides: Fields to change for a single refusal case.
    @return: Mapping shaped like the selected docker inspect fields.
    """
    inspect = {
        "Name": f"/{CONTAINER_NAME}",
        "Mounts": [{"Type": "volume", "Name": VOLUME_NAME, "Destination": "/var/lib/mysql"}],
        "HostConfig": {"PortBindings": {"3306/tcp": [{"HostIp": LOOPBACK_HOST, "HostPort": str(LOOPBACK_PORT)}]}},
    }
    inspect.update(overrides)
    return inspect


def _identity(**overrides) -> dict:
    """@description: Build the live server identity the guard compares, with every field matching by default.
    @param overrides: Fields to change for a single refusal case.
    @return: Mapping of server facts read with read-only SELECT statements.
    """
    identity = {
        "server_uuid": SERVER_UUID,
        "version": "8.0.46",
        "database_name": "jukebox101_test",
        "port": 3306,
    }
    identity.update(overrides)
    return identity


def test_all_facts_agreeing_is_accepted():
    verify_container_target(_inspect(), _identity(), SERVER_UUID)


@pytest.mark.parametrize("name", ["/some-other-container", "/jb101-iso-mysql-copy"])
def test_a_different_container_name_is_refused(name):
    with pytest.raises(ConfigurationError, match="CONTAINER_NAME_MISMATCH"):
        verify_container_target(_inspect(Name=name), _identity(), SERVER_UUID)


def test_a_bind_mount_instead_of_the_named_volume_is_refused():
    mounts = [{"Type": "bind", "Source": "/home/gaston/anything", "Destination": "/var/lib/mysql"}]
    with pytest.raises(ConfigurationError, match="VOLUME_MISMATCH"):
        verify_container_target(_inspect(Mounts=mounts), _identity(), SERVER_UUID)


def test_a_volume_with_another_name_is_refused():
    mounts = [{"Type": "volume", "Name": "some-other-volume", "Destination": "/var/lib/mysql"}]
    with pytest.raises(ConfigurationError, match="VOLUME_MISMATCH"):
        verify_container_target(_inspect(Mounts=mounts), _identity(), SERVER_UUID)


def test_a_port_published_on_all_interfaces_is_refused():
    bindings = {"3306/tcp": [{"HostIp": "0.0.0.0", "HostPort": str(LOOPBACK_PORT)}]}
    with pytest.raises(ConfigurationError, match="PORT_NOT_LOOPBACK"):
        verify_container_target(_inspect(HostConfig={"PortBindings": bindings}), _identity(), SERVER_UUID)


def test_the_protected_mysql80_port_is_refused_even_on_loopback():
    bindings = {"3306/tcp": [{"HostIp": LOOPBACK_HOST, "HostPort": "3306"}]}
    with pytest.raises(ConfigurationError, match="PORT_PROTECTED"):
        verify_container_target(_inspect(HostConfig={"PortBindings": bindings}), _identity(), SERVER_UUID)


def test_a_database_other_than_the_disposable_test_schema_is_refused():
    with pytest.raises(ConfigurationError, match="DATABASE_MISMATCH"):
        verify_container_target(_inspect(), _identity(database_name="mysql"), SERVER_UUID)


def test_a_server_uuid_that_differs_from_the_pin_is_refused():
    with pytest.raises(ConfigurationError, match="SERVER_UUID_MISMATCH"):
        verify_container_target(_inspect(), _identity(server_uuid="99999999-0000-0000-0000-000000000000"), SERVER_UUID)


def test_a_server_older_than_the_minimum_version_is_refused():
    with pytest.raises(ConfigurationError, match="SERVER_VERSION_TOO_OLD"):
        verify_container_target(_inspect(), _identity(version="8.0.15"), SERVER_UUID)


def test_the_minimum_version_constant_is_the_project_requirement():
    assert MINIMUM_SERVER_VERSION == (8, 0, 16)


def test_refusal_messages_carry_only_the_code_not_the_secret_like_values():
    with pytest.raises(ConfigurationError) as refusal:
        verify_container_target(_inspect(), _identity(server_uuid="99999999-0000-0000-0000-000000000000"), SERVER_UUID)
    assert "99999999" not in str(refusal.value)
    assert SERVER_UUID not in str(refusal.value)
