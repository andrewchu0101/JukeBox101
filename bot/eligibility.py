"""Assess full-video duration using caller-supplied offline evidence only.

No provider is contacted; evidence reliability is an explicit input rather than a verified external fact.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from bot.song_identity import VideoIdentity

MIN_VIDEO_DURATION_SECONDS = 60
MAX_VIDEO_DURATION_SECONDS = 720


class DurationEligibility(Enum):
    """Distinguish a hard duration decision from evidence that remains unverified."""

    PASS = "pass"
    REJECT = "reject"
    PENDING_VALIDATION = "pending_validation"


@dataclass(frozen=True)
class DurationEvidence:
    """Bind an exact full-video duration assertion to its platform identity and source."""

    video: VideoIdentity  # [evidence subject]: Prevent using another video's duration.
    seconds: object  # [untrusted duration]: Only nonnegative int or finite Decimal is assessable.
    source: str | None = None  # [provenance]: Caller-provided evidence source, not a trust certificate.
    reliable: bool = False
    full_video: bool = False  # [duration scope]: URL-selected clips cannot satisfy this contract.


def assess_duration(evidence: DurationEvidence) -> DurationEligibility:
    """@description: Apply inclusive hard limits without rounding or trusting unknown evidence.
    @param evidence: Full-video evidence whose reliability is asserted by an offline caller.
    @return: PASS, REJECT or PENDING_VALIDATION, never a song approval.
    """
    if not isinstance(evidence.video, VideoIdentity):
        return DurationEligibility.PENDING_VALIDATION
    if evidence.reliable is not True or evidence.full_video is not True:
        return DurationEligibility.PENDING_VALIDATION
    if not isinstance(evidence.source, str) or not evidence.source.strip():
        return DurationEligibility.PENDING_VALIDATION
    if not _valid_seconds(evidence.seconds):
        return DurationEligibility.PENDING_VALIDATION
    if MIN_VIDEO_DURATION_SECONDS <= evidence.seconds <= MAX_VIDEO_DURATION_SECONDS:
        return DurationEligibility.PASS
    return DurationEligibility.REJECT


def _valid_seconds(seconds: object) -> bool:
    """@description: Exclude booleans, floats, malformed values and nonfinite durations.
    @param seconds: Synthetic duration value supplied at the evidence boundary.
    @return: Whether exact nonnegative comparisons have a defined meaning.
    """
    if type(seconds) is int:
        return seconds >= 0
    if isinstance(seconds, Decimal):
        return seconds.is_finite() and seconds >= 0
    return False
