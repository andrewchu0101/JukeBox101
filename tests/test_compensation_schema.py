"""Verify the compensation schema guard refuses every incomplete or altered schema by name, and passes the reviewed one.

The guard runs against the offline SQLite mirror, whose SQL text is written separately from the specification, so a missing column, unique
key, CHECK or table is a real catalogue fact. Only a missing table becomes CompensationSchemaMissing; any other error propagates unchanged.
"""

import sqlite3

import pytest

from bot.database.compensation_schema import CompensationSchemaMismatch, CompensationSchemaMissing, require_schema
from tests.compensation_sqlite import SCHEMA, create_database, factory_for


def guard(path) -> None:
    """@description: Run the schema guard on a fresh connection, as a gated transaction would.
    @param path: Database file.
    @return: None.
    """
    connection = factory_for(path)()
    try:
        require_schema(connection.cursor())
    finally:
        connection.close()


def test_the_reviewed_schema_passes_the_guard(tmp_path) -> None:
    """@description: The complete reviewed schema passes without any refusal.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "reviewed.sqlite"
    create_database(path)
    guard(path)


def test_a_missing_table_is_refused_by_name(tmp_path) -> None:
    """@description: A schema without the grant table refuses with the table named and nothing else.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "no_table.sqlite"
    create_database(path)
    connection = sqlite3.connect(str(path))
    connection.execute("DROP TABLE exchange_redelivery_grants")
    connection.commit()
    connection.close()
    with pytest.raises(CompensationSchemaMissing, match="exchange_redelivery_grants"):
        guard(path)


def test_a_missing_column_is_refused_by_name(tmp_path) -> None:
    """@description: A grant table without its disposition-by column refuses with that column named.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "no_column.sqlite"
    create_database(path, SCHEMA.replace("disposition_by INTEGER, disposition_at TEXT", "disposition_at TEXT"))
    with pytest.raises(CompensationSchemaMismatch, match="disposition_by"):
        guard(path)


def test_a_missing_unique_key_is_refused_by_name(tmp_path) -> None:
    """@description: A grant table without the unique incident key refuses, because a second grant per incident would then be possible.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "no_unique.sqlite"
    create_database(path, SCHEMA.replace("incident_id INTEGER NOT NULL UNIQUE", "incident_id INTEGER NOT NULL"))
    with pytest.raises(CompensationSchemaMismatch, match="unique key on: incident_id"):
        guard(path)


def test_a_missing_named_check_is_refused_by_name(tmp_path) -> None:
    """@description: A grant table without its sent-requires-sent-fact CHECK refuses with that constraint named.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "no_check.sqlite"
    create_database(path, SCHEMA.replace("    CONSTRAINT chk_grants_sent CHECK (state <> 'sent' OR delivery_fact = 'sent'),\n", ""))
    with pytest.raises(CompensationSchemaMismatch, match="chk_grants_sent"):
        guard(path)


def test_an_unrelated_database_error_propagates_unchanged() -> None:
    """@description: A lock or connection failure during the probe is not disguised as a missing table; it propagates as it was raised.
    @return: None.
    """
    class BrokenCursor:
        """Fail the first probe the way a lost connection would."""

        def execute(self, sql: str, args=()) -> None:
            """@description: Raise a non-schema error for any statement.
            @param sql: Probe statement, unused.
            @param args: Bound values, unused.
            @return: None.
            """
            raise RuntimeError("connection lost")

    with pytest.raises(RuntimeError, match="connection lost"):
        require_schema(BrokenCursor())
