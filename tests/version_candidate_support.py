"""Build synthetic version and review snapshots without external services.

Helpers use the accepted Phase 2B.1 transition functions and simulate database intake order explicitly.
"""

from datetime import datetime

from bot.eligibility import DurationEvidence
from bot.review import (
    ApprovalSource, ReviewSnapshot, ReviewStatus, ValidationError,
    approve_review, record_validation, record_validation_error, reject_review,
)
from bot.reward_candidates import RewardCandidate, RewardEvaluationInput
from bot.song_identity import VideoIdentity
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionExclusion, VersionIdentityStatus


def synthetic_review(video: VideoIdentity, status: ReviewStatus) -> ReviewSnapshot:
    """@description: Reuse production pure transitions instead of fabricating approved review fields.
    @param video: Synthetic video being qualified.
    @param status: Desired legitimate review state.
    @return: Offline review with reliable synthetic duration when required.
    """
    # [review baseline]: No real verification or Owner authorization is represented.
    snapshot = ReviewSnapshot(video)
    if status is ReviewStatus.PENDING_VALIDATION:
        return snapshot
    if status is ReviewStatus.VALIDATION_FAILED:
        return record_validation_error(snapshot, ValidationError.METADATA_UNAVAILABLE, 0)
    # [full-video fixture]: Exact synthetic seconds, never read from an API or timestamp parameter.
    evidence = DurationEvidence(video, 180, "synthetic-duration", reliable=True, full_video=True)
    snapshot = record_validation(snapshot, evidence, 0)
    if status is ReviewStatus.APPROVED:
        return approve_review(snapshot, ApprovalSource.OWNER, 1, song_eligible=True)
    if status is ReviewStatus.REJECTED:
        return reject_review(snapshot, 1)
    return snapshot


def make_candidate(sequence: int, version_name: str | None = "original", *,
                   operation_id: str | None = None, submitter_id: int | None = None,
                   video_id: str | None = None, review_status: ReviewStatus = ReviewStatus.APPROVED,
                   awarded: bool = False, identity_status: VersionIdentityStatus | None = None,
                   accepted_at: datetime | None = None) -> RewardCandidate:
    """@description: Simulate trusted intake and mapping facts without implementing their acquisition.
    @param sequence: Synthetic successful database acceptance sequence.
    @param version_name: Explicit synthetic canonical identity, or unknown.
    @param operation_id: Optional business operation override for retry scenarios.
    @param submitter_id: Optional synthetic user override.
    @param video_id: Optional exact-video alias override.
    @param review_status: Desired pure Phase 2B.1 review status.
    @param awarded: Existing settlement fact supplied only as test input.
    @param identity_status: Optional conflict or unresolved identity override.
    @param accepted_at: Informational timezone-aware time, never used for expiration.
    @return: Immutable candidate carrying no actual settlement.
    """
    # [platform subject]: Different synthetic videos may explicitly map to the same recording.
    video = VideoIdentity(video_id or f"video{sequence:06d}")
    # [trusted version fact]: Names are opaque test identifiers, not inferred titles.
    version = RecordingVersionIdentity(version_name) if version_name is not None else None
    # [identity evidence state]: Unresolved has no asserted positive recording reference.
    status = identity_status or (VersionIdentityStatus.CONFIRMED if version else VersionIdentityStatus.UNRESOLVED)
    # [provenance fixture]: Simulates an internal decision reference, not verified external proof.
    reference = None if status is VersionIdentityStatus.UNRESOLVED else "synthetic-identity"
    # [mapping fixture]: No automatic cross-video matching occurs in this helper.
    identity = VersionAssignment(video, status, version, reference)
    return RewardCandidate(operation_id or f"op-{sequence}", submitter_id or sequence,
                           sequence, identity, synthetic_review(video, review_status), awarded, accepted_at)


def make_context(*candidates: RewardCandidate, history: tuple[str, ...] = (),
                 exclusions: tuple[VersionExclusion, ...] = (), complete: bool = True) -> RewardEvaluationInput:
    """@description: Declare complete synthetic scopes without claiming real database completeness.
    @param candidates: All synthetic potentially competing operations.
    @param history: Explicit already-cataloged version identifiers.
    @param exclusions: Trusted negative identity fixture assertions.
    @param complete: Whether the test asserts that no predecessors are missing.
    @return: Immutable offline evaluation scope.
    """
    return RewardEvaluationInput(candidates, frozenset(RecordingVersionIdentity(item) for item in history), exclusions, complete)
