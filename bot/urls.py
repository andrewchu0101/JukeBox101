"""Normalize supported YouTube video links without contacting the platform.

Strict parsing identifies videos for pending submissions, never certifying musical eligibility.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

MAX_SOURCE_URL_LENGTH = 2048
YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})
VIDEO_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{11}", re.ASCII)


class InvalidVideoUrl(ValueError):
    """Report an unsupported or ambiguous link without echoing private input."""


@dataclass(frozen=True)
class YouTubeVideo:
    """Carry original provenance and normalized identity for a pending video."""

    video_id: str
    source_url: str
    canonical_url: str


def canonical_watch_url(video_id: str) -> str:
    """@description: Build the single canonical watch address of an already validated video identifier, used as the verified delivery link.
    @param video_id: Platform video identifier that matched the strict identifier pattern.
    @return: Canonical https watch URL with no tracking parameters.
    """
    if VIDEO_ID_PATTERN.fullmatch(video_id) is None:
        raise InvalidVideoUrl("請提供有效的 YouTube 影片網址。")
    return f"https://www.youtube.com/watch?v={video_id}"


def parse_youtube_url(source_url: str) -> YouTubeVideo:
    """@description: Separate video identity from tracking and untrusted hostname claims.
    @param source_url: User-provided URL retained as submission provenance.
    @return: Normalized video identity, without asserting that it is music.
    """
    if not source_url or len(source_url) > MAX_SOURCE_URL_LENGTH:
        raise InvalidVideoUrl("請提供有效的 YouTube 影片網址。")
    if any(character.isspace() or ord(character) < 32 for character in source_url):
        raise InvalidVideoUrl("網址不得包含空白或控制字元。")
    try:
        parsed_url = urlsplit(source_url)
        invalid_authority = parsed_url.username or parsed_url.password or parsed_url.port is not None
    except ValueError:
        raise InvalidVideoUrl("網址格式不正確。") from None
    if parsed_url.scheme not in {"http", "https"} or invalid_authority:
        raise InvalidVideoUrl("請使用不含帳密或連接埠的 YouTube 網址。")
    # [video identity]: Only approved exact hostnames and unambiguous video paths are accepted.
    video_id = _video_id_from_parts(parsed_url.hostname, parsed_url.path, parsed_url.query)
    if VIDEO_ID_PATTERN.fullmatch(video_id) is None:
        raise InvalidVideoUrl("YouTube 影片 ID 必須為 11 個有效字元。")
    return YouTubeVideo(video_id, source_url, f"https://www.youtube.com/watch?v={video_id}")


def _video_id_from_parts(hostname: str | None, path: str, query: str) -> str:
    """@description: Reject playlists and conflicting identifiers before persistence.
    @param hostname: Parsed hostname, requiring an exact approved platform match.
    @param path: Video resource path, without percent-decoding ambiguous identifiers.
    @param query: Query parameters potentially containing the watch identifier.
    @return: Single platform-specific video identifier.
    """
    parameters = parse_qs(query, keep_blank_values=True)
    if hostname in YOUTUBE_HOSTS and path == "/watch":
        video_identifiers = parameters.get("v", [])
        if len(video_identifiers) == 1:
            return video_identifiers[0]
        raise InvalidVideoUrl("網址必須指定唯一的 YouTube 影片。")
    path_segments = path.split("/")
    if hostname == "youtu.be" and len(path_segments) == 2:
        video_id = path_segments[1]
    elif hostname in YOUTUBE_HOSTS and len(path_segments) == 3 and path_segments[1] in {"shorts", "embed", "live"}:
        video_id = path_segments[2]
    else:
        raise InvalidVideoUrl("只支援 YouTube 影片網址，不支援 Spotify 或播放清單網址。")
    if "v" in parameters:
        raise InvalidVideoUrl("網址含有多個影片識別來源。")
    return video_id
