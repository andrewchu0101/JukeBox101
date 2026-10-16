"""Exercise immutable Hybrid Review transitions and future award gates offline.

Synthetic evidence covers failure recovery, stale results and both decision paths without services.
"""

from dataclasses import FrozenInstanceError, replace

import pytest

from bot.eligibility import DurationEvidence
from bot.review import (
    ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, StaleReviewVersion,
    ValidationError, approve_review, is_submission_reward_eligible, record_validation,
    record_validation_error, reject_review,
)
from bot.song_identity import SubmissionDisposition, VideoIdentity

VIDEO = VideoIdentity("Abc_123-Xyz")


@pytest.fixture
def valid_evidence():
    """@description: Provide synthetic full-video evidence shared by both approval paths.
    @param: none
    @return: Reliable three-minute evidence for the review subject.
    """
    return DurationEvidence(VIDEO, 180, "synthetic-fixture", reliable=True, full_video=True)


@pytest.fixture
def pending_review(valid_evidence):
    """@description: Separate passing duration from explicit song approval in every scenario.
    @param valid_evidence: Synthetic evidence passing the hard duration gate.
    @return: Revision one awaiting song review.
    """
    return record_validation(ReviewSnapshot(VIDEO), valid_evidence, 0)


def test_duration_pass_never_approves_or_qualifies(pending_review):
    """@description: Prevent the duration contract from silently certifying songs or awards.
    @param pending_review: Review after passing duration only.
    @return: None.
    """
    assert pending_review.status is ReviewStatus.PENDING_REVIEW
    assert pending_review.version == 1 and pending_review.approval_source is None
    assert not is_submission_reward_eligible(pending_review, SubmissionDisposition.NEW_CANDIDATE)


@pytest.mark.parametrize("source", list(ApprovalSource))
@pytest.mark.parametrize("disposition, eligible", [
    (SubmissionDisposition.UNKNOWN, False), (SubmissionDisposition.DUPLICATE, False),
    (SubmissionDisposition.NEW_CANDIDATE, True),
])
def test_approval_paths_share_award_gate(pending_review, source, disposition, eligible):
    """@description: Require approved music and explicit novelty independently of approval source.
    @param pending_review: Duration-qualified but unapproved song.
    @param source: Synthetic automatic or Owner decision path.
    @param disposition: Independent caller-supplied submission outcome.
    @param eligible: Expected future award precondition, never an actual payment.
    @return: None.
    """
    approved = approve_review(pending_review, source, 1, song_eligible=True)
    assert approved.status is ReviewStatus.APPROVED and approved.version == 2
    assert approved.approval_source is source
    assert is_submission_reward_eligible(approved, disposition) is eligible
    assert pending_review.status is ReviewStatus.PENDING_REVIEW


@pytest.mark.parametrize("source", list(ApprovalSource))
@pytest.mark.parametrize("song_eligible", [False, None, 1, "yes"])
def test_approval_requires_explicit_song_decision(pending_review, source, song_eligible):
    """@description: Reject implicit truthiness instead of inventing a song classifier.
    @param pending_review: Review awaiting a song decision.
    @param source: Both approval paths must satisfy the same condition.
    @param song_eligible: Missing, negative or incorrectly typed song decision.
    @return: None.
    """
    with pytest.raises(InvalidReviewTransition):
        approve_review(pending_review, source, 1, song_eligible=song_eligible)


@pytest.mark.parametrize("seconds, status", [
    (59, ReviewStatus.REJECTED), (721, ReviewStatus.REJECTED),
    (None, ReviewStatus.PENDING_VALIDATION), ("180", ReviewStatus.PENDING_VALIDATION),
])
@pytest.mark.parametrize("source", list(ApprovalSource))
def test_neither_approval_path_bypasses_duration(valid_evidence, seconds, status, source):
    """@description: Refuse manual and automatic approval of invalid or unknown duration.
    @param valid_evidence: Baseline full-video evidence.
    @param seconds: Out-of-range or unknown value.
    @param status: Required review outcome after validation.
    @param source: Automatic and Owner approval paths.
    @return: None.
    """
    snapshot = record_validation(ReviewSnapshot(VIDEO), replace(valid_evidence, seconds=seconds), 0)
    assert snapshot.status is status
    assert not is_submission_reward_eligible(snapshot, SubmissionDisposition.NEW_CANDIDATE)
    with pytest.raises(InvalidReviewTransition):
        approve_review(snapshot, source, 1, song_eligible=True)


@pytest.mark.parametrize("error", list(ValidationError))
def test_validation_failure_can_retry_without_approval(valid_evidence, error):
    """@description: Recover transient failures while preserving explicit song review.
    @param valid_evidence: Synthetic evidence returned by a later successful retry.
    @param error: Structured temporary error.
    @return: None.
    """
    failed = record_validation_error(ReviewSnapshot(VIDEO), error, 0)
    assert failed.status is ReviewStatus.VALIDATION_FAILED and failed.validation_error is error
    assert failed.duration is None and failed.approval_source is None
    assert not is_submission_reward_eligible(failed, SubmissionDisposition.NEW_CANDIDATE)
    recovered = record_validation(failed, valid_evidence, 1)
    assert recovered.status is ReviewStatus.PENDING_REVIEW and recovered.version == 2
    assert recovered.validation_error is None


def test_revalidation_failure_invalidates_previous_evidence(pending_review):
    """@description: Prevent cached passing evidence surviving a newer validation failure.
    @param pending_review: Review with previously passing evidence.
    @return: None.
    """
    failed = record_validation_error(pending_review, ValidationError.VERIFICATION_FAILED, 1)
    assert failed.duration is None
    with pytest.raises(InvalidReviewTransition):
        approve_review(failed, ApprovalSource.OWNER, 2, song_eligible=True)


@pytest.mark.parametrize("operation", ["validate", "fail", "approve", "reject"])
def test_stale_operations_preserve_newer_review(pending_review, valid_evidence, operation):
    """@description: Reject every kind of stale result before it changes a newer revision.
    @param pending_review: Current authoritative revision one.
    @param valid_evidence: Synthetic worker result.
    @param operation: Decision prepared for stale revision zero.
    @return: None.
    """
    with pytest.raises(StaleReviewVersion):
        _apply_operation(operation, pending_review, valid_evidence, 0)
    assert pending_review.version == 1 and pending_review.status is ReviewStatus.PENDING_REVIEW


@pytest.mark.parametrize("terminal", ["approve", "reject"])
@pytest.mark.parametrize("operation", ["validate", "fail", "approve", "reject"])
def test_terminal_reviews_cannot_reopen_or_repeat(pending_review, valid_evidence, terminal, operation):
    """@description: Leave rejection reopening and approval revocation outside this phase.
    @param pending_review: Ready review before its terminal decision.
    @param valid_evidence: Synthetic later worker evidence.
    @param terminal: Approval or rejection path.
    @param operation: Attempted operation against the terminal revision.
    @return: None.
    """
    decided = _apply_operation(terminal, pending_review, valid_evidence, 1)
    with pytest.raises(InvalidReviewTransition):
        _apply_operation(operation, decided, valid_evidence, 2)
    with pytest.raises(StaleReviewVersion):
        record_validation(decided, valid_evidence, 1)


@pytest.mark.parametrize("changes", [
    {"version": -1}, {"version": True}, {"status": "approved"}, {"video": "Abc_123-Xyz"},
    {"status": ReviewStatus.APPROVED, "approval_source": ApprovalSource.OWNER},
    {"status": ReviewStatus.PENDING_REVIEW}, {"approval_source": ApprovalSource.AUTO},
    {"validation_error": ValidationError.INVALID_METADATA},
    {"status": ReviewStatus.VALIDATION_FAILED}, {"duration": "180"},
])
def test_contradictory_snapshot_construction_fails(changes):
    """@description: Keep malformed snapshots from becoming award authorization shortcuts.
    @param changes: Inconsistent fields applied to the initial review.
    @return: None.
    """
    with pytest.raises(InvalidReviewTransition):
        replace(ReviewSnapshot(VIDEO), **changes)


def test_evidence_cannot_cross_video_identity(valid_evidence):
    """@description: Prevent one video's passing duration authorizing another video.
    @param valid_evidence: Evidence bound to the original video.
    @return: None.
    """
    other_review = ReviewSnapshot(VideoIdentity("Def_456-Uvw"))
    with pytest.raises(InvalidReviewTransition):
        record_validation(other_review, valid_evidence, 0)
    with pytest.raises(InvalidReviewTransition):
        replace(other_review, duration=valid_evidence)


def test_snapshot_is_immutable(pending_review):
    """@description: Prevent accidentally editing an accepted revision in place.
    @param pending_review: Frozen review value.
    @return: None.
    """
    with pytest.raises(FrozenInstanceError):
        pending_review.version = 100


@pytest.mark.parametrize("source", list(ApprovalSource))
@pytest.mark.parametrize("changes", [{"reliable": False}, {"full_video": False}, {"source": None}])
def test_untrusted_evidence_cannot_reach_approval(valid_evidence, source, changes):
    """@description: Enforce evidence reliability on automatic and manual approval equally.
    @param valid_evidence: Baseline evidence before reliability is removed.
    @param source: Both decision paths.
    @param changes: Missing source, reliable status or full-video scope.
    @return: None.
    """
    unverified = record_validation(ReviewSnapshot(VIDEO), replace(valid_evidence, **changes), 0)
    assert not is_submission_reward_eligible(unverified, SubmissionDisposition.NEW_CANDIDATE)
    with pytest.raises(InvalidReviewTransition):
        approve_review(unverified, source, 1, song_eligible=True)


@pytest.mark.parametrize("source", [None, "owner", "auto"])
def test_approval_source_must_be_explicit_enum(pending_review, source):
    """@description: Keep an arbitrary string from masquerading as a decision source.
    @param pending_review: Duration-qualified review.
    @param source: Missing or wrongly typed source.
    @return: None.
    """
    with pytest.raises(InvalidReviewTransition):
        approve_review(pending_review, source, 1, song_eligible=True)


@pytest.mark.parametrize("expected_version", [True, -1, None, "1"])
def test_invalid_revision_tokens_are_refused(pending_review, expected_version):
    """@description: Require an exact integer revision rather than coercing caller input.
    @param pending_review: Current revision one.
    @param expected_version: Invalid or mismatching token.
    @return: None.
    """
    with pytest.raises(StaleReviewVersion):
        approve_review(pending_review, ApprovalSource.OWNER, expected_version, song_eligible=True)


def test_failed_review_cannot_be_approved_directly():
    """@description: Require successful revalidation after a transient failure.
    @param: none
    @return: None.
    """
    failed = record_validation_error(ReviewSnapshot(VIDEO), ValidationError.METADATA_UNAVAILABLE, 0)
    with pytest.raises(InvalidReviewTransition):
        approve_review(failed, ApprovalSource.AUTO, 1, song_eligible=True)


def test_clip_length_cannot_replace_original_duration(valid_evidence):
    """@description: Reject a long original video despite a qualifying URL-selected segment.
    @param valid_evidence: Same video identity as the timestamped URL.
    @return: None.
    """
    original = record_validation(ReviewSnapshot(VIDEO), replace(valid_evidence, seconds=900), 0)
    clip_only = record_validation(ReviewSnapshot(VIDEO), replace(valid_evidence, full_video=False), 0)
    assert original.status is ReviewStatus.REJECTED
    assert clip_only.status is ReviewStatus.PENDING_VALIDATION
    assert not is_submission_reward_eligible(original, SubmissionDisposition.NEW_CANDIDATE)
    assert not is_submission_reward_eligible(clip_only, SubmissionDisposition.NEW_CANDIDATE)


def test_rejection_and_initial_state_have_no_award(pending_review):
    """@description: Exclude negative and not-yet-validated reviews from award eligibility.
    @param pending_review: Review that can receive a negative song decision.
    @return: None.
    """
    rejected = reject_review(pending_review, 1)
    assert rejected.status is ReviewStatus.REJECTED and rejected.approval_source is None
    assert not is_submission_reward_eligible(rejected, SubmissionDisposition.NEW_CANDIDATE)
    assert not is_submission_reward_eligible(ReviewSnapshot(VIDEO), SubmissionDisposition.NEW_CANDIDATE)


def _apply_operation(operation, snapshot, evidence, version):
    """@description: Exercise all public decision paths under the same version scenarios.
    @param operation: Synthetic test operation name.
    @param snapshot: Current review.
    @param evidence: Synthetic duration assertion.
    @param version: Expected revision token.
    @return: Result of the selected pure transition.
    """
    if operation == "validate":
        return record_validation(snapshot, evidence, version)
    if operation == "fail":
        return record_validation_error(snapshot, ValidationError.METADATA_UNAVAILABLE, version)
    if operation == "approve":
        return approve_review(snapshot, ApprovalSource.OWNER, version, song_eligible=True)
    return reject_review(snapshot, version)
