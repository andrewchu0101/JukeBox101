"""Refine an automatic approval with the version comparison, so a clean result may confirm the version and any doubt reaches the Owner.

Inputs are the trust verdict, the new video's metadata and a batch of existing songs read in memory; the output is a verdict that carries codes and video IDs only.
A failed or exhausted comparison asks for a later retry; it never approves, rejects or writes a pending review.
"""

import uuid
from dataclasses import replace
from datetime import datetime

from bot.auto_review import AUTO_REVIEW_POLICY_VERSION, AutoReviewKind, AutoReviewVerdict, ComparisonBasis, refined_verdict
from bot.evidence_record import SYSTEM_JUDGE_ID, EvidenceRecord, EvidenceSourceType, record_evidence
from bot.owner_card_versions import new_version_identity
from bot.song_identity import VideoIdentity
from bot.version_concerns import VersionConcern, ConcernThresholds, assess_concerns
from bot.youtube_metadata import BatchLookup, BatchStatus, VideoMetadata

MAX_CITED_VIDEO_IDS = 10  # [audit bound]: The reason column holds 1024 characters; the card needs only the first few links.
RETRY_REASON_CODE = "api_unavailable"  # [same cause as a failed lookup]: The video itself was not judged.
CONCERN_REASON_CODE = "version_concern"  # [Owner review]: A doubt, not a rejection.
STALE_REASON_CODE = "comparison_stale"  # [Owner review]: A submission arrived during the comparison, so the clearance did not cover it.
CONFIRMED_REASON_CODE = "trusted_channel_music_no_version_concern"  # [system judgement]: Authorised by the Owner, not proof of a new recording.


def refine_verdict(verdict: AutoReviewVerdict, candidate: VideoMetadata, comparison: BatchLookup, compared_count: int,
                   thresholds: ConcernThresholds) -> AutoReviewVerdict:
    """@description: Apply the version comparison to an approval and leave every other verdict untouched.
    @param verdict: Verdict from the trust policy.
    @param candidate: Official metadata of the new video.
    @param comparison: Existing songs fetched for this comparison.
    @param compared_count: How many existing songs were asked about.
    @param thresholds: Active concern limits.
    @return: The same verdict, a retry request, an Owner-review verdict with concerns, or an approval carrying its comparison basis.
    """
    if verdict.kind is not AutoReviewKind.AUTO_APPROVE:
        return verdict
    if comparison.status is not BatchStatus.COMPLETE:
        return refined_verdict(verdict, AutoReviewKind.RETRY, RETRY_REASON_CODE, None, keep_evidence=False)
    concerns = assess_concerns(candidate, comparison.found, thresholds)
    if concerns:
        return refined_verdict(verdict, AutoReviewKind.DURATION_ONLY, CONCERN_REASON_CODE, candidate,
                               detail=_concern_detail(concerns), concerns=concerns)
    return refined_verdict(verdict, AutoReviewKind.AUTO_APPROVE, CONFIRMED_REASON_CODE, candidate, version_basis=ComparisonBasis(compared_count))


def _concern_detail(concerns: tuple[VersionConcern, ...]) -> str:
    """@description: Name the concern codes and the first few existing videos for the audit text, never a title or channel.
    @param concerns: Doubts found.
    @return: Text starting with a space.
    """
    codes = ",".join(sorted({concern.code.value for concern in concerns}))
    video_ids = list(dict.fromkeys(concern.other_video_id for concern in concerns if concern.other_video_id))[:MAX_CITED_VIDEO_IDS]
    return f" concerns={codes}" + (f" other_videos={','.join(video_ids)}" if video_ids else "")


def stale_verdict(verdict: AutoReviewVerdict) -> AutoReviewVerdict:
    """@description: Withdraw a clean comparison that a later submission made out of date, so the case waits for the Owner instead of being confirmed.
    @param verdict: Approval carrying a comparison basis.
    @return: Owner-review verdict with the stale reason and no basis; the duration evidence is kept.
    """
    return replace(verdict, kind=AutoReviewKind.DURATION_ONLY, version_basis=None,
                   reason=verdict.reason.replace(CONFIRMED_REASON_CODE, STALE_REASON_CODE),
                   public_reason=verdict.public_reason.replace(CONFIRMED_REASON_CODE, STALE_REASON_CODE))


def system_version_evidence(video: VideoIdentity, compared_count: int, retrieved_at: datetime) -> EvidenceRecord:
    """@description: Build the audit record of an Owner-authorised automatic version confirmation, never claiming the Owner or a recording category.
    @param video: The newly approved video.
    @param compared_count: How many existing songs the clean comparison covered.
    @param retrieved_at: Timezone-aware time of the judgement.
    @return: System-judged evidence naming a fresh opaque version; it holds no title or channel.
    """
    reason = f"系統判定：與最近 {compared_count} 首既有歌曲比對無疑慮（Owner 授權的自動判斷，不是證明）"
    return record_evidence(uuid.uuid4().hex, video, EvidenceSourceType.SYSTEM_JUDGEMENT, f"auto-compare:{AUTO_REVIEW_POLICY_VERSION}",
                           retrieved_at, reason, SYSTEM_JUDGE_ID, new_version_identity())
