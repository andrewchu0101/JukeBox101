"""Static checks of the PostgreSQL schema text (bot/database/postgres/0001_initial_schema.sql).

They replace the MySQL schema.sql static tests (kept in legacy/mysql/tests) with equivalent invariants; they do not prove
the DDL executes, which the postgres-marked integration tests do against a disposable PostgreSQL 18 server.
"""

import re
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "bot" / "database" / "postgres" / "0001_initial_schema.sql"
TABLE_PATTERN = r"CREATE TABLE (\w+) \((.*?)\n\);"
MYSQL_ONLY_SYNTAX = r"\b(UNSIGNED|AUTO_INCREMENT|ENGINE\s*=|ENUM\s*\(|CHARACTER SET|ON UPDATE CURRENT_TIMESTAMP|DATETIME)\b"


@pytest.fixture(scope="module")
def schema_text() -> str:
    """@description: Load the shipped schema without comments so assertions read declarations only.
    @return: Comment-free schema text.
    """
    return re.sub(r"--[^\n]*", "", SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tables(schema_text: str) -> dict[str, str]:
    """@description: Index each CREATE TABLE body by table name.
    @param schema_text: Comment-free schema text.
    @return: Table name to body text.
    """
    return dict(re.findall(TABLE_PATTERN, schema_text, flags=re.DOTALL))


def test_every_reference_targets_a_declared_table_and_no_mysql_syntax_remains(schema_text, tables):
    """@description: Foreign keys only reference tables created in the same file, and no MySQL-only syntax survives the port.
    @param schema_text: Comment-free schema text.
    @param tables: Table bodies.
    @return: None.
    """
    referenced = set(re.findall(r"REFERENCES (\w+)", schema_text))
    assert referenced <= set(tables), referenced - set(tables)
    assert not re.search(MYSQL_ONLY_SYNTAX, schema_text, flags=re.IGNORECASE)
    assert "jb2_control" in tables and "nickel_grants" in tables and "nickel_claim_requests" in tables


def test_no_guild_column_exists_because_data_is_shared_across_guilds(schema_text):
    """@description: Balances, submissions, songs and award priority are global; no tenant or guild column was added.
    @param schema_text: Comment-free schema text.
    @return: None.
    """
    assert "guild" not in schema_text.lower()


def test_youtube_identity_and_global_award(tables):
    """@description: One row per YouTube video ID (case-sensitive) and at most one award per song, system-wide.
    @param tables: Table bodies.
    @return: None.
    """
    songs = tables["songs"]
    assert "song_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY" in songs
    assert 'platform_track_id VARCHAR(11) COLLATE "C" NOT NULL' in songs
    assert "CHECK (platform_track_id ~ '^[A-Za-z0-9_-]{11}$')" in songs
    assert "UNIQUE (platform, platform_track_id)" in songs
    assert "UNIQUE (discord_user_id, song_id)" in tables["submissions"]
    assert "CONSTRAINT uq_ledger_song_award UNIQUE (awarded_song_id)" in tables["credit_ledger"]
    assert "CONSTRAINT uq_ledger_idempotency UNIQUE (idempotency_key)" in tables["credit_ledger"]
    assert "accepted_sequence BIGINT NOT NULL UNIQUE" in tables["jb2_events"]


def test_discord_ids_are_bigint_with_positive_checks_and_balances_cannot_go_negative(tables):
    """@description: External Discord IDs are stored as positive BIGINT; balances keep their non-negative check.
    @param tables: Table bodies.
    @return: None.
    """
    assert "discord_user_id BIGINT NOT NULL PRIMARY KEY" in tables["users"]
    assert "CHECK (discord_user_id > 0)" in tables["users"]
    assert "CHECK (credit_balance >= 0)" in tables["users"]
    assert "CHECK (discord_message_id IS NULL OR discord_message_id > 0)" in tables["recommendations"]


def test_delivery_states_ownership_and_ledger_reasons(tables):
    """@description: Delivery states, ownership foreign keys and every ledger reason keep their reviewed checks.
    @param tables: Table bodies.
    @return: None.
    """
    recommendations, dispatches, ledger = tables["recommendations"], tables["daily_dispatches"], tables["credit_ledger"]
    assert "status IN ('reserved', 'sending', 'delivered', 'failed', 'uncertain')" in recommendations
    assert "(status = 'delivered' AND delivered_at IS NOT NULL) OR (status <> 'delivered' AND delivered_at IS NULL)" in recommendations
    assert "CHECK (released_at IS NULL OR status = 'failed')" in recommendations
    assert "(status = 'pending' AND song_id IS NULL AND recommendation_id IS NULL)" in dispatches
    assert re.search(r"FOREIGN KEY \(recommendation_id, discord_user_id, song_id\)\s+REFERENCES recommendations \(recommendation_id, discord_user_id, song_id\)", dispatches)
    assert re.search(r"FOREIGN KEY \(recommendation_id, discord_user_id\)\s+REFERENCES recommendations \(recommendation_id, discord_user_id\)", ledger)
    for reason in ("submission_award", "exchange_debit", "delivery_refund", "nickel_entry_grant", "nickel_weekly_grant"):
        assert f"reason = '{reason}'" in ledger


def test_released_reservations_free_the_slot_through_a_partial_unique_index(schema_text):
    """@description: The former MySQL generated column is a partial unique index over unreleased reservations.
    @param schema_text: Comment-free schema text.
    @return: None.
    """
    assert ("CREATE UNIQUE INDEX uq_recommendations_user_song_active ON recommendations (discord_user_id, song_id) "
            "WHERE released_at IS NULL") in schema_text


def test_timestamps_are_timezone_aware_and_identifiers_are_case_sensitive(tables):
    """@description: Every timestamp column is TIMESTAMPTZ and every textual identifier key uses the byte-wise C collation.
    @param tables: Table bodies.
    @return: None.
    """
    for body in tables.values():
        assert not re.search(r"\bTIMESTAMP\b(?!TZ)", body)
    for table, column in (("jb2_events", "operation_id"), ("jb2_videos", "video_id"), ("credit_ledger", "idempotency_key"),
                          ("nickel_claim_requests", "request_key"), ("jb2_versions", "version_id")):
        assert re.search(rf'{column} VARCHAR\(\d+\) COLLATE "C" NOT NULL', tables[table]), (table, column)


def test_no_duplicate_explicit_indexes(schema_text):
    """@description: Explicit indexes are not declared twice for the same column list.
    @param schema_text: Comment-free schema text.
    @return: None.
    """
    indexes = re.findall(r"CREATE (?:UNIQUE )?INDEX \w+ ON (\w+) \(([^)]*)\)", schema_text)
    normalized = [(table, " ".join(columns.split())) for table, columns in indexes]
    assert len(normalized) == len(set(normalized))
