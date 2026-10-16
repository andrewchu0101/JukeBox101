"""Verify URL aliases share video identity without inferring recording equivalence.

Synthetic identifiers preserve existing canonical URLs and leave recording matching unimplemented.
"""

import pytest

from bot.song_identity import SongIdentity, VideoIdentity, video_identity_from_url
from bot.urls import parse_youtube_url

VIDEO_ID = "Abc_123-Xyz"


@pytest.mark.parametrize("source_url", [
    f"https://youtube.com/watch?v={VIDEO_ID}",
    f"https://youtube.com/watch?list=PLsynthetic&v={VIDEO_ID}&index=3",
    f"https://youtube.com/watch?v={VIDEO_ID}&t=900&start=20&utm_source=synthetic",
    f"https://youtu.be/{VIDEO_ID}?list=PLsynthetic&t=1&si=synthetic",
    f"https://music.youtube.com/watch?v={VIDEO_ID}&list=RDsynthetic",
    f"https://youtube.com/shorts/{VIDEO_ID}?t=10",
])
def test_aliases_preserve_video_and_canonical_url(source_url):
    """@description: Prevent timestamps and playlist context from creating new identities.
    @param source_url: Supported synthetic single-video representation.
    @return: None.
    """
    assert video_identity_from_url(source_url) == VideoIdentity(VIDEO_ID)
    assert parse_youtube_url(source_url).canonical_url == f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.mark.parametrize("video_id", [None, "", "short", "ABCDEFGHIJ!", "ＡBCDEFGHIJK", 123])
def test_invalid_identity_is_refused(video_id):
    """@description: Keep malformed platform identifiers out of review evidence.
    @param video_id: Invalid synthetic identifier.
    @return: None.
    """
    with pytest.raises(ValueError):
        VideoIdentity(video_id)


def test_distinct_videos_are_not_automatically_matched():
    """@description: Reserve recording references without introducing cross-version matching.
    @param: none
    @return: None.
    """
    first_identity = SongIdentity(VideoIdentity(VIDEO_ID))
    second_identity = SongIdentity(VideoIdentity("Def_456-Uvw"))
    assert first_identity != second_identity
    assert first_identity.recording_id is None and second_identity.recording_id is None
    assert VideoIdentity(VIDEO_ID) != VideoIdentity(VIDEO_ID.lower())
