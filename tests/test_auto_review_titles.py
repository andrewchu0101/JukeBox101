"""Verify that English non-song title hints match whole words only, while Chinese hints stay substrings.

Inputs are synthetic official metadata; the trust condition is read in music_category mode. Nothing touches the network or a database.
"""

import pytest

from bot.auto_review import _trust_withheld
from tests.test_youtube_metadata import UNTRUSTED, found


def withheld(title: str | None) -> str | None:
    """@description: Read the trust condition for one title with every other condition satisfied.
    @param title: Official title.
    @return: Withheld reason code, or None when the title does not withhold trust.
    """
    return _trust_withheld(found(channel_id=UNTRUSTED, title=title).metadata, frozenset(), False)


@pytest.mark.parametrize("title", ["Eclipse", "Total Eclipse of the Heart", "Previewing Tomorrow", "Shortstop Blues", "Highlighter", "Reactionary Love",
                                   "Interviewer", "Playlisted", "Remaking Of Dreams", "Trailerpark", "Tapeclip Song", "Behind The Scenesy"])
def test_an_english_hint_inside_a_longer_word_does_not_withhold_trust(title) -> None:
    """@description: The hint must be a whole word, so ordinary song titles are no longer sent to the Owner.
    @param title: Title that merely contains a hint as part of another word.
    @return: None.
    """
    assert withheld(title) is None


@pytest.mark.parametrize("title", ["Song (Official Trailer)", "Song Teaser", "Song PREVIEW", "Song Clip", "Song Highlights", "Song Highlight",
                                   "Song #shorts", "Fan Reaction", "Artist Interview", "Best Playlist", "Song - Shorts", "Song Clips"])
def test_an_english_hint_as_a_whole_word_still_withholds_trust(title) -> None:
    """@description: A real preview, clip or highlight is still not auto-approved.
    @param title: Title containing a hint as a whole word.
    @return: None.
    """
    assert withheld(title) == "title_needs_owner"


@pytest.mark.parametrize("title", ["Song Behind The Scenes", "Song (behind the scenes)", "The Making Of Song", "Full Album", "FULL ALBUM stream"])
def test_a_multi_word_hint_matches_the_whole_phrase_on_word_edges(title) -> None:
    """@description: Phrases are matched as phrases with word edges.
    @param title: Title containing a phrase hint.
    @return: None.
    """
    assert withheld(title) == "title_needs_owner"


@pytest.mark.parametrize("title", ["Song 預告", "歌名片段", "歌名剪輯版", "精華", "幕後故事", "訪談", "花絮", "合輯"])
def test_a_chinese_hint_still_matches_as_a_substring(title) -> None:
    """@description: Chinese has no word boundary, so the substring rule is unchanged.
    @param title: Title containing a Chinese hint.
    @return: None.
    """
    assert withheld(title) == "title_needs_owner"


@pytest.mark.parametrize("title", [None, "", "Plain Song Title"])
def test_missing_title_still_goes_to_the_owner_and_a_plain_title_does_not(title) -> None:
    """@description: Empty or missing titles remain unjudgeable.
    @param title: Official title.
    @return: None.
    """
    assert withheld(title) == (None if title == "Plain Song Title" else "title_needs_owner")
