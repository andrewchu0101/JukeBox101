"""Decide what official metadata and the Owner's channel allowlist may do to one review.

Input is a metadata lookup and the allowlist; output is a verdict the repository applies. The policy never asserts a recording version.
Automatic approval requires every condition below; anything less keeps the Owner in the loop rather than guessing.
"""

import re
from dataclasses import dataclass, replace
from enum import Enum

from bot.eligibility import DurationEligibility, DurationEvidence, assess_duration
from bot.song_identity import VideoIdentity
from bot.version_concerns import VersionConcern
from bot.youtube_metadata import SOURCE_LABEL, LookupStatus, MetadataLookup, VideoMetadata

AUTO_REVIEW_POLICY_VERSION = "trusted-channel-v2"  # [audit]: Written in every automatic decision; change it when the conditions change.
AUTO_REVIEW_DECISION_PREFIX = "auto-review policy="  # [audit marker]: Starts every automatic decision reason; the report counts only such rows.
MUSIC_CATEGORY_ID = "10"  # [YouTube category]: Music; a trusted channel's vlogs and notices do not qualify.
ENGLISH_TITLE_HINTS = ("trailer", "teaser", "preview", "clip", "shorts?", "#short", "highlight", "reaction", "interview", "behind the scenes",
                       "making of", "full album", "playlist")  # [whole-word terms]: Matched on word edges, with an optional plural s, never inside another word.
CHINESE_TITLE_HINTS = ("預告", "片段", "剪輯", "精華", "幕後", "訪談", "花絮", "合輯")  # [substring terms]: Chinese has no word boundary.
NON_SONG_TITLE_PATTERN = re.compile(r"(?<![a-z0-9])(?:" + "|".join(hint.replace(" ", r"\s+") + ("" if hint.endswith("?") else "s?") for hint in ENGLISH_TITLE_HINTS)
                                    + r")(?![a-z0-9])")  # [negative signal only]: A hint withholds trust; its absence proves nothing and it never rejects.
APPROVAL_CODE_ALLOWLIST = "trusted_channel_music"  # [audit]: Approval by an Owner-listed channel.
APPROVAL_CODE_MUSIC_CATEGORY = "music_category"  # [audit]: Approval by the Music category alone; distinguishable from the allowlist code.
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
class ComparisonBasis:
    """Describe the world a clean comparison saw, so the writing transaction can refuse a stale clearance."""

    compared_count: int  # [requested songs]: How many existing songs the comparison asked about.
    last_accepted_sequence: int | None = None  # [staleness mark]: Newest accepted submission when the comparison began; set by the pipeline.


@dataclass(frozen=True)
class AutoReviewVerdict:
    """Carry one verdict with the evidence and audit reason it would write."""

    kind: AutoReviewKind  # [effect]: Applied by the repository under the writer gate.
    reason: str  # [audit explanation]: Policy version, reason code, channel and retrieval time.
    duration: DurationEvidence | None = None  # [official evidence]: Only for verdicts that record a duration.
    public_reason: str = ""  # [retention-mode explanation]: Policy version and reason code only; no channel or time.
    observation: ObservedValues | None = None  # [short-lived values]: Present only for verdicts that recorded a duration.
    concerns: tuple[VersionConcern, ...] = ()  # [Owner card reasons]: Codes and existing video IDs only, never a title.
    version_basis: ComparisonBasis | None = None  # [clean comparison]: Present only when the Owner authorised an automatic version confirmation.


def decide_auto_review(lookup: MetadataLookup, trusted_channel_ids: frozenset[str], require_trusted_channel: bool = True) -> AutoReviewVerdict:
    """@description: Map one lookup to its only permitted effect, so an empty allowlist or a failed API can never approve anything.
    @param lookup: Official metadata outcome.
    @param trusted_channel_ids: Owner-approved stable channel IDs; may be empty.
    @param require_trusted_channel: True (allowlist mode) demands a listed channel; False (music_category mode) drops only that condition.
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
    return _duration_verdict(metadata, trusted_channel_ids, require_trusted_channel)


def _duration_verdict(metadata: VideoMetadata, trusted_channel_ids: frozenset[str], require_trusted_channel: bool) -> AutoReviewVerdict:
    """@description: Apply the shared hard duration limits, then the trust conditions, to a video with a final official duration.
    @param metadata: Official metadata of a processed, non-live video.
    @param trusted_channel_ids: Owner-approved stable channel IDs.
    @param require_trusted_channel: Whether the channel must be on the allowlist.
    @return: Rejection, Owner review or automatic approval.
    """
    source = f"{SOURCE_LABEL}@{metadata.fetched_at.isoformat(timespec='seconds')}"
    evidence = DurationEvidence(VideoIdentity(metadata.video_id), metadata.duration_seconds, source, True, True)
    if assess_duration(evidence) is DurationEligibility.REJECT:
        return _verdict(AutoReviewKind.DURATION_REJECT, "duration_out_of_range", metadata, evidence)
    withheld = _trust_withheld(metadata, trusted_channel_ids, require_trusted_channel)
    if withheld is not None:
        return _verdict(AutoReviewKind.DURATION_ONLY, withheld, metadata, evidence)
    approval_code = APPROVAL_CODE_ALLOWLIST if require_trusted_channel else APPROVAL_CODE_MUSIC_CATEGORY
    return _verdict(AutoReviewKind.AUTO_APPROVE, approval_code, metadata, evidence)


def _trust_withheld(metadata: VideoMetadata, trusted_channel_ids: frozenset[str], require_trusted_channel: bool = True) -> str | None:
    """@description: Name the first automatic-approval condition this video fails.
    @param metadata: Official metadata.
    @param trusted_channel_ids: Owner-approved stable channel IDs.
    @param require_trusted_channel: Whether the channel must be on the allowlist.
    @return: Reason code, or None when every condition holds.
    """
    if require_trusted_channel and metadata.channel_id not in trusted_channel_ids:
        return "channel_not_trusted"
    if metadata.channel_id is None:
        return "channel_unknown"  # [open mode fails closed]: Malformed official data never approves; the version comparison also relies on the channel.
    if metadata.category_id != MUSIC_CATEGORY_ID:
        return "category_not_music"
    if metadata.privacy_status != "public":
        return "video_not_public"
    title = (metadata.title or "").casefold()
    if not title or NON_SONG_TITLE_PATTERN.search(title) or any(hint in title for hint in CHINESE_TITLE_HINTS):
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
    return f"{AUTO_REVIEW_DECISION_PREFIX}{AUTO_REVIEW_POLICY_VERSION} reason={reason_code}{facts}"


def refined_verdict(base: AutoReviewVerdict, kind: AutoReviewKind, reason_code: str, metadata: VideoMetadata | None, *,
                    detail: str = "", keep_evidence: bool = True, concerns: tuple[VersionConcern, ...] = (),
                    version_basis: ComparisonBasis | None = None) -> AutoReviewVerdict:
    """@description: Change a verdict's effect and reason after the version comparison, rebuilding both audit texts the same way as the original.
    @param base: Verdict produced by the trust policy.
    @param kind: New effect.
    @param reason_code: Stable decision reason.
    @param metadata: Official metadata for the full audit text.
    @param detail: Extra audit text, limited to codes and video IDs by the caller.
    @param keep_evidence: False drops the duration evidence and short-lived values, as for a verdict that wrote nothing.
    @param concerns: Doubts to show the Owner.
    @param version_basis: Comparison basis for an automatic version confirmation.
    @return: New verdict.
    """
    evidence = (base.duration, base.observation) if keep_evidence else (None, None)
    return replace(base, kind=kind, reason=_audit(reason_code, metadata if keep_evidence else None) + detail,
                   public_reason=_audit(reason_code) + detail, duration=evidence[0], observation=evidence[1],
                   concerns=concerns, version_basis=version_basis)
