"""Verify formal reexamination recomputes identity without erasing or freezing it when evidence is only missing.

Real service -> repository -> fake DB path; synthetic data only.
"""

from datetime import datetime, timedelta, timezone

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import ReviewSnapshot
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


class Clock:
    """@description: Fixed synthetic clock for token issuance.
    @param: none
    @return: Callable clock.
    """

    def __call__(self) -> datetime:
        """@description: Return the synthetic instant.
        @param self: Clock.
        @return: Timezone-aware instant.
        """
        return START


def approved_with_confirmed_identity():
    """@description: Approve one submission and seed an Owner-mapped confirmed identity A (no evidence record behind it).
    @param: none
    @return: Tuple of database, service and video.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, Clock(), token_ttl=timedelta(minutes=5))
    video = VideoIdentity("Synth0001X1")
    repository.accept(SubmissionRequest(f"https://youtu.be/{video.video_id}", "op-1", 101, 999, VersionAssignment(video),
                                        ReviewSnapshot(video), 0,
                                        DurationEvidence(video, 180, "synthetic-duration", True, True)))
    judgement = ContentJudgement(video, OWNER_ID, "synthetic song judgement", START, "demo-content-op-1", True)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic approval", 1, "demo-cite-1",
                            content=judgement).token_id
    service.confirm(OWNER_ID, token)
    revision = service.view(OWNER_ID, "op-1").review.version
    repository.review(ReviewCommand("seed-identity-a", "op-1", OWNER_ID, "synthetic confirmed mapping", revision,
                                    identity=VersionAssignment(video, VersionIdentityStatus.CONFIRMED,
                                                               RecordingVersionIdentity("synth-version-A"), "ev-a")))
    return database, service, video


def formal_reexamination(service, video, version_name: str, kind):
    """@description: Run the formal reexamination path with one owner-judged record.
    @param service: Owner review service.
    @param video: Video under review.
    @param version_name: Asserted version name of the new evidence.
    @param kind: Declared category, or None.
    @return: Identity after the reexamination.
    """
    record = record_evidence("ev-new", video, EvidenceSourceType.OWNER_REVIEW, "synthetic-note",
                             START + timedelta(hours=1), "synthetic reexamination", OWNER_ID,
                             RecordingVersionIdentity(version_name), version_kind=kind)
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview_reexamination(OWNER_ID, "op-1", record, "synthetic reexamination", revision).token_id
    service.confirm(OWNER_ID, token)
    return service.view(OWNER_ID, "op-1").identity


def test_reexamination_without_a_confirming_category_keeps_the_existing_identity():
    """@description: New evidence that confirms nothing must neither erase nor freeze an existing confirmed identity.
    @param: none
    @return: None.
    """
    _, service, video = approved_with_confirmed_identity()
    identity = formal_reexamination(service, video, "synth-version-A", None)
    assert identity.status is VersionIdentityStatus.CONFIRMED
    assert identity.version == RecordingVersionIdentity("synth-version-A")


def test_reexamination_with_a_contradicting_confirmed_category_is_a_conflict():
    """@description: A confirming contradiction of the existing version is recorded as CONFLICT.
    @param: none
    @return: None.
    """
    _, service, video = approved_with_confirmed_identity()
    identity = formal_reexamination(service, video, "synth-version-B", VersionClaimKind.COVER)
    assert identity.status is VersionIdentityStatus.CONFLICT
