"""Exercise the Owner review service over the real repository and the offline SQL-protocol fake.

Every scenario uses synthetic submissions and an in-memory token store; no Discord, MySQL or Gateway is involved.
"""

from tests.intake_fixture import seed_intake
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from bot.database.review_persistence import ReviewCommand, review_command_digest
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.eligibility import DurationEvidence
from bot.evidence_record import VersionClaimKind, EvidenceRecord, EvidenceSourceType, record_evidence
from bot.owner_review import (
    ExpiredReviewToken, OwnerReviewService, ReviewAction, ReviewOutcomeUnknown, UnknownReviewToken,
)
from bot.review import ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, StaleReviewVersion
from bot.reward_candidates import RewardCandidateStatus
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest
from bot.version_evidence import resolve_video_version
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777
STRANGER_ID = 999
EVIDENCE_REFERENCE = "ev-owner-decision-1"  # [decision citation]: Every preview cites the evidence behind its decision.
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
TOKEN_TTL = timedelta(minutes=5)


class Clock:
    """@description: Provide a controllable synthetic clock for token expiry scenarios.
    @param: none
    @return: Callable clock with an advance method.
    """

    def __init__(self) -> None:
        """@description: Start at a fixed synthetic instant.
        @param self: Mutable synthetic clock.
        @return: None.
        """
        self.current = START  # [synthetic now]: Advanced explicitly by scenarios.

    def __call__(self) -> datetime:
        """@description: Return the synthetic current instant.
        @param self: Synthetic clock.
        @return: Current timezone-aware instant.
        """
        return self.current

    def advance(self, seconds: int) -> None:
        """@description: Move synthetic time forward for expiry checks.
        @param self: Synthetic clock.
        @param seconds: Elapsed seconds.
        @return: None.
        """
        self.current += timedelta(seconds=seconds)


def submission(number: int, seconds: int | None = 180) -> SubmissionRequest:
    """@description: Create one synthetic submission with optional full-video duration evidence.
    @param number: Stable synthetic number used for the video and operation identifiers.
    @param seconds: Reliable duration, or None for a submission still missing duration.
    @return: Repository intake request without any review approval.
    """
    video = VideoIdentity(f"Synth{number:04d}X{number}")
    duration = DurationEvidence(video, seconds, "synthetic-duration", True, True) if seconds is not None else None
    return SubmissionRequest(f"https://youtu.be/{video.video_id}", f"op-{number}", 100 + number, 999,
                             VersionAssignment(video), _initial_review(video), 0, duration)


def _initial_review(video: VideoIdentity):
    """@description: Use the pure initial review snapshot required by the intake contract.
    @param video: Synthetic video subject.
    @return: Initial review snapshot.
    """
    from bot.review import ReviewSnapshot

    return ReviewSnapshot(video)


def evidence(video: VideoIdentity, evidence_id: str, version: str | None,
             source: EvidenceSourceType = EvidenceSourceType.OWNER_REVIEW, hours: int = 0, *,
             owner_confirmed: bool = False):
    """@description: Create one synthetic audited evidence record about a specific video.
    @param video: Video the evidence describes.
    @param evidence_id: Stable synthetic evidence identifier.
    @param version: Asserted version name, or None for a non-version note.
    @param source: Source category.
    @param hours: Retrieval offset for ordering.
    @param owner_confirmed: Whether the Owner confirmed this evidence at the trusted boundary.
    @return: Immutable evidence record.
    """
    asserted = RecordingVersionIdentity(version) if version is not None else None
    return record_evidence(evidence_id, video, source, f"synthetic-{evidence_id}", START + timedelta(hours=hours),
                           "synthetic inspection", OWNER_ID, asserted, owner_confirmed=owner_confirmed, version_kind=VersionClaimKind.SAME_RECORDING_AUDIO_MV)


@pytest.fixture
def world():
    """@description: Build a fake database, real repository, service and controllable clock.
    @param: none
    @return: Tuple of database, repository, service and clock.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)  # [fake UTC clock]: Matches the fake's naive database clock.
    repository = SubmissionRepository(database.connect, OWNER_ID)
    clock = Clock()
    service = OwnerReviewService(repository, OWNER_ID, clock, token_ttl=TOKEN_TTL)
    return database, repository, service, clock


def seed_pending(repository: SubmissionRepository, number: int = 1) -> None:
    """@description: Accept one submission whose duration passes and whose identity is still unresolved.
    @param repository: Real repository over the fake.
    @param number: Synthetic submission number.
    @return: None.
    """
    seed_intake(repository, submission(number))


def test_non_owner_cannot_preview_or_confirm_and_no_review_is_written(world):
    """@description: Refuse a non-Owner before any token, review revision or audit row is created.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    before = dict(database.state["jb2_review_history"])
    with pytest.raises(PermissionError):
        service.preview(STRANGER_ID, "op-1", ReviewAction.APPROVE, "stranger attempt", 1, EVIDENCE_REFERENCE)
    assert dict(database.state["jb2_review_history"]) == before


def test_preview_describes_state_and_financial_effect_without_writing(world):
    """@description: Show the projected status and the explicit no-award effect before any confirmation.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    before = dict(database.state["jb2_review_history"])
    preview = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE, content=approval_content())
    assert preview.current_status is ReviewStatus.PENDING_REVIEW
    assert preview.projected_status is ReviewStatus.APPROVED
    assert "不發 Nickel" in preview.financial_effect
    assert dict(database.state["jb2_review_history"]) == before


def test_confirm_applies_once_and_a_repeated_confirmation_returns_the_original_result(world):
    """@description: Execute a confirmed approval exactly once and replay the original result on repeat.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE, content=approval_content()).token_id
    history_before = len(database.state["jb2_review_history"])
    first = service.confirm(OWNER_ID, token)
    second = service.confirm(OWNER_ID, token)
    assert first == second
    assert first.status is ReviewStatus.APPROVED
    assert len(database.state["jb2_review_history"]) == history_before + 1


def test_expired_token_is_refused_without_writing(world):
    """@description: Refuse a confirmation after its time limit and leave the review untouched.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, clock = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 1, EVIDENCE_REFERENCE).token_id
    clock.advance(int(TOKEN_TTL.total_seconds()) + 1)
    before = dict(database.state["jb2_review_history"])
    with pytest.raises(ExpiredReviewToken):
        service.confirm(OWNER_ID, token)
    assert dict(database.state["jb2_review_history"]) == before


def test_token_for_an_earlier_revision_is_refused_after_the_review_changed(world):
    """@description: Refuse a stale confirmation when another decision already advanced the revision.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    stale_token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE, content=approval_content()).token_id
    fresh_token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 1, EVIDENCE_REFERENCE).token_id
    service.confirm(OWNER_ID, fresh_token)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, stale_token)
    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.REJECTED


def test_unknown_commit_outcome_retries_with_the_same_decision_and_applies_once(world):
    """@description: Keep an unknown commit result retryable with the same decision key, never a second write.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE, content=approval_content()).token_id
    history_before = len(database.state["jb2_review_history"])
    database.commit_fault = "after"  # [injected ambiguity]: Commit may have happened without acknowledgement.
    with pytest.raises(ReviewOutcomeUnknown):
        service.confirm(OWNER_ID, token)
    database.commit_fault = None
    result = service.confirm(OWNER_ID, token)
    assert result.status is ReviewStatus.APPROVED
    assert len(database.state["jb2_review_history"]) == history_before + 1


def test_approving_a_conflicted_video_keeps_the_conflict_and_awards_nothing(world):
    """@description: Refuse to clear a persisted identity conflict through approval.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    conflict = resolve_video_version(video, [evidence(video, "ev-a", "orig-A", owner_confirmed=True),
                                             evidence(video, "ev-b", "cover-B", owner_confirmed=True)])
    assert conflict.status is VersionIdentityStatus.CONFLICT
    repository.review(ReviewCommand("identity-conflict", "op-1", OWNER_ID, "two contradicting inspections", 1,
                                    identity=conflict))
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 2, EVIDENCE_REFERENCE,
                                  content=approval_content()).token_id
    service.confirm(OWNER_ID, token)
    view = service.view(OWNER_ID, "op-1")
    assert view.identity.status is VersionIdentityStatus.CONFLICT
    assert view.review.status is ReviewStatus.APPROVED
    assert not database.state["credit_ledger"]


def test_reexamination_evidence_keeps_the_original_decision_and_adds_audit_without_state_change(world):
    """@description: Record new evidence as an audit fact while the decision, identity and order stay unchanged.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE, content=approval_content()).token_id
    service.confirm(OWNER_ID, token)
    before = service.view(OWNER_ID, "op-1")
    video = before.review.video
    service.add_reexamination_evidence(OWNER_ID, "op-1", evidence(video, "ev-new", "orig-A", hours=3),
                                       "fresh owner inspection", "reexam-1")
    after = service.view(OWNER_ID, "op-1")
    assert after.review == before.review
    assert after.identity == before.identity
    assert after.accepted_sequence == before.accepted_sequence
    assert [item.evidence_id for item in after.evidence] == ["ev-new"]
    assert not database.state["credit_ledger"]


def test_reexamination_is_idempotent_for_the_same_decision_key(world):
    """@description: Replay the same evidence entry without duplicating its audit fact.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    entry = evidence(submission(1).identity.video, "ev-new", "orig-A")
    service.add_reexamination_evidence(OWNER_ID, "op-1", entry, "fresh owner inspection", "reexam-1")
    service.add_reexamination_evidence(OWNER_ID, "op-1", entry, "fresh owner inspection", "reexam-1")
    assert len(service.view(OWNER_ID, "op-1").evidence) == 1


def test_pending_evidence_leaves_identity_unresolved_and_approval_still_awards_nothing(world):
    """@description: Keep title-only evidence pending and show that approval does not confirm the recording.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    pending = resolve_video_version(video, [evidence(video, "ev-title", "orig-A", EvidenceSourceType.PLATFORM_METADATA)])
    assert pending.status is VersionIdentityStatus.UNRESOLVED
    preview = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE, content=approval_content())
    assert "版本未確認" in preview.financial_effect
    service.confirm(OWNER_ID, preview.token_id)
    assert not database.state["credit_ledger"]


def test_unknown_token_is_refused(world):
    """@description: Refuse a confirmation token that was never issued by this service.
    @param world: Offline service fixture.
    @return: None.
    """
    _, _, service, _ = world
    with pytest.raises(UnknownReviewToken):
        service.confirm(OWNER_ID, "not-issued")


def test_preview_refuses_an_approval_the_hard_gates_would_reject(world):
    """@description: Refuse at preview time when a 30-second video cannot pass the duration gate.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_intake(repository, submission(2, seconds=30))
    with pytest.raises(InvalidReviewTransition):
        service.preview(OWNER_ID, "op-2", ReviewAction.APPROVE, "attempted bypass", 1, EVIDENCE_REFERENCE,
                        content=approval_content(submission(2).identity.video))


def approve_op1(service: OwnerReviewService, revision: int = 1) -> None:
    """@description: Approve the synthetic operation through a confirmed preview.
    @param service: Owner review coordinator over the fake.
    @param revision: Revision the Owner examined.
    @return: None.
    """
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", revision, EVIDENCE_REFERENCE,
                                   content=approval_content())
    service.confirm(OWNER_ID, token.token_id)


def reexamine(service: OwnerReviewService, actor_id: int, operation_id: str, entry: EvidenceRecord, reason: str,
              expected_revision: int) -> ReviewSnapshot:
    """@description: Formal reexamination the way the Owner performs it: preview the exact evidence, then confirm.
    @param service: Owner review coordinator over the fake.
    @param actor_id: Caller identity, which must be the Owner.
    @param operation_id: Accepted operation to reopen.
    @param entry: Evidence record cited by the reexamination.
    @param reason: Audit explanation.
    @param expected_revision: Revision the Owner examined.
    @return: Pending review produced by the confirmed token.
    """
    token = service.preview_reexamination(actor_id, operation_id, entry, reason, expected_revision)
    return service.confirm(actor_id, token.token_id)


def reexamination_evidence(video: VideoIdentity, evidence_id: str = "ev-reexam-1") -> object:
    """@description: Create reliable Owner-confirmed evidence naming the original recording for reexamination.
    @param video: Video the evidence describes.
    @param evidence_id: Stable synthetic evidence identifier.
    @return: Reliable evidence record.
    """
    return evidence(video, evidence_id, "orig-A", hours=3, owner_confirmed=True)


def test_preview_binds_the_evidence_reference_and_a_changed_reference_needs_its_own_token(world):
    """@description: Audit the decision with the evidence reference the Owner previewed, never another one.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    first = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, "ev-first",
                            content=approval_content())
    second = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, "ev-second",
                             content=approval_content())
    assert first.token_id != second.token_id
    assert first.evidence_reference == "ev-first"
    service.confirm(OWNER_ID, first.token_id)
    (audit_row,) = database.state["jb2_decisions"].values()
    previewed = ReviewCommand(audit_row["decision_id"], "op-1", OWNER_ID, "synthetic verified song", 1,
                              approval_source=ApprovalSource.OWNER, song_eligible=True, evidence_reference="ev-first",
                              content_judgement=approval_content())
    changed = ReviewCommand(audit_row["decision_id"], "op-1", OWNER_ID, "synthetic verified song", 1,
                            approval_source=ApprovalSource.OWNER, song_eligible=True, evidence_reference="ev-second",
                            content_judgement=approval_content())
    assert audit_row["request_digest"] == review_command_digest(previewed)
    assert audit_row["request_digest"] != review_command_digest(changed)


def test_concurrent_confirmations_of_one_token_execute_at_most_once(world):
    """@description: Serialize simultaneous confirms of the same preview so exactly one review revision is written.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE, content=approval_content()).token_id
    history_before = len(database.state["jb2_review_history"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: service.confirm(OWNER_ID, token), range(4)))
    assert all(result == results[0] for result in results)
    assert len(database.state["jb2_review_history"]) == history_before + 1


def test_non_owner_cannot_request_reexamination_and_nothing_is_written(world):
    """@description: Refuse a non-Owner reexamination before any reopen revision is written.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    history_before = len(database.state["jb2_review_history"])
    with pytest.raises(PermissionError):
        reexamine(service, STRANGER_ID, "op-1", reexamination_evidence(submission(1).identity.video),
                                      "stranger attempt", 2)
    assert len(database.state["jb2_review_history"]) == history_before


@pytest.mark.parametrize("make_evidence", [
    lambda video: evidence(video, "ev-title", "orig-A", EvidenceSourceType.PLATFORM_METADATA, 3, owner_confirmed=True),
    lambda video: evidence(video, "ev-unnamed", None, hours=3, owner_confirmed=True),
])
def test_reexamination_requires_a_named_version_and_a_non_metadata_source(world, make_evidence):
    """@description: Refuse a reexamination based on platform metadata or evidence that names no version, leaving the approval intact.
    @param world: Offline service fixture.
    @param make_evidence: Builder for a non-reliable evidence record about the operation's video.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    history_before = len(database.state["jb2_review_history"])
    with pytest.raises(ValueError):
        reexamine(service, OWNER_ID, "op-1", make_evidence(submission(1).identity.video),
                                      "fresh owner inspection", 2)
    assert len(database.state["jb2_review_history"]) == history_before
    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.APPROVED


def test_reexamination_reopens_with_block_and_keeps_order_and_awards_while_recomputing_identity(world):
    """@description: Reopen an approved review for reexamination, recomputing the identity from the new Owner evidence without changing order or the ledger.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    before = service.view(OWNER_ID, "op-1")
    reopened = reexamine(service, OWNER_ID, "op-1", reexamination_evidence(before.review.video),
                                             "fresh owner inspection", 2)
    after = service.view(OWNER_ID, "op-1")
    assert reopened.status is ReviewStatus.PENDING_REVIEW
    assert reopened.reexamination_pending is True
    assert reopened.version == before.review.version + 1
    assert after.review == reopened
    assert after.accepted_sequence == before.accepted_sequence
    assert after.identity == VersionAssignment(before.review.video, VersionIdentityStatus.CONFIRMED,
                                               RecordingVersionIdentity("orig-A"), "ev-reexam-1")
    assert [item.evidence_id for item in after.evidence] == ["ev-reexam-1"]
    assert not database.state["credit_ledger"]


def test_reexamination_retry_after_unknown_commit_replays_the_original_revision(world):
    """@description: Replay a confirmed reexamination after an unknown commit outcome without a second reopen.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    token = service.preview_reexamination(OWNER_ID, "op-1", reexamination_evidence(submission(1).identity.video),
                                          "fresh owner inspection", 2).token_id
    history_before = len(database.state["jb2_review_history"])
    database.commit_fault = "after"
    with pytest.raises(ReviewOutcomeUnknown):
        service.confirm(OWNER_ID, token)
    database.commit_fault = None
    retried = service.confirm(OWNER_ID, token)
    assert retried.reexamination_pending is True
    assert len(database.state["jb2_review_history"]) == history_before + 1


def test_reexamination_refuses_a_stale_revision(world):
    """@description: Refuse a reexamination the Owner prepared against an earlier revision.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    with pytest.raises(StaleReviewVersion):
        reexamine(service, OWNER_ID, "op-1", reexamination_evidence(submission(1).identity.video),
                                      "fresh owner inspection", 1)
    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.APPROVED


def test_supplementary_evidence_never_reopens_or_sets_the_block(world):
    """@description: Keep an ordinary 補件 entry as a case fact that leaves status, revision and the block unchanged.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    before = service.view(OWNER_ID, "op-1").review
    service.add_reexamination_evidence(OWNER_ID, "op-1", reexamination_evidence(before.video), "supplementary note", "supp-1")
    after = service.view(OWNER_ID, "op-1").review
    assert after == before
    assert after.status is ReviewStatus.APPROVED
    assert after.reexamination_pending is False


def test_preview_refuses_approval_after_reopen_but_allows_rejection(world):
    """@description: Refuse re-approval at preview time and allow the Owner to reject the reopened review.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    reexamine(service, OWNER_ID, "op-1", reexamination_evidence(submission(1).identity.video),
                                  "fresh owner inspection", 2)
    history_before = len(database.state["jb2_review_history"])
    with pytest.raises(InvalidReviewTransition, match="Reexamination pending"):
        service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "attempted re-approval", 3, EVIDENCE_REFERENCE,
                                  content=approval_content())
    assert len(database.state["jb2_review_history"]) == history_before
    token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 3, EVIDENCE_REFERENCE).token_id
    assert service.confirm(OWNER_ID, token).status is ReviewStatus.REJECTED
    assert not database.state["credit_ledger"]


def test_unconfirmed_evidence_stays_unresolved_not_conflict(world):
    """@description: Records the Owner never confirmed cannot create a conflict or confirm a version.
    @param world: Offline service fixture.
    @return: None.
    """
    _, _, _, _ = world
    video = submission(1).identity.video
    unconfirmed = resolve_video_version(video, [evidence(video, "ev-a", "orig-A"), evidence(video, "ev-b", "cover-B")])
    assert unconfirmed.status is VersionIdentityStatus.UNRESOLVED


def test_add_reexamination_evidence_stores_the_owner_confirmation_stamped_by_the_service(world):
    """@description: Stamp the stored evidence as Owner-confirmed at the trusted boundary, without changing its other facts.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    entry = evidence(submission(1).identity.video, "ev-new", "orig-A", hours=3)
    assert entry.owner_confirmed is False
    service.add_reexamination_evidence(OWNER_ID, "op-1", entry, "fresh owner inspection", "reexam-1")
    (stored,) = service.view(OWNER_ID, "op-1").evidence
    assert stored.owner_confirmed is True
    assert replace(stored, owner_confirmed=False) == entry


def test_formal_reexamination_has_no_direct_entry_that_changes_state(world):
    """@description: Refuse to expose any reexamination path that is not a preview token confirmed by the Owner.
    @param world: Offline service fixture.
    @return: None.
    """
    _, _, service, _ = world
    assert not hasattr(service, "request_reexamination")


def test_reexamination_preview_binds_evidence_digest_and_a_changed_record_needs_its_own_preview(world):
    """@description: Bind the confirmed reexamination to the exact evidence record it previewed.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    video = submission(1).identity.video
    first = evidence(video, "ev-reexam-1", "orig-A", hours=3, owner_confirmed=True)
    changed = evidence(video, "ev-reexam-1", "cover-B", hours=3, owner_confirmed=True)
    preview = service.preview_reexamination(OWNER_ID, "op-1", first, "fresh owner inspection", 2)
    other = service.preview_reexamination(OWNER_ID, "op-1", changed, "fresh owner inspection", 2)
    assert preview.evidence_digest != other.evidence_digest
    assert preview.evidence_reference == "ev-reexam-1"
    service.confirm(OWNER_ID, preview.token_id)
    audit_row = list(database.state["jb2_decisions"].values())[-1]  # [reexamination audit]: The approve row came first.
    previewed = ReviewCommand(audit_row["decision_id"], "op-1", OWNER_ID, "fresh owner inspection", 2)
    assert audit_row["request_digest"] != review_command_digest(previewed)
    stored = service.view(OWNER_ID, "op-1").evidence
    assert [item.version.value for item in stored] == ["orig-A"]


def test_reexamination_confirmation_is_single_use_and_repeats_its_original_result(world):
    """@description: Replay a confirmed reexamination token without a second reopen.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    token = service.preview_reexamination(OWNER_ID, "op-1", reexamination_evidence(submission(1).identity.video),
                                          "fresh owner inspection", 2).token_id
    history_before = len(database.state["jb2_review_history"])
    first = service.confirm(OWNER_ID, token)
    assert service.confirm(OWNER_ID, token) == first
    assert len(database.state["jb2_review_history"]) == history_before + 1


def test_reexamination_confirmation_after_the_review_moved_is_refused(world):
    """@description: Refuse a reexamination token whose revision another decision has since advanced.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    approve_op1(service)
    entry = reexamination_evidence(submission(1).identity.video)
    stale = service.preview_reexamination(OWNER_ID, "op-1", entry, "fresh owner inspection", 2).token_id
    moved = service.preview_reexamination(OWNER_ID, "op-1", entry, "fresh owner inspection", 2).token_id
    service.confirm(OWNER_ID, moved)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, stale)
    assert service.view(OWNER_ID, "op-1").review.version == 3


@pytest.mark.parametrize("reference", ["", " ev-1", "ev-1 "])
def test_approve_and_reject_previews_refuse_empty_or_padded_evidence_references(world, reference):
    """@description: Require a clean evidence citation before any approval or rejection preview exists.
    @param world: Offline service fixture.
    @param reference: Empty or padded citation.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    with pytest.raises(ValueError):
        service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, reference)
    with pytest.raises(ValueError):
        service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 1, reference)


def test_approve_and_reject_previews_require_an_evidence_reference(world):
    """@description: Refuse an approval or rejection preview that gives no evidence citation at all.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service, _ = world
    seed_pending(repository)
    with pytest.raises(TypeError):
        service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1)


@pytest.mark.parametrize(("action", "decision_reason", "expected_status", "content_citation"), [
    (ReviewAction.APPROVE, "synthetic verified song", ReviewStatus.APPROVED,
     " [content_judgement: evidence_reference=ev-content-1, judged_by=777]"),
    (ReviewAction.REJECT, "not an eligible song", ReviewStatus.REJECTED, ""),
])
def test_the_decision_evidence_reference_is_readable_in_the_audit_trail(world, action, decision_reason, expected_status,
                                                                         content_citation):
    """@description: Store the cited evidence reference in plain text beside the decision, not only inside its digest.
    @param world: Offline service fixture.
    @param action: Approval or rejection path.
    @param decision_reason: Owner-written audit explanation.
    @param expected_status: Review status the decision produces.
    @param content_citation: Plain-text content judgement citation the audit row carries, empty for rejection.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    content = approval_content() if action is ReviewAction.APPROVE else None
    token = service.preview(OWNER_ID, "op-1", action, decision_reason, 1, "ev-cited-42", content=content).token_id
    service.confirm(OWNER_ID, token)
    decided = max(database.state["jb2_review_history"].values(), key=lambda row: row["revision"])
    assert decided["decision_reason"] == f"{decision_reason} [evidence_reference: ev-cited-42]{content_citation}"
    (audit_row,) = database.state["jb2_decisions"].values()
    assert audit_row["decision_reason"] == decided["decision_reason"]
    assert service.view(OWNER_ID, "op-1").review.status is expected_status


def cite_owner_evidence(repository: SubmissionRepository, operation_id: str, video: VideoIdentity, version: str,
                        evidence_id: str) -> None:
    """@description: Store the Owner-confirmed record that an ordinary identity confirmation must cite, leaving the review revision unchanged.
    @param repository: Real repository over the fake.
    @param operation_id: Accepted operation whose video the record describes.
    @param video: Video whose recording version is being confirmed.
    @param version: Canonical recording version the stored record establishes.
    @param evidence_id: Identifier that the confirmation will cite.
    @return: None.
    """
    record = evidence(video, evidence_id, version, owner_confirmed=True)
    repository.record_reexamination_evidence(f"evidence:{operation_id}:{evidence_id}", operation_id, OWNER_ID, "owner evidence", record)


def confirmed_identity(video: VideoIdentity, version: str, evidence_id: str) -> VersionAssignment:
    """@description: Build the trusted identity an Owner records through the ordinary review identity path.
    @param video: Video whose recording version is confirmed.
    @param version: Canonical recording version name.
    @param evidence_id: Trusted provenance reference for the confirmation.
    @return: Confirmed version assignment.
    """
    return VersionAssignment(video, VersionIdentityStatus.CONFIRMED, RecordingVersionIdentity(version), evidence_id)


def test_formal_reexamination_contradicting_a_confirmed_version_settles_as_conflict_without_a_ledger_row(world):
    """@description: Let a formal reexamination whose Owner evidence contradicts the confirmed version freeze the reward.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    cite_owner_evidence(repository, "op-1", video, "orig-A", "ev-a")
    repository.review(ReviewCommand("identity-orig-a", "op-1", OWNER_ID, "owner confirmed original recording", 1,
                                    identity=confirmed_identity(video, "orig-A", "ev-a")))
    repository.review(ReviewCommand("approve-op-1", "op-1", OWNER_ID, "synthetic verified song", 2,
                                    approval_source=ApprovalSource.OWNER, song_eligible=True))
    revision = service.view(OWNER_ID, "op-1").review.version
    reexamine(service, OWNER_ID, "op-1", evidence(video, "ev-cover-b", "cover-B", hours=3, owner_confirmed=True),
              "fresh owner inspection", revision)
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFLICT
    assert repository.settle("op-1").status is RewardCandidateStatus.CONFLICT
    assert not database.state["credit_ledger"]


def test_formal_reexamination_recomputes_from_earlier_supplementary_evidence_as_well(world):
    """@description: Count Owner evidence stored by an earlier 補件 when the formal reexamination recomputes identity.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    repository.review(ReviewCommand("approve-op-1", "op-1", OWNER_ID, "synthetic verified song", 1,
                                    approval_source=ApprovalSource.OWNER, song_eligible=True))
    service.add_reexamination_evidence(OWNER_ID, "op-1", evidence(video, "ev-orig-a", "orig-A", hours=1, owner_confirmed=True),
                                       "earlier supplementary note", "supp-1")
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.UNRESOLVED
    reexamine(service, OWNER_ID, "op-1", evidence(video, "ev-cover-b", "cover-B", hours=3, owner_confirmed=True),
              "fresh owner inspection", 2)
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFLICT
    assert repository.settle("op-1").status is RewardCandidateStatus.CONFLICT
    assert not database.state["credit_ledger"]


def test_supplementary_contradicting_evidence_leaves_identity_and_settlement_unchanged_unlike_formal_reexamination(world):
    """@description: Keep a contradicting 補件 as a case fact that neither changes identity nor blocks a later approval.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    confirmed = confirmed_identity(video, "orig-A", "ev-a")
    cite_owner_evidence(repository, "op-1", video, "orig-A", "ev-a")
    repository.review(ReviewCommand("identity-orig-a", "op-1", OWNER_ID, "owner confirmed original recording", 1,
                                    identity=confirmed))
    before = repository.settle("op-1")
    service.add_reexamination_evidence(OWNER_ID, "op-1", evidence(video, "ev-cover-b", "cover-B", hours=3, owner_confirmed=True),
                                       "supplementary note", "supp-1")
    assert service.view(OWNER_ID, "op-1").identity == confirmed
    assert service.view(OWNER_ID, "op-1").review.reexamination_pending is False
    assert repository.settle("op-1") == before
    repository.review(ReviewCommand("approve-op-1", "op-1", OWNER_ID, "synthetic verified song", 2,
                                    approval_source=ApprovalSource.OWNER, song_eligible=True))
    assert repository.settle("op-1").status is RewardCandidateStatus.AWARDED
    assert len(database.state["credit_ledger"]) == 1


def approval_content(video: VideoIdentity | None = None, *, is_song: bool = True, evidence_reference: str = "ev-content-1",
                     judged_by: int = OWNER_ID, reason: str = "synthetic song judgement") -> ContentJudgement:
    """@description: Build the Owner's content judgement the approve gate consumes, defaulting to the seeded synthetic video.
    @param video: Video the judgement describes, or None for the seeded synthetic video.
    @param is_song: Owner verdict on whether the content is a song.
    @param evidence_reference: Trusted citation of the judged evidence.
    @param judged_by: Internal judge identity.
    @param reason: Audit explanation of the verdict.
    @return: Immutable content judgement.
    """
    if video is None:
        video = submission(1).identity.video
    return ContentJudgement(video=video, judged_by=judged_by, reason=reason, retrieved_at=START,
                            evidence_reference=evidence_reference, is_song=is_song)


def test_approve_preview_refuses_when_no_content_judgement_is_given(world):
    """@description: Refuse an approval preview that lacks an Owner content judgement, before any token or write.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    history_before = len(database.state["jb2_review_history"])
    with pytest.raises(ValueError):
        service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE)
    assert len(database.state["jb2_review_history"]) == history_before


def test_approve_preview_refuses_a_content_judgement_for_another_video(world):
    """@description: Surface the gate's cross-video refusal as a refused preview with no token issued.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    history_before = len(database.state["jb2_review_history"])
    other_video = VideoIdentity("Other00000X")
    with pytest.raises(ValueError):
        service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE,
                        content=approval_content(other_video))
    assert len(database.state["jb2_review_history"]) == history_before


def test_approve_preview_refuses_a_judgement_by_someone_other_than_the_owner(world):
    """@description: Accept content verdicts only from the configured Owner, so approval never rests on another judge.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    with pytest.raises(ValueError):
        service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE,
                        content=approval_content(video, judged_by=STRANGER_ID))


def test_approve_preview_refuses_when_the_gate_is_not_eligible_and_takes_its_song_decision_from_the_gate(world, monkeypatch):
    """@description: Refuse approval whenever the approval gate denies eligibility, so no approval is ever a hard-coded True.
    @param world: Offline service fixture.
    @param monkeypatch: Pytest patcher used to make the gate's verdict explicit.
    @return: None.
    """
    from types import SimpleNamespace

    from bot import owner_review

    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    monkeypatch.setattr(owner_review, "evaluate_approval_gate",
                        lambda *_args: SimpleNamespace(song_eligible=False, reason="stubbed gate refusal"))
    history_before = len(database.state["jb2_review_history"])
    with pytest.raises(ValueError):
        service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE,
                        content=approval_content(video))
    assert len(database.state["jb2_review_history"]) == history_before


def test_eligible_approval_with_unresolved_identity_is_approved_and_waits_for_identity_at_settlement(world):
    """@description: Approve a song whose recording is still unknown, and keep the award waiting until identity is confirmed.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE,
                            content=approval_content(video)).token_id
    assert service.confirm(OWNER_ID, token).status is ReviewStatus.APPROVED
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.UNRESOLVED
    assert repository.settle("op-1").status is RewardCandidateStatus.WAITING_IDENTITY
    assert not database.state["credit_ledger"]


def test_changed_content_judgement_needs_its_own_preview_and_is_bound_into_the_decision_digest(world):
    """@description: Bind the approval token and its audit digest to the exact content judgement the Owner previewed.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    first = approval_content(video)
    changed = approval_content(video, reason="reconsidered after listening again")
    preview = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE,
                              content=first)
    other = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, EVIDENCE_REFERENCE,
                            content=changed)
    assert preview.token_id != other.token_id
    assert preview.content_digest != other.content_digest
    service.confirm(OWNER_ID, preview.token_id)
    (audit_row,) = database.state["jb2_decisions"].values()
    previewed = ReviewCommand(audit_row["decision_id"], "op-1", OWNER_ID, "synthetic verified song", 1,
                        approval_source=ApprovalSource.OWNER, song_eligible=True, evidence_reference=EVIDENCE_REFERENCE,
                        content_judgement=first)
    assert audit_row["request_digest"] == review_command_digest(previewed)
    assert audit_row["request_digest"] != review_command_digest(replace(previewed, content_judgement=changed))


def test_the_content_judgement_citation_is_readable_in_the_audit_trail(world):
    """@description: Store the content judgement's citation and judge in plain text beside the decision.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1, "ev-cited-42",
                            content=approval_content(video, evidence_reference="ev-content-42")).token_id
    service.confirm(OWNER_ID, token)
    decided = max(database.state["jb2_review_history"].values(), key=lambda row: row["revision"])
    assert decided["decision_reason"] == ("synthetic verified song [evidence_reference: ev-cited-42] "
                                          "[content_judgement: evidence_reference=ev-content-42, judged_by=777]")
    (audit_row,) = database.state["jb2_decisions"].values()
    assert audit_row["decision_reason"] == decided["decision_reason"]


def merged_a_into_b(world) -> VideoIdentity:
    """@description: Build two videos whose versions A and B the Owner merged, leaving op-1 still citing alias A.
    @param world: Offline service fixture.
    @return: Video of op-1, whose confirmed version is the merged alias A.
    """
    _, repository, service, _ = world
    seed_pending(repository, 1)
    seed_pending(repository, 2)
    video = submission(1).identity.video
    cite_owner_evidence(repository, "op-2", submission(2).identity.video, "B", "ev-b")
    repository.review(ReviewCommand("identity-b", "op-2", OWNER_ID, "owner confirmed B", 1,
                                    identity=confirmed_identity(submission(2).identity.video, "B", "ev-b")))
    cite_owner_evidence(repository, "op-1", video, "A", "ev-a")
    repository.review(ReviewCommand("identity-a", "op-1", OWNER_ID, "owner confirmed A", 1,
                                    identity=confirmed_identity(video, "A", "ev-a")))
    repository.review(ReviewCommand("approve-op-1", "op-1", OWNER_ID, "synthetic verified song",
                                    service.view(OWNER_ID, "op-1").review.version,
                                    approval_source=ApprovalSource.OWNER, song_eligible=True))
    repository.merge("merge-a-into-b", OWNER_ID, "owner merge evidence", "A", "B")
    return video


def test_formal_reexamination_naming_a_merged_alias_is_not_a_conflict(world):
    """@description: Treat evidence naming a merged alias as the same canonical recording, never as a contradiction.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service, _ = world
    video = merged_a_into_b(world)
    reexamine(service, OWNER_ID, "op-1", evidence(video, "ev-b-reexam", "B", hours=3, owner_confirmed=True),
              "fresh owner inspection", service.view(OWNER_ID, "op-1").review.version)
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFIRMED
    assert repository.settle("op-1").status is not RewardCandidateStatus.CONFLICT


def test_formal_reexamination_contradicting_a_merged_identity_still_conflicts(world):
    """@description: Keep a true contradiction after a merge frozen, even when the merge itself stays intact.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    video = merged_a_into_b(world)
    reexamine(service, OWNER_ID, "op-1", evidence(video, "ev-c-reexam", "C", hours=3, owner_confirmed=True),
              "fresh owner inspection", service.view(OWNER_ID, "op-1").review.version)
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFLICT
    assert repository.settle("op-1").status is RewardCandidateStatus.CONFLICT
    assert not database.state["credit_ledger"]
