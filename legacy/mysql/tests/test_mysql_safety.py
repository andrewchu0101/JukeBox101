"""Exercise disposable database protections with synthetic settings and mock connections.

No test in this module reads dotenv files or contacts a real database.
"""

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from bot.config import ConfigurationError, DatabaseSettings
from tests.mysql_safety import (
    EXPECTED_TABLES, EXPECTED_TEST_ACCOUNT, TEST_DATABASE_NAME,
    load_test_database_settings, validate_connected_test_database, validate_test_destination,
    required_constraint_types, validate_constraint_metadata,
    validate_test_key_columns,
)
from tests.test_mysql_readonly import EXPECTED_CHECKS, EXPECTED_PRIMARY_KEYS, EXPECTED_UNIQUE_KEYS, EXPECTED_FOREIGN_KEYS


@pytest.fixture
def isolated_test_settings():
    """@description: Supply approved test coordinates with a synthetic secret.
    @param: none
    @return: Test-only configuration suitable for mocked safety checks.
    """
    return DatabaseSettings("127.0.0.1", 3306, "jukebox101_test_app", "synthetic-only", TEST_DATABASE_NAME)


@pytest.mark.parametrize("changes", [{"database": "jukebox101"}, {"user": "root"}, {"user": "jukebox101_app"}, {"host": "localhost"}, {"port": 3307}])
def test_reject_unapproved_destination(isolated_test_settings, changes):
    """@description: Block production and administrative settings independently of switches.
    @param isolated_test_settings: Synthetic approved destination.
    @param changes: Unsafe replacement coordinates.
    @return: None.
    """
    with pytest.raises(ConfigurationError):
        validate_test_destination(replace(isolated_test_settings, **changes))


def test_test_environment_does_not_fall_back_to_production(monkeypatch, database_settings):
    """@description: Reject missing TEST_DB keys even when complete production settings exist.
    @param monkeypatch: Isolated environment modification facility.
    @param database_settings: Synthetic production-shaped configuration.
    @return: None.
    """
    monkeypatch.setenv("JUKEBOX_TEST_SCHEMA_READY", "1")
    monkeypatch.setenv("JUKEBOX_DISPOSABLE_DATABASE", TEST_DATABASE_NAME)
    monkeypatch.delenv("TEST_DB_HOST", raising=False)
    with pytest.raises(ConfigurationError, match="TEST_DB_HOST"):
        load_test_database_settings()


@pytest.mark.parametrize("authorization", ["1", "jukebox101", ""])
def test_reject_legacy_disposable_authorization(monkeypatch, authorization):
    """@description: Require the exact disposable database name rather than a generic toggle.
    @param monkeypatch: Isolated environment modification facility.
    @param authorization: Legacy or unsafe authorization value.
    @return: None.
    """
    monkeypatch.setenv("JUKEBOX_TEST_SCHEMA_READY", "1")
    monkeypatch.setenv("JUKEBOX_DISPOSABLE_DATABASE", authorization)
    with pytest.raises(ConfigurationError, match="explicitly name"):
        load_test_database_settings()


def test_connected_production_identity_blocks_writes():
    """@description: Catch an unexpected server database before fixture inserts become possible.
    @param: none
    @return: None.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {"account": EXPECTED_TEST_ACCOUNT, "database_name": "jukebox101"}
    with pytest.raises(ConfigurationError, match="Server identity"):
        validate_connected_test_database(connection)
    assert cursor.execute.call_count == 1
    assert cursor.execute.call_args.args[0].startswith("SELECT ")


@pytest.mark.parametrize("engine", ["InnoDB", "MyISAM", None])
def test_connected_test_schema_requires_transactional_tables(engine):
    """@description: Verify missing or nontransactional tables cannot bypass rollback isolation.
    @param engine: Simulated table engine or absence in metadata.
    @return: None.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {"account": EXPECTED_TEST_ACCOUNT, "database_name": TEST_DATABASE_NAME}
    cursor.fetchall.return_value = [{"TABLE_NAME": name, "ENGINE": engine} for name in EXPECTED_TABLES]
    constraint_rows = [
        {"TABLE_NAME": table, "CONSTRAINT_NAME": name, "CONSTRAINT_TYPE": kind, "ENFORCED": "YES", "CHECK_CLAUSE": None}
        for table, name, kind in required_constraint_types()
    ]
    constraint_rows.extend(
        {"TABLE_NAME": "unused", "CONSTRAINT_NAME": name, "CONSTRAINT_TYPE": "CHECK", "ENFORCED": "YES", "CHECK_CLAUSE": clause}
        for name, clause in EXPECTED_CHECKS.items()
    )
    cursor.fetchall.side_effect = [cursor.fetchall.return_value, constraint_rows, reviewed_key_metadata()]
    if engine == "InnoDB":
        validate_connected_test_database(connection)
    else:
        with pytest.raises(ConfigurationError, match="InnoDB"):
            validate_connected_test_database(connection)
    assert all(call.args[0].startswith("SELECT ") for call in cursor.execute.call_args_list)


def test_incomplete_constraints_rejected():
    """@description: Reject incomplete schema metadata before any fixture inserts.
    @param: none
    @return: None.
    """
    with pytest.raises(ConfigurationError, match="missing reviewed"):
        validate_constraint_metadata([])


def test_explicit_test_settings_load_without_dotenv(monkeypatch):
    """@description: Verify approved test settings load using synthetic TEST_DB values only.
    @param monkeypatch: Isolated environment modification facility.
    @return: None.
    """
    environment_values = {
        "JUKEBOX_TEST_SCHEMA_READY": "1", "JUKEBOX_DISPOSABLE_DATABASE": TEST_DATABASE_NAME,
        "TEST_DB_HOST": "127.0.0.1", "TEST_DB_PORT": "3306", "TEST_DB_USER": "jukebox101_test_app",
        "TEST_DB_NAME": TEST_DATABASE_NAME, "TEST_DB_PASSWORD": "synthetic-test-secret",
        "DB_NAME": "jukebox101",
    }
    for setting_name, setting_value in environment_values.items():
        monkeypatch.setenv(setting_name, setting_value)
    settings = load_test_database_settings()
    assert settings.database == TEST_DATABASE_NAME
    assert "synthetic-test-secret" not in repr(settings)


def test_fixture_rejects_production_before_connect(monkeypatch, isolated_test_settings):
    """@description: Verify the actual write fixture cannot open a production destination.
    @param monkeypatch: Mock replacement facility.
    @param isolated_test_settings: Synthetic approved destination.
    @return: None.
    """
    from tests import test_mysql

    monkeypatch.setenv("JUKEBOX_TEST_SCHEMA_READY", "1")
    connect_mock = MagicMock()
    monkeypatch.setattr(test_mysql, "open_connection", connect_mock)
    fixture_generator = test_mysql.isolated_mysql_rows.__wrapped__(replace(isolated_test_settings, database="jukebox101"))
    with pytest.raises(ConfigurationError):
        next(fixture_generator)
    connect_mock.assert_not_called()


def test_fixture_identity_failure_rolls_back_and_closes(monkeypatch, isolated_test_settings):
    """@description: Ensure an unexpected connected database closes before any insert statement.
    @param monkeypatch: Mock replacement facility.
    @param isolated_test_settings: Synthetic approved destination.
    @return: None.
    """
    from tests import test_mysql

    monkeypatch.setenv("JUKEBOX_TEST_SCHEMA_READY", "1")
    monkeypatch.setenv("JUKEBOX_DISPOSABLE_DATABASE", TEST_DATABASE_NAME)
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {"account": EXPECTED_TEST_ACCOUNT, "database_name": "jukebox101"}
    monkeypatch.setattr(test_mysql, "open_connection", MagicMock(return_value=connection))
    fixture_generator = test_mysql.isolated_mysql_rows.__wrapped__(isolated_test_settings)
    with pytest.raises(ConfigurationError):
        next(fixture_generator)
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()
    connection.begin.assert_not_called()
    assert all(call.args[0].startswith("SELECT ") for call in cursor.execute.call_args_list)


def reviewed_key_metadata() -> list[dict]:
    """@description: Supply synthetic column contracts for testing schema preflight without MySQL.
    @param: none
    @return: Ordered mock key metadata matching the reviewed primary, unique and foreign keys.
    """
    key_rows = [{"TABLE_NAME": table, "CONSTRAINT_NAME": "PRIMARY", "COLUMN_NAME": column,
        "REFERENCED_TABLE_SCHEMA": None} for table, column in EXPECTED_PRIMARY_KEYS.items()]
    key_rows.extend({"TABLE_NAME": table, "CONSTRAINT_NAME": name, "COLUMN_NAME": column,
        "REFERENCED_TABLE_SCHEMA": None}
        for table, keys in EXPECTED_UNIQUE_KEYS.items() for name, columns in keys.items() for column in columns)
    for table_name, keys in EXPECTED_FOREIGN_KEYS.items():
        for name, (target_table, columns, target_columns) in keys.items():
            key_rows.extend({"TABLE_NAME": table_name, "CONSTRAINT_NAME": name, "COLUMN_NAME": column,
                "REFERENCED_TABLE_SCHEMA": TEST_DATABASE_NAME, "REFERENCED_TABLE_NAME": target_table,
                "REFERENCED_COLUMN_NAME": target_column} for column, target_column in zip(columns, target_columns))
    return key_rows


@pytest.mark.parametrize("changed_field, changed_value", [("COLUMN_NAME", "incorrect_column"), ("REFERENCED_TABLE_SCHEMA", "jukebox101"), ("REFERENCED_TABLE_NAME", "users"), ("REFERENCED_COLUMN_NAME", "incorrect_column")])
def test_reject_wrong_compound_foreign_key(changed_field, changed_value):
    """@description: Reject incorrect composite ownership keys even when constraint names match.
    @param changed_field: Foreign-key metadata field deliberately corrupted.
    @param changed_value: Incorrect value that must not pass preflight.
    @return: None.
    """
    key_rows = reviewed_key_metadata()
    foreign_key_row = next(row for row in key_rows if row["CONSTRAINT_NAME"] == "fk_dispatch_owner")
    foreign_key_row[changed_field] = changed_value
    with pytest.raises(ConfigurationError, match="foreign key"):
        validate_test_key_columns(key_rows)


def test_reject_same_name_unique_key_with_wrong_columns():
    """@description: Prevent misleading named uniqueness constraints from passing schema preflight.
    @param: none
    @return: None.
    """
    key_rows = reviewed_key_metadata()
    unique_key_row = next(row for row in key_rows if row["CONSTRAINT_NAME"] == "uq_songs_platform_track")
    unique_key_row["COLUMN_NAME"] = "source_url"
    with pytest.raises(ConfigurationError, match="primary or unique"):
        validate_test_key_columns(key_rows)


def test_fixture_rechecks_disposable_authorization(monkeypatch, isolated_test_settings):
    """@description: Refuse connection when authorization disappears after settings were validated.
    @param monkeypatch: Isolated environment and mock modification facility.
    @param isolated_test_settings: Synthetic approved destination.
    @return: None.
    """
    from tests import test_mysql

    monkeypatch.setenv("JUKEBOX_TEST_SCHEMA_READY", "1")
    monkeypatch.setenv("JUKEBOX_DISPOSABLE_DATABASE", "1")
    connect_mock = MagicMock()
    monkeypatch.setattr(test_mysql, "open_connection", connect_mock)
    fixture_generator = test_mysql.isolated_mysql_rows.__wrapped__(isolated_test_settings)
    with pytest.raises(ConfigurationError, match="explicitly name"):
        next(fixture_generator)
    connect_mock.assert_not_called()
