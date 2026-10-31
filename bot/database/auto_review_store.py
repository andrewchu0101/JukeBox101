"""Apply automatic review verdicts and read settlement work under the shared writer gate.

Inputs are a locked cursor and a verdict computed before the transaction began; outputs are audited review revisions under the system actor, never the Owner's identity.
"""

from dataclasses import dataclass
from typing import Any

from bot.auto_review import AutoReviewKind, AutoReviewVerdict
from bot.database.api_observations import basis_expired, purge_video, record_observation
from bot.eligibility import DurationEligibility
from bot.song_identity import VideoIdentity
from bot.database.persisted_snapshot import decode_current_review, fetch_all
from bot.database.review_persistence import ReviewCommand, _current_request, apply_review
from bot.database.reward_settlement import current_decisions
from bot.review import ApprovalSource, DurationVerdict, ReviewSnapshot, ReviewStatus, ValidationError
from bot.reward_candidates import RewardCandidateStatus, RewardDecision
from bot.version_identity import VersionIdentityStatus

SYSTEM_ACTOR_ID = 0  # [audit identity]: No Discord snowflake is zero, so automatic decisions can never read as the Owner's.
VALIDATING_STATUSES = frozenset({ReviewStatus.PENDING_VALIDATION, ReviewStatus.VALIDATION_FAILED})  # [automatic scope]: Decided reviews are never touched.


@dataclass(frozen=True)
class AutoReviewSubject:
    """Describe the video and revision a lookup is being made for."""

    video_id: str  # [lookup subject]: Stored parser-validated identifier.
    revision: int  # [concurrency token]: A verdict for an older revision applies nothing.
    awaits_validation: bool  # [lookup needed]: False for decided, reviewed or reexamined videos.


def _awaits_validation(review: ReviewSnapshot) -> bool:
    """@description: Limit automation to first validation; a reexamination belongs to the Owner.
    @param review: Current review.
    @return: Whether the automatic path may act.
    """
    return review.status in VALIDATING_STATUSES and not review.reexamination_pending


def read_subject(cursor: Any, operation_id: str) -> AutoReviewSubject:
    """@description: Read what a lookup needs, so the transaction can close before any network call.
    @param cursor: Cursor holding the writer gate.
    @param operation_id: Accepted operation.
    @return: Subject with its current revision.
    """
    review = _current_request(cursor, operation_id, 0).review
    return AutoReviewSubject(review.video.video_id, review.version, _awaits_validation(review))


def apply_verdict(cursor: Any, control: dict, operation_id: str, expected_revision: int, verdict: AutoReviewVerdict,
                  retain_api_values: bool = False) -> ReviewSnapshot:
    """@description: Write one verdict through the audited review path, or nothing when the review moved during the lookup.
    @param cursor: Cursor holding the writer gate.
    @param control: Locked control row.
    @param operation_id: Accepted operation.
    @param expected_revision: Revision the lookup was made for.
    @param verdict: Verdict computed outside the transaction.
    @param retain_api_values: True keeps official values only in the short-lived observation table and writes the conclusion everywhere else.
    @return: The review after this call; unchanged for a stale or repeated verdict.
    """
    request = _current_request(cursor, operation_id, expected_revision)
    current = request.review
    if current.version != expected_revision or not _awaits_validation(current):
        return current  # [stale result]: Newer Owner or automatic facts always win.
    if verdict.kind is AutoReviewKind.UNAVAILABLE and current.status is ReviewStatus.VALIDATION_FAILED:
        return current  # [no churn]: Repeating the same failure adds no audit fact.
    approve = verdict.kind is AutoReviewKind.AUTO_APPROVE and request.identity.status is not VersionIdentityStatus.CONFLICT
    if retain_api_values:
        return _apply_retained(cursor, control, operation_id, expected_revision, verdict, approve)
    command = ReviewCommand(f"auto-review:{current.video.video_id}:{expected_revision}", operation_id, SYSTEM_ACTOR_ID, verdict.reason,
                            expected_revision, duration=verdict.duration,
                            validation_error=ValidationError.METADATA_UNAVAILABLE if verdict.kind is AutoReviewKind.UNAVAILABLE else None,
                            approval_source=ApprovalSource.AUTO if approve else None, song_eligible=True if approve else None)
    return apply_review(cursor, control, command)


def _conclusion(verdict: AutoReviewVerdict, video_id: str) -> DurationVerdict | None:
    """@description: Reduce a verdict that measured a duration to its pass or reject conclusion.
    @param verdict: Verdict computed outside the transaction.
    @param video_id: Video the conclusion is about.
    @return: Conclusion, or None for verdicts that measured nothing.
    """
    if verdict.duration is None:
        return None
    outcome = DurationEligibility.REJECT if verdict.kind is AutoReviewKind.DURATION_REJECT else DurationEligibility.PASS
    return DurationVerdict(VideoIdentity(video_id), outcome)


def _apply_retained(cursor: Any, control: dict, operation_id: str, expected_revision: int, verdict: AutoReviewVerdict,
                    approve: bool) -> ReviewSnapshot:
    """@description: Apply a verdict so that official values exist only in the observation table, which is cleared as soon as the case is decided.
    @param cursor: Cursor holding the writer gate.
    @param control: Locked control row.
    @param operation_id: Accepted operation.
    @param expected_revision: Revision the lookup was made for.
    @param verdict: Verdict computed outside the transaction.
    @param approve: Whether this verdict also approves the song.
    @return: The review after this call.
    """
    video_id = _current_request(cursor, operation_id, expected_revision).review.video.video_id
    conclusion = _conclusion(verdict, video_id)
    if conclusion is not None and verdict.observation is not None and conclusion.outcome is DurationEligibility.PASS:
        record_observation(cursor, video_id, verdict.observation.duration_seconds, verdict.observation.channel_id)
    command = ReviewCommand(f"auto-review:{video_id}:{expected_revision}", operation_id, SYSTEM_ACTOR_ID, verdict.public_reason, expected_revision,
                            duration_verdict=conclusion,
                            validation_error=ValidationError.METADATA_UNAVAILABLE if verdict.kind is AutoReviewKind.UNAVAILABLE else None,
                            approval_source=ApprovalSource.AUTO if approve else None, song_eligible=True if approve else None)
    review = apply_review(cursor, control, command)
    if review.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
        purge_video(cursor, video_id, "completed")  # [decided]: The conclusion is stored; the values are no longer needed.
    return review


def refresh_observation(cursor: Any, operation_id: str, verdict: AutoReviewVerdict) -> bool:
    """@description: Replace the expired values behind a pending review's passing conclusion, writing no review revision.
    @param cursor: Cursor holding the writer gate.
    @param operation_id: Accepted operation whose review rests only on an expired API conclusion.
    @param verdict: Fresh verdict from the provider.
    @return: True when the review is still waiting on an expired conclusion and the provider confirmed a pass, so new values were stored.
    """
    review = _current_request(cursor, operation_id, 0).review
    passes = verdict.observation is not None and verdict.kind in {AutoReviewKind.AUTO_APPROVE, AutoReviewKind.DURATION_ONLY}
    if not passes or review.status is not ReviewStatus.PENDING_REVIEW or not basis_expired(cursor, review):
        return False
    record_observation(cursor, review.video.video_id, verdict.observation.duration_seconds, verdict.observation.channel_id)
    return True


def pending_validation_rows(cursor: Any, limit: int, excluded_video_ids: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """@description: List the oldest operation of each video still waiting for first validation, skipping videos that are paused.
    @param cursor: Cursor holding the writer gate.
    @param limit: Maximum rows read.
    @param excluded_video_ids: Videos the sweep is pausing; excluded in SQL so they cannot fill the read window.
    @return: Pairs of operation and video identifiers in acceptance order.
    """
    rows = fetch_all(cursor, "SELECT e.operation_id,e.video_id,v.review_json,v.review_revision,v.review_status FROM jb2_events e "
                             "JOIN jb2_videos v ON v.video_id=e.video_id WHERE v.review_status=%s AND NOT (e.video_id = ANY(%s::text[])) "
                             "ORDER BY e.accepted_sequence LIMIT %s",
                     (ReviewStatus.PENDING_VALIDATION.value, list(excluded_video_ids), limit))
    pending: dict[str, str] = {}  # [one lookup per video]: Later submitters of a video share its review.
    for row in rows:
        if _awaits_validation(decode_current_review(row)):
            pending.setdefault(row["video_id"], row["operation_id"])
    return [(operation_id, video_id) for video_id, operation_id in pending.items()]


def ready_rows(cursor: Any, control: dict) -> list[str]:
    """@description: Recompute which candidates are READY right now, never trusting a stored status.
    @param cursor: Cursor holding the writer gate.
    @param control: Locked control row.
    @return: READY operations in acceptance order.
    """
    return [item.operation_id for item in current_decisions(cursor, control) if item.status is RewardCandidateStatus.READY]


def read_reward_state(cursor: Any, control: dict, operation_id: str) -> tuple[ReviewStatus, RewardDecision]:
    """@description: Report the current review and reward decision of one operation without writing.
    @param cursor: Cursor holding the writer gate.
    @param control: Locked control row.
    @param operation_id: Accepted operation.
    @return: Review status and the recomputed decision.
    """
    review = _current_request(cursor, operation_id, 0).review
    decision = next(item for item in current_decisions(cursor, control) if item.operation_id == operation_id)
    return review.status, decision
