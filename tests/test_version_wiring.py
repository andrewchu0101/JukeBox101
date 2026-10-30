"""Prove the version-category rules constrain confirmation on the real reexamination path.

Formal reexamination runs through SubmissionRepository.reexamine and settle over the offline fake; no MySQL or Discord is involved.
"""

from tests.intake_fixture import seed_intake
from datetime import datetime, timezone

import pytest

from bot.database.review_persistence import ReexaminationCommand, ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceRecord, EvidenceSourceType, VersionClaimKind, record_evidence
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.reward_candidates import RewardDecisionReason as Reason
from bot.review import ApprovalSource, ReviewStatus
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus
from tests.submission_persistence_fake import FakeDatabase
from tests.version_candidate_support import make_candidate

OWNER_ID = 777
SYNTHETIC_TIME = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
COVER = RecordingVersionIdentity("cover-B")


def intake(number: int, version: str | None) -> SubmissionRequest:
    """@description: Supply a synthetic pending submission with passing full-video duration and no trusted identity.
    @param number: Stable synthetic sequence and identity number.
    @param version: Trusted intake identity, or None so the video starts unresolved.
    @return: Repository intake request without review approval.
    """
    candidate = make_candidate(number, version, review_status=ReviewStatus.PENDING_VALIDATION)
    duration = DurationEvidence(candidate.identity.video, 180, "synthetic-duration", True, True)
    return SubmissionRequest(f"https://youtu.be/{candidate.identity.video.video_id}", candidate.operation_id,
                             candidate.submitter_id, 999, candidate.identity, candidate.review, 0, duration)


def owner_evidence(video, evidence_id: str, kind: VersionClaimKind | None) -> EvidenceRecord:
    """@description: Create reliable Owner-confirmed evidence that names the cover recording and the category the Owner marked.
    @param video: Video the evidence describes.
    @param evidence_id: Stable synthetic evidence identifier.
    @param kind: Category the Owner marked on the evidence, or None when no category is named.
    @return: Owner-confirmed evidence record asserting COVER.
    """
    return record_evidence(evidence_id, video, EvidenceSourceType.OWNER_REVIEW, f"synthetic-{evidence_id}",
                           SYNTHETIC_TIME, "fresh owner inspection", OWNER_ID, COVER, owner_confirmed=True,
                           version_kind=kind)


@pytest.fixture
def persistence() -> tuple[FakeDatabase, SubmissionRepository]:
    """@description: Inject a fully offline connection protocol and a configured Owner.
    @param: none
    @return: Fake state and the production repository.
    """
    database = FakeDatabase()
    return database, SubmissionRepository(database.connect, OWNER_ID)


def approve_song(repository: SubmissionRepository, operation_id: str, revision: int) -> None:
    """@description: Apply a legitimate Owner song approval through the production review command.
    @param repository: Repository over the fake connection factory.
    @param operation_id: Accepted synthetic operation.
    @param revision: Current expected review revision.
    @return: None.
    """
    repository.review(ReviewCommand(f"approve:{operation_id}:{revision}", operation_id, OWNER_ID,
                                   "synthetic verified song decision", revision,
                                   approval_source=ApprovalSource.OWNER, song_eligible=True))


def test_uncategorized_reexamination_confirms_its_basic_identity_but_awards_nothing_while_pending(persistence: tuple) -> None:
    """@description: An owner-confirmed record with no category establishes the basic version (version-unify rule); the reopened item still awards nothing.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, None))
    approve_song(repository, "op-1", 1)
    video = repository.review_view(OWNER_ID, "op-1").review.video
    repository.reexamine(ReexaminationCommand("reexam-1", "op-1", OWNER_ID, "fresh owner inspection", 2,
                                             owner_evidence(video, "ev-cover", kind=None)))
    identity = repository.review_view(OWNER_ID, "op-1").identity
    assert identity.status is VersionIdentityStatus.CONFIRMED
    assert repository.settle("op-1").status is not Status.AWARDED
    assert not database.state["credit_ledger"]


def test_distinct_reexamination_with_matching_kind_confirms_identity_and_still_awards_nothing_while_reopened(
        persistence: tuple) -> None:
    """@description: A distinct-version record with its matching category confirms the identity; the reopened review still waits.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, None))
    approve_song(repository, "op-1", 1)
    video = repository.review_view(OWNER_ID, "op-1").review.video
    repository.reexamine(ReexaminationCommand("reexam-1", "op-1", OWNER_ID, "fresh owner inspection", 2,
                                             owner_evidence(video, "ev-cover", kind=VersionClaimKind.COVER)))
    identity = repository.review_view(OWNER_ID, "op-1").identity
    assert (identity.status, identity.version) == (VersionIdentityStatus.CONFIRMED, COVER)
    decision = repository.settle("op-1")
    assert (decision.status, decision.reason) == (Status.WAITING_VALIDATION, Reason.REEXAMINATION_PENDING)
    assert not database.state["credit_ledger"]


def test_edit_reexamination_never_confirms_identity_and_awards_nothing(persistence: tuple) -> None:
    """@description: An edit-category record naming a version confirms nothing on the real path (Q17).
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence
    seed_intake(repository, intake(1, None))
    approve_song(repository, "op-1", 1)
    video = repository.review_view(OWNER_ID, "op-1").review.video
    repository.reexamine(ReexaminationCommand("reexam-1", "op-1", OWNER_ID, "fresh owner inspection", 2,
                                             owner_evidence(video, "ev-edit", kind=VersionClaimKind.EDIT)))
    assert repository.review_view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.UNRESOLVED
    assert not database.state["credit_ledger"]
