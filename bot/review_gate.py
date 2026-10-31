"""Combine the song judgement and the version identity into one Owner approval gate.

Inputs are full-video duration evidence, an Owner content judgement and a version assignment for the same video; the
result tells the approve path whether the song may proceed and whether its version is confirmed yet.
"""

from dataclasses import dataclass

from bot.eligibility import DurationEvidence
from bot.review import DurationVerdict
from bot.song_judgement import ContentJudgement, SongStatus, judge_song
from bot.version_identity import VersionAssignment, VersionIdentityStatus


@dataclass(frozen=True)
class ApprovalGate:
    """Report the song decision and the version confirmation separately, with one audit reason for both."""

    song_status: SongStatus  # [song decision]: Only ELIGIBLE lets the Owner approve the song.
    version_confirmed: bool  # [version decision]: Unconfirmed version waits at reward time; it does not refuse the song.
    reason: str  # [audit explanation]: Names the song input or rule and the version state.

    @property
    def song_eligible(self) -> bool:
        """@description: Expose the single yes or no the approve path needs for the song itself.
        @param self: Immutable gate result.
        @return: True only when the song judgement is ELIGIBLE.
        """
        return self.song_status is SongStatus.ELIGIBLE


def evaluate_approval_gate(duration: DurationEvidence | None, content: ContentJudgement | None,
                           identity: VersionAssignment, verdict: DurationVerdict | None = None) -> ApprovalGate:
    """@description: Decide the song and report the version separately, so an unconfirmed version never blocks an eligible song.
    @param duration: Full-video duration evidence for the video, or None when no evidence exists.
    @param content: Owner content judgement for the video, or None when no judgement exists.
    @param identity: Version assignment for the same video, confirmed or not.
    @param verdict: Official-data duration conclusion, consulted only when there is no duration evidence.
    @return: ApprovalGate with the song status, version confirmation and an audit reason.
    """
    if duration is not None and duration.video != identity.video:
        raise ValueError("Duration evidence must describe the identity's video")
    if content is not None and content.video != identity.video:
        raise ValueError("Content judgement must describe the identity's video")
    song = judge_song(duration, content, verdict)
    version_confirmed = identity.status is VersionIdentityStatus.CONFIRMED
    reason = f"Song: {song.reason}. Version: {_version_reason(identity.status)}."
    return ApprovalGate(song.status, version_confirmed, reason)


def _version_reason(status: VersionIdentityStatus) -> str:
    """@description: Name the version state in audit terms so the Owner sees which part is still pending.
    @param status: Identity status of the version assignment.
    @return: Fixed explanation for that status.
    """
    if status is VersionIdentityStatus.CONFIRMED:
        return "identity confirmed"
    if status is VersionIdentityStatus.CONFLICT:
        return "identity conflict, not confirmed"
    return "identity pending confirmation, the reward waits until it is confirmed"
