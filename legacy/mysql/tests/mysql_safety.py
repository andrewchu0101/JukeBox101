"""Guard write-test destinations before any fixture inserts are possible.

Explicit test settings and authenticated identity protect the production database from test writes.
"""

import os
from unittest.mock import patch

from bot.config import ConfigurationError, DatabaseSettings, load_database_settings, required_environment_value
from tests.test_mysql_readonly import (
    EXPECTED_CHECKS, EXPECTED_FOREIGN_KEYS, EXPECTED_PRIMARY_KEYS, EXPECTED_UNIQUE_KEYS,
    normalized_check_clause,
    ordered_key_columns,
)

TEST_DATABASE_NAME = "jukebox101_test"
TEST_DATABASE_USER = "jukebox101_test_app"
EXPECTED_TEST_ACCOUNT = "jukebox101_test_app@127.0.0.1"
EXPECTED_TABLES = {"users", "songs", "submissions", "recommendations", "credit_ledger", "daily_dispatches"}


def require_disposable_authorization() -> None:
    """@description: Reject legacy switches and demand explicit test-database authorization.
    @param: none
    @return: None; raises a configuration error when preparation is not confirmed.
    """
    if os.environ.get("JUKEBOX_TEST_SCHEMA_READY") != "1":
        raise ConfigurationError("JUKEBOX_TEST_SCHEMA_READY must confirm manual test schema creation")
    if os.environ.get("JUKEBOX_DISPOSABLE_DATABASE") != TEST_DATABASE_NAME:
        raise ConfigurationError("JUKEBOX_DISPOSABLE_DATABASE must explicitly name jukebox101_test")


def validate_test_destination(settings: DatabaseSettings) -> None:
    """@description: Prevent production or administrative destinations even when switches are enabled.
    @param settings: Explicit test-only settings awaiting connection.
    @return: None; rejects any destination outside the fixed local test account/database.
    """
    if (settings.host, settings.port, settings.user, settings.database) != (
        "127.0.0.1", 3306, TEST_DATABASE_USER, TEST_DATABASE_NAME,
    ):
        raise ConfigurationError("Write tests require the fixed local jukebox101_test application destination")


def load_test_database_settings() -> DatabaseSettings:
    """@description: Avoid inheriting production DB settings or reading the production dotenv file.
    @param: none
    @return: Validated test configuration from TEST_DB environment variables only.
    """
    require_disposable_authorization()
    # [test credentials]: Dedicated environment keys never fall back to DB_* or .env.
    connection_environment = {
        setting_name: required_environment_value("TEST_" + setting_name)
        for setting_name in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME")
    }
    with patch.dict(os.environ, connection_environment):
        settings = load_database_settings()
    validate_test_destination(settings)
    return settings


def validate_connected_test_database(connection) -> None:
    """@description: Verify server-reported identity and transactional tables before inserting fixtures.
    @param connection: New connection that has not performed any application write.
    @return: None; rejects identity mismatches, absent tables or non-InnoDB tables.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT CURRENT_USER() AS account, DATABASE() AS database_name")
        identity = cursor.fetchone()
        if identity != {"account": EXPECTED_TEST_ACCOUNT, "database_name": TEST_DATABASE_NAME}:
            raise ConfigurationError("Server identity does not match the isolated test destination")
        cursor.execute(
            "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s",
            (TEST_DATABASE_NAME,),
        )
        table_engines = {row["TABLE_NAME"]: row["ENGINE"] for row in cursor.fetchall()}
    if any(table_engines.get(table_name) != "InnoDB" for table_name in EXPECTED_TABLES):
        raise ConfigurationError("Test schema must contain all six InnoDB tables before fixture writes")
    validate_test_constraints(connection)


def required_constraint_types() -> set[tuple[str, str, str]]:
    """@description: Reuse the reviewed read-only schema contract for isolated test preflight.
    @param: none
    @return: Table, name and type triples required before fixture writes.
    """
    required_types = {(table, "PRIMARY", "PRIMARY KEY") for table in EXPECTED_PRIMARY_KEYS}
    required_types.update((table, name, "UNIQUE")
        for table, constraints in EXPECTED_UNIQUE_KEYS.items() for name in constraints)
    required_types.update((table, name, "FOREIGN KEY")
        for table, constraints in EXPECTED_FOREIGN_KEYS.items() for name in constraints)
    return required_types


def validate_test_constraints(connection) -> None:
    """@description: Reject missing constraints and production foreign-key targets before writes.
    @param connection: Test connection that has performed metadata reads only.
    @return: None; raises on incomplete or unsafe test schema metadata.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT tc.TABLE_NAME, tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE, tc.ENFORCED, cc.CHECK_CLAUSE FROM information_schema.TABLE_CONSTRAINTS tc LEFT JOIN information_schema.CHECK_CONSTRAINTS cc ON tc.CONSTRAINT_SCHEMA = cc.CONSTRAINT_SCHEMA AND tc.CONSTRAINT_NAME = cc.CONSTRAINT_NAME WHERE tc.CONSTRAINT_SCHEMA = %s",
            (TEST_DATABASE_NAME,),
        )
        constraints = cursor.fetchall()
        validate_constraint_metadata(constraints)
        cursor.execute(
            "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE CONSTRAINT_SCHEMA = %s ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION",
            (TEST_DATABASE_NAME,),
        )
        key_columns = cursor.fetchall()
        if any(row["REFERENCED_TABLE_SCHEMA"] not in (None, TEST_DATABASE_NAME) for row in key_columns):
            raise ConfigurationError("Test foreign keys must not reference another database")
        validate_test_key_columns(key_columns)


def validate_test_key_columns(key_columns: list[dict]) -> None:
    """@description: Reject same-name keys that protect different columns than the reviewed schema.
    @param key_columns: MySQL key-column metadata sorted by key ordinal position.
    @return: None; rejects primary, unique or foreign keys with incorrect column contracts.
    """
    # [reviewed key contract]: Column order preserves uniqueness and ownership semantics.
    expected_keys = [(table, "PRIMARY", (column,)) for table, column in EXPECTED_PRIMARY_KEYS.items()]
    expected_keys.extend((table, name, columns)
        for table, keys in EXPECTED_UNIQUE_KEYS.items() for name, columns in keys.items())
    for table_name, constraint_name, expected_columns in expected_keys:
        rows = ordered_key_columns(key_columns, table_name, constraint_name)
        if tuple(row["COLUMN_NAME"] for row in rows) != expected_columns:
            raise ConfigurationError("Test primary or unique key columns differ from reviewed schema")
    validate_test_foreign_key_columns(key_columns)


def validate_test_foreign_key_columns(key_columns: list[dict]) -> None:
    """@description: Preserve canonical song and recipient ownership in real test foreign keys.
    @param key_columns: MySQL key-column metadata sorted by key ordinal position.
    @return: None; rejects wrong local columns or referenced schema, table or columns.
    """
    for table_name, foreign_keys in EXPECTED_FOREIGN_KEYS.items():
        for constraint_name, (target_table, local_columns, target_columns) in foreign_keys.items():
            rows = ordered_key_columns(key_columns, table_name, constraint_name)
            actual_local_columns = tuple(row["COLUMN_NAME"] for row in rows)
            actual_target_columns = tuple(row["REFERENCED_COLUMN_NAME"] for row in rows)
            actual_targets = {(row["REFERENCED_TABLE_SCHEMA"], row["REFERENCED_TABLE_NAME"]) for row in rows}
            if (actual_local_columns, actual_target_columns, actual_targets) != (
                local_columns, target_columns, {(TEST_DATABASE_NAME, target_table)},
            ):
                raise ConfigurationError("Test foreign key columns or targets differ from reviewed schema")


def validate_constraint_metadata(constraints: list[dict]) -> None:
    """@description: Require reviewed keys and enforced CHECK text before trusting test isolation.
    @param constraints: Public information_schema rows for the test database.
    @return: None; rejects missing keys or changed CHECK declarations.
    """
    actual_types = {(row["TABLE_NAME"], row["CONSTRAINT_NAME"], row["CONSTRAINT_TYPE"]) for row in constraints}
    if not required_constraint_types() <= actual_types:
        raise ConfigurationError("Disposable test schema is missing reviewed primary, unique or foreign keys")
    enforced_checks = {row["CONSTRAINT_NAME"]: row["CHECK_CLAUSE"] for row in constraints
        if row["CONSTRAINT_TYPE"] == "CHECK" and row["ENFORCED"] == "YES"}
    for constraint_name, expected_clause in EXPECTED_CHECKS.items():
        actual_clause = enforced_checks.get(constraint_name)
        if actual_clause is None or normalized_check_clause(actual_clause) != normalized_check_clause(expected_clause):
            raise ConfigurationError("Disposable test schema requires reviewed enforced CHECK constraints")
