"""Verify offline that the guarded cleanup of the compensation MySQL tests saves its evidence before any delete, stops when the evidence cannot be saved, never overwrites a plan, stays inside the fixture scope, and rolls back on any count mismatch.

Runs on an in-memory fake connection that records every statement. Nothing here connects to MySQL, and the file is not part of the isolated run.
"""

import re
from pathlib import Path

import pytest

from tests.test_compensation_mysql import (
    OPERATIONS, USER_HIGH, USER_LOW, VIDEO_IDS, guarded_cleanup,
)

IDENTITY = {"test": "offline", "server_uuid": "offline", "user_range": [USER_LOW, USER_HIGH]}  # [offline identity]: Fixed values for the fake only.
ALLOWED_PARAMETERS = {USER_LOW, USER_HIGH, *OPERATIONS, *VIDEO_IDS, "2b2-b1", 1}  # [fixture scope]: Every bound value a delete may carry.


class FakeCursor:
    """Answer the planner's reads from a dictionary of table rows and record every statement in order."""

    def __init__(self, connection) -> None:
        """@description: Bind the cursor to the fake connection that owns the tables and the statement log.
        @param connection: Fake connection holding the state.
        @return: None.
        """
        self._connection = connection
        self.rowcount = 0
        self._rows = []

    def __enter__(self):
        """@description: Support the context-manager form used by the production code.
        @return: This cursor.
        """
        return self

    def __exit__(self, *details):
        """@description: Nothing to release in the fake.
        @param details: Exception details, unused.
        @return: None.
        """
        return None

    def execute(self, sql: str, params=()) -> None:
        """@description: Record one statement and answer reads or apply deletes against the fake tables.
        @param sql: Statement text.
        @param params: Bound values.
        @return: None.
        """
        self._connection.statements.append((sql, list(params)))
        self._rows = []
        if sql.startswith("DELETE FROM "):
            table = re.match(r"DELETE FROM (\w+)", sql).group(1)
            self._connection.plan_seen_at_delete.append(self._connection.plan_path.exists() if self._connection.plan_path else None)
            self.rowcount = self._connection.override.pop(table, len(self._connection.tables.get(table, [])))
            self._connection.tables[table] = []
        elif sql.startswith("SELECT COUNT(*)"):
            table = re.search(r"FROM (\w+)", sql).group(1)
            self._rows = [{"total": len(self._connection.tables.get(table, []))}]
        elif sql.startswith("SELECT 0"):
            self._rows = [{"total": 0}]
        elif sql.startswith("SELECT"):
            table = re.search(r"FROM (\w+)", sql).group(1)
            self._rows = [dict(row) for row in self._connection.tables.get(table, [])]

    def fetchall(self) -> list:
        """@description: Return the rows of the last read.
        @return: List of row dictionaries.
        """
        return self._rows

    def fetchone(self):
        """@description: Return the first row of the last read.
        @return: Row dictionary.
        """
        return self._rows[0]


class FakeConnection:
    """Hold fake tables, record transaction boundaries and every statement, and allow one table's delete count to be overridden."""

    def __init__(self, tables: dict | None = None) -> None:
        """@description: Start with the given fake rows and empty logs.
        @param tables: Table name to list of row dictionaries.
        @return: None.
        """
        self.tables = tables or {}
        self.statements = []
        self.override = {}
        self.plan_path = None
        self.plan_seen_at_delete = []
        self.begun = self.committed = self.rolled_back = 0

    def begin(self) -> None:
        """@description: Count a transaction start.
        @return: None.
        """
        self.begun += 1

    def commit(self) -> None:
        """@description: Count a commit.
        @return: None.
        """
        self.committed += 1

    def rollback(self) -> None:
        """@description: Count a rollback.
        @return: None.
        """
        self.rolled_back += 1

    def cursor(self) -> FakeCursor:
        """@description: Return a cursor over the shared fake state.
        @return: Fake cursor.
        """
        return FakeCursor(self)

    def deletes(self) -> list:
        """@description: List the delete statements and their bound values in order.
        @return: Pairs of statement text and parameters.
        """
        return [(sql, params) for sql, params in self.statements if sql.startswith("DELETE FROM ")]


def synthetic_tables() -> dict:
    """@description: Fake fixture rows for the tables the cleanup touches, one synthetic user and one video each.
    @return: Table name to rows.
    """
    user = USER_LOW
    return {
        "exchange_incidents": [{"incident_id": 1, "discord_user_id": user}],
        "exchange_redelivery_grants": [{"grant_id": 1, "discord_user_id": user}],
        "recommendations": [{"recommendation_id": 1, "discord_user_id": user}],
        "credit_ledger": [{"ledger_id": 1, "discord_user_id": user}],
        "songs": [{"song_id": 1, "platform_track_id": VIDEO_IDS[0]}],
        "jb2_control": [{"singleton_id": 1, "schema_version": "2b2-b1"}],
    }


def test_plan_is_saved_before_the_first_delete_and_the_result_after_the_commit(tmp_path: Path) -> None:
    """@description: The plan file exists when the first delete runs, the commit happens once, and the result file is written only afterwards.
    @param tmp_path: Pytest temporary directory used as the evidence folder.
    @return: None.
    """
    connection = FakeConnection(synthetic_tables())
    connection.plan_path = tmp_path / "cleanup-plan-offline.json"
    guarded_cleanup(connection, tmp_path, "offline", IDENTITY)
    assert connection.plan_seen_at_delete and all(connection.plan_seen_at_delete)
    assert (connection.begun, connection.committed, connection.rolled_back) == (1, 1, 0)
    assert (tmp_path / "cleanup-result-offline.json").exists()


def test_an_unwritable_evidence_folder_blocks_every_delete(tmp_path: Path) -> None:
    """@description: When the evidence folder does not exist, the plan cannot be saved, so no delete runs and the transaction rolls back.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    connection = FakeConnection(synthetic_tables())
    with pytest.raises(FileNotFoundError):
        guarded_cleanup(connection, tmp_path / "missing", "offline", IDENTITY)
    assert connection.deletes() == []
    assert (connection.committed, connection.rolled_back) == (0, 1)


def test_an_existing_plan_file_is_never_overwritten(tmp_path: Path) -> None:
    """@description: A plan file from an earlier attempt blocks this attempt before any delete; the earlier evidence is left unchanged.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    (tmp_path / "cleanup-plan-offline.json").write_text("earlier evidence", encoding="utf-8")
    connection = FakeConnection(synthetic_tables())
    with pytest.raises(FileExistsError):
        guarded_cleanup(connection, tmp_path, "offline", IDENTITY)
    assert connection.deletes() == []
    assert (tmp_path / "cleanup-plan-offline.json").read_text(encoding="utf-8") == "earlier evidence"


def test_every_delete_stays_inside_the_fixture_scope_and_never_truncates(tmp_path: Path) -> None:
    """@description: Each delete is row-scoped by the fixture's own user range, operation keys, video identifiers or singleton, and no statement truncates or drops.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    connection = FakeConnection(synthetic_tables())
    guarded_cleanup(connection, tmp_path, "offline", IDENTITY)
    deletes = connection.deletes()
    assert deletes, "the cleanup must have something to check"
    for sql, params in deletes:
        assert "WHERE" in sql, sql
        assert set(params) <= ALLOWED_PARAMETERS, (sql, params)
    assert not any(re.search(r"\b(TRUNCATE|DROP)\b", sql) for sql, _ in connection.statements)


def test_a_count_mismatch_rolls_back_and_writes_no_result(tmp_path: Path) -> None:
    """@description: If the delete count differs from the planned count, the transaction rolls back, nothing commits, and no result is recorded.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    connection = FakeConnection(synthetic_tables())
    connection.override["recommendations"] = 0
    with pytest.raises(AssertionError, match="recommendations"):
        guarded_cleanup(connection, tmp_path, "offline", IDENTITY)
    assert (connection.committed, connection.rolled_back) == (0, 1)
    assert not (tmp_path / "cleanup-result-offline.json").exists()
