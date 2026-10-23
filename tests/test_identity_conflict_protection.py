"""Verify a persisted identity CONFLICT stays frozen through ordinary mapping, re-approval and version confirmation.

Real repository and service over the offline fake database; synthetic data only; no money is moved.
"""

from datetime import datetime, timedelta, timezone

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.owner_review import OwnerReviewService
from bot.review import ReviewSnapshot
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from bot.review import ReviewStatus
from bot.reward_candidates import RewardCandidateStatus
from tests.intake_fixture import seed_intake
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


class Clock:
    """@description: Fixed synthetic clock.
    @param: none
    @return: Callable clock.
    """

    def __call__(self) -> datetime:
        """@description: Return the synthetic instant.
        @param self: Clock.
        @return: Timezone-aware instant.
        """
        return START


def world():
    """@description: Build a fake database, real repository and service with one approved-ready submission.
    @param: none
    @return: Tuple of database, repository, service and video.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, Clock(), token_ttl=timedelta(minutes=5))
    video = VideoIdentity("Synth0001X1")
    seed_intake(repository, SubmissionRequest(f"https://youtu.be/{video.video_id}", "op-1", 101, 999,
                                              VersionAssignment(video), ReviewSnapshot(video), 0,
                                              DurationEvidence(video, 180, "synthetic-duration", True, True)))
    return database, repository, service, video


def owner_record(video, evidence_id: str, version: str) -> object:
    """@description: Build one Owner-confirmed record asserting a version with a declared category.
    @param video: Video the record describes.
    @param evidence_id: Stable synthetic evidence identifier.
    @param version: Asserted canonical version name.
    @return: Owner-confirmed evidence record.
    """
    return record_evidence(evidence_id, video, EvidenceSourceType.OWNER_REVIEW, f"synthetic-{evidence_id}", START,
                           "synthetic inspection", OWNER_ID, RecordingVersionIdentity(version), owner_confirmed=True,
                           version_kind=VersionClaimKind.SAME_RECORDING_AUDIO_MV)


def money(database) -> tuple:
    """@description: Snapshot every money-bearing table the reward path can touch.
    @param database: Fake database.
    @return: Immutable comparison tuple of ledger, balances, settlements and claims.
    """
    return (list(database.state["credit_ledger"]), dict(database.state["users"]),
            dict(database.state["jb2_settlements"]), dict(database.state["jb2_claims"]))


def frozen_conflict(repository, video) -> None:
    """@description: Persist a CONFLICT through the ordinary mapping path, with one stored Owner record supporting A.
    @param repository: Real repository.
    @param video: Video under test.
    @return: None.
    """
    repository.record_reexamination_evidence("ev-store-a", "op-1", OWNER_ID, "synthetic basis", owner_record(video, "ev-a", "synth-A"))
    revision = repository.review_view(OWNER_ID, "op-1").review.version
    repository.review(ReviewCommand("freeze-1", "op-1", OWNER_ID, "synthetic contradiction", revision,
                                    identity=VersionAssignment(video, VersionIdentityStatus.CONFLICT, None, "ev-a")))


def test_ordinary_citation_of_a_resolvable_version_cannot_lift_a_persisted_conflict():
    """@description: CONFLICT stays even when stored Owner evidence now resolves to one version; no money moves.
    @param: none
    @return: None.
    """
    database, repository, _, video = world()
    frozen_conflict(repository, video)
    before = money(database)
    revision = repository.review_view(OWNER_ID, "op-1").review.version
    repository.review(ReviewCommand("lift-1", "op-1", OWNER_ID, "attempt to lift conflict", revision,
                                    identity=VersionAssignment(video, VersionIdentityStatus.CONFIRMED,
                                                               RecordingVersionIdentity("synth-A"), "ev-a")))
    assert repository.review_view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFLICT
    assert repository.settle("op-1").status is RewardCandidateStatus.CONFLICT
    assert database.state["jb2_events"]["op-1"]["conflict_frozen"] is True
    assert money(database) == before


def test_ordinary_unresolved_downgrade_cannot_replace_a_persisted_conflict():
    """@description: An UNRESOLVED proposal leaves the frozen CONFLICT identity in place.
    @param: none
    @return: None.
    """
    database, repository, _, video = world()
    frozen_conflict(repository, video)
    revision = repository.review_view(OWNER_ID, "op-1").review.version
    repository.review(ReviewCommand("downgrade-1", "op-1", OWNER_ID, "attempt downgrade", revision,
                                    identity=VersionAssignment(video)))
    assert repository.review_view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFLICT


def test_formal_version_confirmation_cannot_lift_a_persisted_conflict():
    """@description: The formal version-confirmation path also leaves a persisted CONFLICT frozen; no money moves.
    @param: none
    @return: None.
    """
    database, repository, service, video = world()
    frozen_conflict(repository, video)
    before = money(database)
    revision = repository.review_view(OWNER_ID, "op-1").review.version
    token = service.preview_version_confirmation(OWNER_ID, "op-1", owner_record(video, "ev-b", "synth-A"),
                                                "synthetic confirmation", revision).token_id
    service.confirm(OWNER_ID, token)
    assert repository.review_view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFLICT
    assert money(database) == before
