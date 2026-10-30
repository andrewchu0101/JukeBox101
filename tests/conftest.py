"""Supply isolated configuration for unit tests.

Environment fixtures prevent local credentials from affecting mock-based verification.
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from bot.config import load_database_settings
from tests.batch_b_assertion_guard import ASSERTIONS_DISABLED_EXIT_CODE, AssertionsDisabledError, require_assertions_enabled


def pytest_configure(config):
    """@description: Stop the whole pytest session under -O so assert statements in helper modules cannot yield a vacuous pass.
    @param config: Pytest configuration object for this session.
    @return: None; ends the session with the approved refusal status when assertions are disabled.
    """
    try:
        require_assertions_enabled()
    except AssertionsDisabledError as error:
        pytest.exit(str(error), returncode=ASSERTIONS_DISABLED_EXIT_CODE)


@pytest.fixture
def database_settings(monkeypatch):
    """@description: Keep unit tests independent of credentials and local dotenv files.
    @param monkeypatch: Pytest environment isolation facility.
    @return: Valid settings containing a synthetic password.
    """
    # [test configuration]: Synthetic credentials must never reach a real connection.
    environment_values = {
        "DB_HOST": "127.0.0.1", "DB_PORT": "5432",
        "DB_USER": "jukebox101_app", "DB_PASSWORD": "synthetic-secret",
        "DB_NAME": "jukebox101",
    }
    for setting_name, setting_value in environment_values.items():
        monkeypatch.setenv(setting_name, setting_value)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    return load_database_settings()


@pytest.fixture
def unix_permission_dir(tmp_path):
    """@description: Give permission-bit tests a directory on the Linux filesystem, because the project's pytest basetemp on a Windows drive mount ignores chmod.
    @param tmp_path: Default temporary directory, used unchanged on Windows where permission bits are emulated.
    @return: Directory whose mode changes persist, removed after the test.
    """
    if os.name != "posix":
        return tmp_path
    permission_dir = Path(tempfile.mkdtemp(prefix="jb101-perm-"))  # [Unix permission scope]: Created under the system temp directory, not the project drive.
    yield permission_dir
    shutil.rmtree(permission_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def pg_cluster():
    """@description: Provide the verified disposable PostgreSQL server, or skip postgres-marked tests when none is configured.
    @return: Prepared cluster description (random role passwords held in memory only).
    """
    from tests.postgres_support import _environment_cluster, prepare_cluster
    cluster = _environment_cluster()
    if cluster is None:
        pytest.skip("JB_PG_TEST_HOST and admin password are not set; disposable PostgreSQL not configured")
    prepare_cluster(cluster)
    return cluster


@pytest.fixture
def pg_database(pg_cluster):
    """@description: Create a fresh, fully migrated test database for one test and always drop it afterwards.
    @param pg_cluster: Verified disposable server.
    @return: Settings for each production role on the new database.
    """
    from tests.postgres_support import create_database, drop_database
    database = create_database(pg_cluster)
    try:
        yield database
    finally:
        drop_database(pg_cluster, database.name)
