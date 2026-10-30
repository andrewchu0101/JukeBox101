"""Inspect application SQL and transaction lifecycle through the existing driver boundary.

Mocked connections expose parameters and failure cleanup without evaluating real MySQL behavior.
"""

from unittest.mock import MagicMock
from functools import partial

import psycopg
from psycopg import errors as pg_errors
import pytest

from bot.database import connection as database_boundary
from bot.database.repository import MusicRepository
from bot.models import HISTORY_LIMIT
from bot.urls import parse_youtube_url


@pytest.fixture
def repository_context(database_settings, monkeypatch):
    """@description: Replace only connection creation so production transaction cleanup is tested.
    @param database_settings: Synthetic environment configuration.
    @param monkeypatch: Connection injection facility.
    @return: Repository, owned mock connection and transaction cursor.
    """
    owned_connection = MagicMock()
    monkeypatch.setattr(database_boundary, "open_connection", MagicMock(return_value=owned_connection))
    return MusicRepository(database_settings), owned_connection, owned_connection.cursor.return_value.__enter__.return_value


@pytest.mark.parametrize("existing_submission", [None, {"submission_id": 9}])
def test_pending_submission_transaction(repository_context, existing_submission):
    """@description: Serialize duplicate attempts without writing a ledger or overwriting provenance.
    @param repository_context: Mock repository and transaction resources.
    @param existing_submission: Evidence of a previous attempt by the same user.
    @return: None.
    """
    repository, connection, cursor = repository_context
    cursor.fetchone.side_effect = [{"song_id": 7, "resolution_status": "pending"}, existing_submission]
    video = parse_youtube_url("https://youtu.be/Abc_123-Xyz?si=source")
    result = repository.submit_pending(123, video)
    assert result.duplicate_submission == (existing_submission is not None)
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert "FOR UPDATE" in statements[2]
    assert "ON CONFLICT (platform, platform_track_id) DO NOTHING" in statements[1]  # [first provenance kept]: A duplicate video never overwrites the stored row.
    assert cursor.execute.call_args_list[1].args[1] == ("youtube", video.video_id, video.source_url)
    assert all("credit_ledger" not in statement and "credit_balance" not in statement for statement in statements)
    assert len(statements) == (4 if existing_submission else 5)
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.parametrize("account, expected", [(None, 0), ({"credit_balance": 4}, 4)])
def test_balance_missing_or_existing(repository_context, account, expected):
    """@description: Return zero only for an absent account, using parameterized identity.
    @param repository_context: Mock repository and resources.
    @param account: Successful query row or absence.
    @param expected: Correct balance for the successful query.
    @return: None.
    """
    repository, connection, cursor = repository_context
    cursor.fetchone.return_value = account
    assert repository.balance(123) == expected
    assert cursor.execute.call_args.args[1] == (123,)
    connection.close.assert_called_once()


@pytest.mark.parametrize("operation", ["share", "balance", "daily", "history"])
def test_query_failure_rolls_back(repository_context, operation):
    """@description: Fail entire operations and close resources on SQL exceptions.
    @param repository_context: Mock repository and resources.
    @param operation: Application transaction to fail at its first SQL statement.
    @return: None.
    """
    repository, connection, cursor = repository_context
    cursor.execute.side_effect = pg_errors.LockNotAvailable("Synthetic lock timeout")
    operations = {
        "share": partial(repository.submit_pending, 123, parse_youtube_url("https://youtu.be/Abc_123-Xyz")),
        "balance": partial(repository.balance, 123), "daily": partial(repository.set_daily, 123, True),
        "history": partial(repository.history, 123),
    }
    with pytest.raises(psycopg.OperationalError):
        operations[operation]()
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    connection.close.assert_called_once()


def test_history_order_and_recipient_scope(repository_context):
    """@description: Limit history to delivered rows with a stable recipient-specific ordering.
    @param repository_context: Mock repository and resources.
    @return: None.
    """
    repository, connection, cursor = repository_context
    cursor.fetchall.return_value = []
    assert repository.history(123) == []
    query, parameters = cursor.execute.call_args.args
    assert "r.discord_user_id = %s AND r.status = %s" in query
    assert "ORDER BY r.delivered_at DESC, r.recommendation_id DESC LIMIT %s" in query
    assert parameters == (123, "delivered", HISTORY_LIMIT)
    assert "source_url" not in query and "submission" not in query


@pytest.mark.parametrize("enabled", [True, False])
def test_daily_updates_only_preference(repository_context, enabled):
    """@description: Ensure repeated preferences cannot overwrite balance or create dispatches.
    @param repository_context: Mock repository and resources.
    @param enabled: Requested final preference.
    @return: None.
    """
    repository, connection, cursor = repository_context
    repository.set_daily(123, enabled)
    assert cursor.execute.call_args.args == ("UPDATE users SET daily_enabled = %s WHERE discord_user_id = %s", (enabled, 123))
    assert all("credit_balance" not in call.args[0] and "daily_dispatches" not in call.args[0] for call in cursor.execute.call_args_list)
    connection.commit.assert_called_once()
