"""Prove the reexamination block holds on the real award path: SubmissionRepository.settle over the offline fake.

Scenarios use synthetic accepted submissions and the production review and settlement transactions; no MySQL or Discord is involved.
"""

from tests.intake_fixture import seed_intake
from datetime import datetime, timezone

import pytest

from bot.database.review_persistence import ReexaminationCommand, ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceRecord, EvidenceSourceType, record_evidence
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.reward_candidates import RewardDecisionReason as Reason
from bot.review import ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, StaleReviewVersion
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionRequest
from bot.version_evidence import resolve_video_version
from bot.version_identity import VersionAssignment
from tests.submission_persistence_fake import FakeDatabase
from tests.version_candidate_support import make_candidate

OWNER_ID = 777
SYNTHETIC_TIME = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def intake(number: int, version: str | None = "original") -> SubmissionRequest:
    """@description: Supply a synthetic pending submission with passing full-video duration.
    @param number: Stable synthetic sequence and identity number.
    @param version: Trusted synthetic recording identity, or None for unknown.
    @return: Repository intake request without review approval.
    """
    candidate = make_candidate(number, version, review_status=ReviewStatus.PENDING_VALIDATION)
    duration = DurationEvidence(candidate.identity.video, 180, "synthetic-duration", True, True)
    return SubmissionRequest(f"https://youtu.be/{candidate.identity.video.video_id}", candidate.operation_id,
                             candidate.submitter_id, 999, candidate.identity, candidate.review, 0, duration)


def owner_evidence(video: VideoIdentity, evidence_id: str, version: str, hours: int = 0) -> EvidenceRecord:
    """@description: Create evidence the Owner has confirmed at the trusted boundary, naming one recording version.
    @param video: Video the evidence describes.
    @param evidence_id: Stable synthetic evidence identifier.
    @param version: Recording version the Owner confirmed.
    @param hours: Retrieval offset for ordering.
    @return: Reliable evidence record.
    """
    from bot.version_identity import RecordingVersionIdentity

    return record_evidence(evidence_id, video, EvidenceSourceType.OWNER_REVIEW, f"synthetic-{evidence_id}",
                           SYNTHETIC_TIME.replace(hour=9 + hours), "synthetic inspection", OWNER_ID,
                           RecordingVersionIdentity(version), owner_confirmed=True)


def approve(repository: SubmissionRepository, operation_id: str, revision: int) -> ReviewSnapshot:
    """@description: Apply a legitimate Owner approval through the production hard gates.
    @param repository: Repository over the fake connection factory.
    @param operation_id: Previously accepted synthetic operation.
    @param revision: Current expected review revision.
    @return: Persisted approved review.
    """
    return repository.review(ReviewCommand(f"approve:{operation_id}:{revision}", operation_id, OWNER_ID,
                                          "synthetic verified song decision", revision,
                                          approval_source=ApprovalSource.OWNER, song_eligible=True))


def reexamine(repository: SubmissionRepository, operation_id: str, revision: int, decision_id: str = "reexam-1",
              evidence: EvidenceRecord | None = None):
    """@description: Request formal reexamination through the persistence command with reliable new evidence.
    @param repository: Repository over the fake connection factory.
    @param operation_id: Accepted operation to reopen.
    @param revision: Revision the Owner examined.
    @param decision_id: Idempotent reexamination key.
    @param evidence: Evidence record; defaults to a reliable record naming the intake's recording version.
    @return: Persisted pending review.
    """
    entry = evidence or owner_evidence(repository.review_view(OWNER_ID, operation_id).review.video, "ev-reexam", "A", hours=3)
    return repository.reexamine(ReexaminationCommand(decision_id, operation_id, OWNER_ID, "fresh owner inspection",
                                                     revision, entry))


def reject(repository: SubmissionRepository, operation_id: str, revision: int) -> ReviewSnapshot:
    """@description: Reject a song through the production review command.
    @param repository: Repository over the fake connection factory.
    @param operation_id: Accepted operation to reject.
    @param revision: Current expected review revision.
    @return: Persisted rejected review.
    """
    return repository.review(ReviewCommand(f"reject:{operation_id}:{revision}", operation_id, OWNER_ID,
                                          "synthetic not an eligible song", revision, song_eligible=False))


@pytest.fixture
def persistence() -> tuple[FakeDatabase, SubmissionRepository]:
    """@description: Inject a fully offline connection protocol and a configured Owner.
    @param: none
    @return: Fake state and the production repository.
    """
    database = FakeDatabase()
    return database, SubmissionRepository(database.connect, OWNER_ID)


def test_reopened_approved_item_settles_as_waiting_with_no_ledger_row(persistence: tuple) -> None:
    """@description: Keep a reopened, previously approved item from awarding until a new ordering event exists.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    approve(repository, "op-1", 1)
    reexamine(repository, "op-1", 2)
    decision = repository.settle("op-1")
    assert decision.status is Status.WAITING_VALIDATION
    assert decision.reason is Reason.REEXAMINATION_PENDING
    assert not database.state["credit_ledger"]


def test_approve_after_reopen_is_the_reexamination_pass_and_creates_a_new_tail_ordering_event(persistence: tuple) -> None:
    """@description: Owner ruling 2026-10-23: approving a reopened submission is the reexamination pass, which takes a new tail position in the same operation.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    approve(repository, "op-1", 1)
    reexamine(repository, "op-1", 2)
    counter = dict(database.state["control"])["accepted_sequence"]
    approve(repository, "op-1", 3)
    assert dict(database.state["control"])["accepted_sequence"] == counter + 1
    assert repository.settle("op-1").status is Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1


def test_reject_after_reopen_is_allowed_and_still_awards_nothing(persistence: tuple) -> None:
    """@description: Let the Owner end a reexamination with a rejection, which is never an award.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    approve(repository, "op-1", 1)
    reexamine(repository, "op-1", 2)
    rejected = reject(repository, "op-1", 3)
    assert rejected.status is ReviewStatus.REJECTED
    decision = repository.settle("op-1")
    assert decision.status is Status.INELIGIBLE
    assert decision.reason is Reason.REVIEW_REJECTED
    assert not database.state["credit_ledger"]


def test_reopen_keeps_the_original_accepted_sequence(persistence: tuple) -> None:
    """@description: Leave the original intake order intact after a reexamination reopens the review.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    seed_intake(repository, intake(2, "B"))
    before = repository.review_view(OWNER_ID, "op-1").accepted_sequence
    approve(repository, "op-1", 1)
    reexamine(repository, "op-1", 2)
    after = repository.review_view(OWNER_ID, "op-1")
    assert after.accepted_sequence == before
    assert after.review.status is ReviewStatus.PENDING_REVIEW
    assert after.review.reexamination_pending is True


def test_reopening_an_awarded_item_keeps_the_award_and_adds_no_ledger_row(persistence: tuple) -> None:
    """@description: Never revoke or duplicate a settled reward when reexamination reopens its review.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    approve(repository, "op-1", 1)
    assert repository.settle("op-1").status is Status.AWARDED
    reexamine(repository, "op-1", 2)
    assert repository.settle("op-1").status is Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1
    assert database.state["users"][1]["credit_balance"] == 1


def test_unrelated_later_submission_is_not_blocked_by_a_reopened_item(persistence: tuple) -> None:
    """@description: Report the real blocker behavior: a later submission for another recording is not held by the reopened item.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    seed_intake(repository, intake(2, "B"))
    approve(repository, "op-1", 1)
    approve(repository, "op-2", 1)
    reexamine(repository, "op-1", 2)
    later = repository.settle("op-2")
    assert later.status is Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1
    assert repository.settle("op-1").status is Status.WAITING_VALIDATION


def test_later_same_recording_is_no_longer_held_back_by_the_reopened_earlier_submission(persistence: tuple) -> None:
    """@description: Owner ruling 2026-10-23: the reopened earlier submission yields its priority, so the later same-recording submission can be awarded, and the recording is paid only once.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    seed_intake(repository, intake(3, "A"))
    approve(repository, "op-1", 1)
    approve(repository, "op-3", 1)
    reexamine(repository, "op-1", 2)
    assert repository.settle("op-3").status is Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1
    assert repository.settle("op-1").status is not Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1


def test_owner_confirmed_conflict_blocks_settlement_even_when_approved(persistence: tuple) -> None:
    """@description: An Owner-confirmed contradiction freezes settlement of an approved review without a ledger row.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    approve(repository, "op-1", 1)
    video = VideoIdentity("video000001")
    conflict = resolve_video_version(video, [owner_evidence(video, "ev-a", "orig-A"), owner_evidence(video, "ev-b", "cover-B", hours=1)])
    repository.review(ReviewCommand("identity-conflict", "op-1", OWNER_ID, "two contradicting inspections", 2, identity=conflict))
    assert repository.review_view(OWNER_ID, "op-1").review.status is ReviewStatus.APPROVED
    assert repository.settle("op-1").status is Status.CONFLICT
    assert not database.state["credit_ledger"]


def test_reexamination_replays_the_same_decision_key_without_a_second_revision(persistence: tuple) -> None:
    """@description: Replay the original reexamination result for a repeated key without another history row.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    approve(repository, "op-1", 1)
    first = reexamine(repository, "op-1", 2)
    history = len(database.state["jb2_review_history"])
    assert reexamine(repository, "op-1", 2) == first
    assert len(database.state["jb2_review_history"]) == history


def test_reexamination_refuses_a_stale_revision_without_writing(persistence: tuple) -> None:
    """@description: Refuse a reexamination prepared against an earlier revision and leave the review untouched.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    approve(repository, "op-1", 1)
    history = len(database.state["jb2_review_history"])
    with pytest.raises(StaleReviewVersion):
        reexamine(repository, "op-1", 1)
    assert len(database.state["jb2_review_history"]) == history
    assert repository.review_view(OWNER_ID, "op-1").review.status is ReviewStatus.APPROVED


def test_reexamination_refuses_evidence_that_is_not_owner_confirmed_or_about_another_video(persistence: tuple) -> None:
    """@description: Refuse reexamination on unreliable or misdirected evidence before any reopen is written.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, "A"))
    approve(repository, "op-1", 1)
    video = VideoIdentity("video000001")
    unconfirmed = record_evidence("ev-raw", video, EvidenceSourceType.OWNER_REVIEW, "synthetic-raw", SYNTHETIC_TIME,
                                  "synthetic inspection", OWNER_ID, None)
    other_video = owner_evidence(VideoIdentity("video000099"), "ev-other", "original")
    for evidence in (unconfirmed, other_video):
        with pytest.raises(ValueError):
            reexamine(repository, "op-1", 2, evidence=evidence)
    assert repository.review_view(OWNER_ID, "op-1").review.status is ReviewStatus.APPROVED
