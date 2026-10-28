"""Verify harness guardrails with synthetic inventories and offline cursor spies only.

These tests do not claim MySQL behavior; actual transaction scenarios live in the separately opt-in integration module.
"""

import ast
from pathlib import Path
from unittest.mock import MagicMock
import pytest
from bot.config import ConfigurationError, DatabaseSettings
from bot.database.submission_migration import migration_plan
from tests.batch_b_harness_contract import (RunScope, TABLES, PRIMARY_KEYS, CONTROL_COLUMNS,
    HARNESS_USER, RUN_SWITCH, HARNESS_SWITCH, HarnessSafetyError, load_profiles, owned_rows,
    consistent_accounting, harness_requirements)
from tests.batch_b_harness_database import (require_baseline, delete_primary, verified_connection,
    verify_harness_grants, set_control, read_table)
from tests import batch_b_harness as runtime
from tests.batch_b_mysql_cases import request
from tests.test_phase2_quality import _implementation_lines, _control_depth, _assert_function_documentation


@pytest.fixture
def scope() -> RunScope:
    """@description: Register synthetic roots without reading credentials or creating a database session.
    @param: none
    @return: Reproducible declared test run.
    """
    return RunScope("a" * 32)


def empty_inventory() -> dict:
    """@description: Represent the accepted migrated baseline without an actual database engine.
    @param: none
    @return: Synthetic 23-table inventory with only control and registry populated.
    """
    rows = {name: () for name in TABLES}  # [synthetic baseline]: Counts alone never claim real MySQL verification.
    rows["jb2_migrations"] = ({"version": "2b2-b1", "checksum": migration_plan()[0], "state": "applied"},)
    rows["jb2_control"] = (dict(singleton_id=1, schema_version="2b2-b1", accepted_sequence=0, revision=0,
                               snapshot_ready=0, legacy_writers_quiesced=0, settlement_enabled=0),)
    return rows


def owned_inventory(scope: RunScope) -> dict:
    """@description: Build explicit FK-linked sample rows for ownership checks, not SQL emulation.
    @param scope: Registered run roots.
    @return: Synthetic committed intake without an award.
    """
    rows = empty_inventory()  # [linked roots]: Generated song IDs must map through registered video IDs.
    operation, subject = next(iter(scope.subjects.items()))
    rows["users"] = ({"discord_user_id": subject["user"], "credit_balance": 0},)
    rows["songs"] = ({"song_id": 42, "platform": "youtube", "platform_track_id": subject["video"]},)
    rows["jb2_versions"] = ({"version_id": subject["version"], "canonical_id": subject["version"]},)
    rows["jb2_videos"] = ({"video_id": subject["video"], "song_id": 42},)
    rows["jb2_events"] = ({"operation_id": operation, "discord_user_id": subject["user"], "video_id": subject["video"], "accepted_sequence": 1},)
    rows["jb2_control"][0].update(accepted_sequence=1, revision=1, snapshot_ready=1, legacy_writers_quiesced=1, settlement_enabled=1)
    rows["jb2_receipts"] = ({"operation_id": operation},)
    rows["jb2_review_history"] = ({"video_id": subject["video"], "revision": 0, "actor_id": subject["user"]},)
    rows["jb2_evidence"] = ({"evidence_id": 1, "video_id": subject["video"], "actor_id": subject["user"]},)
    rows["jb2_reminders"] = ({"video_id": subject["video"]},)
    return rows


@pytest.fixture
def profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Supply separate synthetic profile namespaces with no live credential access.
    @param monkeypatch: Scoped synthetic environment injection.
    @return: None.
    """
    import os
    for key in tuple(os.environ):
        if key.startswith("JUKEBOX_RUN_"):
            monkeypatch.setenv(key, "0")
    for prefix, user in (("TEST_DB_", "jukebox101_test_app"), ("TEST_HARNESS_DB_", HARNESS_USER)):
        for name, value in dict(HOST="127.0.0.1", PORT="3306", USER=user, PASSWORD="synthetic-only", NAME="jukebox101_test").items():
            monkeypatch.setenv(prefix + name, value)
    monkeypatch.setenv("TEST_DB_SERVER_UUID", "11111111-2222-4333-8444-555555555555")
    for switch in (RUN_SWITCH, HARNESS_SWITCH):
        monkeypatch.setenv(switch, "1")
    monkeypatch.setenv("JUKEBOX_BATCH_B_EXCLUSIVE", "jukebox101_test")


@pytest.mark.parametrize("key", [RUN_SWITCH, HARNESS_SWITCH, "JUKEBOX_BATCH_B_EXCLUSIVE"])
def test_explicit_authorization_required(profiles: None, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    """@description: Reject missing write or isolation consent before any session can be opened.
    @param profiles: Synthetic settings.
    @param monkeypatch: Scoped switch removal.
    @param key: Required authorization field.
    @return: None.
    """
    monkeypatch.delenv(key)
    with pytest.raises(ConfigurationError):
        load_profiles()


@pytest.mark.parametrize("name,value", [("HOST", "localhost"), ("PORT", "3307"), ("USER", "root"), ("NAME", "jukebox101")])
def test_harness_target_rejected(profiles: None, monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    """@description: Confine cleanup authority to the one fixed loopback test identity.
    @param profiles: Synthetic profiles only.
    @param monkeypatch: Scoped environment edit.
    @param name: Corrupted destination field.
    @param value: Forbidden value.
    @return: None.
    """
    monkeypatch.setenv("TEST_HARNESS_DB_" + name, value)
    with pytest.raises(ConfigurationError):
        load_profiles()


def test_harness_password_never_falls_back(profiles: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Reject absent harness credentials despite an available app password.
    @param profiles: Synthetic profile configuration.
    @param monkeypatch: Scoped required-key removal.
    @return: None.
    """
    monkeypatch.delenv("TEST_HARNESS_DB_PASSWORD")
    with pytest.raises(ConfigurationError):
        load_profiles()


def test_migration_switch_blocks_harness(profiles: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep migrations and other real-test switches off throughout this isolated suite.
    @param profiles: Synthetic opt-in profiles.
    @param monkeypatch: Scoped conflicting gate.
    @return: None.
    """
    monkeypatch.setenv("JUKEBOX_RUN_BATCH_B_MIGRATION", "1")
    with pytest.raises(ConfigurationError):
        load_profiles()


@pytest.mark.parametrize("table", sorted(TABLES - {"jb2_control", "jb2_migrations"}))
def test_nonempty_baseline_always_refused(table: str) -> None:
    """@description: Never treat preexisting business rows as disposable run data.
    @param table: One of all 21 protected baseline-empty business tables.
    @return: None.
    """
    rows = empty_inventory()  # [foreign baseline]: Even a single unknown row blocks enable.
    rows[table] = ({"unknown": True},)
    with pytest.raises(HarnessSafetyError, match="NONEMPTY_BASELINE"):
        require_baseline(rows)


@pytest.mark.parametrize("field,value", [("state", "applying"), ("checksum", "wrong"), ("version", "other")])
def test_registry_drift_blocks_enable(field: str, value: str) -> None:
    """@description: Preserve the accepted registry instead of replaying or repairing Migration.
    @param field: Contradicted registry fact.
    @param value: Unapproved persisted value.
    @return: None.
    """
    rows = empty_inventory()
    rows["jb2_migrations"][0][field] = value
    with pytest.raises(HarnessSafetyError, match="MIGRATION_MARKER"):
        require_baseline(rows)


@pytest.mark.parametrize("name", CONTROL_COLUMNS[2:])
def test_preexisting_enabled_switch_blocks(name: str) -> None:
    """@description: Require all three preexisting business switches OFF before claiming run ownership.
    @param name: Preexisting enabled control field.
    @return: None.
    """
    rows = empty_inventory()
    rows["jb2_control"][0][name] = 1
    with pytest.raises(HarnessSafetyError, match="CONTROL_NOT_OFF"):
        require_baseline(rows)


def test_full_registered_inventory_can_be_owned(scope: RunScope) -> None:
    """@description: Accept exact roots and actual parent relationships rather than prefix matching alone.
    @param scope: Registered synthetic roots.
    @return: None.
    """
    rows = owned_inventory(scope)
    owned_rows(scope, rows)
    consistent_accounting(rows)


@pytest.mark.parametrize("table,field,value", [("songs", "platform_track_id", "foreignvideo"),
    ("jb2_events", "discord_user_id", 999), ("jb2_videos", "song_id", 999),
    ("jb2_versions", "canonical_id", "foreign-version"), ("jb2_receipts", "operation_id", "foreign-op")])
def test_registered_prefix_does_not_prove_ownership(scope: RunScope, table: str, field: str, value: object) -> None:
    """@description: Reject mismatched parent links before allowing any DELETE.
    @param scope: Registered run roots.
    @param table: Corrupted sample relation.
    @param field: Identity field that no longer matches.
    @param value: Foreign identity value.
    @return: None.
    """
    rows = owned_inventory(scope)
    rows[table][0][field] = value
    with pytest.raises(HarnessSafetyError, match="UNOWNED_ROW"):
        owned_rows(scope, rows)


@pytest.mark.parametrize("table", ["submissions", "recommendations", "daily_dispatches"])
def test_unexpected_old_workflow_rows_block_cleanup(scope: RunScope, table: str) -> None:
    """@description: Do not delete data from an unrelated writer even in the dedicated test schema.
    @param scope: Registered run.
    @param table: Old table this Batch B workflow never writes.
    @return: None.
    """
    rows = owned_inventory(scope)
    rows[table] = ({"discord_user_id": scope.owner_id},)
    with pytest.raises(HarnessSafetyError):
        owned_rows(scope, rows)


@pytest.mark.parametrize("table", sorted(PRIMARY_KEYS))
def test_cleanup_uses_complete_bound_primary_key(table: str) -> None:
    """@description: Reject all-table cleanup by requiring every primary-key component and one affected row.
    @param table: Reviewed cleanup table.
    @return: None.
    """
    cursor = MagicMock()  # [offline SQL spy]: This unit test never connects to MySQL.
    cursor.rowcount = 1
    cursor.fetchall.return_value = ()  # Explicitly model no undeleted version children.
    row = {key: "synthetic-key" for key in PRIMARY_KEYS[table]}
    delete_primary(cursor, table, row)
    statement, parameters = cursor.execute.call_args.args
    assert " WHERE " in statement and statement.count("=%s") == len(PRIMARY_KEYS[table])
    assert parameters == tuple(row[key] for key in PRIMARY_KEYS[table])
    assert "TRUNCATE" not in statement and "jb2_migrations" not in statement


def test_bad_cleanup_rowcount_stops() -> None:
    """@description: Roll back teardown if the exact owned row was not deleted once.
    @param: none
    @return: None.
    """
    cursor = MagicMock()
    cursor.rowcount = 0
    with pytest.raises(HarnessSafetyError, match="ROWCOUNT"):
        delete_primary(cursor, "users", {"discord_user_id": 1})


def test_accounting_inconsistency_is_preserved(scope: RunScope) -> None:
    """@description: Keep broken atomicity evidence instead of deleting a partially committed result.
    @param scope: Registered synthetic run.
    @return: None.
    """
    rows = owned_inventory(scope)
    rows["users"][0]["credit_balance"] = 1
    with pytest.raises(HarnessSafetyError, match="USER_LEDGER"):
        consistent_accounting(rows)
    rows["jb2_receipts"] = ()
    with pytest.raises(HarnessSafetyError, match="EVENT_RECEIPT"):
        consistent_accounting(rows)


def test_unknown_cleanup_table_never_executes() -> None:
    """@description: Prevent composed arbitrary identifiers from reaching cursor execution.
    @param: none
    @return: None.
    """
    cursor = MagicMock()
    with pytest.raises(HarnessSafetyError):
        read_table(cursor, "users`; DROP DATABASE jukebox101; --")
    cursor.execute.assert_not_called()


def test_wrong_connection_target_rejected_before_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Enforce fixed destinations even when callers bypass environment-loading helpers.
    @param monkeypatch: Driver spy injection.
    @return: None.
    """
    from tests import batch_b_harness_database
    driver = MagicMock()
    monkeypatch.setattr(batch_b_harness_database, "open_connection", driver)
    with pytest.raises(HarnessSafetyError):
        verified_connection(DatabaseSettings("localhost", 3306, HARNESS_USER, "synthetic-only", "jukebox101_test"), HARNESS_USER)
    driver.assert_not_called()


def test_control_schema_identity_cannot_be_updated() -> None:
    """@description: Reject accidental schema-version writes at the harness update boundary.
    @param: none
    @return: None.
    """
    cursor = MagicMock()
    with pytest.raises(HarnessSafetyError):
        set_control(cursor, {"schema_version": "changed"})
    cursor.execute.assert_not_called()


def test_dba_sql_matches_exact_harness_capabilities() -> None:
    """@description: Keep one-time DBA grants synchronized with the reviewed harness permission map.
    @param: none
    @return: None.
    """
    import re
    source = Path("docs/batch-b-test-harness-grants.sql").read_text(encoding="utf-8")
    matches = re.findall(r"^GRANT (DELETE, SELECT|SELECT) ON `jukebox101_test`\.`([^`]+)` TO 'jukebox101_test_harness'@'127\.0\.0\.1';$", source, re.MULTILINE)
    assert {table: set(rights.split(", ")) for rights, table in matches} == harness_requirements()
    assert harness_requirements()["jb2_migrations"] == {"SELECT"}
    grants = [line for line in source.splitlines() if line.startswith("GRANT ")]
    assert len(grants) == 25
    assert not any(".*" in line or "GRANT OPTION" in line or "INSERT" in line or "ALTER" in line for line in grants)


def test_client_faults_delegate_real_calls() -> None:
    """@description: Distinguish client fault injection from substituting fake database state in integration tests.
    @param: none
    @return: None.
    """
    from tests.batch_b_mysql_faults import FaultConnection, InjectedClientFault
    connection = MagicMock()  # [unit-only delegation spy]: Integration execution supplies actual PyMySQL here.
    with pytest.raises(InjectedClientFault):
        FaultConnection(connection, {"commit": "after"}).commit()
    connection.commit.assert_called_once()
    cursor = connection.cursor.return_value
    with pytest.raises(InjectedClientFault):
        FaultConnection(connection, {"sql": "INSERT"}).cursor().execute("INSERT INTO users VALUES (%s)", (1,))
    cursor.execute.assert_called_once()


def test_new_harness_functions_follow_project_limits() -> None:
    """@description: Apply project documentation and complexity limits to every new test infrastructure module.
    @param: none
    @return: None.
    """
    paths = [*Path("tests").glob("batch_b_harness*.py"), *Path("tests").glob("batch_b_mysql*.py"),
             Path("tests/test_batch_b_mysql.py"), Path("tests/test_batch_b_harness_safety.py")]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert ast.get_docstring(tree)
        for function in ast.walk(tree):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert _implementation_lines(function, source.splitlines()) <= 25, (path.name, function.name)
                assert _control_depth(function) <= 3, (path.name, function.name)
                _assert_function_documentation(function, path)


def mock_harness(scope: RunScope, monkeypatch: pytest.MonkeyPatch) -> tuple:
    """@description: Exercise lifecycle control with connection spies without opening sockets or journal files.
    @param scope: Synthetic registered run.
    @param monkeypatch: Scoped metadata and journal substitutes for this unit test only.
    @return: Runtime harness, cursor spy and in-memory nonsecret status events.
    """
    application = DatabaseSettings("127.0.0.1", 3306, "jukebox101_test_app", "synthetic-only", "jukebox101_test")
    management = DatabaseSettings("127.0.0.1", 3306, HARNESS_USER, "synthetic-only", "jukebox101_test")
    harness = runtime.MySQLHarness(application, management, scope=scope, journal=Path("not-written.json"))
    harness.connection = MagicMock()
    harness.baseline = empty_inventory()
    harness.enabled = True
    cursor = harness.connection.cursor.return_value.__enter__.return_value
    cursor.rowcount = 1
    cursor.fetchall.return_value = ()  # Explicitly model no undeleted version children.
    statuses = []  # [unit-only lifecycle trace]: No file is created.
    monkeypatch.setattr(runtime.MySQLHarness, "persist", lambda self, status, rows=None: statuses.append(status))
    monkeypatch.setattr(runtime, "schema_fingerprints", lambda cursor: {})
    monkeypatch.setattr(runtime, "read_inventory", lambda cursor: empty_inventory())
    monkeypatch.setattr(runtime, "validate_payloads", lambda rows: None)
    return harness, cursor, statuses


def test_cleanup_and_control_restore_commit_together(scope: RunScope, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep exact-key teardown and original control restoration in one transaction boundary.
    @param scope: Synthetic run roots.
    @param monkeypatch: Offline connection and inventory spies.
    @return: None.
    """
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    monkeypatch.setattr(runtime, "lock_inventory", MagicMock(side_effect=[owned_inventory(scope), empty_inventory()]))
    connection = harness.connection
    harness.finish()
    assert "RESTORED_VERIFIED" in statuses and not harness.enabled
    connection.commit.assert_called_once()
    connection.close.assert_called_once()
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert any(statement.startswith("DELETE FROM") for statement in statements)
    assert statements[-1].startswith("UPDATE jb2_control SET accepted_sequence=")


def test_unknown_row_prevents_every_delete(scope: RunScope, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Freeze and preserve diagnostic rows instead of treating all new data as run-owned.
    @param scope: Synthetic run roots.
    @param monkeypatch: Offline metadata substitutes.
    @return: None.
    """
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    rows = owned_inventory(scope)
    rows["users"] += ({"discord_user_id": 999, "credit_balance": 0},)
    monkeypatch.setattr(runtime, "lock_inventory", lambda connection: rows)
    shutdown = MagicMock()
    monkeypatch.setattr(runtime.MySQLHarness, "disable_only", shutdown)
    with pytest.raises(HarnessSafetyError, match="UNOWNED_ROW"):
        harness.finish()
    assert not any(call.args[0].startswith("DELETE") for call in cursor.execute.call_args_list)
    shutdown.assert_called_once()
    assert "STOP_MANUAL_RECOVERY_REQUIRED" in statuses


def test_inconsistent_accounting_prevents_every_delete(scope: RunScope, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Preserve a partially committed accounting defect for real-test diagnosis.
    @param scope: Synthetic roots.
    @param monkeypatch: Offline transaction spies.
    @return: None.
    """
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    rows = owned_inventory(scope)
    rows["users"][0]["credit_balance"] = 1
    monkeypatch.setattr(runtime, "lock_inventory", lambda connection: rows)
    monkeypatch.setattr(runtime.MySQLHarness, "disable_only", MagicMock())
    with pytest.raises(HarnessSafetyError, match="USER_LEDGER"):
        harness.finish()
    assert not any(call.args[0].startswith("DELETE") for call in cursor.execute.call_args_list)


def test_cleanup_commit_failure_uses_fresh_shutdown_session(scope: RunScope, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Try switch shutdown through a new verified session after uncertain cleanup commit.
    @param scope: Synthetic run roots.
    @param monkeypatch: Driver and metadata spies, not a fake SQL engine.
    @return: None.
    """
    import pymysql
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    original = harness.connection
    original.commit.side_effect = pymysql.OperationalError(2013, "synthetic-private-driver-text")
    monkeypatch.setattr(runtime, "lock_inventory", lambda connection: owned_inventory(scope))
    recovery = MagicMock()
    recovery_cursor = recovery.cursor.return_value.__enter__.return_value
    recovery_cursor.fetchone.side_effect = [{"owned": 1}, {"schema_version": "2b2-b1"}]
    monkeypatch.setattr(runtime, "verified_connection", lambda *arguments: recovery)
    with pytest.raises(pymysql.OperationalError):
        harness.finish()
    original.rollback.assert_called_once()
    original.close.assert_called_once()
    recovery.commit.assert_called_once()
    assert "STOP_MANUAL_RECOVERY_REQUIRED" in statuses
    assert not any(call.args[0].startswith("DELETE") for call in recovery_cursor.execute.call_args_list)


def test_workers_still_alive_block_cleanup(scope: RunScope, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Never race a surviving worker by restoring or deleting its active transactional scope.
    @param scope: Synthetic run roots.
    @param monkeypatch: Offline harness setup.
    @return: None.
    """
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    harness.workers_stopped = False
    with pytest.raises(HarnessSafetyError, match="WORKERS_ALIVE"):
        harness.finish()
    cursor.execute.assert_not_called()
    assert "STOP_MANUAL_RECOVERY_REQUIRED" in statuses


def test_process_termination_deadlines_are_real_calls() -> None:
    """@description: Require terminate/kill joins with bounded five-second waits before cleanup can proceed.
    @param: none
    @return: None.
    """
    process = MagicMock()
    process.pid = 10
    process.is_alive.side_effect = [True, True, False]
    runtime.stop_process(process)
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert [call.args for call in process.join.call_args_list] == [(5,), (5,)]


def test_process_that_cannot_stop_is_not_declared_clean() -> None:
    """@description: Preserve uncertainty when the operating system cannot terminate a worker.
    @param: none
    @return: None.
    """
    process = MagicMock()
    process.pid = 10
    process.is_alive.return_value = True
    with pytest.raises(HarnessSafetyError, match="TERMINATION_FAILED"):
        runtime.stop_process(process)


def test_scenario_timeout_joins_then_stops_worker(scope: RunScope, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Exercise the actual 30-second scenario deadline branch without spawning a child.
    @param scope: Registered roots.
    @param monkeypatch: Offline multiprocessing context spy.
    @return: None.
    """
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    context = MagicMock()
    context.Pipe.return_value = (MagicMock(), MagicMock())
    process = context.Process.return_value
    process.pid = 10
    process.is_alive.side_effect = [True, True, False, False, False]
    monkeypatch.setattr(runtime.multiprocessing, "get_context", lambda method: context)
    with pytest.raises(HarnessSafetyError, match="TIMEOUT_30"):
        harness.execute("priority")
    assert process.join.call_args_list[0].args == (30,)
    assert harness.failed and harness.workers_stopped
    process.terminate.assert_called_once()
    process.close.assert_called_once()


def test_failed_run_cannot_start_another_worker(scope: RunScope, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Stop later business writes as soon as a scenario reports failure.
    @param scope: Registered roots.
    @param monkeypatch: Offline runtime setup.
    @return: None.
    """
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    harness.failed = True
    context = MagicMock()
    monkeypatch.setattr(runtime.multiprocessing, "get_context", context)
    with pytest.raises(HarnessSafetyError, match="RUN_NOT_WRITABLE"):
        harness.execute("priority")
    context.assert_not_called()


def grant_inventory() -> list:
    """@description: Describe table and column minimum grants using synthetic information-schema rows.
    @param: none
    @return: Four ordered privilege query results for the offline ACL checker.
    """
    tables = [{"TABLE_SCHEMA": "jukebox101_test", "TABLE_NAME": name, "PRIVILEGE_TYPE": right, "IS_GRANTABLE": "NO"}
              for name, rights in harness_requirements().items() for right in rights]
    columns = [{"TABLE_SCHEMA": "jukebox101_test", "TABLE_NAME": "jb2_control", "COLUMN_NAME": name,
                "PRIVILEGE_TYPE": "UPDATE", "IS_GRANTABLE": "NO"} for name in CONTROL_COLUMNS]
    columns.append(dict(columns[0], TABLE_NAME="jb2_reminders", COLUMN_NAME="next_due_at"))
    return [[], [], tables, columns]


@pytest.mark.parametrize("mutation", ["valid", "schema", "global", "registry", "column", "grant_option", "missing"])
def test_harness_grant_checks_reject_broad_or_missing_authority(mutation: str) -> None:
    """@description: Accept exactly the DBA draft without requiring broad schema or migration privileges.
    @param mutation: A minimum grant inventory or one unsafe variation.
    @return: None.
    """
    grants = grant_inventory()
    if mutation == "schema":
        grants[1] = [{"PRIVILEGE_TYPE": "SELECT", "IS_GRANTABLE": "NO"}]
    if mutation == "global":
        grants[0] = [{"PRIVILEGE_TYPE": "SUPER", "IS_GRANTABLE": "NO"}]
    if mutation == "registry":
        grants[2].append(dict(grants[2][0], TABLE_NAME="jb2_migrations", PRIVILEGE_TYPE="DELETE"))
    if mutation == "column":
        grants[3][0]["COLUMN_NAME"] = "schema_version"
    if mutation == "grant_option":
        grants[2][0]["IS_GRANTABLE"] = "YES"
    if mutation == "missing":
        grants[2].pop()
    cursor = MagicMock()
    cursor.fetchall.side_effect = grants + [[{"grant": "GRANT USAGE ON *.* TO `jukebox101_test_harness`@`127.0.0.1`"}]]
    if mutation == "valid":
        verify_harness_grants(cursor)
    else:
        with pytest.raises(HarnessSafetyError):
            verify_harness_grants(cursor)


@pytest.mark.parametrize("table", ["jb2_evidence", "jb2_review_history", "jb2_reminders", "jb2_videos"])
def test_orphaned_intake_stops_cleanup(scope: RunScope, table: str) -> None:
    """@description: Keep partial committed intake records available for diagnosis instead of deleting evidence.
    @param scope: Registered synthetic run.
    @param table: Missing atomic intake relation.
    @return: None.
    """
    rows = owned_inventory(scope)
    rows[table] = ()
    with pytest.raises(HarnessSafetyError, match="INCONSISTENCY"):
        consistent_accounting(rows)


def test_spawn_hides_harness_credentials_and_restores_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Never expose management credentials to the application worker's inherited environment.
    @param monkeypatch: Synthetic parent environment injection.
    @return: None.
    """
    import os
    monkeypatch.setenv("TEST_HARNESS_DB_PASSWORD", "synthetic-harness-only")
    monkeypatch.setenv("TEST_MIGRATION_DB_PASSWORD", "synthetic-migration-only")
    monkeypatch.setenv("TEST_DBA_DB_PASSWORD", "synthetic-dba-only")
    process = MagicMock()
    def check_child_environment() -> None:
        """@description: Observe the spawn boundary without opening an actual process.
        @param: none
        @return: None.
        """
        assert "TEST_HARNESS_DB_PASSWORD" not in os.environ
        assert "TEST_MIGRATION_DB_PASSWORD" not in os.environ
        assert "TEST_DBA_DB_PASSWORD" not in os.environ
        raise RuntimeError("synthetic-start-failure")
    process.start.side_effect = check_child_environment
    with pytest.raises(RuntimeError, match="synthetic-start"):
        runtime.start_application_process(process)
    assert os.environ["TEST_HARNESS_DB_PASSWORD"] == "synthetic-harness-only"
    assert os.environ["TEST_MIGRATION_DB_PASSWORD"] == "synthetic-migration-only"
    assert os.environ["TEST_DBA_DB_PASSWORD"] == "synthetic-dba-only"


def test_harness_row_lock_needs_only_reviewed_revision_column() -> None:
    """@description: Avoid requiring whole-table UPDATE or LOCK TABLES just to acquire the shared control gate.
    @param: none
    @return: None.
    """
    from tests.batch_b_harness_database import lock_control
    cursor = MagicMock()
    cursor.fetchone.return_value = {"schema_version": "2b2-b1"}
    lock_control(cursor)
    statement, values = cursor.execute.call_args_list[0].args
    assert statement == "UPDATE jb2_control SET revision=revision WHERE singleton_id=1 AND schema_version=%s"
    assert values == ("2b2-b1",)


@pytest.mark.parametrize("field", ["counter", "sequence", "revision"])
def test_acceptance_drift_is_not_erased_by_cleanup(scope: RunScope, field: str) -> None:
    """@description: Preserve a broken watermark or partial acceptance commit for diagnosis.
    @param scope: Registered run roots.
    @param field: One contradictory counter or event ordering fact.
    @return: None.
    """
    from tests.batch_b_harness_contract import consistent_acceptance
    rows = owned_inventory(scope)
    original = empty_inventory()["jb2_control"][0]
    consistent_acceptance(rows, original)
    if field == "counter":
        rows["jb2_control"][0]["accepted_sequence"] = 2
    elif field == "sequence":
        rows["jb2_events"][0]["accepted_sequence"] = 2
    else:
        rows["jb2_control"][0]["revision"] = 0
    with pytest.raises(HarnessSafetyError, match="INCONSISTENCY"):
        consistent_acceptance(rows, original)


@pytest.mark.parametrize("table,column", [("jb2_videos", "identity_json"), ("jb2_receipts", "receipt_json"),
    ("jb2_settlements", "result_json"), ("jb2_review_history", "review_json"), ("jb2_evidence", "evidence_json")])
def test_corrupted_persisted_payload_is_not_cleanup_authority(table: str, column: str) -> None:
    """@description: Keep invalid persisted JSON as diagnostic evidence rather than relying only on row counts.
    @param table: Corrupted persistence projection.
    @param column: JSON contract that cannot be decoded.
    @return: None.
    """
    from tests.batch_b_mysql_cases import validate_payloads
    rows = empty_inventory()
    rows[table] = ({column: "not-json"},)
    with pytest.raises(ValueError):
        validate_payloads(rows)


def test_valid_workflow_payloads_remain_compatible(scope: RunScope) -> None:
    """@description: Accept actual production-codec workflow output instead of validating only corrupt negative cases.
    @param scope: Registered synthetic test roots.
    @return: None.
    """
    from dataclasses import replace
    from bot.database.submission_codec import encode_contract
    from bot.submission_workflow import submit_offline
    from tests.test_submission_workflow import snapshot_for
    from tests.batch_b_mysql_cases import validate_payloads
    from tests.version_candidate_support import make_candidate
    pending = request(scope, 1)  # [pure production result]: No database, external evidence or FakeDatabase involved.
    candidate = make_candidate(1, pending.identity.version.value, operation_id=pending.operation_id,
                               submitter_id=pending.submitter_id, video_id=pending.identity.video.video_id)
    result = submit_offline(replace(pending, accepted_sequence=1), snapshot_for(candidate))
    rows = owned_inventory(scope)
    rows["jb2_videos"][0].update(identity_json=encode_contract(result.candidate.identity), review_json=encode_contract(result.candidate.review),
                                 review_revision=result.candidate.review.version, review_status=result.candidate.review.status.value)
    rows["jb2_receipts"][0]["receipt_json"] = encode_contract(result)
    rows["jb2_review_history"][0].update(review_json=encode_contract(result.candidate.review), revision=result.candidate.review.version)
    rows["jb2_evidence"][0]["evidence_json"] = encode_contract((result.candidate.identity, result.candidate.review))
    validate_payloads(rows)


@pytest.mark.parametrize("field", ["revision", "snapshot_ready", "legacy_writers_quiesced", "settlement_enabled"])
def test_other_connection_control_drift_is_not_overwritten(scope: RunScope, field: str) -> None:
    """@description: Refuse restoration that could hide an unrelated connection's extra counter or switch change.
    @param scope: Registered synthetic run.
    @param field: Unexpected mutation to the locked singleton.
    @return: None.
    """
    from tests.batch_b_harness_contract import consistent_acceptance
    rows = owned_inventory(scope)
    rows["jb2_control"][0][field] = 99 if field == "revision" else 0
    with pytest.raises(HarnessSafetyError, match="CONTROL_"):
        consistent_acceptance(rows, empty_inventory()["jb2_control"][0])


@pytest.mark.parametrize("same_root", [True, False])
def test_revision_matches_durable_merge_effect(scope: RunScope, same_root: bool) -> None:
    """@description: Count real merge mutations while preserving legitimate no-op decision receipts.
    @param scope: Registered run.
    @param same_root: Whether the production merge path changes canonical roots.
    @return: None.
    """
    from bot.database.submission_codec import encode_contract
    from tests.batch_b_harness_contract import consistent_acceptance
    rows = owned_inventory(scope)
    roots = (scope.run_id + ":v1", scope.run_id + (":v1" if same_root else ":v2"))
    rows["jb2_decisions"] = ({"decision_id": scope.run_id + ":merge", "result_json": encode_contract(roots)},)
    rows["jb2_control"][0]["revision"] += int(not same_root)
    consistent_acceptance(rows, empty_inventory()["jb2_control"][0])


def test_server_pin_required_before_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Reject an unpinned MySQL instance before opening a socket.
    @param monkeypatch: Scoped missing UUID configuration and driver spy.
    @return: None.
    """
    from tests import batch_b_harness_database as database
    monkeypatch.delenv("TEST_DB_SERVER_UUID", raising=False)
    driver = MagicMock()
    monkeypatch.setattr(database, "open_connection", driver)
    settings = DatabaseSettings("127.0.0.1", 3306, HARNESS_USER, "synthetic-only", "jukebox101_test")
    with pytest.raises(ConfigurationError):
        database.verified_connection(settings, HARNESS_USER)
    driver.assert_not_called()


@pytest.mark.parametrize("mutation", ["uuid", "inactive_role"])
def test_actual_server_uuid_and_inactive_roles_are_checked(monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    """@description: Bind the instance and reject roles even when CURRENT_ROLE reports NONE.
    @param monkeypatch: Synthetic expected server pin.
    @param mutation: Contradicted identity or an available inactive role.
    @return: None.
    """
    from tests.batch_b_harness_database import check_identity
    monkeypatch.setenv("TEST_DB_SERVER_UUID", "11111111-2222-4333-8444-555555555555")
    identity = dict(db="jukebox101_test", account=HARNESS_USER + "@127.0.0.1", port=3306, version="8.0.46",
                    roles="NONE", fk=1, server_uuid="11111111-2222-4333-8444-555555555555")
    if mutation == "uuid":
        identity["server_uuid"] = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    cursor = MagicMock()
    cursor.fetchone.side_effect = [identity, {"role_available": 1}]
    with pytest.raises(HarnessSafetyError, match="UUID|ROLES"):
        check_identity(cursor, HARNESS_USER)


@pytest.mark.parametrize("statement,allowed", [
    ("GRANT USAGE ON *.* TO `jukebox101_test_harness`@`127.0.0.1`", True),
    ("GRANT SELECT, DELETE ON `jukebox101_test`.`users` TO `jukebox101_test_harness`@`127.0.0.1`", True),
    ("GRANT SELECT, UPDATE (`revision`, `snapshot_ready`) ON `jukebox101_test`.`jb2_control` TO `jukebox101_test_harness`@`127.0.0.1`", True),
    ("GRANT SYSTEM_USER ON *.* TO `jukebox101_test_harness`@`127.0.0.1`", False),
    ("GRANT SELECT ON `jukebox101`.`users` TO `jukebox101_test_harness`@`127.0.0.1`", False),
    ("GRANT `powerful_role`@`%` TO `jukebox101_test_harness`@`127.0.0.1`", False),
    ("GRANT SELECT ON `jukebox101_test`.`users` TO `jukebox101_test_harness`@`127.0.0.1` WITH GRANT OPTION", False),
    ("GRANT PROXY ON `root`@`localhost` TO `jukebox101_test_harness`@`127.0.0.1`", False),
    ("GRANT EXECUTE ON PROCEDURE `jukebox101_test`.`cleanup` TO `jukebox101_test_harness`@`127.0.0.1`", False),
    ("GRANT INSERT ON `jukebox101_test`.* TO `jukebox101_test_harness`@`127.0.0.1`", False)])
def test_unmanaged_grant_forms_are_rejected(statement: str, allowed: bool) -> None:
    """@description: Catch privilege forms not covered completely by table/column information-schema views.
    @param statement: Synthetic own-account SHOW GRANTS output, never authentication data.
    @param allowed: Whether its scope and privilege family are reviewed.
    @return: None.
    """
    from tests.batch_b_harness_database import grant_statement_allowed
    assert grant_statement_allowed(statement, HARNESS_USER) is allowed


def test_app_registry_column_grant_is_not_hidden() -> None:
    """@description: Reject column-level write grants that could bypass an application table-level read-only registry check.
    @param: none
    @return: None.
    """
    from tests.batch_b_harness_database import verify_application_columns
    cursor = MagicMock()
    cursor.fetchall.return_value = [{"PRIVILEGE_TYPE": "UPDATE"}]
    with pytest.raises(HarnessSafetyError, match="COLUMN_GRANTS"):
        verify_application_columns(cursor)


def test_batch_b_writes_are_fully_covered_without_submissions_delete() -> None:
    """@description: Keep the exact cleanup grant set synchronized with reachable Batch B SQL, excluding Phase 2A-only submission records.
    @param: none
    @return: None.
    """
    import re
    from tests.batch_b_harness_contract import DELETE_ORDER
    modules = ("submission_store", "review_persistence", "reward_settlement", "submission_maintenance", "reward_history")
    functions = [ast.parse(Path("bot/database", name + ".py").read_text(encoding="utf-8")) for name in modules]
    repository_imports = [node for tree in functions for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "bot.database.repository"]
    assert {name.name for node in repository_imports for name in node.names} == {"_ensure_user", "_upsert_pending_song"}
    legacy = ast.parse(Path("bot/database/repository.py").read_text(encoding="utf-8"))
    functions.extend(node for node in legacy.body if isinstance(node, ast.FunctionDef) and node.name in {"_ensure_user", "_upsert_pending_song"})
    writes = set()  # [reachable SQL targets]: Migration and Phase 2A _record_submission are intentionally outside this suite.
    for function in functions:
        for node in ast.walk(function):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            match = re.match(r"(?:INSERT INTO|UPDATE) ([a-z0-9_]+)\b", node.value)
            if match:
                writes.add(match[1])
    assert "submissions" not in writes
    assert writes - {"jb2_control"} == set(DELETE_ORDER) | {"jb2_versions"}
    assert len(writes - {"jb2_control"}) == 18


def test_cleanup_order_covers_all_existing_foreign_keys() -> None:
    """@description: Verify child-before-parent teardown against both actual SQL schema artifacts.
    @param: none
    @return: None.
    """
    import re
    from tests.batch_b_harness_contract import DELETE_ORDER
    order = {table: index for index, table in enumerate((*DELETE_ORDER, "jb2_versions"))}
    source = Path("bot/database/schema.sql").read_text(encoding="utf-8")
    source += "\n" + ";\n".join(migration_plan()[1])
    for statement in source.split(";"):
        child = re.search(r"CREATE TABLE IF NOT EXISTS ([a-z0-9_]+)", statement)
        if child is None or child[1] not in order:
            continue
        for parent in re.findall(r"REFERENCES ([a-z0-9_]+)\s*\(", statement):
            if parent in order and parent != child[1]:
                assert order[child[1]] < order[parent], (child[1], parent)


def test_cleanup_and_shutdown_failures_retain_both_sanitized_causes(scope: RunScope, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep original cleanup and recovery errors diagnosable while stopping later writes.
    @param scope: Registered synthetic run.
    @param monkeypatch: Offline cleanup and shutdown fault injection.
    @return: None.
    """
    import pymysql
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    monkeypatch.setattr(runtime.MySQLHarness, "cleanup", MagicMock(side_effect=HarnessSafetyError("CONTROL_REVISION_INCONSISTENCY")))
    monkeypatch.setattr(runtime.MySQLHarness, "disable_only", MagicMock(side_effect=pymysql.OperationalError(2013, "synthetic-secret-driver-text")))
    with pytest.raises(pymysql.OperationalError):
        harness.finish()
    assert [detail["stage"] for detail in harness.failure_details] == ["cleanup", "shutdown"]
    assert harness.failure_details[1]["mysql_error_number"] == 2013
    assert "synthetic-secret" not in str(harness.failure_details)
    assert harness.failed and "STOP_MANUAL_RECOVERY_REQUIRED" in statuses
    cursor.execute.assert_not_called()


@pytest.mark.parametrize("field", ["accepted_sequence", "revision", "snapshot_ready"])
def test_control_drift_stops_real_cleanup_boundary_before_delete(scope: RunScope, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    """@description: Demonstrate control mismatch reaches the stop path before any precise cleanup SQL is issued.
    @param scope: Registered synthetic run.
    @param monkeypatch: Offline connection and inventory spies.
    @param field: Contradicted control value.
    @return: None.
    """
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    rows = owned_inventory(scope)
    rows["jb2_control"][0][field] = 0 if field == "snapshot_ready" else 99
    monkeypatch.setattr(runtime, "lock_inventory", lambda connection: rows)
    monkeypatch.setattr(runtime.MySQLHarness, "disable_only", MagicMock())
    with pytest.raises(HarnessSafetyError):
        harness.finish()
    cursor.execute.assert_not_called()
    assert harness.failed and harness.failure_details[0]["stage"] == "cleanup"
    assert "STOP_MANUAL_RECOVERY_REQUIRED" in statuses


@pytest.mark.parametrize("mutation", ["valid", "key", "user", "settlement"])
def test_uncertain_commit_rows_are_discovered_through_registered_parents(scope: RunScope, mutation: str) -> None:
    """@description: Attribute generated IDs after an unknown commit without assuming every table carries a Run ID.
    @param scope: Roots registered before writes.
    @param mutation: Valid generated rows or an unowned/incomplete award.
    @return: None.
    """
    import hashlib
    rows = owned_inventory(scope)
    operation, subject = next(iter(scope.subjects.items()))
    rows["credit_ledger"] = ({"ledger_id": 8675309, "discord_user_id": subject["user"], "awarded_song_id": 42,
        "credit_delta": 1, "reason": "submission_award", "idempotency_key": "jb2:" + hashlib.sha256(operation.encode()).hexdigest()},)
    rows["jb2_claims"] = ({"version_id": subject["version"], "operation_id": operation, "ledger_id": 8675309, "discord_user_id": subject["user"]},)
    rows["jb2_settlements"] = ({"operation_id": operation},)
    rows["users"][0]["credit_balance"] = 1
    if mutation == "key":
        rows["credit_ledger"][0]["idempotency_key"] = "unregistered"
    if mutation == "user":
        rows["credit_ledger"][0]["discord_user_id"] = 999
    if mutation == "settlement":
        rows["jb2_settlements"] = ()
    if mutation == "valid":
        owned_rows(scope, rows)
        consistent_accounting(rows)
    else:
        with pytest.raises(HarnessSafetyError):
            owned_rows(scope, rows)
            consistent_accounting(rows)


def test_dba_script_keeps_new_account_locked_until_reviewed_grants() -> None:
    """@description: Make partial provisioning fail with the new account locked rather than usable with incomplete rights.
    @param: none
    @return: None.
    """
    source = Path("docs/batch-b-test-harness-grants.sql").read_text(encoding="utf-8")
    create = source.index("CREATE USER 'jukebox101_test_harness'")
    first_grant = source.index("GRANT DELETE, SELECT")
    post_gate = source.index("-- POST-GRANT gate:")
    unlock = source.index("ALTER USER 'jukebox101_test_harness'@'127.0.0.1' ACCOUNT UNLOCK")
    assert create < first_grant < post_gate < unlock
    assert "ACCOUNT LOCK;" in source[create:first_grant]
    assert "ALTER USER 'jukebox101_test_migrator'" not in source
    assert "authentication_string" not in source
    assert "SELECT USER, HOST FROM mysql.user" in source
