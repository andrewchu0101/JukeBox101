"""Exercise immutable Hybrid Review transitions and future award gates offline.

Synthetic evidence covers failure recovery, stale results and both decision paths without services.
"""

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from bot.database.submission_codec import decode_contract, encode_contract
from bot.eligibility import DurationEvidence
from bot.review import (
    ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, StaleReviewVersion,
    ValidationError, approve_review, is_submission_reward_eligible, record_validation,
    record_validation_error, reject_review, reopen_for_reexamination,
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


def _reopen(snapshot, expected_version=None, evidence_reference="ev-reexam-1"):
    """@description: Call the reexamination entry with the current revision unless a test overrides it.
    @param snapshot: Approved or rejected review being reopened.
    @param expected_version: Revision the Owner examined, defaulting to the snapshot's own.
    @param evidence_reference: Trusted evidence reference cited by the decision.
    @return: Result of reopen_for_reexamination.
    """
    version = snapshot.version if expected_version is None else expected_version
    return reopen_for_reexamination(snapshot, version, evidence_reference)


def _approved(pending_review):
    """@description: Build the approved state that reexamination may reopen.
    @param pending_review: Passing review awaiting an Owner song decision.
    @return: Approved revision with Owner approval source.
    """
    return approve_review(pending_review, ApprovalSource.OWNER, 1, song_eligible=True)


def test_reexamination_marker_defaults_false_for_old_payload_and_round_trips(pending_review):
    """@description: Old persisted review JSON without the marker decodes as not pending, and new payloads keep it.
    @param pending_review: Passing review used to build both payloads.
    @return: None.
    """
    reopened = _reopen(_approved(pending_review))
    legacy_document = json.loads(encode_contract(pending_review))
    del legacy_document["value"]["fields"]["reexamination_pending"]
    legacy_payload = json.dumps(legacy_document)
    assert decode_contract(legacy_payload, ReviewSnapshot).reexamination_pending is False
    assert decode_contract(encode_contract(reopened), ReviewSnapshot) == reopened
    assert decode_contract(encode_contract(reopened), ReviewSnapshot).reexamination_pending is True


@pytest.mark.parametrize("terminal", ["approve", "reject"])
def test_reopen_returns_pending_review_with_marker_and_no_approval_source(pending_review, terminal):
    """@description: Reopen approved or rejected reviews as pending with a bumped revision and no award source.
    @param pending_review: Passing review awaiting an Owner song decision.
    @param terminal: Terminal path that produced the snapshot being reopened.
    @return: None.
    """
    decided = _approved(pending_review) if terminal == "approve" else reject_review(pending_review, 1)
    reopened = _reopen(decided)
    assert reopened.status is ReviewStatus.PENDING_REVIEW
    assert reopened.reexamination_pending is True
    assert reopened.approval_source is None
    assert reopened.version == decided.version + 1


@pytest.mark.parametrize("state", ["pending_validation", "pending_review", "validation_failed"])
def test_reopen_refuses_nonterminal_states(pending_review, valid_evidence, state):
    """@description: Refuse reexamination of reviews that are still undecided or failed validation.
    @param pending_review: Passing review.
    @param valid_evidence: Synthetic evidence for constructing other states.
    @param state: Nonterminal status under test.
    @return: None.
    """
    snapshot = {
        "pending_validation": ReviewSnapshot(VIDEO),
        "pending_review": pending_review,
        "validation_failed": record_validation_error(pending_review, ValidationError.METADATA_UNAVAILABLE, 1),
    }[state]
    with pytest.raises(InvalidReviewTransition):
        _reopen(snapshot)


def test_reopen_refuses_stale_version(pending_review):
    """@description: Refuse a reexamination prepared against an earlier review revision.
    @param pending_review: Passing review.
    @return: None.
    """
    with pytest.raises(StaleReviewVersion):
        _reopen(_approved(pending_review), expected_version=0)


@pytest.mark.parametrize("reference", ["", " ev-1", "ev-1 "])
def test_reopen_requires_a_clean_nonempty_evidence_reference(pending_review, reference):
    """@description: Require an auditable evidence reference before any reexamination revision exists.
    @param pending_review: Passing review.
    @param reference: Empty or padded reference.
    @return: None.
    """
    with pytest.raises(ValueError):
        _reopen(_approved(pending_review), evidence_reference=reference)


def test_approval_without_an_ordering_event_is_refused_and_with_one_passes_the_reexamination(pending_review):
    """@description: A reopened review needs a new ordering sequence to be approved (the reexamination pass); without it the approval is refused.
    @param pending_review: Passing review.
    @return: None.
    """
    reopened = _reopen(_approved(pending_review))
    with pytest.raises(InvalidReviewTransition, match="requires a new ordering event"):
        approve_review(reopened, ApprovalSource.OWNER, reopened.version, song_eligible=True)
    passed = approve_review(reopened, ApprovalSource.OWNER, reopened.version, song_eligible=True, ordering_sequence=7)
    assert passed.status is ReviewStatus.APPROVED
    assert passed.reexamination_pending is False
    assert passed.ordering_sequence == 7
    assert passed.priority_released is False


def test_reject_clears_reexamination_marker_without_award(pending_review):
    """@description: Let the Owner reject a reopened review, which ends reexamination with no reward qualification.
    @param pending_review: Passing review.
    @return: None.
    """
    reopened = _reopen(_approved(pending_review))
    rejected = reject_review(reopened, reopened.version)
    assert rejected.status is ReviewStatus.REJECTED
    assert rejected.reexamination_pending is False
    assert rejected.approval_source is None
    assert is_submission_reward_eligible(rejected, SubmissionDisposition.NEW_CANDIDATE) is False


def test_reexamination_marker_survives_revalidation_until_rejected(pending_review, valid_evidence):
    """@description: Keep the reexamination marker through validation retries; approval needs a new ordering event, and only a rejection ends the marker.
    @param pending_review: Passing review.
    @param valid_evidence: Passing duration evidence for the retry.
    @return: None.
    """
    reopened = _reopen(_approved(pending_review))
    failed = record_validation_error(reopened, ValidationError.METADATA_UNAVAILABLE, reopened.version)
    retried = record_validation(failed, valid_evidence, failed.version)
    assert failed.reexamination_pending is True and retried.reexamination_pending is True
    with pytest.raises(InvalidReviewTransition, match="requires a new ordering event"):
        approve_review(retried, ApprovalSource.OWNER, retried.version, song_eligible=True)
    too_long = DurationEvidence(VIDEO, 999, "synthetic-long", reliable=True, full_video=True)
    assert record_validation(retried, too_long, retried.version).reexamination_pending is False


def test_terminal_snapshot_cannot_carry_reexamination_marker(pending_review):
    """@description: Refuse contradictory approved or rejected snapshots that still claim a pending reexamination.
    @param pending_review: Passing review.
    @return: None.
    """
    approved = _approved(pending_review)
    with pytest.raises(InvalidReviewTransition):
        replace(approved, reexamination_pending=True)


def test_priority_released_defaults_false_and_rejection_sets_it(pending_review):
    """@description: Mark only explicitly rejected reviews as having released reward priority.
    @param pending_review: Passing review awaiting an Owner song decision.
    @return: None.
    """
    assert ReviewSnapshot(VIDEO).priority_released is False
    assert pending_review.priority_released is False
    assert _approved(pending_review).priority_released is False
    assert reject_review(pending_review, 1).priority_released is True


def test_reopen_releases_an_approved_unawarded_priority_and_keeps_it_for_an_awarded_one(pending_review):
    """@description: Owner ruling 2026-10-23: an approved submission yields its priority on reopen unless it was already awarded.
    @param pending_review: Passing review.
    @return: None.
    """
    assert _reopen(_approved(pending_review)).priority_released is True
    assert reopen_for_reexamination(_approved(pending_review), _approved(pending_review).version, "synthetic-evidence-ref",
                                    awarded=True).priority_released is False
    assert _reopen(reject_review(pending_review, 1)).priority_released is True


def test_duration_rejection_sets_priority_released(valid_evidence):
    """@description: Treat a duration rejection as a definitive negative decision that releases priority.
    @param valid_evidence: Baseline full-video evidence.
    @return: None.
    """
    too_long = replace(valid_evidence, seconds=999)
    assert record_validation(ReviewSnapshot(VIDEO), too_long, 0).priority_released is True


def test_priority_released_must_be_boolean():
    """@description: Refuse a non-boolean priority fact that could be truthy by accident.
    @param: none
    @return: None.
    """
    with pytest.raises(InvalidReviewTransition):
        replace(ReviewSnapshot(VIDEO), priority_released="yes")


def test_duration_rejected_review_reopens_into_pending_validation(valid_evidence):
    """@description: Let a duration-rejected review re-enter validation without carrying its failing evidence forward.
    @param valid_evidence: Baseline full-video evidence.
    @return: None.
    """
    too_long = replace(valid_evidence, seconds=999)
    rejected_for_duration = record_validation(ReviewSnapshot(VIDEO), too_long, 0)
    reopened = _reopen(rejected_for_duration)
    assert reopened.status is ReviewStatus.PENDING_VALIDATION
    assert reopened.duration is None and reopened.reexamination_pending is True
    assert reopened.priority_released is True and reopened.approval_source is None


@pytest.mark.parametrize("reexam_evidence, expected_status", [
    ("unreliable", ReviewStatus.PENDING_VALIDATION),
    ("not_full_video", ReviewStatus.PENDING_VALIDATION),
    ("too_long", ReviewStatus.REJECTED),
    ("passing", ReviewStatus.PENDING_REVIEW),
])
def test_reexamination_evidence_never_bypasses_duration_gate(valid_evidence, reexam_evidence, expected_status):
    """@description: Route reexamination evidence through the same duration gate, never approving it directly.
    @param valid_evidence: Baseline full-video evidence.
    @param reexam_evidence: Kind of new reexamination evidence.
    @param expected_status: Outcome of the shared duration gate.
    @return: None.
    """
    rejected_for_duration = record_validation(ReviewSnapshot(VIDEO), replace(valid_evidence, seconds=999), 0)
    reopened = _reopen(rejected_for_duration)
    evidence = {
        "unreliable": replace(valid_evidence, reliable=False),
        "not_full_video": replace(valid_evidence, full_video=False),
        "too_long": replace(valid_evidence, seconds=999),
        "passing": valid_evidence,
    }[reexam_evidence]
    revalidated = record_validation(reopened, evidence, reopened.version)
    assert revalidated.status is expected_status
    assert revalidated.approval_source is None
    assert not is_submission_reward_eligible(revalidated, SubmissionDisposition.NEW_CANDIDATE)
    if expected_status is ReviewStatus.PENDING_REVIEW:
        with pytest.raises(InvalidReviewTransition, match="requires a new ordering event"):
            approve_review(revalidated, ApprovalSource.OWNER, revalidated.version, song_eligible=True)


def test_validation_failed_review_cannot_be_reopened(valid_evidence):
    """@description: Keep reexamination limited to recorded decisions rather than transient validation failures.
    @param valid_evidence: Baseline full-video evidence.
    @return: None.
    """
    failed = record_validation_error(ReviewSnapshot(VIDEO), ValidationError.METADATA_UNAVAILABLE, 0)
    with pytest.raises(InvalidReviewTransition, match="approved or rejected"):
        _reopen(failed)


def test_reopen_from_rejected_releases_priority_even_when_stored_flag_is_false():
    """@description: Treat a stored rejection as released priority even when a legacy payload decoded the flag as False.
    @param: none
    @return: None.
    """
    # [legacy rejection]: Written before priority_released existed, so the decoded flag is False.
    legacy_rejected = ReviewSnapshot(VIDEO, 2, ReviewStatus.REJECTED)
    assert legacy_rejected.priority_released is False
    reopened = _reopen(legacy_rejected)
    assert reopened.priority_released is True
    assert reopened.status is ReviewStatus.PENDING_VALIDATION and reopened.reexamination_pending is True
