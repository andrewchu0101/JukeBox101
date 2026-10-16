"""Coordinate offline URL, Hybrid Review, identity, priority and presentation contracts.

Trusted callers supply all evidence and intake facts; results never persist events, claim rewards or contact providers.
"""

from dataclasses import dataclass, replace
from random import Random

from bot.eligibility import DurationEvidence
from bot.review import (ApprovalSource, ReviewSnapshot, ReviewStatus, StaleReviewVersion, ValidationError,
                        approve_review, record_validation, record_validation_error, reject_review)
from bot.reward_candidates import (RewardCandidate, RewardCandidateStatus, RewardDecision,
                                   RewardDecisionReason, evaluate_reward_candidates)
from bot.share_replies import ShareScenario, classify_share_scenario, select_share_reply
from bot.song_identity import VideoIdentity
from bot.submission_snapshot import SubmissionSnapshot, aggregate_snapshot
from bot.urls import YouTubeVideo, parse_youtube_url
from bot.version_identity import VersionAssignment, VersionIdentityStatus, VersionRelation, compare_version_assignments


@dataclass(frozen=True)
class SubmissionRequest:
    """Supply trusted intake facts and optional version-checked internal review actions."""

    source_url: str  # [user input]: Parsed locally, never treated as eligibility evidence.
    operation_id: str  # [idempotency identity]: Assigned by the future intake boundary.
    submitter_id: int  # [claimant]: Trusted user identifier.
    accepted_sequence: int  # [intake order]: Already accepted database order, simulated offline.
    identity: VersionAssignment  # [version evidence]: Internal assertion, never inferred from URL.
    review: ReviewSnapshot  # [current review]: Authoritative revision supplied by trusted callers.
    expected_review_version: int  # [concurrency token]: Required even when no action is requested.
    duration: DurationEvidence | None = None  # [validation action]: Full original video evidence only.
    approval_source: ApprovalSource | None = None  # [review action]: Does not authenticate an Owner.
    song_eligible: bool | None = None  # [song decision]: None means no decision yet.
    validation_error: ValidationError | None = None  # [retryable failure]: Never a content rejection.


@dataclass(frozen=True)
class SubmissionResult:
    """Separate event intent, business facts and approved presentation without side effects."""

    video: YouTubeVideo  # [URL provenance]: Original input and canonical single-video URL.
    candidate: RewardCandidate  # [accepted operation]: No actual persistence is performed.
    reward: RewardDecision  # [offline decision]: READY is not a unique claim or payment.
    create_share_event: bool  # [event intent]: False for technical retries; not an inserted row count.
    scenario: ShareScenario | None  # [presentation key]: Never determines business status.
    reply: str | None  # [approved copy]: Chosen from the scenario's isolated approved pool.


def _validate_review_request(request: SubmissionRequest) -> None:
    """@description: Reject stale or contradictory review actions before applying any transition.
    @param request: Trusted internal review action and expected revision.
    @return: None.
    """
    if type(request.expected_review_version) is not int or request.expected_review_version != request.review.version:
        raise StaleReviewVersion("Review version no longer matches")
    if request.duration is not None and request.validation_error is not None:
        raise ValueError("Validation evidence and failure are mutually exclusive")
    if request.song_eligible is not None and type(request.song_eligible) is not bool:
        raise ValueError("Song eligibility must be an explicit internal decision")


def _review_request(request: SubmissionRequest) -> ReviewSnapshot:
    """@description: Apply existing versioned transitions without bypassing hard duration gates.
    @param request: Trusted internal review action bound to one parsed video.
    @return: New pure review snapshot or the unchanged current revision.
    """
    _validate_review_request(request)
    # [review progression]: Every action uses the immediately preceding immutable revision.
    review = request.review
    if request.validation_error is not None:
        review = record_validation_error(review, request.validation_error, review.version)
    if request.duration is not None:
        review = record_validation(review, request.duration, review.version)
    if request.approval_source is not None:
        review = approve_review(review, request.approval_source, review.version, song_eligible=request.song_eligible)
    elif request.song_eligible is False and review.status is ReviewStatus.PENDING_REVIEW:
        review = reject_review(review, review.version)
    elif request.song_eligible is True:
        raise ValueError("Positive song decisions require an approval source")
    return review


def _historical_gate(candidate: RewardCandidate, decision: RewardDecision, snapshot: SubmissionSnapshot) -> RewardDecision:
    """@description: Keep unresolved legacy recordings and conflicting catalog evidence from reopening rewards.
    @param candidate: Unsettled operation with a trusted identity assertion.
    @param decision: Existing evaluator result, never an accounting instruction.
    @param snapshot: Legacy catalog evidence, including unresolved entries.
    @return: Preserved award or conservatively blocked decision when catalog evidence is insufficient.
    """
    if decision.status is RewardCandidateStatus.AWARDED:
        return decision
    if _catalog_conflict(candidate, snapshot):
        return RewardDecision(candidate.operation_id, RewardCandidateStatus.CONFLICT, RewardDecisionReason.EVIDENCE_CONFLICT)
    if decision.status is not RewardCandidateStatus.READY:
        return decision
    for item in snapshot.historical:
        if item.status is VersionIdentityStatus.UNRESOLVED and not any(
                exclusion.video == item.video and exclusion.version == candidate.identity.version
                for exclusion in snapshot.exclusions):
            return RewardDecision(candidate.operation_id, RewardCandidateStatus.WAITING_IDENTITY,
                                  RewardDecisionReason.IDENTITY_UNRESOLVED)
    return decision


def _catalog_conflict(candidate: RewardCandidate, snapshot: SubmissionSnapshot) -> bool:
    """@description: Detect contradictory legacy assertions without silently choosing a winning identity.
    @param candidate: Operation whose version may be affected.
    @param snapshot: All legacy assertions and scoped exclusions.
    @return: Whether conflicting legacy mappings could affect this candidate.
    """
    for item in snapshot.historical:
        if item.video == candidate.identity.video and compare_version_assignments(item, candidate.identity) is VersionRelation.CONFLICT:
            return True
        # [catalog contradiction]: Conflicting assignments and exclusions cannot certify novelty.
        conflicting = _catalog_entry_conflicts(item, snapshot)
        if conflicting and (item.version is None or item.version == candidate.identity.version
                            or item.video == candidate.identity.video):
            return True
    return False


def _catalog_entry_conflicts(item: VersionAssignment, snapshot: SubmissionSnapshot) -> bool:
    """@description: Preserve contradictory catalog mappings and their negative evidence.
    @param item: One trusted historical version assertion.
    @param snapshot: All historical assertions and exclusions in the consistent scope.
    @return: Whether this assertion contradicts another catalog fact.
    """
    return (any(compare_version_assignments(item, other) is VersionRelation.CONFLICT for other in snapshot.historical)
            or any(exclusion.video == item.video and exclusion.version == item.version for exclusion in snapshot.exclusions))


def _retry_result(candidate: RewardCandidate, snapshot: SubmissionSnapshot,
                  original: SubmissionResult | None, random_source: Random | None) -> SubmissionResult | None:
    """@description: Preserve an original operation receipt instead of creating a second share or reapproving it.
    @param candidate: Current request's immutable intake attributes.
    @param snapshot: Trusted history containing every matching operation record.
    @param original: Trusted original receipt required for a technical replay.
    @param random_source: Optional reproducible presentation selector.
    @return: Original business outcome with retry presentation, or None for a new operation.
    """
    if classify_share_scenario(candidate, snapshot.candidates) is not ShareScenario.RETRY_SAME_OPERATION:
        if original is not None:
            raise ValueError("Original receipt requires matching operation history")
        return None
    if original is None or original.candidate.operation_id != candidate.operation_id:
        raise ValueError("A trusted original result is required for operation replay")
    if original.reward.operation_id != candidate.operation_id:
        raise ValueError("Original receipt reward belongs to another operation")
    classify_share_scenario(candidate, (original.candidate,))
    return replace(original, create_share_event=False, scenario=ShareScenario.RETRY_SAME_OPERATION,
                   reply=select_share_reply(ShareScenario.RETRY_SAME_OPERATION, random_source))


def submit_offline(request: SubmissionRequest, snapshot: SubmissionSnapshot, *,
                   original_result: SubmissionResult | None = None, random_source: Random | None = None) -> SubmissionResult:
    """@description: Integrate trusted offline facts into one nonpersistent submission outcome.
    @param request: User URL plus explicitly trusted identity, review and intake evidence.
    @param snapshot: Independent manifest and all existing operation/catalog facts at one revision.
    @param original_result: Original immutable receipt for a technical retry, supplied by future persistence.
    @param random_source: Injectable selector for approved presentation only.
    @return: Structured event intent, review, reward decision and approved reply.
    """
    # [normalized submission]: Parameters never alter full-video evidence or recording identity.
    video = parse_youtube_url(request.source_url)
    if request.identity.video != VideoIdentity(video.video_id):
        raise ValueError("Identity evidence belongs to another URL video")
    # [replay probe]: Validate immutable intake attributes before applying any new review action.
    candidate = RewardCandidate(request.operation_id, request.submitter_id, request.accepted_sequence,
                                request.identity, request.review)
    # [original operation outcome]: Technical retries preserve prior business semantics.
    retry = _retry_result(candidate, snapshot, original_result, random_source)
    if retry is not None:
        return retry
    candidate = replace(candidate, review=_review_request(request))
    return _evaluate_submission(video, candidate, snapshot, random_source)


def _evaluate_submission(video: YouTubeVideo, candidate: RewardCandidate, snapshot: SubmissionSnapshot,
                         random_source: Random | None) -> SubmissionResult:
    """@description: Evaluate the accepted operation inside its manifest-certified global scope.
    @param video: Parsed source provenance and canonical URL.
    @param candidate: Newly accepted operation with completed pure review transitions.
    @param snapshot: Existing rows and expected scope that already includes this operation's intake key.
    @param random_source: Injectable approved-copy selector.
    @return: Offline event intent without saving, claiming or awarding anything.
    """
    # [combined snapshot]: Never manufacture or extend the independent scope manifest from request rows.
    combined = replace(snapshot, candidates=snapshot.candidates + (candidate,))
    # [reward scope]: Existing evaluator validates operation and accepted-sequence uniqueness.
    decisions = evaluate_reward_candidates(aggregate_snapshot(combined))
    # [submission decision]: Include the unknown-history gate before exposing readiness.
    decision = next(item for item in decisions if item.operation_id == candidate.operation_id)
    decision = _historical_gate(candidate, decision, combined)
    # [presentation context]: Historical catalog aliases participate in presentation separately below.
    scenario = classify_share_scenario(candidate, snapshot.candidates)
    if scenario is None and any(item.video != candidate.identity.video
                                and compare_version_assignments(candidate.identity, item) is VersionRelation.SAME
                                for item in snapshot.historical):
        scenario = ShareScenario.SAME_VERSION_DIFFERENT_VIDEO
    return SubmissionResult(video, candidate, decision, True, scenario,
                            select_share_reply(scenario, random_source) if scenario is not None else None)
