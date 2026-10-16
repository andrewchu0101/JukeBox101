"""Provide explicitly opted-in MySQL identity and schema integration checks.

Write checks require a disposable database and always roll back isolated fixture rows.
"""

import os
from datetime import date, datetime
from uuid import uuid4

import pymysql
import pytest

from bot.database.connection import open_connection, inspect_database_identity
from tests.mysql_assertions import (
    ABSENT_SONG_ID, assert_missing_parent_error,
    assert_recommendation_parent_conditions, assert_dispatch_parent_conditions,
)
from tests.mysql_safety import (
    EXPECTED_TABLES, EXPECTED_TEST_ACCOUNT, TEST_DATABASE_NAME,
    load_test_database_settings, validate_connected_test_database, validate_test_destination,
    require_disposable_authorization,
)

pytestmark = pytest.mark.mysql


@pytest.fixture
def mysql_settings():
    """@description: Require explicit authorization before contacting the local application database.
    @param: none
    @return: Validated settings for the existing approved application account.
    """
    if os.environ.get("JUKEBOX_RUN_MYSQL_TEST") != "1":
        pytest.skip("Set JUKEBOX_RUN_MYSQL_TEST=1 only after isolated test preparation is approved")
    return load_test_database_settings()


def test_mysql_identity(mysql_settings):
    """@description: Verify authentication and selected database without schema access.
    @param mysql_settings: Explicitly opted-in application configuration.
    @return: None.
    """
    database_identity = inspect_database_identity(mysql_settings)
    assert database_identity["account"] == EXPECTED_TEST_ACCOUNT
    assert database_identity["database_name"] == TEST_DATABASE_NAME


def require_manual_schema_confirmation():
    """@description: Distinguish unprepared schema from implementation failures.
    @param: none
    @return: None; skips until manual schema execution is confirmed.
    """
    if os.environ.get("JUKEBOX_TEST_SCHEMA_READY") != "1":
        pytest.skip("Manual test schema execution must be confirmed with JUKEBOX_TEST_SCHEMA_READY=1")


def test_required_tables(mysql_settings):
    """@description: Inspect table existence only after manual schema confirmation.
    @param mysql_settings: Approved application configuration.
    @return: None.
    """
    require_manual_schema_confirmation()
    connection = open_connection(mysql_settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s",
                (mysql_settings.database,),
            )
            assert EXPECTED_TABLES <= {row["TABLE_NAME"] for row in cursor.fetchall()}
    finally:
        connection.close()


@pytest.fixture
def isolated_mysql_rows(mysql_settings):
    """@description: Restrict write checks to explicitly designated disposable databases.
    @param mysql_settings: Approved application configuration.
    @return: Connection and unique row identifiers rolled back after each test.
    """
    require_manual_schema_confirmation()
    validate_test_destination(mysql_settings)
    require_disposable_authorization()
    # [fixture identity]: Random IDs isolate inserts; collisions fail without changing existing rows.
    discord_user_id = uuid4().int >> 64
    video_id = uuid4().hex[:11]
    connection = open_connection(mysql_settings)
    try:
        validate_connected_test_database(connection)
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO users (discord_user_id) VALUES (%s)", (discord_user_id,))
            cursor.execute(
                "INSERT INTO songs (platform_track_id, source_url) VALUES (%s, %s)",
                (video_id, "https://www.youtube.com/watch?v=" + video_id),
            )
            canonical_song_id = cursor.lastrowid
        yield connection, discord_user_id, canonical_song_id, video_id
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def test_song_uniqueness(isolated_mysql_rows):
    """@description: Ensure repeated video identifiers cannot create duplicate catalog entries.
    @param isolated_mysql_rows: Disposable uncommitted fixture rows.
    @return: None.
    """
    connection, discord_user_id, canonical_song_id, video_id = isolated_mysql_rows
    with connection.cursor() as cursor:
        with pytest.raises(pymysql.IntegrityError) as captured_error:
            cursor.execute(
                "INSERT INTO songs (platform_track_id, source_url) VALUES (%s, %s)",
                (video_id, "https://www.youtube.com/watch?v=" + video_id),
            )
        assert captured_error.value.args[0] == 1062


def test_recommendation_uniqueness(isolated_mysql_rows):
    """@description: Enforce durable recipient-song uniqueness at the database boundary.
    @param isolated_mysql_rows: Disposable uncommitted fixture rows.
    @return: None.
    """
    connection, discord_user_id, canonical_song_id, video_id = isolated_mysql_rows
    with connection.cursor() as cursor:
        query = "INSERT INTO recommendations (discord_user_id, song_id) VALUES (%s, %s)"
        cursor.execute(query, (discord_user_id, canonical_song_id))
        with pytest.raises(pymysql.IntegrityError) as captured_error:
            cursor.execute(query, (discord_user_id, canonical_song_id))
        assert captured_error.value.args[0] == 1062


def test_foreign_key_integrity(isolated_mysql_rows):
    """@description: Reject recommendations referencing a nonexistent canonical song.
    @param isolated_mysql_rows: Disposable uncommitted fixture rows.
    @return: None.
    """
    connection, discord_user_id, canonical_song_id, video_id = isolated_mysql_rows
    with connection.cursor() as cursor:
        cursor.execute("SELECT song_id FROM songs WHERE song_id = %s", (ABSENT_SONG_ID,))
        if cursor.fetchone() is not None:
            pytest.skip("Reserved nonexistent test song ID is occupied")
        assert_recommendation_parent_conditions(cursor, discord_user_id)
        with pytest.raises(pymysql.IntegrityError) as captured_error:
            cursor.execute(
                "INSERT INTO recommendations (discord_user_id, song_id) VALUES (%s, %s)",
                (discord_user_id, ABSENT_SONG_ID),
            )
        assert_missing_parent_error(captured_error.value, "fk_recommendations_song")


def test_mysql_rollback(isolated_mysql_rows):
    """@description: Verify rolled-back fixture inserts disappear without deleting user data.
    @param isolated_mysql_rows: Disposable uncommitted fixture rows.
    @return: None.
    """
    connection, discord_user_id, canonical_song_id, video_id = isolated_mysql_rows
    connection.rollback()
    with connection.cursor() as cursor:
        cursor.execute("SELECT discord_user_id FROM users WHERE discord_user_id = %s", (discord_user_id,))
        assert cursor.fetchone() is None


@pytest.mark.parametrize("status, delivered_at", [("delivered", None), ("reserved", datetime(2026, 1, 1))])
def test_recommendation_delivery_timestamp(isolated_mysql_rows, status, delivered_at):
    """@description: Reject success without a timestamp and timestamps on unsent reservations.
    @param isolated_mysql_rows: Disposable uncommitted fixture rows.
    @param status: Recommendation state deliberately inconsistent with its timestamp.
    @param delivered_at: Success timestamp deliberately inconsistent with its state.
    @return: None.
    """
    connection, discord_user_id, canonical_song_id, video_id = isolated_mysql_rows
    with connection.cursor() as cursor:
        with pytest.raises(pymysql.OperationalError) as captured_error:
            cursor.execute(
                "INSERT INTO recommendations (discord_user_id, song_id, status, delivered_at) VALUES (%s, %s, %s, %s)",
                (discord_user_id, canonical_song_id, status, delivered_at),
            )
        assert captured_error.value.args[0] == 3819


@pytest.mark.parametrize("status, attach_recommendation", [("pending", True), ("sending", False), ("delivered", False), ("failed", False), ("uncertain", False)])
def test_dispatch_reservation_shape(isolated_mysql_rows, status, attach_recommendation):
    """@description: Reject attached pending rows and non-pending rows without reservations.
    @param isolated_mysql_rows: Disposable uncommitted fixture rows.
    @param status: Daily state deliberately inconsistent with its reservation.
    @param attach_recommendation: Whether the invalid daily row includes reservation IDs.
    @return: None.
    """
    connection, discord_user_id, canonical_song_id, video_id = isolated_mysql_rows
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO recommendations (discord_user_id, song_id) VALUES (%s, %s)",
            (discord_user_id, canonical_song_id),
        )
        recommendation_id = cursor.lastrowid if attach_recommendation else None
        dispatch_song_id = canonical_song_id if attach_recommendation else None
        with pytest.raises(pymysql.OperationalError) as captured_error:
            cursor.execute(
                "INSERT INTO daily_dispatches (discord_user_id, song_id, recommendation_id, dispatch_date, status) VALUES (%s, %s, %s, %s, %s)",
                (discord_user_id, dispatch_song_id, recommendation_id, date(2026, 1, 1), status),
            )
        assert captured_error.value.args[0] == 3819


def test_dispatch_recommendation_owner(isolated_mysql_rows):
    """@description: Reject a daily dispatch borrowing another existing user's recommendation.
    @param isolated_mysql_rows: Disposable uncommitted fixture rows.
    @return: None.
    """
    connection, discord_user_id, canonical_song_id, video_id = isolated_mysql_rows
    # [alternate recipient]: A separate existing user makes the composite FK the failing invariant.
    alternate_discord_user_id = uuid4().int >> 64
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO users (discord_user_id) VALUES (%s)", (alternate_discord_user_id,))
        cursor.execute(
            "INSERT INTO recommendations (discord_user_id, song_id) VALUES (%s, %s)",
            (discord_user_id, canonical_song_id),
        )
        recommendation_id = cursor.lastrowid
        assert_dispatch_parent_conditions(cursor, alternate_discord_user_id, canonical_song_id, recommendation_id)
        with pytest.raises(pymysql.IntegrityError) as captured_error:
            cursor.execute(
                "INSERT INTO daily_dispatches (discord_user_id, song_id, recommendation_id, dispatch_date, status) VALUES (%s, %s, %s, %s, %s)",
                (alternate_discord_user_id, canonical_song_id, recommendation_id, date(2026, 1, 1), "sending"),
            )
        assert_missing_parent_error(captured_error.value, "fk_dispatch_owner")
