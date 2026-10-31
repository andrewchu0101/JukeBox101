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


class DurationEvidenceExpired(InvalidReviewTransition):
    """Refuse an approval whose only duration basis is an official-data conclusion that outlived its retention window."""


@dataclass(frozen=True)
class DurationVerdict:
    """Carry the pass or reject conclusion drawn from official API data, without the data itself."""

    video: VideoIdentity  # [conclusion subject]: A conclusion about one video cannot open another video's review.
    outcome: DurationEligibility  # [conclusion]: Only PASS or REJECT; the measured seconds live in the short-lived observation table.

    def __post_init__(self) -> None:
        """@description: Refuse a malformed or undecided conclusion before it can authorize a review step.
        @param self: Immutable verdict being constructed.
        @return: None.
        """
        if not isinstance(self.video, VideoIdentity):
            raise InvalidReviewTransition("Duration verdict needs a video identity")
        if self.outcome not in (DurationEligibility.PASS, DurationEligibility.REJECT):
            raise InvalidReviewTransition("A duration verdict is either pass or reject")


@dataclass(frozen=True)
class ReviewSnapshot:
    """Carry one video's review revision with evidence, source and error kept separate."""

    video: VideoIdentity  # [review subject]: Evidence must refer to this exact video.
    version: int = 0  # [decision revision]: Every accepted transition increments this token.
    status: ReviewStatus = ReviewStatus.PENDING_VALIDATION
    duration: DurationEvidence | None = None
    approval_source: ApprovalSource | None = None
    validation_error: ValidationError | None = None
    # [reexamination block]: Set by the Owner's reopen; the approval that passes it creates the new ordering event.
    reexamination_pending: bool = False
    # [released priority]: Set by a negative decision or by reopening an approved, unawarded submission; never restored.
    priority_released: bool = False
    # [ordering event]: System sequence assigned when a reexamination approval took effect; it replaces the intake order for priority.
    ordering_sequence: int | None = None
    # [API conclusion]: Set only by the official-data path; omitted from the stored JSON while None so older releases keep reading every other row.
    duration_verdict: DurationVerdict | None = None

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
        if self.ordering_sequence is not None and (type(self.ordering_sequence) is not int or self.ordering_sequence <= 0):
            raise InvalidReviewTransition("Invalid ordering sequence")
        if self.reexamination_pending and self.status in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
            raise InvalidReviewTransition("Only an unresolved review can await reexamination")
        if self.duration is not None:
            if not isinstance(self.duration, DurationEvidence) or self.duration.video != self.video:
                raise InvalidReviewTransition("Duration evidence belongs to another video")
        if self.duration_verdict is not None and (not isinstance(self.duration_verdict, DurationVerdict) or self.duration_verdict.video != self.video):
            raise InvalidReviewTransition("Duration verdict belongs to another video")
        _validate_snapshot_fields(self)


def _duration_passes(snapshot: ReviewSnapshot) -> bool:
    """@description: Centralize the hard gate shared by review and future award preconditions.
    @param snapshot: Review with evidence bound to the same platform identity.
    @return: Whether reliable full-video duration passes the inclusive limits.
    """
    if snapshot.duration is not None and assess_duration(snapshot.duration) is DurationEligibility.PASS:
        return True
    return snapshot.duration is None and snapshot.duration_verdict is not None and snapshot.duration_verdict.outcome is DurationEligibility.PASS


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
        if not isinstance(snapshot.validation_error, ValidationError) or snapshot.duration is not None or snapshot.duration_verdict is not None:
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
                   duration=None, duration_verdict=None, validation_error=error)


def record_duration_verdict(snapshot: ReviewSnapshot, verdict: DurationVerdict, expected_version: int) -> ReviewSnapshot:
    """@description: Turn an official-data conclusion into review progress with the same transitions as duration evidence, without approving music.
    @param snapshot: Current pending or failed review, possibly awaiting reexamination.
    @param verdict: Pass or reject conclusion for the same platform identity.
    @param expected_version: Revision used by the validation worker.
    @return: New revision awaiting review or rejected for duration.
    """
    _require_active_version(snapshot, expected_version)
    if verdict.video != snapshot.video:
        raise InvalidReviewTransition("Duration verdict belongs to another video")
    next_status = ReviewStatus.PENDING_REVIEW if verdict.outcome is DurationEligibility.PASS else ReviewStatus.REJECTED
    # [reexamination block]: Rejection ends reexamination, exactly as for duration evidence.
    still_pending = snapshot.reexamination_pending and next_status is not ReviewStatus.REJECTED
    released = snapshot.priority_released or next_status is ReviewStatus.REJECTED
    return replace(snapshot, version=snapshot.version + 1, status=next_status, duration_verdict=verdict, validation_error=None,
                   reexamination_pending=still_pending, priority_released=released)


def approve_review(snapshot: ReviewSnapshot, source: ApprovalSource, expected_version: int, *, song_eligible: bool,
                   ordering_sequence: int | None = None) -> ReviewSnapshot:
    """@description: Apply identical hard gates to approvals; a reexamination approval is the reexamination pass and creates its ordering event in the same revision.
    Owner ruling 2026-10-23: approval of a reopened review requires the new system ordering sequence, which replaces the old
    position at the tail; ordinary approvals never carry one. The persistence layer allocates the sequence inside the locked transaction.
    @param snapshot: Review awaiting a song eligibility decision.
    @param source: Decision path, not proof of authorization or a real automatic classifier.
    @param expected_version: Revision used by the decision maker.
    @param song_eligible: Explicit positive song decision from a future approved policy.
    @param ordering_sequence: New system ordering sequence for a reexamination approval, or None for an ordinary approval.
    @return: Approved revision, without any persistence or Nickel issuance.
    """
    _require_active_version(snapshot, expected_version)
    if snapshot.reexamination_pending and ordering_sequence is None:
        raise InvalidReviewTransition("Reexamination approval requires a new ordering event")
    if not snapshot.reexamination_pending and ordering_sequence is not None:
        raise InvalidReviewTransition("An ordering event belongs only to a reexamination approval")
    if snapshot.status is not ReviewStatus.PENDING_REVIEW or song_eligible is not True:
        raise InvalidReviewTransition("Explicit song eligibility and pending review are required")
    if not _duration_passes(snapshot):
        raise InvalidReviewTransition("Passing full-video duration is required")
    return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.APPROVED, approval_source=source,
                   reexamination_pending=False, priority_released=False,
                   ordering_sequence=ordering_sequence if ordering_sequence is not None else snapshot.ordering_sequence)


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



def effective_priority_released(snapshot: ReviewSnapshot, *, awarded: bool) -> bool:
    """@description: Decide whether a submission's original priority is yielded, deriving the ruling for data written before it.
    Reopened reviews written by the ruling carry priority_released=True. A reexamination-pending review with the flag False can only
    come from an approval reopened before the ruling, so it yields too unless it was already awarded; awarded items keep their semantics.
    @param snapshot: Stored review of the submission.
    @param awarded: Whether a first-reward claim already exists for the submission.
    @return: True when the submission no longer blocks later same-version submissions by its original position.
    """
    legacy_reopened_unawarded = snapshot.reexamination_pending and not awarded
    return snapshot.priority_released or legacy_reopened_unawarded


def reopen_for_reexamination(snapshot: ReviewSnapshot, expected_version: int, evidence_reference: str, *,
                             awarded: bool = False) -> ReviewSnapshot:
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
    @param awarded: Whether a first-reward claim already exists for this submission, which keeps its priority unchanged.
    @return: Pending review or pending validation with a bumped revision and the reexamination block set.
    """
    require_reference(evidence_reference)
    if type(expected_version) is not int or expected_version != snapshot.version:
        raise StaleReviewVersion("Review version no longer matches")
    if snapshot.status not in {ReviewStatus.APPROVED, ReviewStatus.REJECTED}:
        raise InvalidReviewTransition("Only an approved or rejected review can be reopened")
    # [released priority]: Owner ruling 2026-10-23: an approved submission that was not yet awarded yields its priority on reopen.
    yields_priority = snapshot.status is ReviewStatus.APPROVED and not awarded
    released = snapshot.priority_released or snapshot.status is ReviewStatus.REJECTED or yields_priority
    if _duration_passes(snapshot):
        return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.PENDING_REVIEW,
                       approval_source=None, reexamination_pending=True, priority_released=released)
    # [revalidation requirement]: Failing duration evidence is discarded so new evidence must pass the gate.
    return replace(snapshot, version=snapshot.version + 1, status=ReviewStatus.PENDING_VALIDATION,
                   duration=None, approval_source=None, reexamination_pending=True, priority_released=released)
