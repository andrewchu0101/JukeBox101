"""Expose worker-local persisted submission, review and settlement operations.

Construction performs no I/O; an explicit injected connection factory is invoked only by authorized future callers.
"""

from collections.abc import Callable
from contextlib import closing, contextmanager
import logging
from dataclasses import replace
from datetime import datetime, timezone
from random import Random
from typing import Any

from bot.database.exchange_delivery_store import (
    DeliveryClaim,
    RefundContradiction,
    RefundOutcome,
    claim_reservation,
    owner_summary,
    pending_refund_ids,
    recover_interrupted,
    user_balance,
    record_result,
    refund_undelivered,
    user_results,
)
from bot.database.exchange_reservation import ExchangeOutcome, reserve_exchange
from bot.database.compensation_schema import CompensationDisabled, compensation_tables_present, require_schema
from bot.database.compensation_store import (
    approve_incident, claim_grant, record_not_system_fault, expire_grant_claims, open_grants, record_grant_delivery,
    pending_incident_rows, record_owner_disposition, settle_incident, user_incident_summary, close_non_fault_refund,
)
from bot.database.pending_review import owner_stranded_rows
from bot.database.persisted_snapshot import decode_current_identity, load_snapshot
from bot.database.review_persistence import (
    PendingPage, ReexaminationCommand, ReexaminationPlan, ReviewCommand, VersionConfirmationCommand, VersionConfirmationPlan,
    apply_reexamination, apply_review, apply_version_confirmation, page_pending, pending_review_items, plan_reexamination,
    plan_version_confirmation, require_owner, stored_evidence_record,
)
from bot.database.review_projection import ReviewProjection, append_evidence, review_projection
from bot.auto_review import AutoReviewVerdict
from bot.database.auto_review_store import AutoReviewSubject, apply_verdict, pending_validation_rows, read_reward_state, read_subject, ready_rows
from bot.database.reward_settlement import settle_reward
from bot.database.submission_maintenance import merge_versions, pending_summary, store_exclusion
from bot.database.submission_store import accept_submission
from bot.database.submission_transaction import PersistenceNotReady, submission_transaction
from bot.evidence_record import EvidenceRecord
from bot.models import HISTORY_LIMIT
from bot.review import ReviewSnapshot, ReviewStatus
from bot.song_identity import VideoIdentity
from bot.urls import parse_youtube_url
from bot.reward_candidates import RewardDecision
from bot.submission_snapshot import SubmissionSnapshot
from bot.submission_workflow import SubmissionRequest, SubmissionResult
from bot.version_identity import VersionAssignment, VersionExclusion, VersionIdentityStatus, require_reference


LOGGER = logging.getLogger(__name__)  # [sweep diagnostics]: Class names and reservation identifiers only.

class SubmissionRepository:
    """Own complete synchronous transactions without changing the existing Discord-facing MusicRepository."""

    def __init__(self, connection_factory: Callable[[], Any], owner_discord_user_id: int, *, compensation_enabled: bool = False,
                 automatic_settlement: bool = False) -> None:
        """@description: Keep connection creation deferred and Owner identity outside user-controlled requests.
        @param self: New persistent submission boundary.
        @param connection_factory: Existing worker-local PyMySQL creator or offline fake.
        @param owner_discord_user_id: Deployment-configured Owner Discord user ID, not a role or nickname.
            @param compensation_enabled: Feature switch, off by default; when off no compensation table is ever read or written.
            @param automatic_settlement: Deployment mode, off by default; only an exact True lets intake run with the database settlement switch on.
    @return: None.
        """
        if type(owner_discord_user_id) is not int or owner_discord_user_id <= 0:
            raise ValueError("A configured Owner identity is required")
        self._factory = connection_factory  # [worker connection]: Never retain or share a live connection.
        self._owner_id = owner_discord_user_id  # [authorization authority]: Must not be taken from command options.
        self._compensation_enabled = compensation_enabled  # [feature switch]: Off by default; when off no compensation table is ever touched.
        self._automatic_settlement = automatic_settlement is True  # [intake policy]: Otherwise an open settlement switch still stops intake.

    def accept(self, request: SubmissionRequest) -> SubmissionResult:
        """@description: Accept one share or replay its original receipt without issuing Nickel.
        @param self: Synchronous worker-owned repository boundary.
        @param request: URL and trusted evidence; the database assigns acceptance order.
        @return: Persisted original submission outcome.
        """
        require_reference(request.operation_id)
        if request.approval_source is not None or request.song_eligible is not None:
            raise ValueError("Approval must use the audited review boundary after acceptance")
        if request.identity.status is not VersionIdentityStatus.UNRESOLVED:
            raise ValueError("Intake cannot assert a version identity; identity is confirmed only from evidence")
        with submission_transaction(self._factory) as (cursor, control):
            return accept_submission(cursor, control, request)

    def accept_pending(self, discord_user_id: int, source_url: str, operation_id: str) -> SubmissionResult:
        """@description: Admit untrusted Discord URLs without accepting evidence or awarding Nickel.
        @param self: Existing transaction-owning submission repository.
        @param discord_user_id: Authenticated interaction submitter.
        @param source_url: User URL, never identity or eligibility evidence.
        @param operation_id: Stable server-assigned interaction retry key.
        @return: New pending receipt or the original operation receipt on replay.
        """
        request = _pending_request(discord_user_id, source_url, operation_id)
        with submission_transaction(self._factory) as (cursor, control):
            settlement_conflict = control["settlement_enabled"] and not self._automatic_settlement
            if not control["snapshot_ready"] or not control["legacy_writers_quiesced"] or settlement_conflict:
                raise PersistenceNotReady("Pending intake requires reviewed isolation and disabled settlement")
            cursor.execute("SELECT video_id,identity_json,review_json,review_revision,review_status "
                           "FROM jb2_videos WHERE video_id = %s", (request.identity.video.video_id,))
            current = cursor.fetchone()  # [authoritative identity]: A public resubmission never replaces Owner evidence.
            if current is not None:
                request = replace(request, identity=decode_current_identity(current))
            return accept_submission(cursor, control, request)

    def history(self, discord_user_id: int) -> list[dict]:
        """@description: Limit the public projection to the caller's submissions and current review states.
        @param self: Existing repository with worker-owned connections.
        @param discord_user_id: Authenticated recipient, never a slash-command option.
        @return: Bounded newest-first submission rows without another user's private evidence.
        """
        with closing(self._factory()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT e.operation_id,e.video_id,v.review_status,e.reward_status "
                    "FROM jb2_events e JOIN jb2_videos v ON v.video_id=e.video_id "
                    "WHERE e.discord_user_id=%s ORDER BY e.accepted_sequence DESC LIMIT %s",
                    (discord_user_id, HISTORY_LIMIT),
                )
                return list(cursor.fetchall())

    def snapshot(self) -> SubmissionSnapshot:
        """@description: Supply a globally covered current projection without a caller completeness switch.
        @param self: Synchronous repository boundary.
        @return: Repository-certified snapshot, possibly conservatively incomplete.
        """
        with submission_transaction(self._factory) as (cursor, control):
            return load_snapshot(cursor, control)

    def settle(self, operation_id: str) -> RewardDecision:
        """@description: Recompute eligibility and atomically settle a first-version reward.
        @param self: Synchronous repository boundary, not a public Discord award command.
        @param operation_id: Existing accepted operation or its settlement retry key.
        @return: Durable award receipt or current blocked decision.
        """
        require_reference(operation_id)
        with submission_transaction(self._factory) as (cursor, control):
            return settle_reward(cursor, control, operation_id)

    def auto_review_subject(self, operation_id: str) -> AutoReviewSubject:
        """@description: Read what an official lookup needs in a transaction that closes before the network call.
        @param self: Synchronous repository boundary.
        @param operation_id: Accepted operation.
        @return: Video, revision and whether first validation is still open.
        """
        require_reference(operation_id)
        with submission_transaction(self._factory) as (cursor, _control):
            return read_subject(cursor, operation_id)

    def apply_auto_review(self, operation_id: str, expected_revision: int, verdict: AutoReviewVerdict) -> ReviewSnapshot:
        """@description: Apply one automatic verdict under the system actor; a verdict for an older revision writes nothing.
        @param self: Synchronous repository boundary, not reachable from any user command.
        @param operation_id: Accepted operation.
        @param expected_revision: Revision the lookup was made for.
        @param verdict: Verdict computed outside this transaction.
        @return: Current review after the call.
        """
        with submission_transaction(self._factory) as (cursor, control):
            return apply_verdict(cursor, control, operation_id, expected_revision, verdict)

    def pending_validation_operations(self, limit: int, excluded_video_ids: tuple[str, ...] = ()) -> list[tuple[str, str]]:
        """@description: List operations whose video still waits for first validation, for the bounded sweep.
        @param self: Synchronous repository boundary.
        @param limit: Maximum rows read.
        @param excluded_video_ids: Videos to leave out because they are paused; they never consume the read window.
        @return: Pairs of operation and video identifiers in acceptance order.
        """
        with submission_transaction(self._factory) as (cursor, _control):
            return pending_validation_rows(cursor, limit, excluded_video_ids)

    def ready_operations(self) -> list[str]:
        """@description: Recompute the candidates that may be settled right now.
        @param self: Synchronous repository boundary.
        @return: READY operations in acceptance order.
        """
        with submission_transaction(self._factory) as (cursor, control):
            return ready_rows(cursor, control)

    def reward_state(self, operation_id: str) -> tuple[ReviewStatus, RewardDecision]:
        """@description: Read one operation's current review and reward decision without changing anything.
        @param self: Synchronous repository boundary.
        @param operation_id: Accepted operation.
        @return: Review status and recomputed decision.
        """
        require_reference(operation_id)
        with submission_transaction(self._factory) as (cursor, control):
            return read_reward_state(cursor, control, operation_id)

    def settlement_enabled(self, requester_id: int) -> bool:
        """@description: Read the real-settlement switch under the control lock, so an operator sees the value a settlement would honour.
        @param self: Repository retaining the configured Owner identity.
        @param requester_id: Authenticated caller, which must be the configured Owner.
        @return: True when real reward settlement is currently enabled.
        """
        self._require_owner(requester_id)
        with submission_transaction(self._factory) as (_cursor, control):
            return bool(control["settlement_enabled"])

    def set_settlement_enabled(self, requester_id: int, enabled: bool) -> None:
        """@description: Write the real-settlement switch under the control lock; callers verify the stored value with a separate read.
        @param self: Repository retaining the configured Owner identity.
        @param requester_id: Authenticated caller, which must be the configured Owner.
        @param enabled: Target switch value; the caller decides when settlement may run.
        @return: None; the write commits with its transaction.
        """
        self._require_owner(requester_id)
        with submission_transaction(self._factory) as (cursor, _control):
            cursor.execute("UPDATE jb2_control SET settlement_enabled=%s WHERE singleton_id=1", (enabled,))

    def exchange(self, discord_user_id: int, request_key: str, rng: Random, now: datetime | None = None) -> ExchangeOutcome:
        """@description: Reserve one exchange recommendation and debit one point atomically, or report why nothing changed.
        @param self: Synchronous repository boundary, not a public Discord command.
        @param discord_user_id: Authenticated requesting user.
        @param request_key: Technical idempotency key that retries must reuse.
        @param rng: Injected random source for the uniform draw.
        @param now: Optional naive UTC clock; defaults to the current time.
        @return: Reserved, replayed or unchanged outcome from the locked transaction.
        """
        require_reference(request_key)
        clock = now if now is not None else datetime.now(timezone.utc).replace(tzinfo=None)  # [naive UTC]: Matches the stored timestamps.
        with submission_transaction(self._factory) as (cursor, control):
            return reserve_exchange(cursor, control, discord_user_id, request_key, rng, clock, compensation_enabled=self._compensation_enabled)

    def owner_pending_reservations(self, requester_id: int, now: datetime) -> list[dict]:
        """@description: Show the Owner the stranded reservations, refusing every other identity before any read.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the configured Owner.
        @param now: Current time, naive UTC.
        @return: Stranded reservation rows, bounded and without links, tokens or balances.
        """
        if requester_id != self._owner_id:
            raise PermissionError("OWNER_ONLY")  # [authority]: Checked before any database work.
        with self._compensation_transaction() as (cursor, _):
            return owner_stranded_rows(cursor, now)

    @contextmanager
    def _compensation_transaction(self):
        """@description: Open a gated transaction for a compensation operation only when the feature is switched on and its tables exist.
        @param self: Synchronous repository boundary.
        @return: Context yielding the cursor and control row of a gated transaction.
        """
        if not self._compensation_enabled:
            raise CompensationDisabled("COMPENSATION_DISABLED")  # [explicit]: Refused before any database work.
        with submission_transaction(self._factory) as (cursor, control):
            require_schema(cursor)  # [explicit]: A missing table refuses the operation; nothing is continued.
            yield cursor, control

    def _require_owner(self, requester_id: int) -> None:
        """@description: Refuse any identity other than the configured Owner before any database work.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request.
        @return: None; a non-Owner raises PermissionError with a code only.
        """
        if requester_id != self._owner_id:
            raise PermissionError("OWNER_ONLY")  # [authority]: Never taken from command options.

    def owner_approve_incident(self, requester_id: int, recommendation_id: int, reason_code: str, now: datetime) -> dict:
        """@description: Record the Owner's approval for one stranded reservation; repeated approvals create nothing new.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the Owner.
        @param recommendation_id: Stranded reservation.
        @param reason_code: Reviewed reason code.
        @param now: Current time, naive UTC.
        @return: Incident identifier, state and whether it was created now.
        """
        self._require_owner(requester_id)
        with self._compensation_transaction() as (cursor, _):
            return approve_incident(cursor, recommendation_id, self._owner_id, reason_code, now)

    def owner_record_not_system_fault(self, requester_id: int, recommendation_id: int, reason_code: str, now: datetime) -> dict:
        """@description: Record that a stranded reservation is not a system fault; no refund or redelivery follows, the debit stays and the block lifts.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the Owner.
        @param recommendation_id: Stranded reservation.
        @param reason_code: Reviewed reason code.
        @param now: Current time, naive UTC.
        @return: Incident identifier and state.
        """
        self._require_owner(requester_id)
        with self._compensation_transaction() as (cursor, _):
            return record_not_system_fault(cursor, recommendation_id, self._owner_id, reason_code, now)

    def owner_settle_incident(self, requester_id: int, incident_id: int, redelivery_approved: bool, now: datetime) -> dict:
        """@description: Atomically refund the bound original debit once and record the Owner's separate redelivery decision.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the Owner.
        @param incident_id: Approved incident.
        @param redelivery_approved: Whether to issue the free redelivery grant; declining it still refunds.
        @param now: Current time, naive UTC.
        @return: Settlement outcome.
        """
        self._require_owner(requester_id)
        with self._compensation_transaction() as (cursor, _):
            return settle_incident(cursor, incident_id, redelivery_approved, now)

    def owner_record_disposition(self, requester_id: int, grant_id: int, disposition: str, now: datetime) -> dict:
        """@description: Record the Owner's closing decision for a not-sent or unknown grant without changing the delivery fact.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the Owner.
        @param grant_id: Grant being closed.
        @param disposition: Reviewed closing decision.
        @param now: Current time, naive UTC.
        @return: Grant identifier and whether the song remains held.
        """
        self._require_owner(requester_id)
        with self._compensation_transaction() as (cursor, _):
            return record_owner_disposition(cursor, grant_id, self._owner_id, disposition, now)

    def owner_open_grants(self, requester_id: int, now: datetime) -> list[dict]:
        """@description: Show the Owner the open compensation grants, with identifiers, state, times and an overdue flag only.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the Owner.
        @param now: Current time, naive UTC, used only for the overdue flag.
        @return: Bounded list of open grants.
        """
        self._require_owner(requester_id)
        with self._compensation_transaction() as (cursor, _):
            return open_grants(cursor, now)

    def owner_user_incident_summary(self, requester_id: int, user: int) -> list[dict]:
        """@description: Show the Owner one requester's most recent incidents for manual judgement; no score or automatic limit is computed.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the Owner.
        @param user: Requester whose incidents are shown.
        @return: Up to ten recent incidents with state, grant state and delivery fact.
        """
        self._require_owner(requester_id)
        with self._compensation_transaction() as (cursor, _):
            return user_incident_summary(cursor, user)

    def owner_close_non_fault_refund(self, requester_id: int, incident_id: int, confirmed_undelivered: bool, now: datetime) -> dict:
        """@description: Close a not-system-fault incident by refunding its original debit once, only when the Owner confirmed non-delivery; no free redelivery is created.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the Owner.
        @param incident_id: Not-system-fault incident.
        @param confirmed_undelivered: Explicit confirmation that the original order was never delivered; no default.
        @param now: Current time, naive UTC.
        @return: Outcome and the refund ledger identifier.
        """
        self._require_owner(requester_id)
        with self._compensation_transaction() as (cursor, _):
            return close_non_fault_refund(cursor, incident_id, self._owner_id, confirmed_undelivered, now)

    def owner_pending_incidents(self, requester_id: int, now: datetime) -> list[dict]:
        """@description: Show the Owner the incidents still awaiting a decision (approved, or judged not a system fault), with an overdue flag only.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the Owner.
        @param now: Current time, naive UTC, used only for the overdue flag.
        @return: Bounded list of pending incidents.
        """
        self._require_owner(requester_id)
        with self._compensation_transaction() as (cursor, _):
            return pending_incident_rows(cursor, now)

    def owner_expire_grant_claims(self, requester_id: int, now: datetime) -> int:
        """@description: Turn expired grant claims into unknown deliveries; nothing is sent again.
        @param self: Synchronous repository boundary.
        @param requester_id: Identity making the request; must be the Owner.
        @param now: Current time, naive UTC.
        @return: Number of claims moved to uncertain.
        """
        self._require_owner(requester_id)
        with self._compensation_transaction() as (cursor, _):
            return expire_grant_claims(cursor, now)

    def claim_grant(self, grant_id: int, claim_token: str, now: datetime) -> tuple:
        """@description: Claim one open grant for dispatch before any direct message is attempted; a grant in any other state sends nothing.
        @param self: Synchronous repository boundary.
        @param grant_id: Grant to dispatch.
        @param claim_token: Unique token of this attempt.
        @param now: Attempt start time, naive UTC.
        @return: State, and the recipient and link only for the caller that made the claim.
        """
        with self._compensation_transaction() as (cursor, _):
            return claim_grant(cursor, grant_id, claim_token, now)

    def record_grant_delivery(self, grant_id: int, claim_token: str, result: str, message_id: int | None, now: datetime) -> str:
        """@description: Record the classified result of one grant dispatch against the claim that made the attempt.
        @param self: Synchronous repository boundary.
        @param grant_id: Dispatched grant.
        @param claim_token: Token of the claim that performed the attempt.
        @param result: One of sent, not_sent or unknown.
        @param message_id: Accepted message identifier, or None.
        @param now: Result time, naive UTC.
        @return: Stored grant state.
        """
        with self._compensation_transaction() as (cursor, _):
            return record_grant_delivery(cursor, grant_id, claim_token, result, message_id, now)

    def claim_delivery(self, recommendation_id: int, claim_token: str, now: Any) -> tuple:
        """@description: Move one reserved exchange recommendation to sending under this attempt's token before any DM is attempted.
        @param self: Synchronous repository boundary, called from a worker thread.
        @param recommendation_id: Reservation identifier.
        @param claim_token: Unique token of this attempt.
        @param now: Time the attempt starts.
        @return: Current status, message identifier, and a claim only for this caller.
        """
        with submission_transaction(self._factory) as (cursor, _):
            guarded = self._compensation_enabled or compensation_tables_present(cursor)  # [claim guard]: Switched on, or the tables exist; a switched-off repository still holds an open incident's original order.
            return claim_reservation(cursor, recommendation_id, claim_token, now, guard_incidents=guarded)

    def record_delivery(self, claim: DeliveryClaim, status: str, message_id: int | None, delivered_at: Any) -> str:
        """@description: Record the classified outcome of one claimed direct-message attempt, never overwriting a newer state.
        @param self: Synchronous repository boundary, called from a worker thread.
        @param claim: Claim returned by claim_delivery to the caller that sent.
        @param status: Delivered, failed or uncertain.
        @param message_id: Accepted DM identifier, or None.
        @param delivered_at: Acceptance time, or None.
        @return: The status the database holds afterwards.
        """
        with submission_transaction(self._factory) as (cursor, _):
            return record_result(cursor, claim, status, message_id, delivered_at)

    def recover_interrupted_deliveries(self, now: Any) -> int:
        """@description: At startup, mark attempts whose sending lease has expired as uncertain without resending them.
        @param self: Synchronous repository boundary.
        @param now: Current time.
        @return: Number of reservations moved to uncertain.
        """
        with submission_transaction(self._factory) as (cursor, _):
            return recover_interrupted(cursor, now)

    def retry_pending_refunds(self, now: Any) -> list[RefundOutcome]:
        """@description: Retry refunds that a previous request could not commit, each still at most once.
        @param self: Synchronous repository boundary, called from a worker thread.
        @param now: Release time for refunds that commit now.
        @return: Outcome per pending reservation attempted in this sweep.
        """
        with submission_transaction(self._factory) as (cursor, _):
            ids = pending_refund_ids(cursor)
        outcomes = []
        for recommendation_id in ids:  # [one transaction each]: A failure on one never blocks the others.
            try:
                with submission_transaction(self._factory) as (cursor, _):
                    outcomes.append(refund_undelivered(cursor, recommendation_id, now))
            except RefundContradiction:
                LOGGER.error("Refund sweep contradiction: reservation needs human review; nothing refunded")  # [explicit]: Never skipped silently.
                outcomes.append(RefundOutcome("contradiction", 0))  # [stop]: Human review; nothing is credited or released.
            except Exception as error:
                LOGGER.error("Refund sweep deferred one reservation: %s", type(error).__name__)  # [class only]: Nothing is released or credited here.
                outcomes.append(RefundOutcome("pending", 0))  # [retry later]: Nothing is released or credited here.
        return outcomes

    def user_exchange_results(self, discord_user_id: int) -> list[dict]:
        """@description: Show one requester only their own exchange results, with no token, link or other user's data.
        @param self: Synchronous repository boundary, called from a worker thread.
        @param discord_user_id: Authenticated requester.
        @return: Projected results for that requester.
        """
        with submission_transaction(self._factory) as (cursor, _):
            return user_results(cursor, discord_user_id)

    def user_exchange_balance(self, discord_user_id: int) -> int | None:
        """@description: Show one requester their own spendable credits, or None for an unknown account.
        @param self: Synchronous repository boundary, called from a worker thread.
        @param discord_user_id: Authenticated requester.
        @return: Credits of that account, or None.
        """
        with submission_transaction(self._factory) as (cursor, _):
            return user_balance(cursor, discord_user_id)

    def owner_exchange_summary(self) -> dict:
        """@description: Give the Owner aggregate exchange counts that expose no identifier granting access.
        @param self: Synchronous repository boundary, called from a worker thread.
        @return: Counts per delivery state and pending refunds.
        """
        with submission_transaction(self._factory) as (cursor, _):
            return owner_summary(cursor)

    def refund_undelivered(self, recommendation_id: int, now: Any) -> RefundOutcome:
        """@description: Refund one point for a reservation proven unsent, from its authoritative original debit only.
        @param self: Synchronous repository boundary, called from a worker thread.
        @param recommendation_id: Reservation whose refund is requested.
        @param now: Release time.
        @return: Refund outcome from the locked transaction.
        """
        with submission_transaction(self._factory) as (cursor, _):
            return refund_undelivered(cursor, recommendation_id, now)

    def review(self, command: ReviewCommand) -> ReviewSnapshot:
        """@description: Authorize Owner decisions before entering the database transaction.
        @param self: Repository retaining deployment Owner identity.
        @param command: Authenticated actor and version-checked audited review intent.
        @return: Original or newly persisted review result.
        """
        require_owner(command.actor_id, self._owner_id, command.reason)
        with submission_transaction(self._factory) as (cursor, control):
            return apply_review(cursor, control, command)

    def reexamine(self, command: ReexaminationCommand) -> ReviewSnapshot:
        """@description: Reopen a decided review on reliable new evidence through the audited persistence boundary.
        @param self: Repository retaining deployment Owner identity.
        @param command: Authenticated Owner reexamination with its evidence record and revision.
        @return: Original or newly persisted pending review.
        """
        require_owner(command.actor_id, self._owner_id, command.reason)
        with submission_transaction(self._factory) as (cursor, control):
            return apply_reexamination(cursor, control, command)

    def confirm_version(self, command: VersionConfirmationCommand) -> ReviewSnapshot:
        """@description: Stamp an Owner-confirmed recording version through the audited boundary, leaving the review and money untouched.
        @param self: Repository retaining deployment Owner identity.
        @param command: Authenticated Owner confirmation with its evidence record and revision.
        @return: Original or current unchanged review.
        """
        require_owner(command.actor_id, self._owner_id, command.reason)
        with submission_transaction(self._factory) as (cursor, control):
            return apply_version_confirmation(cursor, control, command)

    def preview_version_confirmation(self, actor_id: int, operation_id: str, evidence: EvidenceRecord,
                                     expected_revision: int) -> VersionConfirmationPlan:
        """@description: Compute, without writing, what a version confirmation would produce so the Owner preview can refuse early.
        @param self: Repository retaining deployment Owner identity.
        @param actor_id: Authenticated caller that must be the configured Owner.
        @param operation_id: Accepted operation whose video the evidence confirms.
        @param evidence: Owner-stamped evidence fact.
        @param expected_revision: Review revision the Owner examined.
        @return: Plan read in one transaction, with the current snapshot and the identity the confirmation would persist.
        """
        require_owner(actor_id, self._owner_id, "owner version confirmation preview")
        with submission_transaction(self._factory) as (cursor, _control):
            return plan_version_confirmation(cursor, operation_id, evidence, actor_id, expected_revision)

    def preview_reexamination(self, actor_id: int, operation_id: str, evidence: EvidenceRecord,
                              expected_revision: int) -> ReexaminationPlan:
        """@description: Compute, without writing, what a formal reexamination would reopen and store so its preview matches the confirmation.
        @param self: Repository retaining deployment Owner identity.
        @param actor_id: Authenticated caller that must be the configured Owner.
        @param operation_id: Accepted operation whose video the evidence describes.
        @param evidence: Owner-stamped reliable evidence fact.
        @param expected_revision: Review revision the Owner examined.
        @return: Plan read in one transaction, with the current snapshot, reopened review and projected identity.
        """
        require_owner(actor_id, self._owner_id, "owner reexamination preview")
        with submission_transaction(self._factory) as (cursor, _control):
            return plan_reexamination(cursor, operation_id, evidence, actor_id, expected_revision)

    def pending_items(self, actor_id: int, after_sequence: int | None, limit: int) -> PendingPage:
        """@description: Read one keyset page of the Owner's queue of open, failed, unconfirmed-approved and conflict-frozen items without writing.
        @param self: Repository retaining deployment Owner identity.
        @param actor_id: Authenticated caller that must be the configured Owner.
        @param after_sequence: Last accepted sequence already shown, or None for the first page.
        @param limit: Page size already capped by the service.
        @return: One page of pending items in acceptance order.
        """
        require_owner(actor_id, self._owner_id, "owner pending list")
        with submission_transaction(self._factory) as (cursor, _control):
            return page_pending(pending_review_items(cursor), after_sequence, limit)

    def stored_evidence(self, actor_id: int, operation_id: str, evidence_id: str) -> EvidenceRecord:
        """@description: Return a stored Owner evidence record of the operation's video so the Owner can cite it again without retyping.
        @param self: Repository retaining deployment Owner identity.
        @param actor_id: Authenticated caller that must be the configured Owner.
        @param operation_id: Accepted operation whose video the citation must describe.
        @param evidence_id: Evidence identifier to cite.
        @return: The stored record; unknown or foreign-video identifiers are refused.
        """
        require_owner(actor_id, self._owner_id, "owner stored evidence lookup")
        require_reference(evidence_id)
        with submission_transaction(self._factory) as (cursor, _control):
            return stored_evidence_record(cursor, operation_id, evidence_id)

    def review_view(self, actor_id: int, operation_id: str) -> ReviewProjection:
        """@description: Show the Owner one operation's current review, identity and audit trail without writing.
        @param self: Repository retaining deployment Owner identity.
        @param actor_id: Authenticated caller that must be the configured Owner.
        @param operation_id: Accepted operation chosen for inspection.
        @return: Consistent read-only projection of the operation's video.
        """
        require_owner(actor_id, self._owner_id, "owner review view")
        require_reference(operation_id)
        with submission_transaction(self._factory) as (cursor, _control):
            return review_projection(cursor, operation_id)

    def record_reexamination_evidence(self, decision_id: str, operation_id: str, actor_id: int, reason: str,
                                      evidence: EvidenceRecord) -> EvidenceRecord:
        """@description: Append Owner-judged evidence without changing review status, identity or accepted order.
        @param self: Repository retaining deployment Owner identity.
        @param decision_id: Idempotent reexamination entry key.
        @param operation_id: Accepted operation whose video the evidence describes.
        @param actor_id: Authenticated Owner identity.
        @param reason: Required audit explanation.
        @param evidence: Validated evidence record about the same video.
        @return: Original or newly stored evidence record.
        """
        require_owner(actor_id, self._owner_id, reason)
        require_reference(decision_id)
        if len(decision_id) > 128:
            raise ValueError("Decision identifiers exceed storage limits")
        with submission_transaction(self._factory) as (cursor, _control):
            return append_evidence(cursor, decision_id, operation_id, actor_id, reason, evidence)

    def merge(self, decision_id: str, actor_id: int, reason: str, source: str, target: str) -> tuple[str, str]:
        """@description: Persist an Owner-confirmed identity merge while preserving original awards.
        @param self: Repository with configured Owner authorization.
        @param decision_id: Idempotent merge action key.
        @param actor_id: Authenticated Discord actor identity.
        @param reason: Trusted merge evidence explanation.
        @param source: Existing source version.
        @param target: Existing retained version.
        @return: Original canonical source and target references.
        """
        require_owner(actor_id, self._owner_id, reason)
        with submission_transaction(self._factory) as (cursor, control):
            return merge_versions(cursor, control, decision_id, actor_id, reason, source, target)

    def exclude(self, decision_id: str, actor_id: int, reason: str, evidence: VersionExclusion) -> VersionExclusion:
        """@description: Add audited scoped negative evidence without opening a new recording identity.
        @param self: Owner-authorized repository boundary.
        @param decision_id: Idempotent evidence action key.
        @param actor_id: Authenticated Owner identity.
        @param reason: Trusted evidence explanation.
        @param evidence: Explicit exclusion of one video from one version.
        @return: Original or newly persisted proof.
        """
        require_owner(actor_id, self._owner_id, reason)
        with submission_transaction(self._factory) as (cursor, control):
            return store_exclusion(cursor, control, decision_id, actor_id, reason, evidence)

    def pending_reminders(self, actor_id: int) -> tuple[str, ...]:
        """@description: Reserve the Owner's daily pending summary without external delivery.
        @param self: Repository with configured Owner identity.
        @param actor_id: Trusted scheduler's configured Owner target.
        @return: Stable UTC-day pending operation IDs, with no Discord send.
        """
        require_owner(actor_id, self._owner_id, "scheduled pending summary")
        with submission_transaction(self._factory) as (cursor, control):
            return pending_summary(cursor, self._owner_id)


def _pending_request(discord_user_id: int, source_url: str, operation_id: str) -> SubmissionRequest:
    """@description: Keep public intake facts separate from privileged review evidence.
    @param discord_user_id: Authenticated Discord submitter.
    @param source_url: Untrusted YouTube URL normalized by the existing parser.
    @param operation_id: Stable interaction-derived idempotency reference.
    @return: Evidence-free request whose sequence is assigned inside the database transaction.
    """
    require_reference(operation_id)
    if len(operation_id) > 128 or type(discord_user_id) is not int or discord_user_id <= 0:
        raise ValueError("Invalid public submission identity")
    video = VideoIdentity(parse_youtube_url(source_url).video_id)  # [unresolved subject]: URL identity is not recording evidence.
    return SubmissionRequest(source_url, operation_id, discord_user_id, 0,
                             VersionAssignment(video), ReviewSnapshot(video), 0)
