"""Inspect additive migration structure and exercise its runner only with mock connections.

No SQL in this module reaches a driver; DDL recovery and real constraint validity remain integration-test obligations.
"""

from unittest.mock import MagicMock
import re

import pytest
from bot.config import DatabaseSettings
from bot.database.test_profiles import MIGRATION_SWITCH

SETTINGS = DatabaseSettings("127.0.0.1", 3306, "jukebox101_test_migrator", "synthetic-only", "jukebox101_test")


@pytest.fixture(autouse=True)
def execution_opt_in(monkeypatch) -> None:
    """@description: Enable only the mock runner, with no real credentials.
    @param monkeypatch: Scoped synthetic switch.
    @return: None.
    """
    monkeypatch.setenv(MIGRATION_SWITCH, "1")


from bot.database.submission_migration import (MIGRATION_AUTHORIZATION,
                                              apply_migration, migration_plan)


def migration_context(previous: dict | None) -> tuple[MagicMock, MagicMock, MagicMock]:
    """@description: Script metadata and migration markers without a database engine.
    @param previous: Existing version marker or None for first application.
    @return: Factory, connection and cursor mocks.
    """
    factory = MagicMock()  # [offline migration factory]: Never imports settings or credentials.
    connection = factory.return_value
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect = [{"database_name": "jukebox101_test", "account": "jukebox101_test_migrator@127.0.0.1",
                                    "server_port": 3306, "mysql_version": "8.0.46", "active_roles": "NONE"},
                                   {"acquired": 1}, previous]
    # [expected metadata]: Table names come from the reviewed artifact, not a hand-waved success flag.
    names = [statement.split()[5] for statement in migration_plan()[1]]
    metadata = [{"table_name": name, "engine": "InnoDB"} for name in names]
    cursor.fetchall.side_effect = [metadata] if previous else [[], metadata]
    return factory, connection, cursor


def test_migration_is_additive_and_protects_six_existing_tables() -> None:
    """@description: Reject destructive statements and preserve established table definitions.
    @param: none
    @return: None.
    """
    checksum, statements = migration_plan()  # [reviewed plan]: Reading this artifact executes no SQL.
    assert len(checksum) == 64 and len(statements) == 16
    assert all(statement.startswith("CREATE TABLE IF NOT EXISTS jb2_") for statement in statements)
    assert not re.search(r"\b(DROP|TRUNCATE|DELETE|ALTER)\b", "\n".join(statements), re.IGNORECASE)
    assert "accepted_sequence BIGINT UNSIGNED NOT NULL UNIQUE" in "\n".join(statements)
    assert "version_id VARCHAR(128) NOT NULL PRIMARY KEY" in "\n".join(statements)
    assert "settlement_enabled BOOLEAN NOT NULL DEFAULT FALSE" in statements[0]


def test_migration_requires_explicit_future_authorization_before_connect() -> None:
    """@description: Prevent accidental migration startup even with a usable connection factory.
    @param: none
    @return: None.
    """
    factory = MagicMock()  # [must remain unused]: No I/O before the deliberate invocation guard.
    with pytest.raises(PermissionError):
        apply_migration(factory, "not-authorized", settings=SETTINGS)
    factory.assert_not_called()


def test_mock_migration_applies_once_and_bootstraps_only_legacy_membership() -> None:
    """@description: Record applying before DDL and applied after nonrewarding legacy bootstrap.
    @param: none
    @return: None.
    """
    factory, connection, cursor = migration_context(None)  # [mock-only DDL]: No statement leaves the fake boundary.
    assert apply_migration(factory, MIGRATION_AUTHORIZATION, settings=SETTINGS)
    statements = [call.args[0] for call in cursor.execute.call_args_list]  # [SQL intent]: Auditable DDL order.
    assert statements.index("INSERT INTO jb2_legacy (song_id) SELECT song_id FROM songs") > 16
    assert not any(statement.startswith(("UPDATE users", "INSERT INTO credit_ledger", "INSERT INTO jb2_events")) for statement in statements)
    connection.commit.assert_any_call()
    assert connection.commit.call_count == 2
    connection.close.assert_called_once()


def test_identical_applied_migration_is_a_noop_after_metadata_verification() -> None:
    """@description: Avoid repeating schema or historical bootstrap on an already applied version.
    @param: none
    @return: None.
    """
    previous = {"checksum": migration_plan()[0], "state": "applied"}  # [same reviewed bytes]: Idempotent replay.
    factory, connection, cursor = migration_context(previous)
    assert not apply_migration(factory, MIGRATION_AUTHORIZATION, settings=SETTINGS)
    assert not any(call.args[0].startswith("INSERT") for call in cursor.execute.call_args_list)
    connection.close.assert_called_once()


@pytest.mark.parametrize("state,checksum", [("applying", None), ("applied", "changed-digest")])
def test_partial_or_changed_migration_stops_for_manual_recovery(state: str, checksum: str | None) -> None:
    """@description: Never pretend MySQL partial DDL was rolled back or safe to replay automatically.
    @param state: Persisted migration state.
    @param checksum: Existing digest or current reviewed bytes.
    @return: None.
    """
    factory, connection, cursor = migration_context({"checksum": checksum or migration_plan()[0], "state": state})
    with pytest.raises(RuntimeError, match="manual recovery"):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=SETTINGS)
    assert not any(call.args[0].startswith("CREATE TABLE IF NOT EXISTS jb2_control") for call in cursor.execute.call_args_list)
    connection.close.assert_called_once()


def test_unversioned_tables_and_nontransactional_metadata_are_rejected() -> None:
    """@description: Fail closed on unknown leftovers instead of letting IF NOT EXISTS hide them.
    @param: none
    @return: None.
    """
    factory, connection, cursor = migration_context(None)  # [unowned partial schema]: Must be manually inspected.
    cursor.fetchall.side_effect = [[{"table_name": "jb2_control", "engine": "InnoDB"}]]
    with pytest.raises(RuntimeError, match="Unversioned"):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=SETTINGS)
    factory, connection, cursor = migration_context({"checksum": migration_plan()[0], "state": "applied"})
    cursor.fetchall.side_effect = [[{"table_name": "jb2_control", "engine": "MyISAM"}]]
    with pytest.raises(RuntimeError, match="Incomplete"):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=SETTINGS)


def test_mock_ddl_failure_never_marks_applied_and_closes_session() -> None:
    """@description: Retain the durable applying marker when a later implicit-commit DDL step fails.
    @param: none
    @return: None.
    """
    factory, connection, cursor = migration_context(None)  # [partial DDL fault]: Cannot be repaired by normal rollback.
    def fail_ddl(statement: str, parameters: tuple = ()) -> None:
        """@description: Fail only after the version guard has been committed.
        @param statement: Mock SQL statement.
        @param parameters: Bound mock parameters.
        @return: None.
        """
        if statement.startswith("CREATE TABLE IF NOT EXISTS jb2_versions"):
            raise ValueError("synthetic DDL failure")
    cursor.execute.side_effect = fail_ddl
    with pytest.raises(ValueError, match="synthetic DDL"):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=SETTINGS)
    assert connection.commit.call_count == 1
    assert not any(call.args[0].startswith("UPDATE jb2_migrations") for call in cursor.execute.call_args_list)
    connection.close.assert_called_once()


@pytest.mark.parametrize("database_name,account", [("jukebox101", "jukebox101_test_app@127.0.0.1"),
                                                 ("jukebox101_test", "jukebox101_app@127.0.0.1")])
def test_migration_actual_destination_is_checked_before_any_ddl(database_name: str, account: str) -> None:
    """@description: Refuse production database or account even if the injected factory advertised test settings.
    @param database_name: Actual selected database returned by the mocked server.
    @param account: Actual authenticated server account.
    @return: None.
    """
    factory, connection, cursor = migration_context(None)  # [wrong actual identity]: No DDL is allowed after this probe.
    cursor.fetchone.side_effect = [{"database_name": database_name, "account": account, "server_port": 3306, "mysql_version": "8.0.46", "active_roles": "NONE"}]
    with pytest.raises(PermissionError, match="fixed disposable"):
        apply_migration(factory, MIGRATION_AUTHORIZATION, settings=SETTINGS)
    assert not any(call.args[0].startswith("CREATE") for call in cursor.execute.call_args_list)
    connection.close.assert_called_once()
