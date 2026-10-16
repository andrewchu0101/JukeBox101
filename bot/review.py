"""Model versioned Hybrid Review decisions without persistence or external validation.

Automatic and Owner decisions share duration gates; reward eligibility is only an offline precondition.
"""

from dataclasses import dataclass, replace
from enum import Enum

from bot.eligibility import DurationEligibility, DurationEvidence, assess_duration
from bot.song_identity import SubmissionDisposition, VideoIdentity


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

    def __post_init__(self) -> None:
        """@description: Reject contradictory snapshots before they can authorize future rewards.
        @param self: Immutable review state being constructed or replaced.
        @return: None.
        """
        if not isinstance(self.video, VideoIdentity) or type(self.version) is not int or self.version < 0:
            raise InvalidReviewTransition("Invalid review identity or version")
        if not isinstance(self.status, ReviewStatus):
            raise InvalidReviewTransition("Invalid review status")
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
    @param snapshot: Current pending or failed review.
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
    return replace(snapshot, version=snapshot.version + 1, status=next_status,
                   duration=evidence, validation_error=None)


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
    """@description: Apply identical hard gates to synthetic automatic and Owner decisions.
    @param snapshot: Review awaiting a song eligibility decision.
    @param source: Decision path, not proof of authorization or a real automatic classifier.
    @param expected_version: Revision used by the decision maker.
    @param song_eligible: Explicit positive song decision from a future approved policy.
    @return: Approved revision, without any persistence or Nickel issuance.
    """
    _require_active_version(snapshot, expected_version)
    if snapshot.status is not ReviewStatus.PENDING_REVIEW or song_eligible is not True:
        raise InvalidReviewTransition("Explicit song eligibility and pending review are required")
    if not _duration_passes(snapshot):
        raise InvalidReviewTransition("Passing full-video duration is required")
    return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.APPROVED, approval_source=source)


def reject_review(snapshot: ReviewSnapshot, expected_version: int) -> ReviewSnapshot:
    """@description: Represent an explicit negative song decision after duration validation.
    @param snapshot: Review currently awaiting the song decision.
    @param expected_version: Revision used by the decision maker.
    @return: Rejected revision with no reward qualification.
    """
    _require_active_version(snapshot, expected_version)
    if snapshot.status is not ReviewStatus.PENDING_REVIEW:
        raise InvalidReviewTransition("A pending song review is required")
    return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.REJECTED)


def is_submission_reward_eligible(snapshot: ReviewSnapshot, disposition: SubmissionDisposition) -> bool:
    """@description: Expose necessary offline award conditions without proving global novelty or paying.
    @param snapshot: Reviewed video whose hard gates remain valid.
    @param disposition: Caller-supplied novelty outcome; unknown and duplicate never qualify.
    @return: Whether approved review and a new candidate satisfy future award preconditions only.
    """
    return (snapshot.status is ReviewStatus.APPROVED and _duration_passes(snapshot)
            and disposition is SubmissionDisposition.NEW_CANDIDATE)
