"""Judge whether a complete song is eligible for the reward flow using synthetic evidence only.

Duration, completeness, content judgement and reliability each keep their own pending or refusal path.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from bot.eligibility import DurationEvidence
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement, SongStatus, judge_song

VIDEO = VideoIdentity("Abc_123-Xyz")
OTHER_VIDEO = VideoIdentity("Zzz_999-Qqq")
RETRIEVED_AT = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def duration_evidence():
    """@description: Supply a trusted synthetic full-video duration inside the hard limits.
    @param: none
    @return: Reliable full-video evidence for a three-minute video.
    """
    return DurationEvidence(VIDEO, 180, source="synthetic-fixture", reliable=True, full_video=True)


@pytest.fixture
def content_judgement():
    """@description: Supply a valid synthetic Owner judgement that the content is a song.
    @param: none
    @return: Content judgement with every audit field populated.
    """
    return ContentJudgement(video=VIDEO, judged_by=1, reason="synthetic song", retrieved_at=RETRIEVED_AT,
                            evidence_reference="synthetic-evidence-1", is_song=True)


@pytest.mark.parametrize("seconds, expected", [
    (59, SongStatus.NOT_ELIGIBLE),
    (60, SongStatus.ELIGIBLE),
    (720, SongStatus.ELIGIBLE),
    (721, SongStatus.NOT_ELIGIBLE),
])
def test_duration_boundaries_are_inclusive(duration_evidence, content_judgement, seconds, expected):
    """@description: Apply the 60 to 720 second limits through the existing duration assessment.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic song judgement.
    @param seconds: Exact full-video duration under test.
    @param expected: Required song status for that duration.
    @return: None.
    """
    judgement = judge_song(replace(duration_evidence, seconds=seconds), content_judgement)
    assert judgement.status is expected


def test_clip_is_not_eligible(duration_evidence, content_judgement):
    """@description: Refuse an excerpt even when its duration falls inside the limits.
    @param duration_evidence: Synthetic reliable evidence, altered to a clip.
    @param content_judgement: Synthetic song judgement.
    @return: None.
    """
    clip = replace(duration_evidence, full_video=False)
    assert judge_song(clip, content_judgement).status is SongStatus.NOT_ELIGIBLE


def test_missing_duration_is_pending(content_judgement):
    """@description: Keep a song pending when no duration evidence exists, never guessing one.
    @param content_judgement: Synthetic song judgement.
    @return: None.
    """
    judgement = judge_song(None, content_judgement)
    assert judgement.status is SongStatus.PENDING
    assert "duration" in judgement.reason.lower()


def test_missing_content_is_pending(duration_evidence):
    """@description: Keep a song pending until an Owner content judgement exists.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @return: None.
    """
    judgement = judge_song(duration_evidence, None)
    assert judgement.status is SongStatus.PENDING
    assert "content" in judgement.reason.lower()


def test_content_not_a_song_is_not_eligible(duration_evidence, content_judgement):
    """@description: Refuse a full video that the content judgement says is not a song.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic judgement, altered to a negative result.
    @return: None.
    """
    not_song = replace(content_judgement, is_song=False)
    assert judge_song(duration_evidence, not_song).status is SongStatus.NOT_ELIGIBLE


def test_unreliable_duration_is_pending(duration_evidence, content_judgement):
    """@description: Treat duration evidence without the reliable flag as pending, not as a decision.
    @param duration_evidence: Synthetic full-video evidence, altered to unreliable.
    @param content_judgement: Synthetic song judgement.
    @return: None.
    """
    unreliable = replace(duration_evidence, reliable=False)
    assert judge_song(unreliable, content_judgement).status is SongStatus.PENDING


def test_unassessable_duration_is_pending(duration_evidence, content_judgement):
    """@description: Keep malformed duration values pending instead of comparing them.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic song judgement.
    @return: None.
    """
    malformed = replace(duration_evidence, seconds="180")
    assert judge_song(malformed, content_judgement).status is SongStatus.PENDING


def test_fully_valid_case_is_eligible(duration_evidence, content_judgement):
    """@description: Accept a complete, reliable, in-range song judged to be a song.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic song judgement.
    @return: None.
    """
    judgement = judge_song(duration_evidence, content_judgement)
    assert judgement.status is SongStatus.ELIGIBLE


@pytest.mark.parametrize("field, value", [
    ("video", "Abc_123-Xyz"), ("video", None),
    ("judged_by", 0), ("judged_by", -1), ("judged_by", True), ("judged_by", "1"), ("judged_by", None),
    ("reason", ""), ("reason", "   "), ("reason", " padded"), ("reason", "padded "), ("reason", 1),
    ("retrieved_at", datetime(2026, 10, 23, 8, 0)), ("retrieved_at", "2026-10-23"),
    ("evidence_reference", ""), ("evidence_reference", "  "), ("evidence_reference", None),
    ("is_song", "yes"), ("is_song", 1), ("is_song", None),
])
def test_invalid_content_judgement_is_refused(field, value):
    """@description: Refuse audit fields that cannot later explain who judged what and when.
    @param field: Constructor field under test.
    @param value: Invalid value for that field.
    @return: None.
    """
    fields = {
        "video": VIDEO, "judged_by": 1, "reason": "synthetic song", "retrieved_at": RETRIEVED_AT,
        "evidence_reference": "synthetic-evidence-1", "is_song": True,
    }
    fields[field] = value
    with pytest.raises(ValueError):
        ContentJudgement(**fields)


def test_naive_retrieval_time_is_refused():
    """@description: Require an aware retrieval time so later records can be ordered reliably.
    @param: none
    @return: None.
    """
    with pytest.raises(ValueError):
        ContentJudgement(video=VIDEO, judged_by=1, reason="synthetic song", retrieved_at=datetime(2026, 10, 23, 8, 0),
                         evidence_reference="synthetic-evidence-1", is_song=True)


def test_aware_retrieval_time_with_offset_is_accepted():
    """@description: Accept any timezone-aware retrieval time, not only UTC.
    @param: none
    @return: None.
    """
    taipei = timezone(timedelta(hours=8))
    judgement = ContentJudgement(video=VIDEO, judged_by=1, reason="synthetic song",
                                 retrieved_at=datetime(2026, 10, 23, 16, 0, tzinfo=taipei),
                                 evidence_reference="synthetic-evidence-1", is_song=True)
    assert judgement.retrieved_at.utcoffset() == timedelta(hours=8)


def test_judgement_reason_is_deterministic(duration_evidence, content_judgement):
    """@description: Return identical status and reason text for identical inputs, so audits can compare them.
    @param duration_evidence: Synthetic reliable full-video evidence.
    @param content_judgement: Synthetic song judgement.
    @return: None.
    """
    first = judge_song(replace(duration_evidence, seconds=721), content_judgement)
    second = judge_song(replace(duration_evidence, seconds=721), content_judgement)
    assert first == second


def test_content_judgement_for_another_video_is_refused_against_this_duration(duration_evidence):
    """@description: Refuse pairing a duration for one video with a content judgement made for a different video.
    @param duration_evidence: Synthetic reliable full-video evidence for VIDEO.
    @return: None.
    """
    other_content = ContentJudgement(video=OTHER_VIDEO, judged_by=1, reason="synthetic song", retrieved_at=RETRIEVED_AT,
                                     evidence_reference="synthetic-evidence-2", is_song=True)
    with pytest.raises(ValueError):
        judge_song(duration_evidence, other_content)


def test_duration_for_another_video_is_refused_against_this_content(content_judgement):
    """@description: Refuse pairing a content judgement with a duration measured for a different video.
    @param content_judgement: Synthetic song judgement for VIDEO.
    @return: None.
    """
    other_duration = DurationEvidence(OTHER_VIDEO, 180, source="synthetic-fixture", reliable=True, full_video=True)
    with pytest.raises(ValueError):
        judge_song(other_duration, content_judgement)


def test_missing_duration_takes_precedence_over_missing_content():
    """@description: Name the duration as the missing input when both inputs are absent, in a fixed order.
    @param: none
    @return: None.
    """
    judgement = judge_song(None, None)
    assert judgement.status is SongStatus.PENDING
    assert "duration" in judgement.reason.lower()
