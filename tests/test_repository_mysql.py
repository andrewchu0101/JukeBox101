"""Prepare explicitly opted-in repository SQL acceptance against the fixed disposable MySQL database.

All fixtures remain inside rollback-only outer transactions; collection and default runs never connect.
"""

from datetime import datetime, timedelta

import pymysql
import pytest

from bot.models import HISTORY_LIMIT
from bot.urls import parse_youtube_url
from tests.repository_mysql_support import (
    authorized_repository_settings, execute_statement, new_video, require_empty_tables,
    rollback_repository_session, select_rows, table_counts, verified_counts,
)

pytestmark = pytest.mark.mysql
FIXTURE_DELIVERY_TIME = datetime(2026, 1, 1)


@pytest.fixture
def repository_mysql_settings():
    """@description: Require explicit repository acceptance opt-in before opening any connection.
    @param: none
    @return: Authorized fixed test configuration; missing opt-ins fail before network access.
    """
    import os
    if os.environ.get("JUKEBOX_RUN_REPOSITORY_MYSQL") != "1":
        pytest.skip("Repository integration requires JUKEBOX_RUN_REPOSITORY_MYSQL=1 after approval")
    return authorized_repository_settings()


@pytest.fixture
def repository_mysql_session(repository_mysql_settings):
    """@description: Compare six-table baselines and rollback all rows even after failed assertions.
    @param repository_mysql_settings: Explicit fixed TEST_DB configuration.
    @return: Worker-owned repository session with post-cleanup fresh-connection counts.
    """
    # [cleanup evidence]: Fresh snapshots compare committed rows before and after the owned transaction.
    before_counts = verified_counts(repository_mysql_settings)
    require_empty_tables(before_counts)
    try:
        with rollback_repository_session(repository_mysql_settings) as session:
            yield session
    finally:
        assert verified_counts(repository_mysql_settings) == before_counts, "Six-table row counts changed after repository acceptance"


def test_pending_submission_sql(repository_mysql_session):
    """@description: Verify pending defaults and exact identity without a credit award.
    @param repository_mysql_session: Rollback-only SQL acceptance session.
    @return: None.
    """
    session = repository_mysql_session
    result = session.repository.submit_pending(session.discord_user_id, session.video)
    assert result.resolution_status == "pending" and result.duplicate_submission is False
    song = _song_row(session)
    assert song["platform_track_id"] == session.video.video_id
    assert song["source_url"] == session.video.source_url
    assert song["youtube_url"] is None and song["verification_provenance"] is None
    counts = table_counts(session.connection)
    assert counts == {"users": 1, "songs": 1, "submissions": 1, "recommendations": 0, "credit_ledger": 0, "daily_dispatches": 0}
    assert session.repository.balance(session.discord_user_id) == 0


@pytest.mark.parametrize("same_submitter", [True, False])
def test_repeated_submission_sql(repository_mysql_session, same_submitter):
    """@description: Verify global song deduplication and per-user attempt semantics.
    @param repository_mysql_session: Rollback-only SQL acceptance session.
    @param same_submitter: Retry same recipient or let another recipient submit the same video.
    @return: None.
    """
    session = repository_mysql_session
    session.repository.submit_pending(session.discord_user_id, session.video)
    original_song = _song_row(session)
    retry_user_id = session.discord_user_id if same_submitter else session.alternate_user_id
    retry_video = parse_youtube_url(session.video.canonical_url + "&utm_source=retry")
    result = session.repository.submit_pending(retry_user_id, retry_video)
    assert result.duplicate_submission is same_submitter
    assert _song_row(session) == original_song
    counts = table_counts(session.connection)
    assert counts["songs"] == 1
    assert counts["submissions"] == (1 if same_submitter else 2)
    assert counts["credit_ledger"] == 0
    assert session.repository.balance(retry_user_id) == 0


@pytest.mark.parametrize("resolution_status", ["verified", "rejected"])
def test_existing_resolution_is_preserved(repository_mysql_session, resolution_status):
    """@description: Prevent duplicate pending upserts from destroying catalog verification evidence.
    @param repository_mysql_session: Rollback-only SQL acceptance session.
    @param resolution_status: Existing catalog decision to preserve.
    @return: None.
    """
    session = repository_mysql_session
    session.repository.submit_pending(session.discord_user_id, session.video)
    execute_statement(session.connection,
        "UPDATE songs SET resolution_status = %s, youtube_url = %s, verification_provenance = %s WHERE platform_track_id = %s",
        (resolution_status, session.video.canonical_url, "synthetic acceptance fixture; not real verification", session.video.video_id))
    original_song = _song_row(session)
    retry_video = parse_youtube_url(session.video.canonical_url)
    result = session.repository.submit_pending(session.alternate_user_id, retry_video)
    assert result.resolution_status == resolution_status and result.duplicate_submission is False
    assert _song_row(session) == original_song
    assert table_counts(session.connection)["credit_ledger"] == 0


def test_submission_sql_error_rolls_back_operation(repository_mysql_session, monkeypatch):
    """@description: Verify partial user and song writes disappear after a real CHECK rejection.
    @param repository_mysql_session: Rollback-only SQL acceptance session.
    @param monkeypatch: Inject a fixed failing SQL statement after repository user/song writes.
    @return: None.
    """
    session = repository_mysql_session
    before_counts = table_counts(session.connection)
    monkeypatch.setattr("bot.database.repository._record_submission", _violate_balance_check)
    with pytest.raises(pymysql.OperationalError) as captured_error:
        session.repository.submit_pending(session.discord_user_id, session.video)
    assert captured_error.value.args[0] == 3819
    assert table_counts(session.connection) == before_counts
    assert session.repository.balance(session.discord_user_id) == 0


@pytest.mark.parametrize("existing_account", [False, True])
def test_daily_preference_sql(repository_mysql_session, existing_account):
    """@description: Verify final-value idempotency without overwriting existing balances.
    @param repository_mysql_session: Rollback-only SQL acceptance session.
    @param existing_account: Include a preexisting account with a synthetic balance.
    @return: None.
    """
    session = repository_mysql_session
    if existing_account:
        execute_statement(session.connection, "INSERT INTO users (discord_user_id, credit_balance) VALUES (%s, %s)", (session.discord_user_id, 3))
    for enabled in (True, True, False, False):
        session.repository.set_daily(session.discord_user_id, enabled)
        rows = select_rows(session.connection, "SELECT daily_enabled, credit_balance FROM users WHERE discord_user_id = %s", (session.discord_user_id,))
        assert rows == [{"daily_enabled": int(enabled), "credit_balance": 3 if existing_account else 0}]
    counts = table_counts(session.connection)
    assert counts["users"] == 1 and counts["daily_dispatches"] == 0 and counts["credit_ledger"] == 0


def test_balance_and_empty_history_sql(repository_mysql_session):
    """@description: Verify absent account reads and stored balance do not fabricate user rows.
    @param repository_mysql_session: Rollback-only SQL acceptance session.
    @return: None.
    """
    session = repository_mysql_session
    assert session.repository.balance(session.discord_user_id) == 0
    assert session.repository.history(session.discord_user_id) == []
    assert table_counts(session.connection)["users"] == 0
    execute_statement(session.connection, "INSERT INTO users (discord_user_id, credit_balance) VALUES (%s, %s)", (session.discord_user_id, 3))
    assert session.repository.balance(session.discord_user_id) == 3


def test_history_sql_sort_scope_and_limit(repository_mysql_session):
    """@description: Verify tied timestamps, delivery filtering, recipient isolation and query limit.
    @param repository_mysql_session: Rollback-only SQL acceptance session.
    @return: None.
    """
    session = repository_mysql_session
    expected_deliveries = _seed_history(session)
    actual_deliveries = session.repository.history(session.discord_user_id)
    assert len(actual_deliveries) == HISTORY_LIMIT
    assert [(entry.youtube_url, entry.delivered_at) for entry in actual_deliveries] == expected_deliveries
    assert table_counts(session.connection)["credit_ledger"] == 0


def test_explicit_outer_rollback_removes_all_rows(repository_mysql_session):
    """@description: Prove the success-path savepoint never commits pending fixture rows.
    @param repository_mysql_session: Rollback-only SQL acceptance session.
    @return: None.
    """
    session = repository_mysql_session
    session.repository.submit_pending(session.discord_user_id, session.video)
    session.repository.set_daily(session.discord_user_id, True)
    assert table_counts(session.connection)["submissions"] == 1
    session.connection.rollback()
    require_empty_tables(table_counts(session.connection))


def _song_row(session) -> dict:
    """@description: Inspect the precise canonical row and preserved verification fields.
    @param session: Validated rollback-only acceptance session.
    @return: Full song row for this synthetic video.
    """
    return select_rows(session.connection, "SELECT * FROM songs WHERE platform = %s AND platform_track_id = %s", ("youtube", session.video.video_id))[0]


def _violate_balance_check(cursor, discord_user_id: int, canonical_song_id: int) -> bool:
    """@description: Produce a real schema error after partial repository inserts without new DDL.
    @param cursor: Current operation's owned cursor.
    @param discord_user_id: Fixture account whose nonnegative balance CHECK must reject the update.
    @param canonical_song_id: Already inserted song proving the operation has partial writes.
    @return: No normal result; a CHECK error must propagate.
    """
    cursor.execute("UPDATE users SET credit_balance = %s WHERE discord_user_id = %s", (-1, discord_user_id))
    raise AssertionError("Existing balance CHECK failed to reject negative fixture balance")


def _seed_history(session) -> list[tuple]:
    """@description: Build synthetic delivered rows with time ties and newer excluded records.
    @param session: Worker-owned fixture session.
    @return: Exact expected newest-first bounded history.
    """
    delivered_history = []  # [expected ordering]: Insert order supplies the recommendation-ID tie breaker.
    for sequence in range(HISTORY_LIMIT + 3):
        video = new_video()
        delivered_at = FIXTURE_DELIVERY_TIME + timedelta(seconds=sequence // 2)
        _insert_delivery(session, session.discord_user_id, video, "delivered", delivered_at)
        delivered_history.append((video.canonical_url, delivered_at))
    _insert_delivery(session, session.discord_user_id, new_video(), "reserved", None)
    _insert_delivery(session, session.alternate_user_id, new_video(), "delivered", FIXTURE_DELIVERY_TIME + timedelta(days=1))
    return list(reversed(delivered_history))[:HISTORY_LIMIT]


def _insert_delivery(session, discord_user_id: int, video, status: str, delivered_at: datetime | None) -> None:
    """@description: Seed schema-compatible recommendation fixtures without pretending to deliver externally.
    @param session: Validated rollback-only test session.
    @param discord_user_id: Synthetic recommendation recipient.
    @param video: Synthetic parsed target.
    @param status: Fixture recommendation state.
    @param delivered_at: Fixture UTC timestamp, or None for reserved state.
    @return: None.
    """
    session.repository.submit_pending(discord_user_id, video)
    execute_statement(session.connection,
        "UPDATE songs SET resolution_status = %s, youtube_url = %s, verification_provenance = %s WHERE platform_track_id = %s",
        ("verified", video.canonical_url, "synthetic acceptance fixture", video.video_id))
    canonical_song_id = select_rows(session.connection, "SELECT song_id FROM songs WHERE platform_track_id = %s", (video.video_id,))[0]["song_id"]
    execute_statement(session.connection,
        "INSERT INTO recommendations (discord_user_id, song_id, status, delivered_at) VALUES (%s, %s, %s, %s)",
        (discord_user_id, canonical_song_id, status, delivered_at))
