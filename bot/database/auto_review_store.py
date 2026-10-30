"""Apply automatic review verdicts and read settlement work under the shared writer gate.

Inputs are a locked cursor and a verdict computed before the transaction began; outputs are audited review revisions under the system actor, never the Owner's identity.
"""

from dataclasses import dataclass
from typing import Any

from bot.auto_review import AutoReviewKind, AutoReviewVerdict
from bot.database.persisted_snapshot import decode_current_review, fetch_all
from bot.database.review_persistence import ReviewCommand, _current_request, apply_review
from bot.database.reward_settlement import current_decisions
from bot.review import ApprovalSource, ReviewSnapshot, ReviewStatus, ValidationError
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


def apply_verdict(cursor: Any, control: dict, operation_id: str, expected_revision: int, verdict: AutoReviewVerdict) -> ReviewSnapshot:
    """@description: Write one verdict through the audited review path, or nothing when the review moved during the lookup.
    @param cursor: Cursor holding the writer gate.
    @param control: Locked control row.
    @param operation_id: Accepted operation.
    @param expected_revision: Revision the lookup was made for.
    @param verdict: Verdict computed outside the transaction.
    @return: The review after this call; unchanged for a stale or repeated verdict.
    """
    request = _current_request(cursor, operation_id, expected_revision)
    current = request.review
    if current.version != expected_revision or not _awaits_validation(current):
        return current  # [stale result]: Newer Owner or automatic facts always win.
    if verdict.kind is AutoReviewKind.UNAVAILABLE and current.status is ReviewStatus.VALIDATION_FAILED:
        return current  # [no churn]: Repeating the same failure adds no audit fact.
    approve = verdict.kind is AutoReviewKind.AUTO_APPROVE and request.identity.status is not VersionIdentityStatus.CONFLICT
    command = ReviewCommand(f"auto-review:{current.video.video_id}:{expected_revision}", operation_id, SYSTEM_ACTOR_ID, verdict.reason,
                            expected_revision, duration=verdict.duration,
                            validation_error=ValidationError.METADATA_UNAVAILABLE if verdict.kind is AutoReviewKind.UNAVAILABLE else None,
                            approval_source=ApprovalSource.AUTO if approve else None, song_eligible=True if approve else None)
    return apply_review(cursor, control, command)


def pending_validation_rows(cursor: Any, limit: int) -> list[tuple[str, str]]:
    """@description: List the oldest operation of each video still waiting for first validation.
    @param cursor: Cursor holding the writer gate.
    @param limit: Maximum rows read.
    @return: Pairs of operation and video identifiers in acceptance order.
    """
    rows = fetch_all(cursor, "SELECT e.operation_id,e.video_id,v.review_json,v.review_revision,v.review_status FROM jb2_events e "
                             "JOIN jb2_videos v ON v.video_id=e.video_id WHERE v.review_status=%s "
                             "ORDER BY e.accepted_sequence LIMIT %s", (ReviewStatus.PENDING_VALIDATION.value, limit))
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
