"""Review the schema artifact without executing SQL or accessing credentials.

Targeted text checks protect known declarations and relationships; MySQL remains authoritative.
"""

import re
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "bot" / "database" / "schema.sql"
EXPECTED_TABLE_ORDER = ["users", "songs", "submissions", "recommendations", "credit_ledger", "daily_dispatches"]
JOINED_KEYWORDS = r"\b(?:NOTNULL|PRIMARYKEY|FOREIGNKEY|IFNOTEXISTS|NOTEXISTS|AUTOINCREMENT|CHARACTERSET|ONUPDATE)\b"
TABLE_PATTERN = r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\)\s*ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"


@pytest.fixture
def schema_text():
    """@description: Inspect only committed-format SQL text, never local credentials.
    @param: none
    @return: Schema content with SQL line comments removed.
    """
    # [review artifact]: Static assertions inspect declarations without contacting MySQL.
    return re.sub(r"--[^\n]*", "", SCHEMA_PATH.read_text(encoding="utf-8"))


def table_definitions(schema_text: str) -> dict[str, str]:
    """@description: Locate this schema's fixed CREATE TABLE blocks for targeted assertions.
    @param schema_text: Comment-free SQL artifact under review.
    @return: Ordered table names mapped to declaration bodies; not a SQL parser.
    """
    return dict(re.findall(TABLE_PATTERN, schema_text, flags=re.DOTALL))


def test_create_table_scope_and_order(schema_text):
    """@description: Ensure every statement creates an approved table after its parents.
    @param schema_text: Comment-free schema artifact.
    @return: None.
    """
    definitions = table_definitions(schema_text)
    assert list(definitions) == EXPECTED_TABLE_ORDER
    assert re.sub(TABLE_PATTERN, "", schema_text, flags=re.DOTALL).strip() == ""
    created_tables = set()
    for table_name, definition in definitions.items():
        referenced_tables = set(re.findall(r"REFERENCES\s+(\w+)", definition))
        assert referenced_tables <= created_tables, table_name
        created_tables.add(table_name)


def test_keyword_spacing_and_reported_declarations(schema_text):
    """@description: Catch reported joined keywords and changes to essential declarations.
    @param schema_text: Comment-free schema artifact.
    @return: None.
    """
    assert not re.search(JOINED_KEYWORDS, schema_text, flags=re.IGNORECASE)
    definitions = table_definitions(schema_text)
    assert re.search(r"resolution_status\s+ENUM\([^)]*\)\s+NOT\s+NULL", definitions["songs"])
    assert re.search(r"submission_id\s+BIGINT\s+UNSIGNED\s+NOT\s+NULL\s+AUTO_INCREMENT\s+PRIMARY\s+KEY", definitions["submissions"])


@pytest.mark.parametrize("joined_keyword", ["NOTNULL", "PRIMARYKEY", "FOREIGNKEY", "IFNOTEXISTS", "AUTOINCREMENT"])
def test_spacing_guard_detects_regressions(joined_keyword):
    """@description: Verify the guard detects known malformed keyword spellings.
    @param joined_keyword: Deliberate malformed token used to validate the guard.
    @return: None.
    """
    assert re.search(JOINED_KEYWORDS, joined_keyword, flags=re.IGNORECASE)


def test_youtube_identity_and_global_award(schema_text):
    """@description: Preserve independent song identity and one global award per video.
    @param schema_text: Comment-free schema artifact.
    @return: None.
    """
    definitions = table_definitions(schema_text)
    songs = definitions["songs"]
    assert "song_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY" in songs
    assert "platform_track_id VARCHAR(11) CHARACTER SET ascii COLLATE ascii_bin NOT NULL" in songs
    assert "CHECK (platform = 'youtube')" in songs
    assert "CHECK (CHAR_LENGTH(platform_track_id) = 11)" in songs
    assert "UNIQUE (platform, platform_track_id)" in songs
    assert "UNIQUE (discord_user_id, song_id)" in definitions["submissions"]
    assert "UNIQUE (awarded_song_id)" in definitions["credit_ledger"]
    assert not re.search(r"REFERENCES\s+songs\s*\(\s*platform_track_id", schema_text)


def test_delivery_states_and_ownership(schema_text):
    """@description: Retain delivery uncertainty and enforce complete daily ownership references.
    @param schema_text: Comment-free schema artifact.
    @return: None.
    """
    definitions = table_definitions(schema_text)
    recommendations = definitions["recommendations"]
    dispatches = definitions["daily_dispatches"]
    assert "ENUM('reserved', 'sending', 'delivered', 'failed', 'uncertain')" in recommendations
    assert "ENUM('pending', 'sending', 'delivered', 'failed', 'uncertain')" in dispatches
    assert "(status = 'delivered' AND delivered_at IS NOT NULL)" in recommendations
    assert "(status <> 'delivered' AND delivered_at IS NULL)" in recommendations
    assert "(status = 'pending' AND song_id IS NULL AND recommendation_id IS NULL)" in dispatches
    assert "(status <> 'pending' AND song_id IS NOT NULL AND recommendation_id IS NOT NULL)" in dispatches
    assert "UNIQUE (discord_user_id, dispatch_date)" in dispatches
    assert "UNIQUE (recommendation_id)" in dispatches
    assert re.search(r"FOREIGN KEY \(recommendation_id, discord_user_id, song_id\)\s+REFERENCES recommendations \(recommendation_id, discord_user_id, song_id\)", dispatches)
    assert re.search(r"FOREIGN KEY \(recommendation_id, discord_user_id\)\s+REFERENCES recommendations \(recommendation_id, discord_user_id\)", definitions["credit_ledger"])


def test_no_duplicate_explicit_indexes_or_dispatch_song_fk(schema_text):
    """@description: Avoid identical explicit indexes and redundant daily song lookups.
    @param schema_text: Comment-free schema artifact.
    @return: None.
    """
    definitions = table_definitions(schema_text)
    for table_name, definition in definitions.items():
        indexed_columns = re.findall(r"(?:UNIQUE|INDEX\s+\w+)\s*\(([^)]*)\)", definition)
        normalized_indexes = [re.sub(r"\s+", "", columns) for columns in indexed_columns]
        assert len(normalized_indexes) == len(set(normalized_indexes)), table_name
    assert "fk_dispatch_song" not in definitions["daily_dispatches"]
    assert not re.search(r"FOREIGN KEY \(song_id\)", definitions["daily_dispatches"])
