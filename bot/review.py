"""Model versioned Hybrid Review decisions without persistence or external validation.

Automatic and Owner decisions share duration gates; reward eligibility is only an offline precondition.
"""

from dataclasses import dataclass, replace
from enum import Enum

from bot.eligibility import DurationEligibility, DurationEvidence, assess_duration
from bot.song_identity import SubmissionDisposition, VideoIdentity
from bot.version_identity import require_reference


class ReviewStatus(Enum):
    """Describe review progress independently of approval source or duplicate outcomes."""

    PENDING_VALIDATION = "pending_validation"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    VALIDATION_FAILED = "validation_failed"


class ApprovalSource(Enum):
    """Record the decision path without implementing automatic policy or Owner authentication."""

    AUTO = "auto"
    OWNER = "owner"


class ValidationError(Enum):
    """Classify temporary validation failures without treating them as content rejections."""

    METADATA_UNAVAILABLE = "metadata_unavailable"
    INVALID_METADATA = "invalid_metadata"
    VERIFICATION_FAILED = "verification_failed"


class InvalidReviewTransition(ValueError):
    """Refuse unsupported decisions while preserving the original immutable snapshot."""


class StaleReviewVersion(InvalidReviewTransition):
    """Reject results produced for an earlier review revision."""


@dataclass(frozen=True)
class ReviewSnapshot:
    """Carry one video's review revision with evidence, source and error kept separate."""

    video: VideoIdentity  # [review subject]: Evidence must refer to this exact video.
    version: int = 0  # [decision revision]: Every accepted transition increments this token.
    status: ReviewStatus = ReviewStatus.PENDING_VALIDATION
    duration: DurationEvidence | None = None
    approval_source: ApprovalSource | None = None
    validation_error: ValidationError | None = None
    # [reexamination block]: Set by the Owner's reopen; approval waits for the later TK-15 ordering event.
    reexamination_pending: bool = False
    # [released priority]: Set by a negative decision; reopening never restores the predecessor's blocking power.
    priority_released: bool = False

    def __post_init__(self) -> None:
        """@description: Reject contradictory snapshots before they can authorize future rewards.
        @param self: Immutable review state being constructed or replaced.
        @return: None.
        """
        if not isinstance(self.video, VideoIdentity) or type(self.version) is not int or self.version < 0:
            raise InvalidReviewTransition("Invalid review identity or version")
        if not isinstance(self.status, ReviewStatus) or type(self.reexamination_pending) is not bool:
            raise InvalidReviewTransition("Invalid review status")
        if type(self.priority_released) is not bool:
            raise InvalidReviewTransition("Invalid priority release flag")
        if self.reexamination_pending and self.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
            raise InvalidReviewTransition("Only an unresolved review can await reexamination")
        if self.duration is not None:
            if not isinstance(self.duration, DurationEvidence) or self.duration.video != self.video:
                raise InvalidReviewTransition("Duration evidence belongs to another video")
        _validate_snapshot_fields(self)


def _duration_passes(snapshot: ReviewSnapshot) -> bool:
    """@description: Centralize the hard gate shared by review and future award preconditions.
    @param snapshot: Review with evidence bound to the same platform identity.
    @return: Whether reliable full-video duration passes the inclusive limits.
    """
    return snapshot.duration is not None and assess_duration(snapshot.duration) is DurationEligibility.PASS


def _validate_snapshot_fields(snapshot: ReviewSnapshot) -> None:
    """@description: Keep approval, validation errors and duration prerequisites consistent.
    @param snapshot: Candidate snapshot under construction.
    @return: None.
    """
    if snapshot.status in {ReviewStatus.PENDING_REVIEW, ReviewStatus.APPROVED} and not _duration_passes(snapshot):
        raise InvalidReviewTransition("Review requires passing full-video duration")
    if snapshot.status is ReviewStatus.APPROVED:
        if not isinstance(snapshot.approval_source, ApprovalSource):
            raise InvalidReviewTransition("Approval source is required")
    elif snapshot.approval_source is not None:
        raise InvalidReviewTransition("Only approved reviews have an approval source")
    if snapshot.status is ReviewStatus.VALIDATION_FAILED:
        if not isinstance(snapshot.validation_error, ValidationError) or snapshot.duration is not None:
            raise InvalidReviewTransition("Failed validation requires an error and no usable evidence")
    elif snapshot.validation_error is not None:
        raise InvalidReviewTransition("Only failed validation has a validation error")


def _require_active_version(snapshot: ReviewSnapshot, expected_version: int) -> None:
    """@description: Reject stale work and leave terminal policies unimplemented.
    @param snapshot: Current authoritative offline state supplied by the caller.
    @param expected_version: Revision against which the decision was prepared.
    @return: None.
    """
    if type(expected_version) is not int or expected_version != snapshot.version:
        raise StaleReviewVersion("Review version no longer matches")
    if snapshot.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
        raise InvalidReviewTransition("Terminal review cannot be reopened or revoked")


def record_validation(snapshot: ReviewSnapshot, evidence: DurationEvidence, expected_version: int) -> ReviewSnapshot:
    """@description: Turn duration evidence into review progress without approving music.
    @param snapshot: Current pending or failed review, possibly awaiting reexamination.
    @param evidence: Full-video assertion for the same platform identity.
    @param expected_version: Revision used by the validation worker.
    @return: New revision pending validation, awaiting review or rejected for duration.
    """
    _require_active_version(snapshot, expected_version)
    if evidence.video != snapshot.video:
        raise InvalidReviewTransition("Duration evidence belongs to another video")
    # [duration decision]: Passing duration still requires an explicit song decision.
    duration_result = assess_duration(evidence)
    # [review progression]: Map duration evidence to the next state without approving song eligibility.
    next_status = {
        DurationEligibility.PASS: ReviewStatus.PENDING_REVIEW,
        DurationEligibility.REJECT: ReviewStatus.REJECTED,
        DurationEligibility.PENDING_VALIDATION: ReviewStatus.PENDING_VALIDATION,
    }[duration_result]
    # [reexamination block]: Duration rejection ends reexamination; every other outcome keeps the block.
    still_pending = snapshot.reexamination_pending and next_status is not ReviewStatus.REJECTED
    # [released priority]: A duration rejection is a definitive negative decision, like an Owner rejection.
    released = snapshot.priority_released or next_status is ReviewStatus.REJECTED
    return replace(snapshot, version=snapshot.version + 1, status=next_status,
                   duration=evidence, validation_error=None, reexamination_pending=still_pending,
                   priority_released=released)


def record_validation_error(snapshot: ReviewSnapshot, error: ValidationError, expected_version: int) -> ReviewSnapshot:
    """@description: Preserve retryable failures without granting qualification or rejecting content.
    @param snapshot: Current nonterminal review.
    @param error: Structured validation failure, separate from review source.
    @param expected_version: Revision used by the failed operation.
    @return: New failed revision with prior usable evidence removed.
    """
    _require_active_version(snapshot, expected_version)
    return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.VALIDATION_FAILED,
                   duration=None, validation_error=error)


def approve_review(snapshot: ReviewSnapshot, source: ApprovalSource, expected_version: int, *, song_eligible: bool) -> ReviewSnapshot:
    """@description: Apply identical hard gates to synthetic automatic and Owner decisions; refuse reexamination.
    Approval of a reopened review is refused until a later TK-15 reexamination ordering event exists, so the original
    accepted priority is never reused; the Owner may still reject it.
    @param snapshot: Review awaiting a song eligibility decision.
    @param source: Decision path, not proof of authorization or a real automatic classifier.
    @param expected_version: Revision used by the decision maker.
    @param song_eligible: Explicit positive song decision from a future approved policy.
    @return: Approved revision, without any persistence or Nickel issuance.
    """
    _require_active_version(snapshot, expected_version)
    if snapshot.reexamination_pending:
        raise InvalidReviewTransition("Reexamination pending: approval waits for the TK-15 ordering event")
    if snapshot.status is not ReviewStatus.PENDING_REVIEW or song_eligible is not True:
        raise InvalidReviewTransition("Explicit song eligibility and pending review are required")
    if not _duration_passes(snapshot):
        raise InvalidReviewTransition("Passing full-video duration is required")
    return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.APPROVED, approval_source=source)


def reject_review(snapshot: ReviewSnapshot, expected_version: int) -> ReviewSnapshot:
    """@description: Represent an explicit negative song decision, including a reopened reexamination.
    @param snapshot: Review currently awaiting the song decision.
    @param expected_version: Revision used by the decision maker.
    @return: Rejected revision with no reward qualification, no reexamination block and released priority.
    """
    _require_active_version(snapshot, expected_version)
    if snapshot.status is not ReviewStatus.PENDING_REVIEW:
        raise InvalidReviewTransition("A pending song review is required")
    return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.REJECTED,
                   reexamination_pending=False, priority_released=True)


def is_submission_reward_eligible(snapshot: ReviewSnapshot, disposition: SubmissionDisposition) -> bool:
    """@description: Expose necessary offline award conditions without proving global novelty or paying.
    @param snapshot: Reviewed video whose hard gates remain valid.
    @param disposition: Caller-supplied novelty outcome; unknown and duplicate never qualify.
    @return: Whether approved review and a new candidate satisfy future award preconditions only.
    """
    return (snapshot.status is ReviewStatus.APPROVED and _duration_passes(snapshot)
            and disposition is SubmissionDisposition.NEW_CANDIDATE)



def reopen_for_reexamination(snapshot: ReviewSnapshot, expected_version: int, evidence_reference: str) -> ReviewSnapshot:
    """@description: Reopen a terminal decision after new evidence, keeping the old decision in history and no award.
    Only the Owner's formal reexamination entry may call this; ordinary approve and reject never reach it. The
    snapshot does not carry the reference; the audit row written with this revision stores it.
    Allowed transitions (APPROVED or REJECTED only; VALIDATION_FAILED and pending states are refused):
    - APPROVED, or REJECTED with passing duration -> PENDING_REVIEW, passing duration kept, reexamination block set.
    - REJECTED by a failing duration -> PENDING_VALIDATION, failing duration cleared, reexamination block set. The
      next record_validation must pass the same duration gate; approval is never reachable from this state directly.
    In every case approval_source is cleared. A rejected review always leaves reopen with priority released, even when a
    legacy payload stored the flag as False, so it never regains blocking power; the original accepted_sequence stays
    in the record as history and never restores priority.
    @param snapshot: Approved or rejected review whose recorded decision is being challenged.
    @param expected_version: Revision the Owner examined before requesting reexamination.
    @param evidence_reference: Nonempty trusted reference to the new evidence record.
    @return: Pending review or pending validation with a bumped revision and the reexamination block set.
    """
    require_reference(evidence_reference)
    if type(expected_version) is not int or expected_version != snapshot.version:
        raise StaleReviewVersion("Review version no longer matches")
    if snapshot.status not in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
        raise InvalidReviewTransition("Only an approved or rejected review can be reopened")
    # [released priority]: A rejection already released its predecessor's priority, whatever the stored flag says.
    released = snapshot.priority_released or snapshot.status is ReviewStatus.REJECTED
    if _duration_passes(snapshot):
        return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.PENDING_REVIEW,
                       approval_source=None, reexamination_pending=True, priority_released=released)
    # [revalidation requirement]: Failing duration evidence is discarded so new evidence must pass the gate.
    return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.PENDING_VALIDATION,
                   duration=None, approval_source=None, reexamination_pending=True, priority_released=released)
