"""Real PostgreSQL tests for the versioned migration runner (postgres marker; disposable server only).

Covers fresh apply, idempotent re-run, refusal of edited or unknown history, rollback of a failing file, identity
refusals, the single-runner lock and the empty-database guard of the control bootstrap.
"""

import hashlib
import os

import psycopg
import pytest

from bot.database.connection import open_connection
from bot.database.migration_runner import (ADVISORY_LOCK_KEY, Migration, MigrationFailed, MigrationRefused,
                                           discover_migrations, run_migrations)
from tests.postgres_support import admin_query, apply_all_migrations, create_database, drop_database

pytestmark = pytest.mark.postgres


def _run(settings, *, apply: bool, available=None) -> dict:
    """@description: Run the production runner on a fresh connection and always close it.
    @param settings: Role settings.
    @param apply: Whether to apply pending migrations.
    @param available: Optional replacement migration list.
    @return: Runner summary.
    """
    connection = open_connection(settings)
    try:
        return run_migrations(connection, apply=apply, available=available)
    finally:
        connection.close()


def _migration(version: int, name: str, text: str) -> Migration:
    """@description: Build an in-memory migration for history and failure tests.
    @param version: Version number.
    @param name: Name stem.
    @param text: SQL text.
    @return: Migration with its digest.
    """
    return Migration(version, name, text, hashlib.sha256(text.encode("utf-8")).hexdigest())


def test_fresh_database_gets_every_version_and_a_rerun_changes_nothing(pg_database):
    """@description: All shipped versions are recorded with their digests; a second run has nothing pending.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    shipped = discover_migrations()
    ledger = admin_query(pg_database, "SELECT version, sha256 FROM jb_schema_migrations ORDER BY version")
    assert [(row["version"], row["sha256"]) for row in ledger] == [(item.version, item.sha256) for item in shipped]
    summary = _run(pg_database.migrator, apply=True)
    assert summary["pending_before"] == [] and summary["applied_now"] == []
    expected_major = int(os.environ.get("JB_PG_TEST_EXPECTED_MAJOR", "18"))
    assert summary["server_version"].startswith(f"{expected_major}.")
    control = admin_query(pg_database, "SELECT * FROM jb2_control")
    assert control == [{"singleton_id": 1, "schema_version": "2b2-b1", "accepted_sequence": 0, "revision": 0,
                        "snapshot_ready": True, "legacy_writers_quiesced": True, "settlement_enabled": False}]


def test_edited_or_unknown_history_is_refused_before_any_change(pg_database):
    """@description: A changed file for an applied version, or a version this release lacks, stops the run.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    shipped = discover_migrations()
    edited = [_migration(1, shipped[0].name, shipped[0].sql + "\n-- edited")] + shipped[1:]
    with pytest.raises(MigrationRefused, match="different content"):
        _run(pg_database.migrator, apply=True, available=edited)
    with pytest.raises(MigrationRefused, match="does not ship"):
        _run(pg_database.migrator, apply=True, available=shipped[:2])


def test_a_failing_migration_rolls_back_completely_and_a_rerun_reports_it_pending(pg_cluster):
    """@description: DDL is transactional: a failure leaves neither its table nor its ledger row; earlier versions stay.
    @param pg_cluster: Disposable server.
    @return: None.
    """
    database = create_database(pg_cluster, migrate=False)
    try:
        shipped = discover_migrations()
        broken = shipped + [_migration(len(shipped) + 1, "broken", "CREATE TABLE half_done (x int); SELECT 1/0;")]
        with pytest.raises(MigrationFailed, match="rolled back"):
            _run(database.migrator, apply=True, available=broken)
        tables = admin_query(database, "SELECT to_regclass('jukebox101.half_done') AS relation")
        assert tables == [{"relation": None}]
        versions = [row["version"] for row in admin_query(database, "SELECT version FROM jb_schema_migrations ORDER BY version")]
        assert versions == [item.version for item in shipped]
        assert _run(database.migrator, apply=False, available=broken)["pending_before"] == [len(shipped) + 1]
    finally:
        drop_database(pg_cluster, database.name)


def test_superuser_and_non_owner_roles_are_refused(pg_database):
    """@description: Only the schema-owner migrator may migrate; the app role and a superuser are refused.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    with pytest.raises(MigrationRefused, match="owned by the connecting role"):
        _run(pg_database.app, apply=False)
    with pytest.raises(MigrationRefused, match="superuser"):
        _run(pg_database.admin, apply=False)


def test_a_second_concurrent_runner_is_refused_instead_of_waiting(pg_database):
    """@description: The advisory lock admits one runner; a second refuses immediately.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    holder = open_connection(pg_database.migrator)
    try:
        holder.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        holder.commit()
        with pytest.raises(MigrationRefused, match="holds the lock"):
            _run(pg_database.migrator, apply=True)
    finally:
        holder.close()


def test_control_bootstrap_refuses_a_database_that_already_holds_data(pg_cluster):
    """@description: Migration 0003 never certifies imported rows: with data present it fails and records nothing.
    @param pg_cluster: Disposable server.
    @return: None.
    """
    database = create_database(pg_cluster, migrate=False)
    try:
        shipped = discover_migrations()
        _run(database.migrator, apply=True, available=shipped[:2])
        admin_query(database, "INSERT INTO users (discord_user_id) VALUES (42)")
        with pytest.raises(MigrationFailed, match="version 3"):
            _run(database.migrator, apply=True)
        assert admin_query(database, "SELECT count(*) AS rows FROM jb2_control") == [{"rows": 0}]
    finally:
        drop_database(pg_cluster, database.name)


def test_application_role_has_least_privilege(pg_database):
    """@description: The Bot role reads and appends but cannot delete, truncate, change DDL or read the migration ledger.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    statements = ["DELETE FROM users", "TRUNCATE users", "DROP TABLE users", "CREATE TABLE jukebox101.intruder (x int)",
                  "UPDATE credit_ledger SET credit_delta = 0", "SELECT * FROM jb_schema_migrations", "SELECT * FROM daily_dispatches"]
    for statement in statements:
        connection = open_connection(pg_database.app)
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)
        finally:
            connection.close()
    identity = admin_query(pg_database, "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = 'jukebox101_app'")
    assert identity == [{"rolsuper": False, "rolcreaterole": False, "rolcreatedb": False}]


def test_readonly_role_reads_but_cannot_write(pg_database):
    """@description: The maintenance role can SELECT every table and nothing else.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    connection = open_connection(pg_database.readonly)
    try:
        assert connection.execute("SELECT count(*) AS n FROM jb2_control").fetchone()["n"] == 1
        connection.commit()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("INSERT INTO users (discord_user_id) VALUES (1)")
    finally:
        connection.close()


def test_rerunning_after_success_leaves_data_untouched(pg_database):
    """@description: Re-applying with nothing pending never touches business rows.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    admin_query(pg_database, "INSERT INTO users (discord_user_id, credit_balance) VALUES (77, 3)")
    assert apply_all_migrations(pg_database)["applied_now"] == []
    assert admin_query(pg_database, "SELECT credit_balance FROM users WHERE discord_user_id = 77") == [{"credit_balance": 3}]
