"""Verify that a container-hosted disposable MySQL is the one approved isolated target before any test connects.

This is an additional container test mode. It does not replace the local DATADIR guard in isolated_mysql_target:
a container's data directory is a container path, so the local run-folder comparison cannot apply to it.
Every fact must agree: the dedicated container name, the named volume mount, the loopback-only port binding,
the disposable test schema and the live server identity pinned at setup. Refusals carry only a code.
"""

from bot.config import ConfigurationError

CONTAINER_NAME = "jb101-iso-mysql"  # [dedicated container]: The only container this test mode will accept.
VOLUME_NAME = "jb101-iso-data"  # [dedicated volume]: The named volume that must back the server data directory.
VOLUME_DESTINATION = "/var/lib/mysql"  # [container data path]: Checked as a mount point, never trusted as a host path.
LOOPBACK_HOST = "127.0.0.1"  # [loopback only]: The published address must be this exact host IP.
LOOPBACK_PORT = 3307  # [isolated port]: The only host port this container mode will connect to.
PROTECTED_PORTS = frozenset({3306})  # [protected environment]: The existing MySQL80 port is never a test destination.
TEST_DATABASE = "jukebox101_test"  # [disposable schema]: The only database name accepted from the live server.
MINIMUM_SERVER_VERSION = (8, 0, 16)  # [CHECK enforcement]: Earlier servers do not enforce the migration constraints.


def _version_tuple(version_text: str) -> tuple:
    """@description: Turn a server version string into comparable numbers, ignoring any suffix after the dash.
    @param version_text: Version reported by the server.
    @return: Tuple of integers for major, minor and patch.
    """
    return tuple(int(part) for part in version_text.split("-")[0].split(".")[:3])


def _check_container_name(inspect: dict, container_name: str) -> None:
    """@description: Accept only the dedicated container name, so a similarly named container is never used.
    @param inspect: Selected docker inspect fields.
    @param container_name: The one container name this mode accepts.
    @return: None; raises ConfigurationError with a code on mismatch.
    """
    if inspect.get("Name") != f"/{container_name}":
        raise ConfigurationError("CONTAINER_NAME_MISMATCH")


def _check_volume_mount(inspect: dict, volume_name: str) -> None:
    """@description: Require the named volume at the data path, so a bind mount from the host cannot pass as the isolated data.
    @param inspect: Selected docker inspect fields.
    @param volume_name: The one named volume this mode accepts.
    @return: None; raises ConfigurationError with a code on mismatch.
    """
    expected = {"Type": "volume", "Name": volume_name, "Destination": VOLUME_DESTINATION}
    if not any(all(mount.get(key) == value for key, value in expected.items()) for mount in inspect.get("Mounts", [])):
        raise ConfigurationError("VOLUME_MISMATCH")


def _check_port_binding(inspect: dict, loopback_port: int) -> None:
    """@description: Require exactly one loopback binding to the isolated port and refuse the protected port.
    @param inspect: Selected docker inspect fields.
    @param loopback_port: The one host port this mode accepts.
    @return: None; raises ConfigurationError with a code on mismatch.
    """
    bindings = inspect.get("HostConfig", {}).get("PortBindings", {}).get("3306/tcp") or []
    if any(int(binding.get("HostPort", -1)) in PROTECTED_PORTS for binding in bindings):
        raise ConfigurationError("PORT_PROTECTED")
    if len(bindings) != 1 or bindings[0].get("HostIp") != LOOPBACK_HOST:
        raise ConfigurationError("PORT_NOT_LOOPBACK")
    if int(bindings[0].get("HostPort", -1)) != loopback_port:
        raise ConfigurationError("PORT_NOT_ISOLATED")


def _check_live_identity(identity: dict, expected_server_uuid: str, database: str) -> None:
    """@description: Compare the live server identity with the pin, so the connection reaches the approved instance only.
    @param identity: Server facts read with read-only SELECT statements.
    @param expected_server_uuid: UUID pinned when the disposable instance was created.
    @param database: The one schema name the live server must report.
    @return: None; raises ConfigurationError with a code on mismatch.
    """
    if str(identity.get("server_uuid", "")).lower() != expected_server_uuid.lower():
        raise ConfigurationError("SERVER_UUID_MISMATCH")
    if identity.get("database_name") != database:
        raise ConfigurationError("DATABASE_MISMATCH")
    if _version_tuple(str(identity.get("version", "0"))) < MINIMUM_SERVER_VERSION:
        raise ConfigurationError("SERVER_VERSION_TOO_OLD")


def verify_container_target(inspect: dict, identity: dict, expected_server_uuid: str, *, container_name: str = CONTAINER_NAME,
                            volume_name: str = VOLUME_NAME, loopback_port: int = LOOPBACK_PORT, database: str = TEST_DATABASE) -> None:
    """@description: Refuse any container test target unless container, volume, port, schema and server identity all agree.
    @param inspect: Selected docker inspect fields only (name, mounts, port bindings); never the environment.
    @param identity: Live server facts read through the approved test connection.
    @param expected_server_uuid: UUID pinned for this disposable instance.
    @param container_name: Dedicated container name; defaults to the isolated test container.
    @param volume_name: Dedicated named volume; defaults to the isolated test volume.
    @param loopback_port: Dedicated loopback host port; defaults to the isolated test port.
    @param database: Disposable schema the live server must report; defaults to the isolated test schema.
    @return: None when every check passes; raises ConfigurationError carrying only a refusal code otherwise.
    """
    _check_container_name(inspect, container_name)
    _check_volume_mount(inspect, volume_name)
    _check_port_binding(inspect, loopback_port)
    _check_live_identity(identity, expected_server_uuid, database)
