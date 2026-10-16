"""Separate platform video identity from future recording identity and submission outcomes.

These immutable offline values reuse URL parsing without matching recordings or claiming global novelty.
"""

from dataclasses import dataclass
from enum import Enum

from bot.urls import VIDEO_ID_PATTERN, parse_youtube_url


@dataclass(frozen=True)
class VideoIdentity:
    """Identify one case-sensitive YouTube video, never a musical work."""

    video_id: str  # [platform identity]: The same video survives URL parameter changes.

    def __post_init__(self) -> None:
        """@description: Prevent malformed identifiers entering offline review contracts.
        @param self: Immutable platform identifier being constructed.
        @return: None.
        """
        if not isinstance(self.video_id, str) or VIDEO_ID_PATTERN.fullmatch(self.video_id) is None:
            raise ValueError("A valid YouTube video identifier is required")


@dataclass(frozen=True)
class SongIdentity:
    """Reserve an optional recording reference without deriving or merging recordings."""

    video: VideoIdentity  # [video identity]: Existing platform-level deduplication unit.
    recording_id: str | None = None  # [future identity]: Opaque reference, not a matching decision.


class SubmissionDisposition(Enum):
    """Keep externally determined submission novelty separate from review decisions."""

    UNKNOWN = "unknown"
    NEW_CANDIDATE = "new_candidate"
    DUPLICATE = "duplicate"


def video_identity_from_url(source_url: str) -> VideoIdentity:
    """@description: Reuse accepted URL normalization for platform identity comparisons.
    @param source_url: Single-video URL with optional playlist, timestamp or tracking parameters.
    @return: Case-sensitive video identity without content verification.
    """
    return VideoIdentity(parse_youtube_url(source_url).video_id)
