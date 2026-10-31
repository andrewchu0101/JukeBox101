"""Run metadata lookup, automatic review and reward settlement for accepted submissions, outside the Discord event loop.

Inputs are the submission repository, a metadata provider and the configured allowlist; outputs are review and ledger changes made only through audited repository transactions.
The provider is always called with no transaction open. Every step can be repeated: a restart or lost acknowledgement resumes from durable state and never pays twice.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bot.auto_review import AutoReviewKind, decide_auto_review
from bot.database.reward_settlement import LedgerBalanceMismatch
from bot.database.submission_transaction import CommitOutcomeUnknown, PersistenceNotReady
from bot.database.errors import DatabaseError
from bot.review import ReviewStatus
from bot.reward_candidates import RewardCandidateStatus, RewardDecision, RewardDecisionReason

LOGGER = logging.getLogger(__name__)  # [diagnostics]: Class names and counts only.
COMMIT_REPLAYS = 3  # [unknown commit bound]: The same key is replayed at most this many times per step.
SWEEP_LOOKUP_LIMIT = 20  # [quota bound]: Provider calls per sweep.
SWEEP_CANDIDATE_LIMIT = 200  # [read bound]: Pending operations examined per sweep.
RETRY_BACKOFF_SECONDS = 1800.0  # [held-case pause]: A held or failed lookup is not asked again sooner than this.
MAX_TRACKED_BACKOFFS = 1000  # [memory bound]: Older in-memory pauses are dropped; losing one only costs one quota unit.
PURGE_INTERVAL_SECONDS = 86400.0  # [retention cadence]: One purge run per day, plus one at startup before any lookup; a failed run is retried at the next sweep.
STOP_LOOKUPS_AFTER_OVERDUE_DAYS = 3.0  # [overdue escalation]: Past this much retention debt no new official values are fetched or stored.
SWEEP_ERRORS = (DatabaseError, PersistenceNotReady, CommitOutcomeUnknown, LedgerBalanceMismatch, PermissionError, ValueError)  # [isolated step]: One case never stops the others.


@dataclass(frozen=True)
class PipelineOutcome:
    """Report what is durably known about one operation after the pipeline ran."""

    review_status: ReviewStatus  # [song state]: Current review of the operation's video.
    decision: RewardDecision  # [reward state]: Recomputed from committed facts.
    retry_later: bool = False  # [automatic retry]: The provider failed and the sweep will ask again.
    settlement_unknown: bool = False  # [uncertain money]: Repeated lost acknowledgements; the receipt decides later.
    settlement_enabled: bool = False  # [money switch]: Whether this process was allowed to settle, so a READY reply can describe the real cause.


class AutoPipeline:
    """Coordinate one operation or a whole sweep; holds no business state beyond a bounded in-memory retry pause."""

    def __init__(self, repository: Any, metadata_client: Any | None, trusted_channel_ids: frozenset[str], *,
                 settlement_enabled: bool, api_retention: bool = False, monotonic: Callable[[], float] = time.monotonic) -> None:
        """@description: Bind collaborators without any I/O.
        @param self: New pipeline.
        @param repository: Submission repository owning every transaction.
        @param metadata_client: Official provider, or None when automatic review is switched off.
        @param trusted_channel_ids: Owner-approved stable channel IDs; may be empty.
        @param settlement_enabled: Deployment switch; False never writes a reward.
        @param api_retention: Deployment switch; True keeps official values only in the short-lived table and runs the purge.
        @param monotonic: Time source for the retry pause.
        @return: None.
        """
        self._repository = repository  # [write boundary]: Every change goes through its audited transactions.
        self._metadata_client = metadata_client  # [official source]: Called outside transactions only.
        self._trusted_channel_ids = trusted_channel_ids  # [allowlist]: Empty means nothing is automatically approved.
        self._settlement_enabled = settlement_enabled is True  # [money switch]: Only an exact True may pay.
        self._api_retention = api_retention is True  # [API data lifecycle]: Only an exact True changes where official values are stored.
        self._next_purge_at = 0.0  # [purge schedule]: Zero means the next sweep purges first, which is also the startup purge.
        self._lookups_stopped = False  # [overdue stop]: True while the purge is so far behind that no new official value may be fetched.
        self._monotonic = monotonic  # [pause clock]: Injected for tests.
        self._paused_until: dict[str, float] = {}  # [lookup pause]: Video to earliest next lookup; lost on restart by design.

    def process(self, operation_id: str) -> PipelineOutcome:
        """@description: Bring one accepted operation as far as current facts allow, then read back its durable state.
        @param self: Pipeline.
        @param operation_id: Accepted operation.
        @return: Review and reward outcome; an award is reported only when a committed receipt exists.
        """
        retry_later = self._review_step(operation_id)
        unknown = self.settle_ready()
        return self._outcome(operation_id, retry_later, unknown)

    def sweep(self) -> int:
        """@description: Resume everything durable state says is unfinished, which is also the restart recovery path.
        @param self: Pipeline.
        @return: Number of operations whose review step ran.
        """
        reviewed = 0
        self.maintain_retention()
        paused_video_ids = self._paused_video_ids()
        for operation_id, video_id in self._repository.pending_validation_operations(SWEEP_CANDIDATE_LIMIT, paused_video_ids):
            if reviewed >= SWEEP_LOOKUP_LIMIT or self._metadata_client is None or self._lookups_stopped:
                break
            if self._monotonic() < self._paused_until.get(video_id, 0.0):
                continue
            reviewed += 1
            self._guarded_review(operation_id)
        reviewed += self._revalidate_expired(SWEEP_LOOKUP_LIMIT - reviewed)
        self.settle_ready()
        return reviewed

    def purge_expired(self) -> int:
        """@description: Clear every expired or completed API observation now, so no official value outlives its window.
        @param self: Pipeline.
        @return: Number of observations cleared; zero when the purge failed, which the next sweep retries.
        """
        try:
            cleared = self._repository.purge_api_observations()
        except SWEEP_ERRORS as error:
            LOGGER.error("API retention purge failed: %s", type(error).__name__)
            return 0
        self._next_purge_at = self._monotonic() + PURGE_INTERVAL_SECONDS
        return cleared

    def maintain_retention(self) -> None:
        """@description: Purge on schedule and stop new lookups while the purge is far behind, using the database clock for the debt.
        @param self: Pipeline.
        @return: None.
        """
        if not self._api_retention:
            return
        if self._monotonic() >= self._next_purge_at:
            self.purge_expired()
        try:
            status = self._repository.api_retention_status()
            overdue = None if status is None else status.oldest_overdue_days
        except SWEEP_ERRORS as error:
            LOGGER.error("API retention status unreadable, stopping new lookups: %s", type(error).__name__)
            self._lookups_stopped = True
            return
        self._lookups_stopped = overdue is not None and overdue >= STOP_LOOKUPS_AFTER_OVERDUE_DAYS

    def _revalidate_expired(self, budget: int) -> int:
        """@description: Ask the provider again only for pending reviews whose sole duration basis has expired, within the sweep's lookup budget.
        @param self: Pipeline.
        @param budget: Lookups still available in this sweep.
        @return: Number of lookups made.
        """
        if not self._api_retention or self._metadata_client is None or self._lookups_stopped or budget <= 0:
            return 0
        asked = 0
        for operation_id, video_id in self._repository.revalidation_operations(SWEEP_CANDIDATE_LIMIT, self._paused_video_ids()):
            if asked >= budget:
                break
            asked += 1
            self._guarded_revalidation(operation_id, video_id)
        return asked

    def _guarded_revalidation(self, operation_id: str, video_id: str) -> None:
        """@description: Refresh one expired conclusion's values, isolating its failure from the other cases.
        @param self: Pipeline.
        @param operation_id: Pending operation resting on an expired conclusion.
        @param video_id: Its video.
        @return: None.
        """
        try:
            verdict = decide_auto_review(self._metadata_client.lookup(video_id), self._trusted_channel_ids)
            if verdict.kind in {AutoReviewKind.RETRY, AutoReviewKind.HOLD, AutoReviewKind.UNAVAILABLE, AutoReviewKind.DURATION_REJECT}:
                self._pause(video_id)  # [no refresh]: Only a confirmed pass renews the values; anything else leaves the case for the Owner.
                return
            self._replaying(lambda: self._repository.refresh_api_observation(operation_id, verdict))
        except SWEEP_ERRORS as error:
            LOGGER.error("Revalidation deferred one operation: %s", type(error).__name__)

    def settle_ready(self) -> bool:
        """@description: Settle every candidate currently READY, in acceptance order, each in its own transaction, so a resolved predecessor wakes those behind it.
        @param self: Pipeline.
        @return: True when at least one award's commit stayed unconfirmed.
        """
        if not self._settlement_enabled:
            return False
        unknown = False
        for operation_id in self._replaying(self._repository.ready_operations):
            try:
                self._replaying(lambda selected=operation_id: self._repository.settle(selected))
            except CommitOutcomeUnknown:
                LOGGER.error("Settlement commit outcome stayed unknown after replays")
                unknown = True
            except (LedgerBalanceMismatch, PermissionError) as error:
                LOGGER.error("Settlement refused one operation: %s", type(error).__name__)
        return unknown

    def owner_settlement_note(self, operation_id: str) -> str:
        """@description: Run settlement after an Owner confirmation and describe this operation's reward state in the Owner's reply.
        @param self: Pipeline.
        @param operation_id: Operation the Owner just decided.
        @return: Traditional Chinese note stating what really happened.
        """
        unknown = self.settle_ready()
        return _reward_text(self._outcome(operation_id, False, unknown), third_person=True)

    def _guarded_review(self, operation_id: str) -> None:
        """@description: Isolate one sweep step so its failure leaves the remaining cases running.
        @param self: Pipeline.
        @param operation_id: Pending operation.
        @return: None.
        """
        try:
            self._review_step(operation_id)
        except SWEEP_ERRORS as error:
            LOGGER.error("Automatic review deferred one operation: %s", type(error).__name__)

    def _review_step(self, operation_id: str) -> bool:
        """@description: Look up official metadata with no transaction open, then apply the verdict in a new short one.
        @param self: Pipeline.
        @param operation_id: Accepted operation.
        @return: True when the provider gave no usable answer and a later sweep should ask again.
        """
        if self._metadata_client is None or self._lookups_stopped:
            return False
        subject = self._replaying(lambda: self._repository.auto_review_subject(operation_id))
        if not subject.awaits_validation:
            return False
        verdict = decide_auto_review(self._metadata_client.lookup(subject.video_id), self._trusted_channel_ids)
        if verdict.kind in {AutoReviewKind.RETRY, AutoReviewKind.HOLD}:
            self._pause(subject.video_id)
            return verdict.kind is AutoReviewKind.RETRY
        self._replaying(lambda: self._apply(operation_id, subject.revision, verdict))
        return False

    def _apply(self, operation_id: str, revision: int, verdict) -> Any:
        """@description: Apply a verdict, naming the retention mode only when it is on so the previous repository contract stays unchanged.
        @param self: Pipeline.
        @param operation_id: Accepted operation.
        @param revision: Revision the lookup was made for.
        @param verdict: Verdict from the policy.
        @return: Review after the call.
        """
        if self._api_retention:
            return self._repository.apply_auto_review(operation_id, revision, verdict, retain_api_values=True)
        return self._repository.apply_auto_review(operation_id, revision, verdict)

    def _paused_video_ids(self) -> tuple[str, ...]:
        """@description: Name the videos still inside their pause, so the sweep's read window only holds videos it can look up.
        @param self: Pipeline.
        @return: Paused video identifiers at this moment.
        """
        now = self._monotonic()
        return tuple(video_id for video_id, until in self._paused_until.items() if now < until)

    def _pause(self, video_id: str) -> None:
        """@description: Avoid spending quota on the same held video at every sweep, keeping the pause table bounded.
        @param self: Pipeline.
        @param video_id: Video just asked about.
        @return: None.
        """
        if len(self._paused_until) >= MAX_TRACKED_BACKOFFS:
            self._paused_until.clear()
        self._paused_until[video_id] = self._monotonic() + RETRY_BACKOFF_SECONDS

    def _replaying(self, step: Callable[[], Any]) -> Any:
        """@description: Repeat a step under its own durable key when a commit acknowledgement is lost.
        @param self: Pipeline.
        @param step: Idempotent repository call.
        @return: The step's result.
        """
        for _ in range(COMMIT_REPLAYS - 1):
            try:
                return step()
            except CommitOutcomeUnknown:
                LOGGER.warning("Replaying one step after an unknown commit")
        return step()

    def _outcome(self, operation_id: str, retry_later: bool, unknown: bool) -> PipelineOutcome:
        """@description: Read the durable reward decision after every step, so the reply can only state committed facts.
        @param self: Pipeline.
        @param operation_id: Accepted operation.
        @param retry_later: The provider asked for another attempt.
        @param unknown: An award commit stayed unconfirmed.
        @return: Outcome.
        """
        review_status, decision = self._replaying(lambda: self._repository.reward_state(operation_id))
        return PipelineOutcome(review_status, decision, retry_later, unknown and decision.status is RewardCandidateStatus.READY,
                               settlement_enabled=self._settlement_enabled)


REVIEW_TEXT = {  # [review line]: Plain language for each review state.
    ReviewStatus.PENDING_VALIDATION: "待驗證（尚無可用的官方時長資料）",
    ReviewStatus.PENDING_REVIEW: "時長已驗證，待 Owner 審核",
    ReviewStatus.APPROVED: "審核通過",
    ReviewStatus.REJECTED: "審核未通過",
    ReviewStatus.VALIDATION_FAILED: "無法取得影片資料（可能已刪除或未公開），待 Owner 處理",
}


def format_share_outcome(operation_id: str, outcome: PipelineOutcome) -> str:
    """@description: Tell the submitter the review result and exactly which condition the reward still waits for.
    @param operation_id: Receipt reference shown to the user.
    @param outcome: Pipeline outcome read back from durable state.
    @return: Private reply; +1 appears only for a committed award.
    """
    retry = "官方資料暫時無法取得，系統會自動重試。" if outcome.retry_later else ""
    return f"已儲存投稿；審核：{REVIEW_TEXT[outcome.review_status]}。{retry}{_reward_text(outcome)}操作 {operation_id}。"


def reward_label(reward_status: str | None) -> str:
    """@description: Name a stored reward state for /history without echoing unknown database values.
    @param reward_status: Stored candidate status.
    @return: Short Traditional Chinese label.
    """
    labels = {"awarded": "已發放 +1", "ineligible": "不發放", "conflict": "版本衝突待處理", "ready": "符合資格待結算",
              "waiting_identity": "待確認版本", "waiting_validation": "待審核", "waiting_predecessor": "待較早投稿"}
    return labels.get(reward_status, "獎勵待確認")


def _reward_text(outcome: PipelineOutcome, third_person: bool = False) -> str:
    """@description: Translate the reward decision into one sentence, never claiming an award without a receipt.
    @param outcome: Pipeline outcome.
    @param third_person: True for the Owner's reply about someone else's submission.
    @return: Sentence in Traditional Chinese.
    """
    if outcome.settlement_unknown:
        return "發獎結果未確認，請勿重新投稿；系統會以原操作查回結果，請稍後用 /balance 或 /history 核對。"
    decision = outcome.decision
    if decision.status is RewardCandidateStatus.AWARDED:
        return "已為投稿者發放 +1 Nickel。" if third_person else "已發放 +1 Nickel。"
    if decision.status is RewardCandidateStatus.READY:
        if outcome.settlement_enabled:
            return "符合首次獎勵資格，系統尚未完成發放；目前未發放 Nickel。"
        return "符合首次獎勵資格，但自動發獎尚未開啟；目前未發放 Nickel。"
    return _WAITING_TEXT.get(decision.reason, "本次未發放 Nickel。")


_WAITING_TEXT = {  # [reward line]: Names the one thing the reward still waits for.
    RewardDecisionReason.IDENTITY_UNRESOLVED: "待 Owner 確認錄音版本；目前未發放 Nickel。",
    RewardDecisionReason.REVIEW_INCOMPLETE: "待審核完成；目前未發放 Nickel。",
    RewardDecisionReason.REEXAMINATION_PENDING: "重審中；目前未發放 Nickel。",
    RewardDecisionReason.REVIEW_REJECTED: "不發放 Nickel。",
    RewardDecisionReason.EARLIER_SUBMISSION: "正在等待較早的投稿處理完成（先投稿者優先）；目前未發放 Nickel。",
    RewardDecisionReason.INCOMPLETE_SNAPSHOT: "資料尚未確認完整；目前未發放 Nickel。",
    RewardDecisionReason.HISTORICAL_VERSION: "此錄音版本已領過首次獎勵或已在既有目錄中，本次不發放 Nickel。",
    RewardDecisionReason.VERSION_ALREADY_AWARDED: "此錄音版本已領過首次獎勵，本次重複不發放 Nickel。",
    RewardDecisionReason.PREVIOUSLY_AWARDED: "已發放 +1 Nickel。",
    RewardDecisionReason.EVIDENCE_CONFLICT: "版本證據衝突，待 Owner 處理；目前未發放 Nickel。",
}
