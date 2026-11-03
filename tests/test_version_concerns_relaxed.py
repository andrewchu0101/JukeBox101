"""Verify the relaxed version-concern rules the Owner approved on 2026-11-03.

Containment needs a shorter title of at least six characters; the same-channel duration signal also needs title similarity of at least 0.4;
four over-broad English keywords no longer count. Inputs are synthetic metadata; nothing reads the network or a database.
"""

import pytest

from bot.version_concerns import ConcernCode, ConcernThresholds, assess_concerns, concerns_against, has_version_keyword
from tests.test_version_concerns import CHANNEL, OTHER_CHANNEL, UNRELATED_TITLE, codes, video


@pytest.mark.parametrize("short_title,expected", [("Sunset", True), ("Sunse", False), ("Stay", False), ("Alone", False), ("Hypnotize", True)])
def test_containment_needs_a_shorter_title_of_at_least_six_characters(short_title, expected) -> None:
    """@description: A short title inside a long one is no longer a concern; six normalized characters still is.
    @param short_title: Candidate title, wholly contained in the existing one.
    @param expected: Whether SIMILAR_TITLE is raised.
    @return: None.
    """
    longer = f"{short_title} Boulevard Winding Road Long Album Edition Number Nine"
    result = concerns_against(video("cand0000001", short_title, channel=OTHER_CHANNEL), video("othr0000001", longer, channel=OTHER_CHANNEL), ConcernThresholds())
    assert (ConcernCode.SIMILAR_TITLE in codes(result)) is expected


def test_the_length_floor_counts_normalized_characters() -> None:
    """@description: Spaces and brackets do not count toward the six characters.
    @param: none
    @return: None.
    """
    longer = "Sun Boulevard Winding Road Long Album Edition Number Nine"
    result = concerns_against(video("cand0000001", "S u n (Live)", channel=OTHER_CHANNEL), video("othr0000001", longer, channel=OTHER_CHANNEL), ConcernThresholds())
    assert ConcernCode.SIMILAR_TITLE not in codes(result)


def test_the_ratio_rule_still_applies_to_short_titles() -> None:
    """@description: The 0.65 ratio is unchanged, so short near-identical titles are still similar.
    @param: none
    @return: None.
    """
    result = concerns_against(video("cand0000001", "abcd", channel=OTHER_CHANNEL), video("othr0000001", "abce", channel=OTHER_CHANNEL), ConcernThresholds())
    assert ConcernCode.SIMILAR_TITLE in codes(result)


@pytest.mark.parametrize("gap,other_title,expected", [(3, "Alpha Beta Gamma", True), (0, "Alpha Beta Gamma", True), (4, "Alpha Beta Gamma", False),
                                                       (3, UNRELATED_TITLE, False), (0, UNRELATED_TITLE, False)])
def test_same_channel_close_duration_also_needs_some_title_similarity(gap, other_title, expected) -> None:
    """@description: Both a gap of at most three seconds and a similar title are required.
    @param gap: Duration difference in seconds.
    @param other_title: Existing title.
    @param expected: Whether the duration concern is raised.
    @return: None.
    """
    result = concerns_against(video("cand0000001", "Alpha Beta Gamma", seconds=180), video("othr0000001", other_title, seconds=180 + gap), ConcernThresholds())
    assert (ConcernCode.SAME_CHANNEL_CLOSE_DURATION in codes(result)) is expected


@pytest.mark.parametrize("other_title,expected", [("abxyz", True), ("axyzw", False)])
def test_the_duration_signal_title_floor_is_zero_point_four_inclusive(other_title, expected) -> None:
    """@description: 'abcde' against 'abxyz' has ratio exactly 0.4 and counts; against 'axyzw' (0.2) it does not.
    @param other_title: Existing title.
    @param expected: Whether the duration concern is raised.
    @return: None.
    """
    result = concerns_against(video("cand0000001", "abcde", seconds=180), video("othr0000001", other_title, seconds=181), ConcernThresholds())
    assert (ConcernCode.SAME_CHANNEL_CLOSE_DURATION in codes(result)) is expected


def test_a_similar_title_and_close_duration_on_different_channels_is_not_a_duration_concern() -> None:
    """@description: The channel condition remains.
    @param: none
    @return: None.
    """
    result = concerns_against(video("cand0000001", "Alpha Beta Gamma", seconds=180), video("othr0000001", "Alpha Beta Gamma", channel=OTHER_CHANNEL, seconds=180),
                              ConcernThresholds())
    assert ConcernCode.SAME_CHANNEL_CLOSE_DURATION not in codes(result)


def test_a_same_channel_close_duration_with_unrelated_titles_is_now_clean() -> None:
    """@description: A different song of the same length from the same channel no longer goes to the Owner.
    @param: none
    @return: None.
    """
    assert assess_concerns(video("cand0000001", "Alpha Beta Gamma", channel=CHANNEL, seconds=180), [video("othr0000001", UNRELATED_TITLE, seconds=181)]) == ()


@pytest.mark.parametrize("title", ["Song (Radio Edit)", "Song Album Version", "Song Club Mix", "Song Demo", "Song version 2", "Song Edit", "Song Mixtape"])
def test_the_over_broad_keywords_no_longer_count(title) -> None:
    """@description: version, mix, edit and demo are removed from the keyword list.
    @param title: Title using only a removed keyword.
    @return: None.
    """
    assert has_version_keyword(title) is False


@pytest.mark.parametrize("title", ["Song (Remix)", "Song Remixed", "Song cover", "Song LIVE", "Song acoustic", "Song instrumental", "Song sped up", "Song slowed",
                                   "Song nightcore", "Song remastered", "Song unplugged", "Song karaoke", "Song 8D", "Song lo-fi", "Song lofi", "Song bass boosted",
                                   "Song ver.2", "歌名 翻唱", "歌名 現場", "歌名 混音", "歌名 純音樂", "歌名 伴奏", "歌名 加速", "歌名 降速", "歌名 重製", "歌名 重新錄製"])
def test_the_remaining_keywords_still_count(title) -> None:
    """@description: Every other approved keyword is unchanged.
    @param title: Title using a kept keyword.
    @return: None.
    """
    assert has_version_keyword(title) is True
