"""Verify the approval gate separates the song decision from the version identity decision.

Synthetic evidence only; an unconfirmed version never refuses the song, and mismatched videos are refused before judging.
"""

from datetime import datetime, timezone

import pytest

from bot.eligibility import DurationEvidence
from bot.review_gate import ApprovalGate, evaluate_approval_gate
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement, SongStatus
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus

VIDEO = VideoIdentity("Abc_123-Xyz")
OTHER_VIDEO = VideoIdentity("Zzz_999-Qqq")
RETRIEVED_AT = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def duration_evidence():
    """@description: Supply a trusted synthetic full-video duration inside the hard limits for VIDEO.
    @param: none
    @return: Reliable full-video evidence for a three-minute video.
    """
    return DurationEvidence(VIDEO, 180, source="synthetic-fixture", reliable=True, full_video=True)


@pytest.fixture
def content_judgement():
    """@description: Supply a valid synthetic Owner judgement that VIDEO's content is a song.
    @param: none
    @return: Content judgement with every audit field populated.
    """
    return ContentJudgement(video=VIDEO, judged_by=1, reason="synthetic song", retrieved_at=RETRIEVED_AT,
                            evidence_reference="synthetic-evidence-1", is_song=True)


@pytest.fixture
def unresolved_identity():
    """@description: Supply a version assignment with no confirmed identity for VIDEO.
    @param: none
    @return: Unresolved version assignment.
    """
    return VersionAssignment(VIDEO)


@pytest.fixture
def confirmed_identity():
    """@description: Supply a version assignment confirmed by a trusted internal reference for VIDEO.
    @param: none
    @return: Confirmed version assignment.
    """
    return VersionAssignment(VIDEO, VersionIdentityStatus.CONFIRMED, RecordingVersionIdentity("orig-A"), "synthetic-review-1")


def test_missing_content_keeps_song_pending_and_refuses_approval(duration_evidence, unresolved_identity):
    """@description: Refuse approval while the Owner's content judgement is missing, without treating it as a rejection.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param unresolved_identity: Version assignment without confirmation.
    @return: None.
    """
    gate = evaluate_approval_gate(duration_evidence, None, unresolved_identity)
    assert gate.song_status is SongStatus.PENDING
    assert gate.song_eligible is False
    assert "content" in gate.reason.lower()


def test_eligible_song_with_unresolved_identity_is_eligible_but_version_unconfirmed(
        duration_evidence, content_judgement, unresolved_identity):
    """@description: An unconfirmed version does not make the song ineligible; the reason names the pending identity.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic song judgement for VIDEO.
    @param unresolved_identity: Version assignment without confirmation.
    @return: None.
    """
    gate = evaluate_approval_gate(duration_evidence, content_judgement, unresolved_identity)
    assert gate.song_eligible is True
    assert gate.version_confirmed is False
    assert "identity" in gate.reason.lower()


def test_eligible_song_with_confirmed_identity_passes_both_parts(duration_evidence, content_judgement, confirmed_identity):
    """@description: Report both the song and the version as settled when both are confirmed.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic song judgement for VIDEO.
    @param confirmed_identity: Confirmed version assignment for VIDEO.
    @return: None.
    """
    gate = evaluate_approval_gate(duration_evidence, content_judgement, confirmed_identity)
    assert gate.song_eligible is True
    assert gate.version_confirmed is True


def test_conflicting_identity_keeps_song_eligible_but_version_unconfirmed(duration_evidence, content_judgement):
    """@description: A version conflict is reported as unconfirmed, while the song decision stands on its own evidence.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic song judgement for VIDEO.
    @return: None.
    """
    conflict = VersionAssignment(VIDEO, VersionIdentityStatus.CONFLICT, RecordingVersionIdentity("orig-A"), "synthetic-review-2")
    gate = evaluate_approval_gate(duration_evidence, content_judgement, conflict)
    assert gate.song_eligible is True
    assert gate.version_confirmed is False
    assert "conflict" in gate.reason.lower()


def test_not_a_song_is_not_eligible(duration_evidence, content_judgement, confirmed_identity):
    """@description: Refuse the song when the Owner's content judgement says it is not a song, even with a confirmed version.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic judgement, altered to a negative result.
    @param confirmed_identity: Confirmed version assignment for VIDEO.
    @return: None.
    """
    not_song = ContentJudgement(video=VIDEO, judged_by=1, reason="synthetic talk", retrieved_at=RETRIEVED_AT,
                                evidence_reference="synthetic-evidence-3", is_song=False)
    gate = evaluate_approval_gate(duration_evidence, not_song, confirmed_identity)
    assert gate.song_status is SongStatus.NOT_ELIGIBLE
    assert gate.song_eligible is False


def test_approval_gate_is_immutable(duration_evidence, content_judgement, unresolved_identity):
    """@description: Keep the gate result from being altered after the Owner's decision is evaluated.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic song judgement for VIDEO.
    @param unresolved_identity: Version assignment without confirmation.
    @return: None.
    """
    gate = evaluate_approval_gate(duration_evidence, content_judgement, unresolved_identity)
    with pytest.raises(AttributeError):
        gate.song_status = SongStatus.ELIGIBLE
    assert isinstance(gate, ApprovalGate)


@pytest.mark.parametrize("duration_video, content_video, identity_video", [
    (OTHER_VIDEO, VIDEO, VIDEO),
    (VIDEO, OTHER_VIDEO, VIDEO),
    (VIDEO, VIDEO, OTHER_VIDEO),
])
def test_cross_video_mismatch_is_refused(duration_video, content_video, identity_video):
    """@description: Refuse any approval whose duration, content judgement or identity belongs to a different video.
    @param duration_video: Video of the duration evidence.
    @param content_video: Video of the content judgement.
    @param identity_video: Video of the version assignment.
    @return: None.
    """
    duration = DurationEvidence(duration_video, 180, source="synthetic-fixture", reliable=True, full_video=True)
    content = ContentJudgement(video=content_video, judged_by=1, reason="synthetic song", retrieved_at=RETRIEVED_AT,
                               evidence_reference="synthetic-evidence-1", is_song=True)
    with pytest.raises(ValueError):
        evaluate_approval_gate(duration, content, VersionAssignment(identity_video))


def test_missing_duration_and_content_are_not_refused_as_mismatch(unresolved_identity):
    """@description: Missing evidence stays a pending decision and is not confused with a cross-video refusal.
    @param unresolved_identity: Version assignment without confirmation.
    @return: None.
    """
    gate = evaluate_approval_gate(None, None, unresolved_identity)
    assert gate.song_status is SongStatus.PENDING
    assert gate.song_eligible is False


def test_gate_reason_is_deterministic(duration_evidence, content_judgement, unresolved_identity):
    """@description: Return identical gate results and reason text for identical inputs, so audits can compare them.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic song judgement for VIDEO.
    @param unresolved_identity: Version assignment without confirmation.
    @return: None.
    """
    first = evaluate_approval_gate(duration_evidence, content_judgement, unresolved_identity)
    second = evaluate_approval_gate(duration_evidence, content_judgement, unresolved_identity)
    assert first == second
