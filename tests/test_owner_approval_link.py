"""Verify offline that an Owner approval publishes the approved song in the same transaction: verified status, the canonical watch URL of the confirmed
video, and the owner_approval:<decision_id> provenance. Refusals, pending and rejected decisions publish nothing, a replay repeats nothing,
and a failed publish rolls the approval back. Runs on the SQL-protocol fake with the real repository and owner review service.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import ReviewStatus, StaleReviewVersion
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.urls import parse_youtube_url
from tests.submission_persistence_fake import FakeDatabase

OWNER = 777  # [configured Owner]: The identity the fake repository was built with.
URL = "https://youtu.be/Abc_123-Xyz"  # [synthetic public link]: A well-formed 11-character identifier used only in the fake.
NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)  # [fixed clock]: Timezone-aware, as the owner service requires.
EVIDENCE = "owner-observed-full-playback:2026-10-24:manual"  # [Owner statement]: Recorded as the source, never as a platform fact.


@pytest.fixture
def world():
    """@description: Fake database, real repository and owner review service, with one pending intake.
    @return: Tuple of database, repository, owner service, operation identifier and video identifier.
    """
    database = FakeDatabase()
    database.now = NOW.replace(tzinfo=None)
    database.state["control"].update(snapshot_ready=True, legacy_writers_quiesced=True, settlement_enabled=False)  # [intake state]: The reviewed isolation the pending path requires.
    repository = SubmissionRepository(database.connect, OWNER)
    service = OwnerReviewService(repository, OWNER, lambda: NOW, token_ttl=timedelta(minutes=5))
    repository.accept_pending(123, URL, "discord:900")
    video_id = parse_youtube_url(URL).video_id
    return database, repository, service, "discord:900", video_id


def song_of(database, video_id: str) -> dict:
    """@description: Read the committed song row for one video identifier.
    @param database: Fake database.
    @param video_id: Platform track identifier.
    @return: The song row.
    """
    return next(row for row in database.state["songs"].values() if row["platform_track_id"] == video_id)


def record_duration(repository, operation: str, video_id: str, seconds: int, decision: str) -> None:
    """@description: Record the Owner-stated full-video duration through the repository's Owner-gated review command.
    @param repository: Real repository.
    @param operation: Pending operation.
    @param video_id: Video the duration describes.
    @param seconds: Owner-stated seconds.
    @param decision: Idempotency key.
    @return: None.
    """
    revision = repository.review_view(OWNER, operation).review.version
    repository.review(ReviewCommand(decision, operation, OWNER, "owner duration", revision,
                                   duration=DurationEvidence(VideoIdentity(video_id), seconds, EVIDENCE, True, True)))


def preview_approval(service, operation: str, video_id: str, revision: int) -> str:
    """@description: Preview an Owner approval with a content judgement about the same video and return its token.
    @param service: Owner review service.
    @param operation: Operation to approve.
    @param video_id: Video the Owner judges.
    @param revision: Revision the Owner examined.
    @return: Token identifier.
    """
    judgement = ContentJudgement(VideoIdentity(video_id), OWNER, "owner content judgement", NOW, EVIDENCE, True)
    return service.preview(OWNER, operation, ReviewAction.APPROVE, "owner approval", revision, EVIDENCE, content=judgement).token_id


def test_an_approval_publishes_the_verified_canonical_link_in_the_same_transaction(world) -> None:
    """@description: After duration and approval, the song is verified, carries the canonical watch URL of the confirmed video, and records the decision as its provenance.
    @param world: Fake database, repository, service, operation and video.
    @return: None.
    """
    database, repository, service, operation, video_id = world
    record_duration(repository, operation, video_id, 180, "dur-1")
    revision = repository.review_view(OWNER, operation).review.version
    token = preview_approval(service, operation, video_id, revision)
    review = service.confirm(OWNER, token)
    assert review.status is ReviewStatus.APPROVED
    song = song_of(database, video_id)
    assert song["resolution_status"] == "verified"
    assert song["youtube_url"] == f"https://www.youtube.com/watch?v={video_id}"
    assert song["verification_provenance"].startswith("owner_approval:")


def test_a_pending_operation_with_only_a_duration_publishes_nothing(world) -> None:
    """@description: Recording the duration alone leaves the song pending and unlinked.
    @param world: Fake database, repository, service, operation and video.
    @return: None.
    """
    database, repository, _, operation, video_id = world
    record_duration(repository, operation, video_id, 180, "dur-2")
    song = song_of(database, video_id)
    assert song["resolution_status"] == "pending" and song["youtube_url"] is None


def test_an_out_of_range_duration_and_a_rejection_publish_nothing(world) -> None:
    """@description: A duration outside the limits is a final rejection and a rejection decision publishes nothing; the song stays unlinked.
    @param world: Fake database, repository, service, operation and video.
    @return: None.
    """
    database, repository, service, operation, video_id = world
    record_duration(repository, operation, video_id, 721, "dur-3")
    assert repository.review_view(OWNER, operation).review.status is ReviewStatus.REJECTED
    assert song_of(database, video_id)["youtube_url"] is None


def test_a_replayed_approval_repeats_no_publish_and_keeps_the_first_provenance(world) -> None:
    """@description: Confirming the same token again, or repeating the decision with its identifier, neither publishes again nor changes the recorded provenance.
    @param world: Fake database, repository, service, operation and video.
    @return: None.
    """
    database, repository, service, operation, video_id = world
    record_duration(repository, operation, video_id, 180, "dur-4")
    revision = repository.review_view(OWNER, operation).review.version
    token = preview_approval(service, operation, video_id, revision)
    service.confirm(OWNER, token)
    first = dict(song_of(database, video_id))
    service.confirm(OWNER, token)
    assert song_of(database, video_id) == first


def test_a_failed_publish_rolls_the_approval_back_and_leaves_the_song_untouched(world) -> None:
    """@description: If the song update fails, the whole approval rolls back: the review stays pending and the song stays unlinked.
    @param world: Fake database, repository, service, operation and video.
    @return: None.
    """
    database, repository, service, operation, video_id = world
    record_duration(repository, operation, video_id, 180, "dur-5")
    revision = repository.review_view(OWNER, operation).review.version
    token = preview_approval(service, operation, video_id, revision)
    database.sql_fault = "UPDATE songs"
    with pytest.raises(ValueError):
        service.confirm(OWNER, token)
    assert repository.review_view(OWNER, operation).review.status is ReviewStatus.PENDING_REVIEW
    song = song_of(database, video_id)
    assert song["resolution_status"] == "pending" and song["youtube_url"] is None


def test_a_stale_review_revision_refuses_the_approval_and_publishes_nothing(world) -> None:
    """@description: An approval previewed on a revision that the Owner has since moved past is refused at confirmation, and the song is not published.
    @param world: Fake database, repository, service, operation and video.
    @return: None.
    """
    database, repository, service, operation, video_id = world
    record_duration(repository, operation, video_id, 180, "dur-6a")
    revision = repository.review_view(OWNER, operation).review.version
    token = preview_approval(service, operation, video_id, revision)
    record_duration(repository, operation, video_id, 200, "dur-6b")  # [revision moves]: The previewed revision is no longer current.
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER, token)
    assert song_of(database, video_id)["youtube_url"] is None


def test_a_song_already_linked_elsewhere_refuses_the_approval_and_rolls_it_back(world) -> None:
    """@description: A song that already carries a different delivery link cannot take the new one, so the approval refuses and the review stays pending.
    @param world: Fake database, repository, service, operation and video.
    @return: None.
    """
    from bot.database.review_persistence import ApprovalLinkRefused
    database, repository, service, operation, video_id = world
    record_duration(repository, operation, video_id, 180, "dur-7a")
    revision = repository.review_view(OWNER, operation).review.version
    token = preview_approval(service, operation, video_id, revision)
    song = song_of(database, video_id)
    song.update(resolution_status="verified", youtube_url="https://www.youtube.com/watch?v=OtherVideo1")  # [already linked]: Another link is delivered for this song.
    with pytest.raises(ApprovalLinkRefused):
        service.confirm(OWNER, token)
    assert repository.review_view(OWNER, operation).review.status is ReviewStatus.PENDING_REVIEW
    assert song_of(database, video_id)["youtube_url"] == "https://www.youtube.com/watch?v=OtherVideo1"
