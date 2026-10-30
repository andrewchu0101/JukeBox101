"""Verify the public intake boundary never writes a version identity; identities come only from evidence.

Real repository over the offline fake database; synthetic data only.
"""

import pytest

from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.review import ReviewSnapshot
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777


def request(identity: VersionAssignment) -> SubmissionRequest:
    """@description: Build one synthetic intake request carrying the given identity assertion.
    @param identity: Identity assertion supplied at intake.
    @return: Intake request for synthetic video Synth0001X1.
    """
    video = VideoIdentity("Synth0001X1")
    return SubmissionRequest(f"https://youtu.be/{video.video_id}", "op-1", 101, 999, identity, ReviewSnapshot(video), 0,
                             DurationEvidence(video, 180, "synthetic-duration", True, True))


def test_public_intake_refuses_a_confirmed_identity_and_writes_nothing():
    """@description: A confirmed version cannot enter through the public intake; it must be established by evidence.
    @param: none
    @return: None.
    """
    database = FakeDatabase()
    repository = SubmissionRepository(database.connect, OWNER_ID)
    video = VideoIdentity("Synth0001X1")
    confirmed = VersionAssignment(video, VersionIdentityStatus.CONFIRMED, RecordingVersionIdentity("synth-A"), "ev-x")
    with pytest.raises(ValueError):
        repository.accept(request(confirmed))
    assert not database.state["jb2_events"]
    assert not database.state["jb2_videos"]


def test_public_intake_accepts_an_unresolved_identity():
    """@description: Intake without a version assertion stays pending until evidence establishes identity.
    @param: none
    @return: None.
    """
    database = FakeDatabase()
    repository = SubmissionRepository(database.connect, OWNER_ID)
    video = VideoIdentity("Synth0001X1")
    repository.accept(request(VersionAssignment(video)))
    assert len(database.state["jb2_events"]) == 1
