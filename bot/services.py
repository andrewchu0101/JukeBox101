"""Translate independent application operations into safe user-visible outcomes.

Injected synchronous persistence runs outside the event loop; unapproved award and delivery rules stay gated.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import timezone
from typing import TypeVar

import pymysql

from bot.database.repository import MusicRepository
from bot.models import DeliveryHistoryEntry, HISTORY_LIMIT, MAX_REPLY_LENGTH
from bot.urls import InvalidVideoUrl, parse_youtube_url

OperationResult = TypeVar("OperationResult")
LOGGER = logging.getLogger(__name__)


class ServiceUnavailable(RuntimeError):
    """Expose a generic retryable failure without retaining credential-bearing messages."""


class MusicService:
    """Coordinate command use cases with an injected worker-local repository."""

    def __init__(self, repository: MusicRepository) -> None:
        """@description: Keep Discord-independent business operations injectable for tests.
        @param self: Application service instance.
        @param repository: Synchronous persistence collaborator, including test replacements.
        @return: None.
        """
        self.repository = repository  # [persistence boundary]: Complete operations run in workers.

    async def share(self, discord_user_id: int, source_url: str) -> str:
        """@description: Save a pending attempt while withholding unapproved music verification.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Submitter's Discord identity.
        @param source_url: YouTube URL to validate before any database work.
        @return: Honest persisted outcome that never promises an award.
        """
        video = parse_youtube_url(source_url)
        submission = await _database_operation(self.repository.submit_pending, discord_user_id, video)
        if submission.duplicate_submission:
            return "你已投稿過這部影片，本次不會重複發放點數。"
        if submission.resolution_status != "pending":
            return "這部影片已存在於目錄，已記錄你的投稿，本次不發放點數。"
        return "已記錄投稿，待歌曲資格驗證；目前不發放點數，也不加入交換池。"

    async def exchange(self, discord_user_id: int) -> str:
        """@description: Protect credits until eligibility and delivery recovery rules are approved.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Recipient requesting an exchange.
        @return: Explicit pending outcome without reservation, debit, or delivery.
        """
        return "交換功能待歌曲資格驗證與派送恢復流程完成後開放；本次未扣點。"

    async def balance(self, discord_user_id: int) -> str:
        """@description: Report stored spendable credits without masking database failures.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Recipient requesting their balance.
        @return: Private balance display.
        """
        # [spendable balance]: Only a successful repository query may produce a credit amount.
        available_exchange_credits = await _database_operation(self.repository.balance, discord_user_id)
        return f"目前可用交換點數：{available_exchange_credits} 點。"

    async def daily(self, discord_user_id: int, enabled: bool) -> str:
        """@description: Save the desired opt-in state while clearly identifying inactive scheduling.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Recipient changing their preference.
        @param enabled: Desired final daily delivery preference.
        @return: Confirmation of persisted preference and scheduler limitation.
        """
        await _database_operation(self.repository.set_daily, discord_user_id, enabled)
        preference_label = "開啟" if enabled else "關閉"
        return f"已{preference_label}每日推薦偏好。每日排程尚未啟用，目前不會自動發送 DM。"

    async def history(self, discord_user_id: int) -> str:
        """@description: Format bounded successful history without exposing submitter information.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Recipient requesting their history.
        @return: Safe message within the Discord reply limit.
        """
        deliveries = await _database_operation(self.repository.history, discord_user_id)
        if not deliveries:
            return "目前沒有成功取得的推薦紀錄。"
        lines = ["最近成功取得的推薦（UTC）："]
        for delivery in deliveries[:HISTORY_LIMIT]:
            history_line = _history_line(delivery)
            if len("\n".join(lines)) + len(history_line) + 1 > MAX_REPLY_LENGTH:
                break
            lines.append(history_line)
        return "\n".join(lines)


async def _database_operation(operation: Callable[..., OperationResult], *arguments) -> OperationResult:
    """@description: Keep blocking connections off the event loop and sanitize database failures.
    @param operation: Complete synchronous repository transaction.
    @param arguments: Validated business inputs for that operation.
    @return: Successful result; database errors become a safe service exception.
    """
    try:
        return await asyncio.to_thread(operation, *arguments)
    except pymysql.MySQLError as database_error:
        LOGGER.error("Database operation failed: %s", type(database_error).__name__)
    raise ServiceUnavailable("資料服務暫時無法使用，請稍後再試。")


def _history_line(delivery: DeliveryHistoryEntry) -> str:
    """@description: Prevent stored untrusted URLs from becoming unsafe history content.
    @param delivery: Recipient's persisted successful delivery record.
    @return: UTC timestamp and validated canonical URL, or an unavailable marker.
    """
    try:
        canonical_url = parse_youtube_url(delivery.youtube_url).canonical_url
    except (InvalidVideoUrl, TypeError):
        canonical_url = "網址待確認"
    delivered_at = delivery.delivered_at.replace(tzinfo=timezone.utc)
    return f"{delivered_at:%Y-%m-%d %H:%M:%S} · {canonical_url}"
