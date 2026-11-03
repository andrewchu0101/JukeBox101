"""Verify the pure version-concern rules that decide whether a new submission may be confirmed without the Owner.

Inputs are synthetic official metadata; nothing here reads the network, the clock, the environment or a database.
A concern only sends a case to the Owner; no rule here ever proves two videos are, or are not, the same recording.
"""

import dataclasses
from datetime import datetime, timezone

import pytest

from bot.version_concerns import (ConcernCode, ConcernThresholds, VersionConcern, assess_concerns, concerns_against,
                                  has_version_keyword, normalize_title, title_similarity)
from bot.youtube_metadata import VideoMetadata

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
CHANNEL, OTHER_CHANNEL = "UC" + "a" * 22, "UC" + "b" * 22
TITLE_CANARY = "TitleCanaryZq9"  # [leak marker]: Must never appear in a concern, its representation or its fields.
UNRELATED_TITLE = "Zzzz Qqqq 1234"  # [dissimilar title]: Far below the similarity threshold.


def video(video_id: str, title: str | None = "Hypnotize", *, channel: str = CHANNEL, seconds: int | None = 180) -> VideoMetadata:
    """@description: Build one synthetic official metadata record.
    @param video_id: Eleven-character identifier.
    @param title: Official title, or None.
    @param channel: Stable channel identifier.
    @param seconds: Official duration, or None.
    @return: Metadata.
    """
    return VideoMetadata(video_id, channel, "10", title, "none", False, "processed", "public", seconds, NOW)


def codes(concerns) -> set[ConcernCode]:
    """@description: Reduce concerns to their codes.
    @param concerns: Concerns returned by a rule.
    @return: Set of codes.
    """
    return {concern.code for concern in concerns}


CANDIDATE = video("cand0000001", "Alpha Beta Gamma")


def test_default_thresholds_are_the_owner_approved_values() -> None:
    """@description: Lock the approved numbers so a change is a visible policy change.
    @param: none
    @return: None.
    """
    thresholds = ConcernThresholds()
    assert (thresholds.max_duration_gap_seconds, thresholds.min_title_similarity, thresholds.min_judgeable_title_length) == (3, 0.65, 3)


def test_normalization_removes_case_width_brackets_featuring_and_punctuation() -> None:
    """@description: Equivalent upload titles must normalize to the same text.
    @param: none
    @return: None.
    """
    assert normalize_title("Song (Official MV) [4K] feat. X") == normalize_title("SONG")
    assert normalize_title("ＳＯＮＧ！") == normalize_title("song")
    assert normalize_title("Song 【公式】") == normalize_title("song")


@pytest.mark.parametrize("title", [None, "", "   ", "()", "[Official]"])
def test_normalizing_an_absent_or_empty_title_gives_empty_text(title) -> None:
    """@description: Missing titles must not raise.
    @param title: Absent, blank or all-bracket title.
    @return: None.
    """
    assert normalize_title(title) == ""


def test_title_similarity_is_the_difflib_ratio_of_the_normalized_titles() -> None:
    """@description: Fix the algorithm: 'abcd' vs 'abce' is exactly 0.75 and equivalent titles are 1.0.
    @param: none
    @return: None.
    """
    assert title_similarity("abcd", "abce") == pytest.approx(0.75)
    assert title_similarity("Song (Live)", "SONG") == pytest.approx(1.0)


@pytest.mark.parametrize("gap,expected", [(0, True), (3, True), (4, False)])
def test_same_channel_close_duration_boundary_is_three_seconds_inclusive(gap, expected) -> None:
    """@description: Three seconds apart on one channel is a concern; four is not.
    @param gap: Duration difference in seconds.
    @param expected: Whether the concern is raised.
    @return: None.
    """
    other = video("othr0000001", "Beta Gamma Delta", seconds=180 + gap)
    result = concerns_against(video("cand0000001", "Alpha Beta Gamma", seconds=180), other, ConcernThresholds())
    assert (ConcernCode.SAME_CHANNEL_CLOSE_DURATION in codes(result)) is expected
    if expected:
        assert [concern.other_video_id for concern in result] == ["othr0000001"]


def test_identical_duration_on_a_different_channel_is_not_a_duration_concern() -> None:
    """@description: Only the same channel makes a close duration suspicious.
    @param: none
    @return: None.
    """
    other = video("othr0000001", UNRELATED_TITLE, channel=OTHER_CHANNEL, seconds=180)
    assert concerns_against(video("cand0000001", "Alpha Beta Gamma", seconds=180), other, ConcernThresholds()) == ()


@pytest.mark.parametrize("candidate_seconds,other_seconds", [(None, 180), (180, None), (None, None)])
def test_a_missing_duration_never_creates_a_duration_match(candidate_seconds, other_seconds) -> None:
    """@description: Missing durations must not raise and must not look like a match.
    @param candidate_seconds: Candidate duration.
    @param other_seconds: Existing duration.
    @return: None.
    """
    other = video("othr0000001", UNRELATED_TITLE, seconds=other_seconds)
    result = concerns_against(video("cand0000001", "Alpha Beta Gamma", seconds=candidate_seconds), other, ConcernThresholds())
    assert ConcernCode.SAME_CHANNEL_CLOSE_DURATION not in codes(result)


@pytest.mark.parametrize("minimum,expected", [(0.75, True), (0.76, False)])
def test_title_similarity_threshold_boundary_is_inclusive(minimum, expected) -> None:
    """@description: A ratio exactly at the threshold is a concern; just above the ratio is not.
    @param minimum: Configured minimum similarity.
    @param expected: Whether the concern is raised.
    @return: None.
    """
    thresholds = ConcernThresholds(min_title_similarity=minimum)
    result = concerns_against(video("cand0000001", "abcd", channel=OTHER_CHANNEL), video("othr0000001", "abce"), thresholds)
    assert (ConcernCode.SIMILAR_TITLE in codes(result)) is expected


def test_a_title_contained_in_a_longer_one_is_similar_even_below_the_ratio() -> None:
    """@description: Short titles swing the ratio, so full containment of a judgeable title is also a concern.
    @param: none
    @return: None.
    """
    result = concerns_against(video("cand0000001", "Hypnotize", channel=OTHER_CHANNEL),
                              video("othr0000001", "Hypnotize System of a Down Toxicity Album Version Long"), ConcernThresholds())
    assert ConcernCode.SIMILAR_TITLE in codes(result)


def test_dissimilar_titles_on_different_channels_raise_nothing() -> None:
    """@description: Unrelated songs must pass this pair check.
    @param: none
    @return: None.
    """
    other = video("othr0000001", UNRELATED_TITLE, channel=OTHER_CHANNEL, seconds=300)
    assert concerns_against(video("cand0000001", "Alpha Beta Gamma"), other, ConcernThresholds()) == ()


def test_every_pair_signal_is_reported_independently() -> None:
    """@description: One pair can trigger both pair signals, and both must be visible to the Owner.
    @param: none
    @return: None.
    """
    result = concerns_against(video("cand0000001", "Same Song", seconds=181), video("othr0000001", "SAME SONG", seconds=180), ConcernThresholds())
    assert codes(result) == {ConcernCode.SAME_CHANNEL_CLOSE_DURATION, ConcernCode.SIMILAR_TITLE}


@pytest.mark.parametrize("title", ["Song (Remix)", "Song REMIX", "Song live at Wembley", "Song - Acoustic", "Song instrumental",
                                   "Song sped up", "Song slowed", "Song nightcore", "Song remastered 2011", "Song ver.2", "Song cover",
                                   "歌名 翻唱", "歌名 現場", "歌名 混音", "歌名 伴奏", "歌名 加速版", "Song 8D audio", "Song lofi"])
def test_version_keywords_are_detected(title) -> None:
    """@description: Each approved keyword family is a version concern.
    @param title: Title containing a keyword.
    @return: None.
    """
    assert has_version_keyword(title) is True


@pytest.mark.parametrize("title", ["Deliver Us", "Alive", "Olive Tree", "Hypnotize", "Evolve", "Medium", None, ""])
def test_keywords_match_on_word_boundaries_not_substrings(title) -> None:
    """@description: 'live' must not match 'deliver' or 'alive'.
    @param title: Title without a real keyword.
    @return: None.
    """
    assert has_version_keyword(title) is False


@pytest.mark.parametrize("title", [None, "", "  ", "ab", "(Official)"])
def test_an_unjudgeable_title_is_a_concern_not_a_clearance(title) -> None:
    """@description: A blank or too-short normalized title cannot be compared, so it must go to the Owner.
    @param title: Unjudgeable title.
    @return: None.
    """
    result = assess_concerns(video("cand0000001", title), [video("othr0000001", UNRELATED_TITLE, seconds=300)])
    assert ConcernCode.TITLE_UNJUDGEABLE in codes(result)


def test_a_keyword_in_the_candidate_is_reported_even_with_nothing_to_compare() -> None:
    """@description: The keyword check needs no existing song.
    @param: none
    @return: None.
    """
    assert codes(assess_concerns(video("cand0000001", "Great Song (Live)"), [])) == {ConcernCode.VERSION_KEYWORD}


def test_a_clean_candidate_against_unrelated_songs_has_no_concern() -> None:
    """@description: The all-clear case that allows automatic confirmation.
    @param: none
    @return: None.
    """
    others = [video(f"othr{index:07d}", UNRELATED_TITLE, channel=OTHER_CHANNEL, seconds=300 + index) for index in range(5)]
    assert assess_concerns(video("cand0000001", "Alpha Beta Gamma"), others) == ()


def test_concerns_name_the_other_video_for_each_matching_song() -> None:
    """@description: The Owner card needs a link to each suspected song.
    @param: none
    @return: None.
    """
    others = [video("othr0000001", "Alpha Beta Gamma", channel=OTHER_CHANNEL), video("othr0000002", UNRELATED_TITLE, channel=OTHER_CHANNEL),
              video("othr0000003", "alpha beta gamma!", channel=OTHER_CHANNEL)]
    assert {concern.other_video_id for concern in assess_concerns(CANDIDATE, others)} == {"othr0000001", "othr0000003"}


def test_a_concern_carries_a_code_and_a_video_id_and_never_a_title() -> None:
    """@description: The type itself guarantees titles cannot leak through a concern.
    @param: none
    @return: None.
    """
    assert [field.name for field in dataclasses.fields(VersionConcern)] == ["code", "other_video_id"]
    result = assess_concerns(video("cand0000001", f"{TITLE_CANARY} live"), [video("othr0000001", f"{TITLE_CANARY} live", seconds=180)])
    assert result and TITLE_CANARY.lower() not in repr(result).lower()


def test_the_rules_are_pure_and_deterministic() -> None:
    """@description: Same input, same output, no mutation of the inputs.
    @param: none
    @return: None.
    """
    others = [video("othr0000001", "Alpha Beta Gamma", channel=OTHER_CHANNEL)]
    assert assess_concerns(CANDIDATE, others) == assess_concerns(CANDIDATE, others)
    assert CANDIDATE.title == "Alpha Beta Gamma" and others[0].title == "Alpha Beta Gamma"
