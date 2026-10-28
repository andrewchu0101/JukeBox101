"""Verify application outcomes with injected synchronous repository mocks.

Tests use asyncio's standard runner and never create real Discord or MySQL sessions.
"""

import asyncio
import threading
from datetime import datetime
from unittest.mock import MagicMock

import pymysql
import pytest

from bot.models import DeliveryHistoryEntry, HISTORY_LIMIT, MAX_REPLY_LENGTH, SubmissionResult
from bot.services import MusicService, ServiceUnavailable
from bot.urls import InvalidVideoUrl


@pytest.mark.parametrize("status, duplicate, expected", [
    ("pending", False, "待歌曲資格驗證"), ("pending", True, "已投稿過"),
    ("verified", False, "已存在"), ("rejected", False, "已存在"),
])
def test_share_never_fabricates_credit_or_verification(status, duplicate, expected):
    """@description: Gate all awards until an approved eligibility policy exists.
    @param status: Persisted catalog resolution state.
    @param duplicate: Whether this user already submitted this video.
    @param expected: Required honest outcome text.
    @return: None.
    """
    repository = MagicMock()
    repository.submit_pending.return_value = SubmissionResult(status, duplicate)
    reply = asyncio.run(MusicService(repository).share(123, "https://youtu.be/Abc_123-Xyz"))
    assert expected in reply
    assert "不" in reply and "Nickel" in reply
    repository.submit_pending.assert_called_once()
    assert repository.mock_calls[0].args[0] == 123
    assert len(repository.mock_calls) == 1


def test_invalid_share_never_calls_repository():
    """@description: Keep invalid input outside the persistence boundary.
    @param: none
    @return: None.
    """
    repository = MagicMock()
    with pytest.raises(InvalidVideoUrl):
        asyncio.run(MusicService(repository).share(123, "https://evil.example/secret"))
    assert repository.mock_calls == []


def test_exchange_gate_has_no_side_effects():
    """@description: Prevent debit or delivery while unresolved exchange requirements are pending.
    @param: none
    @return: None.
    """
    repository = MagicMock()
    reply = asyncio.run(MusicService(repository).exchange(123))
    assert "未扣除 Nickel" in reply
    assert "資格驗證" in reply and "派送恢復" in reply
    assert repository.mock_calls == []


@pytest.mark.parametrize("balance", [0, 3])
def test_balance_result(balance):
    """@description: Report the repository's actual balance for new and existing accounts.
    @param balance: Successful query result.
    @return: None.
    """
    repository = MagicMock()
    repository.balance.return_value = balance
    assert f"目前可用 Nickel：{balance}。" in asyncio.run(MusicService(repository).balance(123))
    repository.balance.assert_called_once_with(123)


def test_database_failure_is_safe_and_not_zero(caplog):
    """@description: Avoid presenting database outages as a valid empty balance.
    @param caplog: Captured diagnostic output.
    @return: None.
    """
    repository = MagicMock()
    repository.balance.side_effect = pymysql.OperationalError(2003, "synthetic-password-secret")
    with pytest.raises(ServiceUnavailable) as captured_error:
        asyncio.run(MusicService(repository).balance(123))
    assert captured_error.value.__context__ is None
    assert "secret" not in str(captured_error.value) + caplog.text
    assert "OperationalError" in caplog.text


@pytest.mark.parametrize("enabled", [True, False])
def test_daily_persists_explicit_preference(enabled):
    """@description: Persist on/off while disclosing that scheduling remains inactive.
    @param enabled: Final daily preference.
    @return: None.
    """
    repository = MagicMock()
    service = MusicService(repository)
    first_reply = asyncio.run(service.daily(123, enabled))
    assert first_reply == asyncio.run(service.daily(123, enabled))
    assert "不會自動發送 DM" in first_reply
    assert repository.set_daily.call_count == 2
    repository.set_daily.assert_called_with(123, enabled)


def test_empty_history():
    """@description: Distinguish successful empty history from an infrastructure failure.
    @param: none
    @return: None.
    """
    repository = MagicMock()
    repository.history.return_value = []
    assert "沒有" in asyncio.run(MusicService(repository).history(123))
    repository.history.assert_called_once_with(123)


def test_history_is_bounded_and_sanitized():
    """@description: Keep stored unsafe content and excessive history out of Discord replies.
    @param: none
    @return: None.
    """
    repository = MagicMock()
    repository.history.return_value = [DeliveryHistoryEntry("https://evil.example/@everyone", datetime(2026, 1, 1))] * 20
    reply = asyncio.run(MusicService(repository).history(123))
    assert len(reply) <= MAX_REPLY_LENGTH
    assert reply.count("網址待確認") == HISTORY_LIMIT
    assert "@everyone" not in reply and "evil.example" not in reply
    assert "UTC" in reply


def test_repository_runs_in_worker_thread():
    """@description: Verify blocking repository work leaves the event-loop thread.
    @param: none
    @return: None.
    """
    event_loop_thread_id = threading.get_ident()
    repository = MagicMock()
    repository.balance.side_effect = _worker_thread_identity
    worker_reply = asyncio.run(MusicService(repository).balance(123))
    assert str(event_loop_thread_id) not in worker_reply


def _worker_thread_identity(discord_user_id: int) -> int:
    """@description: Observe repository execution context without establishing connections.
    @param discord_user_id: Synthetic recipient passed through the service boundary.
    @return: Thread identifier of the synchronous operation.
    """
    return threading.get_ident()
