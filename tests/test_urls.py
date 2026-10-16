"""Exercise YouTube identity parsing using synthetic URLs only.

Accepted forms retain provenance while ambiguous and unsupported links fail before persistence.
"""

import pytest

from bot.urls import InvalidVideoUrl, parse_youtube_url

VIDEO_ID = "Abc_123-Xyz"


@pytest.mark.parametrize("source_url", [
    f"https://www.youtube.com/watch?v={VIDEO_ID}&utm_source=test&list=PLexample",
    f"http://youtube.com/watch?v={VIDEO_ID}#t=10",
    f"https://m.youtube.com/watch?v={VIDEO_ID}",
    f"https://music.youtube.com/watch?v={VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}?si=tracking",
    f"https://www.youtube.com/shorts/{VIDEO_ID}",
    f"https://youtube.com/embed/{VIDEO_ID}",
    f"https://youtube.com/live/{VIDEO_ID}",
    f"https://youtube.com/watch?v={VIDEO_ID}&list=PLexample&t=900&start=20&index=3&si=tracking",
    f"https://music.youtube.com/watch?list=RDexample&v={VIDEO_ID}&index=2",
])
def test_supported_video_forms(source_url):
    """@description: Preserve case-sensitive video identity while removing delivery tracking.
    @param source_url: Supported synthetic link variant.
    @return: None.
    """
    video = parse_youtube_url(source_url)
    assert video.video_id == VIDEO_ID
    assert video.source_url == source_url
    assert video.canonical_url == f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.mark.parametrize("source_url", [
    "", "not-a-url", "https://open.spotify.com/track/example",
    f"https://youtube.com.evil.example/watch?v={VIDEO_ID}",
    f"https://evil.example/youtube.com/watch?v={VIDEO_ID}",
    f"https://youtube.com@evil.example/watch?v={VIDEO_ID}",
    f"https://user:secret@youtube.com/watch?v={VIDEO_ID}",
    f"https://youtube.com:443/watch?v={VIDEO_ID}",
    f"ftp://youtube.com/watch?v={VIDEO_ID}",
    f"https://youtube.com/watch?v={VIDEO_ID}&v={VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}?v={VIDEO_ID}",
    "https://youtube.com/playlist?list=PLexample", "https://youtube.com/watch?v=short",
    "https://youtube.com/watch?v=ABCDEFGHIJ!", "https://youtube.com/watch?v=ＡBCDEFGHIJK",
    f"https://youtu.be/{VIDEO_ID}/extra", f"https://youtube.com/shorts/{VIDEO_ID}/",
    f" https://youtu.be/{VIDEO_ID}", f"https://youtu.be/{VIDEO_ID}\n",
    "https://[malformed/watch?v=ABCDEFGHIJK", "https://youtu.be/" + "A" * 2048,
    "https://youtube.com/watch?list=PLexample", "https://youtube.com/watch?v=",
    "https://youtube.com/watch", f"https://youtube.com/videos/{VIDEO_ID}",
])
def test_invalid_or_ambiguous_links(source_url):
    """@description: Reject untrusted links without echoing secret-bearing input.
    @param source_url: Unsupported or malicious synthetic input.
    @return: None.
    """
    with pytest.raises(InvalidVideoUrl) as captured_error:
        parse_youtube_url(source_url)
    assert "secret" not in str(captured_error.value)


def test_video_identity_is_case_sensitive():
    """@description: Preserve the accepted schema's case-sensitive platform identity contract.
    @param: none
    @return: None.
    """
    assert parse_youtube_url(f"https://youtu.be/{VIDEO_ID}").video_id != parse_youtube_url(f"https://youtu.be/{VIDEO_ID.lower()}").video_id
