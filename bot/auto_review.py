"""Decide what official metadata and the Owner's channel allowlist may do to one review.

Input is a metadata lookup and the allowlist; output is a verdict the repository applies. The policy never asserts a recording version.
Automatic approval requires every condition below; anything less keeps the Owner in the loop rather than guessing.
"""

from dataclasses import dataclass
from enum import Enum

from bot.eligibility import DurationEligibility, DurationEvidence, assess_duration
from bot.song_identity import VideoIdentity
from bot.youtube_metadata import SOURCE_LABEL, LookupStatus, MetadataLookup, VideoMetadata

AUTO_REVIEW_POLICY_VERSION = "trusted-channel-v1"  # [audit]: Written in every automatic decision; change it when the conditions change.
MUSIC_CATEGORY_ID = "10"  # [YouTube category]: Music; a trusted channel's vlogs and notices do not qualify.
NON_SONG_TITLE_HINTS = ("trailer", "teaser", "preview", "clip", "shorts", "#short", "highlight", "reaction", "interview",
                        "behind the scenes", "making of", "full album", "playlist", "預告", "片段", "剪輯", "精華", "幕後",
                        "訪談", "花絮", "合輯")  # [negative signal only]: A hint withholds trust; its absence proves nothing and it never rejects.
UNAVAILABLE_UPLOAD_STATUSES = frozenset({"deleted", "failed", "rejected"})  # [no playable video]: Reported to the Owner, retryable by evidence.


class AutoReviewKind(Enum):
    """Name each effect a verdict may have on the review."""

    AUTO_APPROVE = "auto_approve"  # [full trust]: Record duration and approve under the automatic source.
    DURATION_ONLY = "duration_only"  # [Owner decides]: Record duration; the song decision stays with the Owner.
    DURATION_REJECT = "duration_reject"  # [hard rule]: Official duration outside the inclusive limits.
    UNAVAILABLE = "unavailable"  # [validation failed]: Missing, private or removed; never a song rejection.
    HOLD = "hold"  # [undetermined]: Live, upcoming, still processing or no duration; nothing is written.
    RETRY = "retry"  # [API failure]: Says nothing about the video; nothing is written.


@dataclass(frozen=True)
class ObservedValues:
    """Hold the official values that only the short-lived observation table may store."""

    duration_seconds: int  # [official duration]: Never written to a snapshot, history row, receipt or reason in retention mode.
    channel_id: str | None  # [official channel]: Same rule.


@dataclass(frozen=True)
class AutoReviewVerdict:
    """Carry one verdict with the evidence and audit reason it would write."""

    kind: AutoReviewKind  # [effect]: Applied by the repository under the writer gate.
    reason: str  # [audit explanation]: Policy version, reason code, channel and retrieval time.
    duration: DurationEvidence | None = None  # [official evidence]: Only for verdicts that record a duration.
    public_reason: str = ""  # [retention-mode explanation]: Policy version and reason code only; no channel or time.
    observation: ObservedValues | None = None  # [short-lived values]: Present only for verdicts that recorded a duration.


def decide_auto_review(lookup: MetadataLookup, trusted_channel_ids: frozenset[str]) -> AutoReviewVerdict:
    """@description: Map one lookup to its only permitted effect, so an empty allowlist or a failed API can never approve anything.
    @param lookup: Official metadata outcome.
    @param trusted_channel_ids: Owner-approved stable channel IDs; may be empty.
    @return: Verdict for the repository to apply.
    """
    if lookup.status in {LookupStatus.TEMPORARY_FAILURE, LookupStatus.QUOTA_EXHAUSTED}:
        return _verdict(AutoReviewKind.RETRY, "api_unavailable")
    if lookup.status is LookupStatus.NOT_FOUND:
        return _verdict(AutoReviewKind.UNAVAILABLE, "video_not_found")
    metadata = lookup.metadata
    if metadata.privacy_status == "private" or metadata.upload_status in UNAVAILABLE_UPLOAD_STATUSES:
        return _verdict(AutoReviewKind.UNAVAILABLE, "video_not_public", metadata)
    if metadata.live_broadcast_content != "none" or metadata.upload_status != "processed" or metadata.duration_seconds is None:
        return _verdict(AutoReviewKind.HOLD, "undetermined", metadata)
    return _duration_verdict(metadata, trusted_channel_ids)


def _duration_verdict(metadata: VideoMetadata, trusted_channel_ids: frozenset[str]) -> AutoReviewVerdict:
    """@description: Apply the shared hard duration limits, then the trust conditions, to a video with a final official duration.
    @param metadata: Official metadata of a processed, non-live video.
    @param trusted_channel_ids: Owner-approved stable channel IDs.
    @return: Rejection, Owner review or automatic approval.
    """
    source = f"{SOURCE_LABEL}@{metadata.fetched_at.isoformat(timespec='seconds')}"
    evidence = DurationEvidence(VideoIdentity(metadata.video_id), metadata.duration_seconds, source, True, True)
    if assess_duration(evidence) is DurationEligibility.REJECT:
        return _verdict(AutoReviewKind.DURATION_REJECT, "duration_out_of_range", metadata, evidence)
    withheld = _trust_withheld(metadata, trusted_channel_ids)
    if withheld is not None:
        return _verdict(AutoReviewKind.DURATION_ONLY, withheld, metadata, evidence)
    return _verdict(AutoReviewKind.AUTO_APPROVE, "trusted_channel_music", metadata, evidence)


def _trust_withheld(metadata: VideoMetadata, trusted_channel_ids: frozenset[str]) -> str | None:
    """@description: Name the first automatic-approval condition this video fails.
    @param metadata: Official metadata.
    @param trusted_channel_ids: Owner-approved stable channel IDs.
    @return: Reason code, or None when every condition holds.
    """
    if metadata.channel_id not in trusted_channel_ids:
        return "channel_not_trusted"
    if metadata.category_id != MUSIC_CATEGORY_ID:
        return "category_not_music"
    if metadata.privacy_status != "public":
        return "video_not_public"
    if metadata.has_live_details:
        return "broadcast_history"
    title = (metadata.title or "").casefold()
    if not title or any(hint in title for hint in NON_SONG_TITLE_HINTS):
        return "title_needs_owner"
    return None


def _verdict(kind: AutoReviewKind, reason_code: str, metadata: VideoMetadata | None = None,
             evidence: DurationEvidence | None = None) -> AutoReviewVerdict:
    """@description: Build a verdict with both audit texts, so retention mode can leave the channel and retrieval time out of every permanent row.
    @param kind: Effect of the verdict.
    @param reason_code: Stable decision reason.
    @param metadata: Official metadata, when any exists.
    @param evidence: Official duration evidence, only for verdicts that record a duration.
    @return: Verdict carrying the full reason, the public reason and the short-lived values.
    """
    observed = None if evidence is None else ObservedValues(metadata.duration_seconds, metadata.channel_id)
    return AutoReviewVerdict(kind, _audit(reason_code, metadata), evidence, _audit(reason_code), observed)


def _audit(reason_code: str, metadata: VideoMetadata | None = None) -> str:
    """@description: Build the audit text; only the stable channel ID and retrieval time are kept, never the title.
    @param reason_code: Stable decision reason.
    @param metadata: Official metadata, when any exists.
    @return: Audit explanation.
    """
    facts = "" if metadata is None else f" channel={metadata.channel_id} fetched_at={metadata.fetched_at.isoformat(timespec='seconds')}"
    return f"auto-review policy={AUTO_REVIEW_POLICY_VERSION} reason={reason_code}{facts}"
