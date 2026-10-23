"""Verify a preview carries and shows the full decision: reason, source, citation and identity before and after.

Real service over the offline fake database; the Discord preview renderer is called directly; synthetic data only.
"""

from datetime import datetime, timedelta, timezone

from bot.database.submission_repository import SubmissionRepository
from bot.discord_owner_review import render_preview
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import ReviewSnapshot
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionAssignment
from tests.intake_fixture import seed_intake
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
REASON = "synthetic confirmation reason 理由甲"
SOURCE = "owner-note:brief-A-人工核對"


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
    """@description: Build a real service over the fake database with one seeded submission.
    @param: none
    @return: Tuple of service and video.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, Clock(), token_ttl=timedelta(minutes=5))
    video = VideoIdentity("Synth0001X1")
    seed_intake(repository, SubmissionRequest(f"https://youtu.be/{video.video_id}", "op-1", 101, 999,
                                              VersionAssignment(video), ReviewSnapshot(video), 0,
                                              DurationEvidence(video, 180, "synthetic-duration", True, True)))
    return service, video


def test_version_confirmation_preview_carries_reason_source_and_both_identities():
    """@description: The preview exposes the reason, the source and citation, and identity before and after the change.
    @param: none
    @return: None.
    """
    service, video = world()
    record = record_evidence("ev-a", video, EvidenceSourceType.OWNER_REVIEW, SOURCE, START, "synthetic inspection",
                             OWNER_ID, RecordingVersionIdentity("synth-A"), version_kind=VersionClaimKind.SAME_RECORDING_AUDIO_MV)
    preview = service.preview_version_confirmation(OWNER_ID, "op-1", record, REASON,
                                                   service.view(OWNER_ID, "op-1").review.version)
    assert preview.reason == REASON
    assert preview.source_reference == SOURCE
    assert preview.current_identity.status.value == "unresolved"
    assert preview.projected_identity.version == RecordingVersionIdentity("synth-A")
    text = render_preview(preview)
    assert REASON in text
    assert SOURCE in text
    assert "synth-A" in text


def test_approve_preview_shows_the_reason_and_the_unchanged_identity():
    """@description: An approval preview states its reason and the identity it leaves unchanged.
    @param: none
    @return: None.
    """
    service, video = world()
    preview = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, REASON, 1, "demo-cite-1")
    assert preview.reason == REASON
    assert preview.current_identity == preview.projected_identity
    assert REASON in render_preview(preview)
