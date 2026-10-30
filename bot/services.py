"""Translate independent application operations into safe user-visible outcomes.

Injected synchronous persistence runs outside the event loop; unapproved award and delivery rules stay gated.
"""

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, TypeVar

from bot.auto_pipeline import format_share_outcome, reward_label
from bot.database.errors import DatabaseError
from bot.database.nickel_grant_store import ClaimResult, ClaimStatus, claim_nickel_grants
from bot.nickel_claim_replies import format_claim_reply
from bot.database.repository import MusicRepository
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown, PersistenceNotReady
from bot.exchange_delivery import ExchangeFlowResult, exchange_and_deliver
from bot.models import DeliveryHistoryEntry, HISTORY_LIMIT, MAX_REPLY_LENGTH
from bot.urls import InvalidVideoUrl, parse_youtube_url

OperationResult = TypeVar("OperationResult")
LOGGER = logging.getLogger(__name__)
SYNTHETIC_LABEL = "【合成測試】"  # [acceptance marker]: Every synthetic reply and DM carries it; production text never does.


class _LabelledSender:
    """Prefix every direct message of the synthetic acceptance flow with the synthetic label."""

    def __init__(self, inner: Any) -> None:
        """@description: Wrap the real or mock DM adapter.
        @param self: Labelling wrapper.
        @param inner: Direct-message adapter.
        @return: None.
        """
        self._inner = inner

    async def send(self, discord_user_id: int, text: str) -> Any:
        """@description: Send the labelled text through the wrapped adapter.
        @param self: Labelling wrapper.
        @param discord_user_id: Recipient.
        @param text: Message body.
        @return: The inner adapter's classified result.
        """
        return await self._inner.send(discord_user_id, SYNTHETIC_LABEL + text)


class _LabelledReplier:
    """Prefix every ephemeral reply of the synthetic acceptance flow with the synthetic label."""

    def __init__(self, inner: Any) -> None:
        """@description: Wrap the real or mock ephemeral adapter.
        @param self: Labelling wrapper.
        @param inner: Ephemeral reply adapter.
        @return: None.
        """
        self._inner = inner

    async def send_ephemeral(self, text: str) -> None:
        """@description: Send the labelled reply through the wrapped adapter.
        @param self: Labelling wrapper.
        @param text: Reply body.
        @return: None.
        """
        await self._inner.send_ephemeral(SYNTHETIC_LABEL + text)


class ServiceUnavailable(RuntimeError):
    """Expose a generic retryable failure without retaining credential-bearing messages."""


class MusicService:
    """Coordinate command use cases with an injected worker-local repository."""

    def __init__(self, repository: MusicRepository, submission_repository: SubmissionRepository | None = None,
                 synthetic_exchange: bool = False, formal_exchange: bool = False,
                 nickel_claim_enabled: bool = False, nickel_connection_factory=None, auto_pipeline: Any | None = None) -> None:
        """@description: Keep Discord-independent business operations injectable for tests.
        @param self: Application service instance.
        @param repository: Synchronous persistence collaborator, including test replacements.
        @param submission_repository: Optional Batch B public intake boundary; mandatory at private startup.
        @param synthetic_exchange: Explicit acceptance-only switch that routes /exchange to the labelled synthetic flow; never set by production startup.
        @param formal_exchange: Formal /exchange switch, off by default; only an exact True opens the unlabelled production route.
        @param nickel_claim_enabled: /claim switch, off by default; when off, /claim never opens a connection.
        @param nickel_connection_factory: Callable returning a fresh connection for one claim transaction; required only when the switch is on.
        @param auto_pipeline: Automatic review and settlement coordinator, or None while those modes are off.
        @return: None.
        """
        self.auto_pipeline = auto_pipeline  # [automatic modes]: None keeps /share and /history replies unchanged.
        self.nickel_claim_enabled = nickel_claim_enabled is True  # [/claim switch]: Only an exact True opens the grant route.
        self._nickel_connection_factory = nickel_connection_factory  # [claim connection]: One fresh connection per claim, closed after it.
        self.submission_repository = submission_repository  # [Beta persistence]: Never instantiate a second service or award worker.
        self.repository = repository  # [persistence boundary]: Complete operations run in workers.
        self._synthetic_exchange = synthetic_exchange  # [explicit acceptance route]: Off unless the caller selects it.
        self._formal_exchange = formal_exchange  # [formal route switch]: Off unless the deployment setting explicitly enables it.

    @property
    def synthetic_exchange_enabled(self) -> bool:
        """@description: Tell the Discord layer whether /exchange is routed to the labelled synthetic acceptance flow.
        @param self: Application service instance.
        @return: True only for an explicitly selected synthetic acceptance service.
        """
        return self._synthetic_exchange and self.submission_repository is not None

    @property
    def formal_exchange_enabled(self) -> bool:
        """@description: Tell the Discord layer whether /exchange runs the production delivery flow; off unless explicitly enabled and a repository exists.
        @param self: Application service instance.
        @return: True only when the formal switch is exactly True and a submission repository is configured.
        """
        return self._formal_exchange is True and self.submission_repository is not None

    async def synthetic_exchange(self, discord_user_id: int, request_key: str, sender: Any, replier: Any) -> Any:
        """@description: Run the labelled synthetic acceptance flow through the shared delivery program, labelling every message and reply.
        @param self: Application service with the explicit synthetic route enabled.
        @param discord_user_id: Requesting user.
        @param request_key: Technical idempotency key for retries.
        @param sender: Direct-message adapter, awaited on the event loop.
        @param replier: Ephemeral reply adapter, awaited on the event loop.
        @return: Flow result whose reply text starts with the synthetic label.
        """
        if not self.synthetic_exchange_enabled:
            raise ServiceUnavailable("交換功能尚未開放。")
        return await self._run_exchange(discord_user_id, request_key, _LabelledSender(sender), _LabelledReplier(replier), SYNTHETIC_LABEL)

    async def formal_exchange(self, discord_user_id: int, request_key: str, sender: Any, replier: Any) -> ExchangeFlowResult:
        """@description: Run the production /exchange request through the same shared delivery program, without any synthetic label; refused unless the formal switch is on.
        @param self: Application service instance.
        @param discord_user_id: Requesting user.
        @param request_key: Technical idempotency key for retries.
        @param sender: Direct-message adapter, awaited on the event loop.
        @param replier: Ephemeral reply adapter, awaited on the event loop.
        @return: Flow result of the production delivery program.
        """
        if not self.formal_exchange_enabled:
            raise ServiceUnavailable("交換功能尚未開放。")
        return await self._run_exchange(discord_user_id, request_key, sender, replier, "")

    async def _run_exchange(self, discord_user_id: int, request_key: str, sender: Any, replier: Any, label: str) -> ExchangeFlowResult:
        """@description: Run one exchange request through the shared delivery program and map its persistence failures to fixed replies; only the label differs between routes.
        @param self: Application service with a configured submission repository.
        @param discord_user_id: Requesting user.
        @param request_key: Technical idempotency key for retries.
        @param sender: Direct-message adapter already wrapped by the caller's route.
        @param replier: Ephemeral reply adapter already wrapped by the caller's route.
        @param label: Prefix for every reply text; empty for the formal route.
        @return: Flow result whose reply text carries the label prefix.
        """
        try:
            flow = await exchange_and_deliver(self.submission_repository, sender, replier, discord_user_id, request_key,
                                              random.Random(), datetime.now(timezone.utc))
            return replace(flow, reply_text=label + flow.reply_text)  # [labelled result]: The prefix is applied to the returned text too.
        except CommitOutcomeUnknown:
            LOGGER.error("Exchange commit outcome is unknown")  # [no payload]: Class-level only.
            return ExchangeFlowResult(label + "交換結果未確認，請勿重新執行 /exchange；稍後以 /history 查詢。", False, None, None, None)
        except (DatabaseError, PersistenceNotReady):
            LOGGER.error("Exchange stopped at persistence boundary")  # [no payload]: Class-level only.
            return ExchangeFlowResult(label + "資料服務暫時無法使用，本次交換未完成，請稍後再試。", False, None, None, None)

    async def share(self, discord_user_id: int, source_url: str, operation_id: str | None = None) -> str:
        """@description: Save a pending attempt while withholding unapproved music verification.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Submitter's Discord identity.
        @param source_url: YouTube URL to validate before any database work.
        @param operation_id: Server-assigned stable interaction key required by private intake.
        @return: Honest persisted outcome that never promises an award.
        """
        if self.submission_repository is not None:
            return await self._share_pending(discord_user_id, source_url, operation_id)
        video = parse_youtube_url(source_url)
        submission = await _database_operation(self.repository.submit_pending, discord_user_id, video)
        if submission.duplicate_submission:
            return "你已投稿過這部影片，本次投稿不會重複發放 Nickel。"
        if submission.resolution_status != "pending":
            return "這部影片已存在於目錄，已記錄你的投稿；本次投稿不發放 Nickel。"
        return "已記錄投稿，待歌曲資格驗證；目前不發放 Nickel，也不加入交換池。"

    async def _share_pending(self, discord_user_id: int, source_url: str, operation_id: str | None) -> str:
        """@description: Return durable receipt facts without retrying an uncertain write under a new key.
        @param self: Existing application service with Batch B persistence.
        @param discord_user_id: Authenticated submitter.
        @param source_url: Untrusted URL accepted only by the shared parser.
        @param operation_id: Stable Discord interaction identity, never generated randomly here.
        @return: Pending, replay or explicitly uncertain result; no Nickel is issued.
        """
        if operation_id is None:
            raise ServiceUnavailable("缺少投稿操作識別碼；本次未嘗試寫入。")
        try:
            result = await _database_operation(self.submission_repository.accept_pending,
                                              discord_user_id, source_url, operation_id)
        except CommitOutcomeUnknown:
            return f"投稿提交結果未知；操作 {operation_id}。請先用 /history 查詢並聯絡 Owner 核對，勿重新投稿。"
        if self.auto_pipeline is not None:
            return await self._automatic_reply(operation_id)
        if not result.create_share_event:
            return f"重複操作，已取回原投稿回執；操作 {operation_id}。本次沒有新增投稿或發放 Nickel。"
        status = result.candidate.review.status.value  # [receipt state]: Existing review evidence is never reset by resubmission.
        return f"已儲存投稿；審核狀態：{status}（Pending 表示待驗證／審核）；操作 {operation_id}。本次不發放 Nickel。"

    async def _automatic_reply(self, operation_id: str) -> str:
        """@description: Run the automatic steps after the receipt committed; a failure here never undoes the accepted submission.
        @param self: Application service with the automatic pipeline.
        @param operation_id: Committed operation.
        @return: Review and reward state read back from durable facts.
        """
        try:
            outcome = await asyncio.to_thread(self.auto_pipeline.process, operation_id)
        except (DatabaseError, PersistenceNotReady, CommitOutcomeUnknown, ValueError) as error:
            LOGGER.error("Automatic review deferred after intake: %s", type(error).__name__)
            return f"已儲存投稿；自動審核暫時未完成，系統會自動重試，請勿重新投稿。目前未確認發放 Nickel；操作 {operation_id}。"
        return format_share_outcome(operation_id, outcome)

    async def exchange(self, discord_user_id: int) -> str:
        """@description: Protect credits until eligibility and delivery recovery rules are approved.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Recipient requesting an exchange.
        @return: Explicit pending outcome without reservation, debit, or delivery.
        """
        return "交換功能待歌曲資格驗證與派送恢復流程完成後開放；本次未扣除 Nickel。"

    async def claim(self, discord_user_id: int, request_key: str) -> str:
        """@description: Give the account every approved Nickel grant it is missing, in one transaction, or report that the feature is closed.
        @param self: Application service instance.
        @param discord_user_id: Discord account making the claim.
        @param request_key: Stable key of this /claim attempt; retries reuse it.
        @return: Private reply naming only the grants that were recorded.
        """
        if not self.nickel_claim_enabled:
            return format_claim_reply(ClaimResult(ClaimStatus.DISABLED, [], None))  # [closed route]: No connection is opened.
        result = await _database_operation(self._claim_transaction, discord_user_id, request_key)
        return format_claim_reply(result)

    def _claim_transaction(self, discord_user_id: int, request_key: str) -> ClaimResult:
        """@description: Run one claim in its own connection, committing on success and rolling back on any failure.
        @param self: Application service instance.
        @param discord_user_id: Discord account making the claim.
        @param request_key: Stable key of this attempt.
        @return: Claim outcome from the store.
        """
        connection = self._nickel_connection_factory()
        try:
            result = claim_nickel_grants(connection, discord_user_id, request_key, datetime.now(timezone.utc), enabled=True)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def balance(self, discord_user_id: int) -> str:
        """@description: Report stored spendable credits without masking database failures.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Recipient requesting their balance.
        @return: Private balance display.
        """
        # [spendable balance]: Only a successful repository query may produce a credit amount.
        available_exchange_credits = await _database_operation(self.repository.balance, discord_user_id)
        return f"目前可用 Nickel：{available_exchange_credits}。"

    async def daily(self, discord_user_id: int, enabled: bool) -> str:
        """@description: Save the desired opt-in state while clearly identifying inactive scheduling.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Recipient changing their preference.
        @param enabled: Desired final daily delivery preference.
        @return: Confirmation of persisted preference and scheduler limitation.
        """
        if self.submission_repository is not None:
            return "私人 Beta 尚未開放每日推薦；本次未保存偏好，也不會發送 DM。"
        await _database_operation(self.repository.set_daily, discord_user_id, enabled)
        preference_label = "開啟" if enabled else "關閉"
        return f"已{preference_label}每日推薦偏好。每日排程尚未啟用，目前不會自動發送 DM。"

    async def history(self, discord_user_id: int) -> str:
        """@description: Present caller-scoped submission history in Beta while preserving the legacy delivery reader.
        @param self: Application service coordinating persistence.
        @param discord_user_id: Recipient requesting their history.
        @return: Safe message within the Discord reply limit.
        """
        if self.submission_repository is not None:
            return await self._submission_history(discord_user_id)
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


    async def _submission_history(self, discord_user_id: int) -> str:
        """@description: Present only the caller's bounded intake and review projection.
        @param self: Existing service with the injected Batch B repository.
        @param discord_user_id: Authenticated caller whose rows may be disclosed.
        @return: Private current submission states, distinct from successful recommendation history.
        """
        submissions = await _database_operation(self.submission_repository.history, discord_user_id)
        if not submissions:
            return "目前沒有你的投稿紀錄。"
        lines = ["最近本人投稿與目前審核狀態："]
        for submission in submissions[:HISTORY_LIMIT]:
            line = _submission_history_line(submission)
            if self.auto_pipeline is not None:
                line = f"{line} · {reward_label(submission['reward_status'])}"
            if len("\n".join(lines)) + len(line) + 1 > MAX_REPLY_LENGTH:
                break
            lines.append(line)
        return "\n".join(lines)


async def _database_operation(operation: Callable[..., OperationResult], *arguments) -> OperationResult:
    """@description: Keep blocking connections off the event loop and sanitize database failures.
    @param operation: Complete synchronous repository transaction.
    @param arguments: Validated business inputs for that operation.
    @return: Successful result; database errors become a safe service exception.
    """
    try:
        return await asyncio.to_thread(operation, *arguments)
    except PersistenceNotReady:
        raise ServiceUnavailable("投稿資料庫尚未通過啟用檢查；本次未受理，請聯絡 Owner。") from None
    except DatabaseError as database_error:
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


def _submission_history_line(submission: dict) -> str:
    """@description: Render stored identifiers through validation instead of echoing private source text.
    @param submission: Caller-scoped repository projection without arbitrary review evidence.
    @return: Canonical URL and allowlisted review state with a safe operation reference.
    """
    canonical_url = parse_youtube_url("https://youtu.be/" + submission["video_id"]).canonical_url
    status = submission["review_status"]  # [public review]: Unknown database values are never echoed.
    if status not in {"pending_validation", "pending_review", "approved", "rejected", "validation_failed"}:
        status = "狀態待確認"
    operation_id = submission["operation_id"]  # [receipt reference]: Public IDs must originate from Discord snowflakes.
    if not operation_id.startswith("discord:") or not operation_id[8:].isascii() or not operation_id[8:].isdecimal():
        operation_id = "歷史操作"
    return f"{operation_id} · {canonical_url} · {status}"
