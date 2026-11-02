"""Refine an automatic approval with the version comparison, so a clean result may confirm the version and any doubt reaches the Owner.

Inputs are the trust verdict, the new video's metadata and a batch of existing songs read in memory; the output is a verdict that carries codes and video IDs only.
A failed or exhausted comparison asks for a later retry; it never approves, rejects or writes a pending review.
"""

from bot.auto_review import AutoReviewKind, AutoReviewVerdict, ComparisonBasis, refined_verdict
from bot.version_concerns import VersionConcern, ConcernThresholds, assess_concerns
from bot.youtube_metadata import BatchLookup, BatchStatus, VideoMetadata

MAX_CITED_VIDEO_IDS = 10  # [audit bound]: The reason column holds 1024 characters; the card needs only the first few links.
RETRY_REASON_CODE = "api_unavailable"  # [same cause as a failed lookup]: The video itself was not judged.
CONCERN_REASON_CODE = "version_concern"  # [Owner review]: A doubt, not a rejection.
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
