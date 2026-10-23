"""Prove legacy stored reviews keep their released priority through the formal reexamination service, and previews match the confirmed status.

Scenarios use synthetic submissions, the real OwnerReviewService and SubmissionRepository over the offline fake; no MySQL, Discord or Gateway is involved.
"""

from tests.intake_fixture import seed_intake
import json
from datetime import datetime, timezone

import pytest

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_codec import decode_contract
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceRecord, EvidenceSourceType, record_evidence
from bot.owner_review import OwnerReviewService
from bot.review import ApprovalSource, ReviewSnapshot, ReviewStatus
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.reward_candidates import RewardDecisionReason as Reason
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity
from tests.submission_persistence_fake import FakeDatabase
from tests.version_candidate_support import make_candidate

OWNER_ID = 777
SYNTHETIC_TIME = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def world():
    """@description: Build a fake database, real repository and owner service over a fixed synthetic clock.
    @param: none
    @return: Tuple of fake database, repository and owner review service.
    """
    database = FakeDatabase()
    database.now = SYNTHETIC_TIME.replace(tzinfo=None)  # [fake UTC clock]: Matches the fake's naive database clock.
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, lambda: SYNTHETIC_TIME)
    return database, repository, service


def intake(number: int, seconds: int = 180):
    """@description: Supply a synthetic pending submission for one recording version with the given full-video duration.
    @param number: Stable synthetic sequence and identity number.
    @param seconds: Duration in seconds; outside 60..720 the review is rejected for duration at intake.
    @return: Repository intake request without review approval.
    """
    candidate = make_candidate(number, "A", review_status=ReviewStatus.PENDING_VALIDATION)
    duration = DurationEvidence(candidate.identity.video, seconds, "synthetic-duration", True, True)
    return SubmissionRequest(f"https://youtu.be/{candidate.identity.video.video_id}", candidate.operation_id,
                             candidate.submitter_id, 999, candidate.identity, candidate.review, 0, duration)


def owner_evidence(video, evidence_id: str, hours: int = 0) -> EvidenceRecord:
    """@description: Create evidence the Owner inspected, naming recording version A, for a formal reexamination preview.
    @param video: Video the evidence describes.
    @param evidence_id: Stable synthetic evidence identifier.
    @param hours: Retrieval offset for ordering.
    @return: Evidence record that the owner review service stamps as Owner-confirmed at preview.
    """
    return record_evidence(evidence_id, video, EvidenceSourceType.OWNER_REVIEW, f"synthetic-{evidence_id}",
                           SYNTHETIC_TIME.replace(hour=9 + hours), "synthetic inspection", OWNER_ID,
                           RecordingVersionIdentity("A"))


def approve(repository: SubmissionRepository, operation_id: str, revision: int) -> ReviewSnapshot:
    """@description: Apply a legitimate Owner approval through the production review command.
    @param repository: Repository over the fake connection factory.
    @param operation_id: Previously accepted synthetic operation.
    @param revision: Current expected review revision.
    @return: Persisted approved review.
    """
    return repository.review(ReviewCommand(f"approve:{operation_id}:{revision}", operation_id, OWNER_ID,
                                          "synthetic verified song decision", revision,
                                          approval_source=ApprovalSource.OWNER, song_eligible=True))


def reject(repository: SubmissionRepository, operation_id: str, revision: int) -> ReviewSnapshot:
    """@description: Reject a song through the production review command, releasing its priority.
    @param repository: Repository over the fake connection factory.
    @param operation_id: Previously accepted synthetic operation.
    @param revision: Current expected review revision.
    @return: Persisted rejected review.
    """
    return repository.review(ReviewCommand(f"reject:{operation_id}:{revision}", operation_id, OWNER_ID,
                                          "synthetic not an eligible song", revision, song_eligible=False))


def strip_priority_released(database: FakeDatabase, video_id: str) -> None:
    """@description: Rewrite a stored review as a legacy payload written before the priority_released field existed.
    @param database: Fake database holding the stored review row.
    @param video_id: Video whose review_json is rewritten in place.
    @return: None.
    """
    stored = database.state["jb2_videos"][video_id]
    document = json.loads(stored["review_json"])
    del document["value"]["fields"]["priority_released"]  # [legacy payload]: Old rows never carried this key.
    stored["review_json"] = json.dumps(document)


def test_legacy_rejected_row_reopened_through_service_does_not_block_a_later_same_version_submission(world):
    """@description: A rejected item whose stored payload lacks priority_released keeps its released priority after the formal reopen.
    @param world: Offline database, repository and owner service.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, intake(1))
    seed_intake(repository, intake(2))
    video = make_candidate(1).identity.video
    reject(repository, "op-1", 1)
    strip_priority_released(database, video.video_id)
    # [legacy decode]: The old payload must decode as not-released, proving the fix does not rely on the stored flag.
    assert decode_contract(database.state["jb2_videos"][video.video_id]["review_json"], ReviewSnapshot).priority_released is False
    token = service.preview_reexamination(OWNER_ID, "op-1", owner_evidence(video, "ev-reexam-1", hours=3),
                                          "fresh owner inspection", 2)
    service.confirm(OWNER_ID, token.token_id)
    approve(repository, "op-2", 1)
    reopened_item = repository.settle("op-1")
    assert reopened_item.status is Status.WAITING_VALIDATION
    assert reopened_item.reason is Reason.REEXAMINATION_PENDING
    assert not database.state["credit_ledger"]
    later = repository.settle("op-2")
    assert later.status is Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1


def test_duration_rejected_reopen_preview_projects_the_status_the_confirmed_reopen_produces(world):
    """@description: The preview of a reopened duration rejection shows the same status that the confirmed reopen writes.
    @param world: Offline database, repository and owner service.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, intake(1, seconds=999))
    video = make_candidate(1).identity.video
    rejected = repository.review_view(OWNER_ID, "op-1").review
    assert rejected.status is ReviewStatus.REJECTED
    token = service.preview_reexamination(OWNER_ID, "op-1", owner_evidence(video, "ev-reexam-1", hours=3),
                                          "fresh owner inspection", rejected.version)
    assert token.projected_status is ReviewStatus.PENDING_VALIDATION
    confirmed = service.confirm(OWNER_ID, token.token_id)
    assert confirmed.status is token.projected_status
