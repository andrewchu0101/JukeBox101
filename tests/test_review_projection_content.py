"""Verify the Owner's review query reads back the complete content judgements stored with approvals.

Real service -> repository -> fake DB path; synthetic data only.
"""

from tests.intake_fixture import seed_intake
from datetime import datetime, timedelta, timezone

from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import ReviewSnapshot
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import VersionAssignment
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


def world():
    """@description: Build a fake database, real repository and service with one accepted submission.
    @param: none
    @return: Tuple of database, service and video identity.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, Clock(), token_ttl=timedelta(minutes=5))
    video = VideoIdentity("Synth0001X1")
    seed_intake(repository, SubmissionRequest(f"https://youtu.be/{video.video_id}", "op-1", 101, 999, VersionAssignment(video),
                                        ReviewSnapshot(video), 0,
                                        DurationEvidence(video, 180, "synthetic-duration", True, True)))
    return database, service, video


def test_view_before_any_approval_has_no_content_judgements():
    """@description: A pending review has no stored song judgement to show.
    @param: none
    @return: None.
    """
    _, service, _ = world()
    assert service.view(OWNER_ID, "op-1").content_judgements == ()


def test_view_after_approval_reads_back_the_complete_content_judgement():
    """@description: The Owner sees every stored field of the judgement that justified the approval.
    @param: none
    @return: None.
    """
    _, service, video = world()
    judgement = ContentJudgement(video, OWNER_ID, "synthetic song judgement", START, "demo-content-op-1", True)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic approval", 1, "demo-cite-1",
                            content=judgement).token_id
    service.confirm(OWNER_ID, token)
    assert service.view(OWNER_ID, "op-1").content_judgements == (judgement,)
