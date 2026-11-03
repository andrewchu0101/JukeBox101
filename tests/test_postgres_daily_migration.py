"""Real PostgreSQL tests of migration 0005 (stage C daily schedule): additive, constrained, least-privilege, and harmless to existing data.

INTERFACE CONTRACT (bot/database/postgres/0005_daily_schedule.sql, any file name starting with 0005 and containing "daily"):
    creates daily_preferences and daily_notices (columns and checks as listed in tests/test_postgres_daily_schedule.py), grants
    SELECT/INSERT/UPDATE (no DELETE) to jukebox101_app_role and SELECT to jukebox101_readonly_role on both, and ALSO grants
    SELECT/INSERT/UPDATE on the existing daily_dispatches (0002 never granted it because nothing used it).
    It alters no existing table, rewrites no row and drops nothing, so a build without 0005 can still read a database that has it.
"""

import psycopg
import pytest

from bot.database.migration_runner import discover_migrations, run_migrations
from bot.database.connection import open_connection
from tests.postgres_support import admin_query, create_database, drop_database

pytestmark = pytest.mark.postgres

NEW_TABLES = ("daily_preferences", "daily_notices")  # [new objects]: The only tables 0005 may add.
SHIPPED_BEFORE_DAILY = 4  # [prior head]: 0004 is the last migration before this stage.


def column_snapshot(database) -> list[tuple]:
    """@description: Read every column of every table so a before/after comparison catches any altered existing table.
    @param database: Test database.
    @return: Sorted (table, column, type, nullable, default) tuples.
    """
    rows = admin_query(database, "SELECT table_name, column_name, data_type, is_nullable, column_default FROM information_schema.columns "
                                 "WHERE table_schema = 'jukebox101' ORDER BY table_name, ordinal_position")
    return [(row["table_name"], row["column_name"], row["data_type"], row["is_nullable"], row["column_default"]) for row in rows]


def test_the_shipped_list_has_a_fifth_daily_migration_after_the_four_existing_ones() -> None:
    """@description: 0005 exists, follows 0004 unchanged, and is named for the daily schedule.
    @return: None.
    """
    shipped = discover_migrations()
    assert [item.version for item in shipped][:5] == [1, 2, 3, 4, 5] and "daily" in shipped[4].name


def test_a_fresh_database_has_both_tables_with_the_agreed_columns(pg_database) -> None:
    """@description: The columns the implementation and the sweep tests rely on exist.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    columns = {}
    for row in column_snapshot(pg_database):
        columns.setdefault(row[0], set()).add(row[1])
    assert {"discord_user_id", "enabled", "delivery_hour", "updated_at"} <= columns["daily_preferences"]
    assert {"discord_user_id", "notice_day", "kind", "delivery", "consecutive_days", "created_at"} <= columns["daily_notices"]


def test_the_constraints_reject_bad_hours_kinds_deliveries_and_a_second_notice_for_one_day(pg_database) -> None:
    """@description: The database itself refuses an hour outside 0..23, unknown kinds or deliveries, and two notices for one person and day.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    admin_query(pg_database, "INSERT INTO users (discord_user_id) VALUES (700001)")
    for hour, accepted in ((0, True), (23, True), (24, False), (-1, False)):
        statement = "INSERT INTO daily_preferences (discord_user_id, enabled, delivery_hour, updated_at) VALUES (700001, TRUE, %s, now()) " \
                    "ON CONFLICT (discord_user_id) DO UPDATE SET delivery_hour = EXCLUDED.delivery_hour"
        if accepted:
            admin_query(pg_database, statement, (hour,))
        else:
            with pytest.raises(psycopg.errors.CheckViolation):
                admin_query(pg_database, statement, (hour,))
    notice = "INSERT INTO daily_notices (discord_user_id, notice_day, kind, delivery, consecutive_days, created_at) VALUES (700001, '2026-11-03', %s, %s, 1, now())"
    admin_query(pg_database, notice, ("insufficient_credit", "sent"))
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin_query(pg_database, notice, ("no_candidate", "sent"))
    with pytest.raises(psycopg.errors.CheckViolation):
        admin_query(pg_database, notice.replace("2026-11-03", "2026-11-04"), ("surprise", "sent"))


def test_the_application_role_may_write_but_never_delete_and_the_readonly_role_only_reads(pg_database) -> None:
    """@description: Least privilege on the two new tables and on the existing dispatch table the feature starts using.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    asked = {"jukebox101_app": {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": False},
             "jukebox101_readonly": {"SELECT": True, "INSERT": False, "UPDATE": False, "DELETE": False}}
    for table in NEW_TABLES + ("daily_dispatches",):
        for role, expectations in asked.items():
            for privilege, expected in expectations.items():
                row = admin_query(pg_database, "SELECT has_table_privilege(%s, %s, %s) AS granted", (role, f"jukebox101.{table}", privilege))
                assert row[0]["granted"] is expected, (role, table, privilege)


def test_upgrading_from_0004_keeps_every_existing_table_column_and_row_exactly(pg_cluster) -> None:
    """@description: A database at 0004 with data gains only the two new tables; no existing table definition changes and no existing row moves.
    @param pg_cluster: Disposable server.
    @return: None.
    """
    database = create_database(pg_cluster, migrate=False)
    try:
        shipped = discover_migrations()
        connection = open_connection(database.migrator)
        try:
            run_migrations(connection, apply=True, available=shipped[:SHIPPED_BEFORE_DAILY])
        finally:
            connection.close()
        admin_query(database, "INSERT INTO users (discord_user_id, credit_balance, daily_enabled) VALUES (700001, 0, TRUE), (700002, 0, FALSE)")
        before_columns, before_users = column_snapshot(database), admin_query(database, "SELECT * FROM users ORDER BY discord_user_id")
        connection = open_connection(database.migrator)
        try:
            summary = run_migrations(connection, apply=True)
        finally:
            connection.close()
        after_columns = column_snapshot(database)
        assert [item for item in after_columns if item[0] not in NEW_TABLES] == before_columns
        assert {row[0] for row in after_columns} - {row[0] for row in before_columns} == set(NEW_TABLES)
        assert admin_query(database, "SELECT * FROM users ORDER BY discord_user_id") == before_users
        assert summary["applied_now"] and summary["pending_before"]
    finally:
        drop_database(pg_cluster, database.name)


def test_the_old_columns_a_previous_build_reads_still_work_on_the_upgraded_schema(pg_database) -> None:
    """@description: A build without 0005 reads users.daily_enabled and daily_dispatches; both still exist, so rolling the image back needs no schema step.
    @param pg_database: Freshly migrated database.
    @return: None.
    """
    admin_query(pg_database, "INSERT INTO users (discord_user_id, daily_enabled) VALUES (700003, TRUE)")
    assert admin_query(pg_database, "SELECT daily_enabled FROM users WHERE discord_user_id = 700003") == [{"daily_enabled": True}]
    assert admin_query(pg_database, "SELECT count(*) AS total FROM daily_dispatches") == [{"total": 0}]
