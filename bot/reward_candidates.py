"""Evaluate offline first-submission priority over a trusted complete candidate snapshot.

Results describe necessary settlement conditions only; no database order, accounting or external action is created.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from bot.review import ReviewSnapshot, ReviewStatus
from bot.version_identity import (
    RecordingVersionIdentity, VersionAssignment, VersionExclusion, VersionIdentityStatus,
    require_reference,
)


class RewardCandidateStatus(Enum):
    """Keep reward scheduling independent from a video's Hybrid Review status."""

    WAITING_IDENTITY = "waiting_identity"
    WAITING_VALIDATION = "waiting_validation"
    WAITING_PREDECESSOR = "waiting_predecessor"
    READY = "ready"
    AWARDED = "awarded"
    INELIGIBLE = "ineligible"
    CONFLICT = "conflict"


class RewardDecisionReason(Enum):
    """Expose stable business reasons without coupling eligibility to Discord text."""

    IDENTITY_UNRESOLVED = "identity_unresolved"
    REVIEW_INCOMPLETE = "review_incomplete"
    REEXAMINATION_PENDING = "reexamination_pending"
    REVIEW_REJECTED = "review_rejected"
    EARLIER_SUBMISSION = "earlier_submission"
    HISTORICAL_VERSION = "historical_version"
    VERSION_ALREADY_AWARDED = "version_already_awarded"
    PREVIOUSLY_AWARDED = "previously_awarded"
    EVIDENCE_CONFLICT = "evidence_conflict"
    INCOMPLETE_SNAPSHOT = "incomplete_snapshot"
    FIRST_ELIGIBLE = "first_eligible"


@dataclass(frozen=True)
class RewardCandidate:
    """Carry trusted accepted-order, identity, review and existing-award facts for one operation."""

    operation_id: str  # [request identity]: Stable business key supplied by a future intake layer.
    submitter_id: int  # [claimant]: Internal Discord user identity, never an authorization assertion.
    accepted_sequence: int  # [priority]: Simulated successful database intake order, not a timestamp.
    identity: VersionAssignment  # [version evidence]: Trusted mapping, not title-based inference.
    review: ReviewSnapshot  # [video qualification]: Reuse the unchanged Phase 2B.1 hard gates.
    awarded: bool = False  # [settlement fact]: Existing external result; never set by this evaluator.
    accepted_at: datetime | None = None  # [audit time]: Informational only; never expires priority.

    def __post_init__(self) -> None:
        """@description: Reject malformed priority facts before any candidate can become ready.
        @param self: Trusted internal operation snapshot under construction.
        @return: None.
        """
        require_reference(self.operation_id)
        if type(self.submitter_id) is not int or self.submitter_id <= 0:
            raise ValueError("A positive submitter identifier is required")
        if type(self.accepted_sequence) is not int or self.accepted_sequence <= 0:
            raise ValueError("A positive accepted sequence is required")
        if not isinstance(self.identity, VersionAssignment) or not isinstance(self.review, ReviewSnapshot):
            raise ValueError("Trusted identity and review contracts are required")
        if self.identity.video != self.review.video or type(self.awarded) is not bool:
            raise ValueError("Review subject or award fact is invalid")
        _validate_audit_time(self.accepted_at)


@dataclass(frozen=True)
class RewardEvaluationInput:
    """Supply a complete trusted scope of possible predecessors, mappings and catalog history."""

    candidates: tuple[RewardCandidate, ...]  # [evaluation scope]: Must include every possible predecessor.
    historical_versions: frozenset[RecordingVersionIdentity] = frozenset()  # [legacy catalog]: Never reopened.
    exclusions: tuple[VersionExclusion, ...] = ()  # [negative evidence]: Explicit release of unknown blockers.
    complete: bool = False  # [scope assertion]: Missing scope cannot authorize READY.

    def __post_init__(self) -> None:
        """@description: Keep mutable or mistyped caller facts outside deterministic evaluation.
        @param self: Trusted complete-snapshot assertion, not proof of database completeness.
        @return: None.
        """
        if not isinstance(self.candidates, tuple) or not all(isinstance(item, RewardCandidate) for item in self.candidates):
            raise ValueError("Candidates must be an immutable tuple of reward candidates")
        if not isinstance(self.historical_versions, frozenset):
            raise ValueError("Historical versions must be immutable")
        if not all(isinstance(item, RecordingVersionIdentity) for item in self.historical_versions):
            raise ValueError("Historical entries must be recording versions")
        if not isinstance(self.exclusions, tuple) or not all(isinstance(item, VersionExclusion) for item in self.exclusions):
            raise ValueError("Exclusions must be immutable trusted evidence")
        if type(self.complete) is not bool:
            raise ValueError("Completeness must be an explicit boolean")


@dataclass(frozen=True)
class RewardDecision:
    """Return an immutable business outcome without changing candidates or awarding Nickel."""

    operation_id: str  # [decision subject]: Original stable operation key.
    status: RewardCandidateStatus  # [reward outcome]: Distinct from ReviewStatus.
    reason: RewardDecisionReason  # [business reason]: Suitable for future presentation adapters.
    blocking_operation_ids: tuple[str, ...] = ()  # [priority explanation]: Earlier unsettled operations.


def _validate_audit_time(accepted_at: datetime | None) -> None:
    """@description: Preserve explicit audit timezone without using time to expire priority.
    @param accepted_at: Optional informational intake timestamp.
    @return: None.
    """
    if accepted_at is None:
        return
    if not isinstance(accepted_at, datetime) or accepted_at.utcoffset() is None:
        raise ValueError("Audit timestamps must be timezone aware")


def evaluate_reward_candidates(context: RewardEvaluationInput) -> tuple[RewardDecision, ...]:
    """@description: Decide priority without issuing rewards or optimistically assuming complete evidence.
    @param context: Trusted complete snapshot including historical facts and identity exclusions.
    @return: Decisions ordered by accepted sequence, with at most one READY per confirmed version.
    """
    # [ordered scope]: Validate request replay and sequence uniqueness before all decisions.
    ordered = _ordered_candidates(context.candidates)
    return tuple(_evaluate_candidate(candidate, ordered, context) for candidate in ordered)


def _ordered_candidates(candidates: tuple[RewardCandidate, ...]) -> tuple[RewardCandidate, ...]:
    """@description: Collapse exact request retries and reject contradictory or tied intake facts.
    @param candidates: Caller-supplied operation snapshots in any order.
    @return: Unique operations in reliable caller-asserted accepted order.
    """
    # [idempotent operations]: Exact repeated snapshots produce one decision.
    operations: dict[str, RewardCandidate] = {}
    for candidate in candidates:
        if candidate.operation_id in operations and operations[candidate.operation_id] != candidate:
            raise ValueError("The same operation has conflicting snapshots")
        operations[candidate.operation_id] = candidate
    # [intake order]: Distinct operations cannot share a purported unique database sequence.
    sequences = [candidate.accepted_sequence for candidate in operations.values()]
    if len(sequences) != len(set(sequences)):
        raise ValueError("Different operations cannot share an accepted sequence")
    return tuple(sorted(operations.values(), key=_accepted_sequence))


def _accepted_sequence(candidate: RewardCandidate) -> int:
    """@description: Keep priority independent of input ordering and audit timestamps.
    @param candidate: Trusted intake snapshot.
    @return: Asserted successful intake order.
    """
    return candidate.accepted_sequence


def _decision(candidate: RewardCandidate, status: RewardCandidateStatus, reason: RewardDecisionReason) -> RewardDecision:
    """@description: Construct consistent non-accounting business outcomes.
    @param candidate: Operation receiving the decision.
    @param status: Independent reward scheduling state.
    @param reason: Stable business explanation.
    @return: Immutable decision without a priority blocker list.
    """
    return RewardDecision(candidate.operation_id, status, reason)


def _evaluate_candidate(candidate: RewardCandidate, ordered: tuple[RewardCandidate, ...], context: RewardEvaluationInput) -> RewardDecision:
    """@description: Preserve settlements before applying conflict, qualification and priority gates.
    @param candidate: Current operation under evaluation.
    @param ordered: Deduplicated complete operation scope.
    @param context: Trusted catalog and exclusion facts.
    @return: Candidate scheduling result, never an accounting instruction.
    """
    if candidate.awarded:
        return _decision(candidate, RewardCandidateStatus.AWARDED, RewardDecisionReason.PREVIOUSLY_AWARDED)
    if _has_identity_conflict(candidate, ordered, context):
        return _decision(candidate, RewardCandidateStatus.CONFLICT, RewardDecisionReason.EVIDENCE_CONFLICT)
    # [qualification gate]: History, rejection and identity must be settled before queue readiness.
    qualification = _qualification_decision(candidate, ordered, context)
    if qualification is not None:
        return qualification
    if not context.complete:
        return _decision(candidate, RewardCandidateStatus.WAITING_PREDECESSOR, RewardDecisionReason.INCOMPLETE_SNAPSHOT)
    # [priority blockers]: Unknown earlier identities remain unless trusted exclusions rule them out.
    blockers = tuple(item.operation_id for item in ordered if _blocks_priority(item, candidate, context))
    if blockers:
        return RewardDecision(candidate.operation_id, RewardCandidateStatus.WAITING_PREDECESSOR,
                              RewardDecisionReason.EARLIER_SUBMISSION, blockers)
    return _decision(candidate, RewardCandidateStatus.READY, RewardDecisionReason.FIRST_ELIGIBLE)


def _qualification_decision(candidate: RewardCandidate, ordered: tuple[RewardCandidate, ...], context: RewardEvaluationInput) -> RewardDecision | None:
    """@description: Withhold rewards for catalog history, failed qualification or missing identity.
    @param candidate: Current unawarded, nonconflicting operation.
    @param ordered: All known operations, including existing awarded ones.
    @param context: Trusted historical catalog facts.
    @return: Blocking or ineligible decision, or None when priority can be considered.
    """
    if candidate.review.status is ReviewStatus.REJECTED:
        return _decision(candidate, RewardCandidateStatus.INELIGIBLE, RewardDecisionReason.REVIEW_REJECTED)
    if candidate.identity.status is VersionIdentityStatus.UNRESOLVED:
        return _decision(candidate, RewardCandidateStatus.WAITING_IDENTITY, RewardDecisionReason.IDENTITY_UNRESOLVED)
    if candidate.identity.version in context.historical_versions:
        return _decision(candidate, RewardCandidateStatus.INELIGIBLE, RewardDecisionReason.HISTORICAL_VERSION)
    if any(item.awarded and item.identity.version == candidate.identity.version for item in ordered):
        return _decision(candidate, RewardCandidateStatus.INELIGIBLE, RewardDecisionReason.VERSION_ALREADY_AWARDED)
    if candidate.review.status is not ReviewStatus.APPROVED:
        # [reexamination reason]: A reopened review waits for its ordering event, not for ordinary review.
        reason = RewardDecisionReason.REEXAMINATION_PENDING if candidate.review.reexamination_pending else RewardDecisionReason.REVIEW_INCOMPLETE
        return _decision(candidate, RewardCandidateStatus.WAITING_VALIDATION, reason)
    return None


def _excluded(candidate: RewardCandidate, version: RecordingVersionIdentity | None, context: RewardEvaluationInput) -> bool:
    """@description: Release an unresolved blocker only for an explicitly evidenced excluded version.
    @param candidate: Operation whose video has uncertain identity.
    @param version: Confirmed version being considered for a reward.
    @param context: Trusted negative evidence supplied by internal review.
    @return: Whether a scoped exclusion proves the video cannot be this version.
    """
    return any(item.video == candidate.identity.video and item.version == version for item in context.exclusions)


def _blocks_priority(earlier: RewardCandidate, candidate: RewardCandidate, context: RewardEvaluationInput) -> bool:
    """@description: Protect the earliest possibly eligible submission regardless of review speed or age.
    @param earlier: Potential predecessor from accepted-order scope; a released one stays released after reopening.
    @param candidate: Confirmed, approved candidate seeking readiness.
    @param context: Trusted exclusions for unresolved predecessors.
    @return: Whether this earlier operation retains priority over the candidate.
    """
    # [released priority]: A rejection, including one later reopened, never restores the predecessor's blocking power.
    priority_released = earlier.review.status is ReviewStatus.REJECTED or earlier.review.priority_released
    if earlier.accepted_sequence >= candidate.accepted_sequence or priority_released:
        return False
    if earlier.identity.version is not None:
        return earlier.identity.version == candidate.identity.version
    return not _excluded(earlier, candidate.identity.version, context)


def _has_identity_conflict(candidate: RewardCandidate, ordered: tuple[RewardCandidate, ...], context: RewardEvaluationInput) -> bool:
    """@description: Freeze affected unsettled operations without reversing any previous award.
    @param candidate: Operation whose identity group may be compromised.
    @param ordered: Current trusted snapshot of operations and evidence.
    @param context: Explicit exclusion evidence that may itself contradict a confirmed mapping.
    @return: Whether conflicting evidence affects this operation or its version.
    """
    return any(_conflict_affects(item, candidate, ordered, context) for item in ordered)


def _conflict_affects(subject: RewardCandidate, candidate: RewardCandidate, ordered: tuple[RewardCandidate, ...], context: RewardEvaluationInput) -> bool:
    """@description: Scope positive, negative and duplicate-award conflicts to affected versions.
    @param subject: Operation potentially carrying contradictory evidence.
    @param candidate: Unsettled operation being protected.
    @param ordered: Complete current mapping scope.
    @param context: Trusted negative identity evidence.
    @return: Whether this conflicting subject can affect the candidate's version.
    """
    # [subject conflict]: Distinct confirmed assignments for one video cannot both be true.
    conflicting = subject.identity.status is VersionIdentityStatus.CONFLICT
    conflicting = conflicting or _contradictory_assignment(subject, ordered)
    conflicting = conflicting or _contradictory_exclusion(subject, context)
    conflicting = conflicting or _contradictory_review(subject, ordered)
    conflicting = conflicting or _multiple_awards(subject, ordered)
    conflicting = conflicting or _award_priority_conflict(subject, ordered, context)
    if not conflicting:
        return False
    if subject.identity.video == candidate.identity.video:
        return True
    if subject.identity.version is not None and candidate.identity.version is not None:
        return subject.identity.version == candidate.identity.version
    return not _excluded(subject, candidate.identity.version, context)


def _contradictory_assignment(subject: RewardCandidate, ordered: tuple[RewardCandidate, ...]) -> bool:
    """@description: Detect canonical mapping contradictions without attempting automatic merges.
    @param subject: Mapping assertion under inspection.
    @param ordered: Other trusted operation assertions.
    @return: Whether one video is assigned to incompatible known versions.
    """
    return any(item.identity.video == subject.identity.video
               and item.identity.version is not None and subject.identity.version is not None
               and item.identity.version != subject.identity.version for item in ordered)


def _contradictory_exclusion(subject: RewardCandidate, context: RewardEvaluationInput) -> bool:
    """@description: Refuse evidence that both includes and excludes a video from one version.
    @param subject: Known positive mapping assertion.
    @param context: Trusted negative mapping assertions.
    @return: Whether positive and negative evidence disagree.
    """
    return subject.identity.version is not None and _excluded(subject, subject.identity.version, context)


def _contradictory_review(subject: RewardCandidate, ordered: tuple[RewardCandidate, ...]) -> bool:
    """@description: Refuse incompatible current qualification evidence for the same video.
    @param subject: Current review assertion under inspection.
    @param ordered: Complete scope whose same-video reviews must agree.
    @return: Whether the caller supplied inconsistent or stale current reviews.
    """
    return any(item.identity.video == subject.identity.video and item.review != subject.review for item in ordered)


def _multiple_awards(subject: RewardCandidate, ordered: tuple[RewardCandidate, ...]) -> bool:
    """@description: Flag existing duplicate settlements while leaving their historical facts untouched.
    @param subject: Version whose existing awards are being counted.
    @param ordered: Deduplicated operation facts.
    @return: Whether more than one prior award is recorded for the same known version.
    """
    if subject.identity.version is None:
        return subject.awarded
    return sum(item.awarded and item.identity.version == subject.identity.version for item in ordered) > 1


def _award_priority_conflict(subject: RewardCandidate, ordered: tuple[RewardCandidate, ...], context: RewardEvaluationInput) -> bool:
    """@description: Freeze unsettled claims when an existing award appears to have bypassed priority.
    @param subject: Potentially settled operation supplied by the caller.
    @param ordered: Current scope including possibly earlier pending operations.
    @param context: Trusted exclusions used to establish unrelated versions.
    @return: Whether an unsettled predecessor conflicts with this historical award.
    """
    return subject.awarded and any(not item.awarded and _blocks_priority(item, subject, context) for item in ordered)
