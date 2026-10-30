"""Inspect live MySQL identity and schema metadata without application-table writes.

An independent opt-in entry loads local dotenv settings and executes SELECTs in a read-only transaction.
"""

import os
import re
from pathlib import Path

import pymysql
import pytest

from bot.config import load_database_settings
from bot.database.connection import open_connection

pytestmark = pytest.mark.mysql_readonly
PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PRIMARY_KEYS = {
    "users": "discord_user_id", "songs": "song_id", "submissions": "submission_id",
    "recommendations": "recommendation_id", "credit_ledger": "ledger_id", "daily_dispatches": "dispatch_id",
}
EXPECTED_UNIQUE_KEYS = {
    "songs": {"uq_songs_platform_track": ("platform", "platform_track_id")},
    "submissions": {"uq_submissions_user_song": ("discord_user_id", "song_id")},
    "recommendations": {
        "uq_recommendations_user_song": ("discord_user_id", "song_id"),
        "uq_recommendations_owner": ("recommendation_id", "discord_user_id", "song_id"),
        "uq_recommendations_recipient": ("recommendation_id", "discord_user_id"),
    },
    "credit_ledger": {
        "uq_ledger_idempotency": ("idempotency_key",), "uq_ledger_song_award": ("awarded_song_id",),
        "uq_ledger_recommendation_reason": ("recommendation_id", "reason"),
    },
    "daily_dispatches": {
        "uq_dispatch_user_date": ("discord_user_id", "dispatch_date"),
        "uq_dispatch_recommendation": ("recommendation_id",),
    },
}
EXPECTED_FOREIGN_KEYS = {
    "submissions": {"fk_submissions_user": ("users", ("discord_user_id",), ("discord_user_id",)),
                    "fk_submissions_song": ("songs", ("song_id",), ("song_id",))},
    "recommendations": {"fk_recommendations_user": ("users", ("discord_user_id",), ("discord_user_id",)),
                        "fk_recommendations_song": ("songs", ("song_id",), ("song_id",))},
    "credit_ledger": {
        "fk_ledger_user": ("users", ("discord_user_id",), ("discord_user_id",)),
        "fk_ledger_award": ("songs", ("awarded_song_id",), ("song_id",)),
        "fk_ledger_recommendation": ("recommendations", ("recommendation_id", "discord_user_id"), ("recommendation_id", "discord_user_id")),
    },
    "daily_dispatches": {
        "fk_dispatch_user": ("users", ("discord_user_id",), ("discord_user_id",)),
        "fk_dispatch_owner": ("recommendations", ("recommendation_id", "discord_user_id", "song_id"), ("recommendation_id", "discord_user_id", "song_id")),
    },
}
EXPECTED_CHECKS = {
    "chk_users_balance": "credit_balance >= 0",
    "chk_users_daily": "daily_enabled IN (0, 1)",
    "chk_songs_platform": "platform = 'youtube'",
    "chk_songs_track": "CHAR_LENGTH(platform_track_id) = 11",
    "chk_songs_verified": "resolution_status <> 'verified' OR (youtube_url IS NOT NULL AND verification_provenance IS NOT NULL)",
    "chk_recommendations_delivery": "(status = 'delivered' AND delivered_at IS NOT NULL) OR (status <> 'delivered' AND delivered_at IS NULL)",
    "chk_ledger_reason": "(reason = 'submission_award' AND credit_delta = 1 AND awarded_song_id IS NOT NULL AND recommendation_id IS NULL) OR (reason = 'exchange_debit' AND credit_delta = -1 AND awarded_song_id IS NULL AND recommendation_id IS NOT NULL) OR (reason = 'delivery_refund' AND credit_delta = 1 AND awarded_song_id IS NULL AND recommendation_id IS NOT NULL)",
    "chk_dispatch_reservation": "(status = 'pending' AND song_id IS NULL AND recommendation_id IS NULL) OR (status <> 'pending' AND song_id IS NOT NULL AND recommendation_id IS NOT NULL)",
}


def local_readonly_settings():
    """@description: Prove local dotenv loading while rejecting any unapproved destination.
    @param: none
    @return: Local application settings without printing or persisting secret values.
    """
    dotenv_path = PROJECT_ROOT / ".env"
    assert dotenv_path.is_file(), "Local .env is required for this validation"
    with pytest.MonkeyPatch.context() as environment_patch:
        for setting_name in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "DB_NAME"):
            environment_patch.delenv(setting_name, raising=False)
        settings = load_database_settings(dotenv_path)
    assert (settings.host, settings.port, settings.user, settings.database) == (
        "127.0.0.1", 3306, "jukebox101_app", "jukebox101",
    ), "Read-only validation requires the approved local application destination"
    return settings


@pytest.fixture(scope="module")
def readonly_connection():
    """@description: Isolate live metadata validation from all existing write-test gates.
    @param: none
    @return: Owned MySQL connection inside a server-enforced read-only transaction.
    """
    if os.environ.get("JUKEBOX_RUN_MYSQL_READONLY") != "1":
        pytest.skip("Set JUKEBOX_RUN_MYSQL_READONLY=1 for the independent read-only entry")
    try:
        connection = open_connection(local_readonly_settings())
    except pymysql.MySQLError as connection_error:
        pytest.fail(f"MySQL connection failed (code {connection_error.args[0]}); credentials suppressed", pytrace=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
        yield connection
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def select_metadata(connection, query: str, parameters: tuple = ()) -> list[dict]:
    """@description: Restrict this validation entry to fixed SELECT statements.
    @param connection: Exclusively owned read-only connection.
    @param query: Trusted single SELECT statement querying identity or metadata.
    @param parameters: Values bound separately from SQL text.
    @return: Dictionary rows containing public identity or schema metadata.
    """
    assert query.lstrip().upper().startswith("SELECT ") and ";" not in query
    with connection.cursor() as cursor:
        cursor.execute(query, parameters)
        return cursor.fetchall()


def test_local_application_identity(readonly_connection):
    """@description: Verify PyMySQL reaches the approved authenticated account and database.
    @param readonly_connection: Local application connection using dotenv settings.
    @return: None.
    """
    identity = select_metadata(readonly_connection, "SELECT CURRENT_USER() AS account, DATABASE() AS database_name")[0]
    assert identity == {"account": "jukebox101_app@127.0.0.1", "database_name": "jukebox101"}


def test_six_tables_exist(readonly_connection):
    """@description: Verify all foundation tables exist without reading user records.
    @param readonly_connection: Local application read-only connection.
    @return: None.
    """
    rows = select_metadata(readonly_connection,
        "SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s",
        ("jukebox101",))
    tables = {row["TABLE_NAME"]: row["ENGINE"] for row in rows}
    for table_name in EXPECTED_PRIMARY_KEYS:
        assert tables.get(table_name) == "InnoDB", table_name


@pytest.fixture(scope="module")
def constraint_metadata(readonly_connection):
    """@description: Read constraint types and ordered key columns for real schema validation.
    @param readonly_connection: Local application read-only connection.
    @return: Constraint definitions and their ordered column metadata.
    """
    # [constraint evidence]: Only information_schema records are inspected, never application rows.
    constraints = select_metadata(readonly_connection,
        "SELECT TABLE_NAME, CONSTRAINT_NAME, CONSTRAINT_TYPE, ENFORCED FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = %s",
        ("jukebox101",))
    columns = select_metadata(readonly_connection,
        "SELECT TABLE_NAME, CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE CONSTRAINT_SCHEMA = %s ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION",
        ("jukebox101",))
    return {(row["TABLE_NAME"], row["CONSTRAINT_NAME"]): row for row in constraints}, columns


def ordered_key_columns(columns: list[dict], table_name: str, constraint_name: str) -> list[dict]:
    """@description: Preserve key column order when comparing compound ownership constraints.
    @param columns: Ordered key-column metadata from MySQL.
    @param table_name: Foundation table owning the constraint.
    @param constraint_name: Required key name in the reviewed schema.
    @return: Ordered rows belonging to the requested constraint.
    """
    return [row for row in columns if (row["TABLE_NAME"], row["CONSTRAINT_NAME"]) == (table_name, constraint_name)]


def test_primary_and_unique_keys(constraint_metadata):
    """@description: Confirm real primary keys and every reviewed unique key use expected columns.
    @param constraint_metadata: Real constraint types and key-column metadata.
    @return: None.
    """
    constraints, columns = constraint_metadata
    expected_keys = [(table, "PRIMARY", "PRIMARY KEY", (column,)) for table, column in EXPECTED_PRIMARY_KEYS.items()]
    expected_keys.extend((table, name, "UNIQUE", key_columns)
        for table, keys in EXPECTED_UNIQUE_KEYS.items() for name, key_columns in keys.items())
    for table_name, constraint_name, constraint_type, expected_columns in expected_keys:
        assert constraints[(table_name, constraint_name)]["CONSTRAINT_TYPE"] == constraint_type
        key_rows = ordered_key_columns(columns, table_name, constraint_name)
        assert tuple(row["COLUMN_NAME"] for row in key_rows) == expected_columns


def test_foreign_key_targets(constraint_metadata):
    """@description: Confirm real foreign keys retain canonical identity and recipient ownership.
    @param constraint_metadata: Real constraint types and key-column metadata.
    @return: None.
    """
    constraints, columns = constraint_metadata
    for table_name, foreign_keys in EXPECTED_FOREIGN_KEYS.items():
        for constraint_name, (target_table, local_columns, target_columns) in foreign_keys.items():
            assert constraints[(table_name, constraint_name)]["CONSTRAINT_TYPE"] == "FOREIGN KEY"
            key_rows = ordered_key_columns(columns, table_name, constraint_name)
            assert tuple(row["COLUMN_NAME"] for row in key_rows) == local_columns
            assert tuple(row["REFERENCED_COLUMN_NAME"] for row in key_rows) == target_columns
            assert {(row["REFERENCED_TABLE_SCHEMA"], row["REFERENCED_TABLE_NAME"]) for row in key_rows} == {("jukebox101", target_table)}


def normalized_check_clause(clause: str) -> str:
    """@description: Ignore MySQL display quoting when comparing this schema's CHECK expressions.
    @param clause: Reviewed or server-rendered CHECK expression.
    @return: Expression suitable for a targeted textual equivalence check, not a SQL parser.
    """
    clause = re.sub(r"_(?:utf8mb4|ascii)", "", clause.lower())
    clause = clause.replace("\\'", "'")
    return re.sub(r"[\s`()]+", "", clause)


@pytest.mark.parametrize("server_clause", ["(`platform` = _ascii'youtube')", r"(`platform` = \'youtube\')"])
def test_check_rendering_normalization(server_clause):
    """@description: Accept server display escaping without changing the expected CHECK content.
    @param server_clause: Synthetic MySQL-rendered expression, containing no credentials.
    @return: None.
    """
    assert normalized_check_clause(server_clause) == normalized_check_clause("platform = 'youtube'")


def test_check_constraints_enforced(readonly_connection, constraint_metadata):
    """@description: Confirm required CHECK expressions exist and MySQL marks them enforced.
    @param readonly_connection: Local application read-only connection.
    @param constraint_metadata: Real constraint declarations.
    @return: None.
    """
    constraints, columns = constraint_metadata
    rows = select_metadata(readonly_connection,
        "SELECT CONSTRAINT_NAME, CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = %s",
        ("jukebox101",))
    checks = {row["CONSTRAINT_NAME"]: row["CHECK_CLAUSE"] for row in rows}
    enforced_checks = {row["CONSTRAINT_NAME"] for row in constraints.values()
        if row["CONSTRAINT_TYPE"] == "CHECK" and row["ENFORCED"] == "YES"}
    assert EXPECTED_CHECKS.keys() <= enforced_checks
    for constraint_name, expected_clause in EXPECTED_CHECKS.items():
        assert normalized_check_clause(checks[constraint_name]) == normalized_check_clause(expected_clause), constraint_name
