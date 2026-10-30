"""Judge whether a complete song may enter the reward flow from offline duration and content evidence.

Inputs are a DurationEvidence and an Owner ContentJudgement; the result is a status and reason for the Owner approve path.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from bot.eligibility import DurationEligibility, DurationEvidence, assess_duration
from bot.song_identity import VideoIdentity


class SongStatus(Enum):
    """Name the three outcomes so missing evidence never looks like a refusal."""

    ELIGIBLE = "eligible"
    NOT_ELIGIBLE = "not_eligible"
    PENDING = "pending"


@dataclass(frozen=True)
class ContentJudgement:
    """Carry one Owner's audited statement about whether one video's content is a song."""

    video: VideoIdentity  # [judged video]: Prevents this verdict from being paired with another video's duration.
    judged_by: int  # [judge identity]: Internal positive Owner identity, never inferred from content.
    reason: str  # [audit explanation]: Why the judge considers the content a song or not.
    retrieved_at: datetime  # [retrieval time]: Timezone-aware, so the judgement can be ordered.
    evidence_reference: str  # [provenance pointer]: Where the judged evidence is recorded.
    is_song: bool  # [content verdict]: False refuses the song even when duration passes.

    def __post_init__(self) -> None:
        """@description: Refuse judgements whose judge, reason, time or verdict cannot be audited later.
        @param self: Immutable Owner judgement being constructed.
        @return: None.
        """
        if not isinstance(self.video, VideoIdentity):
            raise ValueError("A valid video identity is required")
        if type(self.judged_by) is not int or self.judged_by <= 0:
            raise ValueError("A positive internal judge identity is required")
        if not _is_unpadded_text(self.reason) or not _is_unpadded_text(self.evidence_reference):
            raise ValueError("Nonempty, unpadded reason and evidence reference are required")
        if not isinstance(self.retrieved_at, datetime) or self.retrieved_at.utcoffset() is None:
            raise ValueError("A timezone-aware retrieval time is required")
        if type(self.is_song) is not bool:
            raise ValueError("A boolean content verdict is required")


@dataclass(frozen=True)
class SongJudgement:
    """Return one song status with the reason that explains it to the Owner."""

    status: SongStatus  # [song status]: Eligibility for the approve path, not an approval.
    reason: str  # [audit explanation]: Names the missing input or the refusal rule that applied.


def judge_song(duration: DurationEvidence | None, content: ContentJudgement | None) -> SongJudgement:
    """@description: Decide song eligibility in a fixed order so identical inputs give identical reasons.
    Order: cross-video mismatch refusal, missing duration, missing content, unreliable duration, clip, unvalidatable or out-of-range duration, content not a song.
    @param duration: Full-video duration evidence, or None when no evidence exists.
    @param content: Owner content judgement, or None when no judgement exists.
    @return: SongJudgement whose ELIGIBLE status only permits the Owner approve path to consume it.
    """
    if duration is not None and content is not None and duration.video != content.video:
        raise ValueError("Duration and content judgement must describe the same video")
    if duration is None:
        return SongJudgement(SongStatus.PENDING, "Duration evidence is missing")
    if content is None:
        return SongJudgement(SongStatus.PENDING, "Content judgement is missing")
    if duration.reliable is not True:
        return SongJudgement(SongStatus.PENDING, "Duration evidence is not marked reliable")
    if duration.full_video is not True:
        return SongJudgement(SongStatus.NOT_ELIGIBLE, "Clip or excerpt is not a complete song")
    duration_result = assess_duration(duration)
    if duration_result is DurationEligibility.PENDING_VALIDATION:
        return SongJudgement(SongStatus.PENDING, "Duration evidence could not be validated")
    if duration_result is DurationEligibility.REJECT:
        return SongJudgement(SongStatus.NOT_ELIGIBLE, "Duration is outside the 60 to 720 second limits")
    if content.is_song is not True:
        return SongJudgement(SongStatus.NOT_ELIGIBLE, "Content judgement says the video is not a song")
    return SongJudgement(SongStatus.ELIGIBLE, "Complete reliable song within limits and judged to be a song")


def _is_unpadded_text(value: object) -> bool:
    """@description: Require audit text to be a string with visible content and no leading or trailing spaces.
    @param value: Candidate audit text from the Owner judgement.
    @return: Whether the text can be stored as an unambiguous audit field.
    """
    return isinstance(value, str) and value != "" and value == value.strip()
