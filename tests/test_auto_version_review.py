"""Verify how version concerns refine an automatic approval into a confirmation, an Owner review or a later retry.

Metadata and comparison batches are synthetic. An approval is only refined, never created: every other verdict passes through unchanged,
a failed or exhausted comparison never approves, and no verdict text carries a title.
"""

from datetime import datetime, timezone

import pytest

from bot.auto_review import AutoReviewKind, decide_auto_review
from bot.auto_version_review import refine_verdict
from bot.version_concerns import ConcernCode, ConcernThresholds
from bot.youtube_metadata import BatchLookup, BatchStatus, LookupStatus, MetadataLookup, VideoMetadata

NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)
TRUSTED, OTHER = "UC" + "a" * 22, "UC" + "b" * 22
TITLE_CANARY = "TitleCanaryZq9"


def meta(video_id: str = "cand0000001", title: str = "Alpha Beta Gamma", *, channel: str = TRUSTED, seconds: int = 189) -> VideoMetadata:
    """@description: Build trusted-channel music metadata.
    @param video_id: Identifier.
    @param title: Official title.
    @param channel: Channel.
    @param seconds: Duration.
    @return: Metadata.
    """
    return VideoMetadata(video_id, channel, "10", title, "none", False, "processed", "public", seconds, NOW)


def approved(candidate: VideoMetadata):
    """@description: Produce the pre-refinement automatic approval for a trusted candidate.
    @param candidate: Candidate metadata.
    @return: AUTO_APPROVE verdict.
    """
    verdict = decide_auto_review(MetadataLookup(LookupStatus.FOUND, candidate), frozenset({TRUSTED}))
    assert verdict.kind is AutoReviewKind.AUTO_APPROVE
    return verdict


def batch(*found: VideoMetadata, status: BatchStatus = BatchStatus.COMPLETE) -> BatchLookup:
    """@description: Build a comparison batch.
    @param found: Existing songs the provider returned.
    @param status: Batch status.
    @return: Batch.
    """
    return BatchLookup(status, tuple(found))


def refine(candidate: VideoMetadata, comparison: BatchLookup, compared: int | None = None):
    """@description: Refine an approval with default thresholds.
    @param candidate: Candidate metadata.
    @param comparison: Comparison batch.
    @param compared: How many existing songs were requested.
    @return: Refined verdict.
    """
    count = len(comparison.found) if compared is None else compared
    return refine_verdict(approved(candidate), candidate, comparison, count, ConcernThresholds())


def test_no_concern_keeps_the_approval_and_attaches_the_comparison_basis() -> None:
    """@description: The all-clear case approves and carries what was compared, for re-validation inside the transaction.
    @param: none
    @return: None.
    """
    verdict = refine(meta(), batch(meta("othr0000001", "Zzzz Qqqq 1234", channel=OTHER, seconds=300)))
    assert verdict.kind is AutoReviewKind.AUTO_APPROVE and verdict.version_basis is not None
    assert verdict.concerns == () and "trusted-channel-v2" in verdict.reason


@pytest.mark.parametrize("candidate,other", [
    (meta(seconds=180), meta("othr0000001", "Zzzz Qqqq 1234", seconds=182)),
    (meta(), meta("othr0000001", "ALPHA BETA GAMMA", channel=OTHER, seconds=300)),
    (meta(title="Alpha Beta Gamma (Live)"), meta("othr0000001", "Zzzz Qqqq 1234", channel=OTHER, seconds=300)),
    (meta(title="ab"), meta("othr0000001", "Zzzz Qqqq 1234", channel=OTHER, seconds=300))])
def test_any_single_concern_sends_the_case_to_the_owner_and_never_confirms(candidate, other) -> None:
    """@description: Duration, title, keyword and unjudgeable-title concerns each prevent confirmation but still record the duration.
    @param candidate: Candidate triggering one concern.
    @param other: Existing song.
    @return: None.
    """
    verdict = refine(candidate, batch(other))
    assert verdict.kind is AutoReviewKind.DURATION_ONLY and verdict.version_basis is None
    assert verdict.concerns and verdict.duration is not None
    assert "version_concern" in verdict.reason


def test_every_concern_code_is_listed_on_the_verdict() -> None:
    """@description: The Owner card shows all reasons, not only the first.
    @param: none
    @return: None.
    """
    verdict = refine(meta(title="Same Song (Live)", seconds=180), batch(meta("othr0000001", "SAME SONG", seconds=181)))
    assert {concern.code for concern in verdict.concerns} == {
        ConcernCode.SAME_CHANNEL_CLOSE_DURATION, ConcernCode.SIMILAR_TITLE, ConcernCode.VERSION_KEYWORD}


@pytest.mark.parametrize("status", [BatchStatus.QUOTA_EXHAUSTED, BatchStatus.INCOMPLETE])
def test_a_failed_or_exhausted_comparison_retries_later_and_never_approves(status) -> None:
    """@description: Owner decision: API trouble writes nothing and is retried later; it is neither approval nor rejection.
    @param status: Failing batch status.
    @return: None.
    """
    verdict = refine(meta(), batch(status=status), compared=500)
    assert verdict.kind is AutoReviewKind.RETRY and verdict.version_basis is None


def test_a_complete_comparison_may_return_fewer_songs_than_requested() -> None:
    """@description: Deleted or private old videos shrink the found list but do not invalidate a complete comparison.
    @param: none
    @return: None.
    """
    verdict = refine(meta(), batch(meta("othr0000001", "Zzzz Qqqq 1234", channel=OTHER, seconds=300)), compared=500)
    assert verdict.kind is AutoReviewKind.AUTO_APPROVE


@pytest.mark.parametrize("overrides", [{"channel": OTHER}, {"seconds": 59}, {"seconds": 721}])
def test_a_verdict_that_was_not_an_approval_passes_through_unchanged(overrides) -> None:
    """@description: Untrusted channel (Owner review) and out-of-range duration (rejection) are never altered by the comparison.
    @param overrides: Metadata overrides producing a non-approval.
    @return: None.
    """
    candidate = meta(**overrides)
    base = decide_auto_review(MetadataLookup(LookupStatus.FOUND, candidate), frozenset({TRUSTED}))
    assert base.kind is not AutoReviewKind.AUTO_APPROVE
    assert refine_verdict(base, candidate, batch(), 0, ConcernThresholds()) == base


def test_no_verdict_text_contains_a_title() -> None:
    """@description: Reason strings are audit rows; titles are never stored.
    @param: none
    @return: None.
    """
    candidate = meta(title=f"{TITLE_CANARY} live")
    verdict = refine(candidate, batch(meta("othr0000001", f"{TITLE_CANARY} live", seconds=189)))
    assert TITLE_CANARY not in verdict.reason + verdict.public_reason + repr(verdict.concerns)
