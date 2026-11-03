"""Verify the automatic decision in music_category mode: no allowlist is required, every other protection stays.

The decision function gets an optional third argument require_trusted_channel (default True = allowlist mode), so every existing call and test is unchanged.
Verdicts are built from synthetic official metadata; nothing here touches the network or a database.
"""

import re

import pytest

from bot.auto_review import AutoReviewKind, decide_auto_review
from bot.eligibility import DurationEligibility, assess_duration
from bot.youtube_metadata import LookupStatus, MetadataLookup
from tests.test_youtube_metadata import TRUSTED, UNTRUSTED, found



def decide_open(lookup, trusted=frozenset()):
    """@description: Decide in music_category mode with an empty allowlist unless one is given.
    @param lookup: Official metadata outcome.
    @param trusted: Allowlist, empty by default.
    @return: Verdict.
    """
    return decide_auto_review(lookup, trusted, require_trusted_channel=False)


def test_an_untrusted_channel_with_music_category_and_a_sensible_duration_is_approved_with_an_empty_allowlist() -> None:
    """@description: The headline rule: the channel is no longer a condition.
    @param: none
    @return: None.
    """
    verdict = decide_open(found(channel_id=UNTRUSTED))
    assert verdict.kind is AutoReviewKind.AUTO_APPROVE
    assert assess_duration(verdict.duration) is DurationEligibility.PASS and verdict.duration.seconds == 189


def test_the_approval_is_audited_under_its_own_reason_code_not_as_a_trusted_channel() -> None:
    """@description: A later reader must be able to tell a category approval from an allowlist approval.
    @param: none
    @return: None.
    """
    verdict = decide_open(found(channel_id=UNTRUSTED))
    assert re.search(r"reason=music_category(\s|$)", verdict.reason) and "trusted_channel_music" not in verdict.reason
    assert re.search(r"reason=music_category(\s|$)", verdict.public_reason)


def test_an_allowlisted_channel_is_approved_in_both_modes() -> None:
    """@description: The allowlist is simply not required; it never hurts.
    @param: none
    @return: None.
    """
    assert decide_open(found(channel_id=TRUSTED), frozenset({TRUSTED})).kind is AutoReviewKind.AUTO_APPROVE


@pytest.mark.parametrize("category", ["1", "20", "22", "24", "25", "27", "28", None, ""])
def test_a_category_that_is_not_music_goes_to_the_owner_with_category_not_music(category) -> None:
    """@description: The uploader-set category is the one remaining content gate, and a miss never approves.
    @param category: Any category other than 10.
    @return: None.
    """
    verdict = decide_open(found(channel_id=UNTRUSTED, category_id=category))
    assert verdict.kind is AutoReviewKind.DURATION_ONLY and "reason=category_not_music" in verdict.reason


@pytest.mark.parametrize("seconds,kind", [(59, AutoReviewKind.DURATION_REJECT), (60, AutoReviewKind.AUTO_APPROVE),
                                          (720, AutoReviewKind.AUTO_APPROVE), (721, AutoReviewKind.DURATION_REJECT)])
def test_the_duration_limits_are_unchanged_and_inclusive(seconds, kind) -> None:
    """@description: 60 to 720 seconds, inclusive, exactly as before.
    @param seconds: Official duration.
    @param kind: Expected verdict.
    @return: None.
    """
    assert decide_open(found(channel_id=UNTRUSTED, duration_seconds=seconds)).kind is kind


@pytest.mark.parametrize("fields,reason", [({"privacy_status": "unlisted"}, "video_not_public"),
                                           ({"title": "Official Trailer"}, "title_needs_owner"), ({"title": ""}, "title_needs_owner"),
                                           ({"title": None}, "title_needs_owner")])
def test_the_other_trust_conditions_still_send_the_case_to_the_owner(fields, reason) -> None:
    """@description: Only the channel condition is dropped; visibility and title hints still withhold trust.
    @param fields: Metadata override.
    @param reason: Reason code that must appear.
    @return: None.
    """
    verdict = decide_open(found(channel_id=UNTRUSTED, **fields))
    assert verdict.kind is AutoReviewKind.DURATION_ONLY and f"reason={reason}" in verdict.reason


@pytest.mark.parametrize("fields,kind", [({"live_broadcast_content": "live"}, AutoReviewKind.HOLD), ({"live_broadcast_content": "upcoming"}, AutoReviewKind.HOLD),
                                         ({"upload_status": "uploaded"}, AutoReviewKind.HOLD), ({"duration_seconds": None}, AutoReviewKind.HOLD),
                                         ({"privacy_status": "private"}, AutoReviewKind.UNAVAILABLE), ({"upload_status": "deleted"}, AutoReviewKind.UNAVAILABLE)])
def test_live_unprocessed_private_and_deleted_videos_are_still_excluded(fields, kind) -> None:
    """@description: Live, still processing, no duration, private or removed videos never reach approval in any mode.
    @param fields: Metadata override.
    @param kind: Expected effect.
    @return: None.
    """
    assert decide_open(found(channel_id=UNTRUSTED, **fields)).kind is kind


@pytest.mark.parametrize("status,kind", [(LookupStatus.NOT_FOUND, AutoReviewKind.UNAVAILABLE), (LookupStatus.TEMPORARY_FAILURE, AutoReviewKind.RETRY),
                                         (LookupStatus.QUOTA_EXHAUSTED, AutoReviewKind.RETRY)])
def test_lookup_failures_keep_their_current_meaning(status, kind) -> None:
    """@description: Not found, API trouble and exhausted quota behave exactly as in allowlist mode.
    @param status: Lookup status.
    @param kind: Expected effect.
    @return: None.
    """
    assert decide_open(MetadataLookup(status, None)).kind is kind


def test_a_missing_channel_id_never_auto_approves_even_without_an_allowlist() -> None:
    """@description: Malformed official data fails closed; the version comparison also relies on the channel.
    @param: none
    @return: None.
    """
    verdict = decide_open(found(channel_id=None))
    assert verdict.kind is AutoReviewKind.DURATION_ONLY and "reason=channel_unknown" in verdict.reason


def test_allowlist_mode_is_unchanged_an_untrusted_channel_still_waits_for_the_owner() -> None:
    """@description: Omitting the argument or passing True gives today's result.
    @param: none
    @return: None.
    """
    lookup = found(channel_id=UNTRUSTED)
    for verdict in (decide_auto_review(lookup, frozenset({TRUSTED})), decide_auto_review(lookup, frozenset({TRUSTED}), require_trusted_channel=True),
                    decide_auto_review(lookup, frozenset(), True)):
        assert verdict.kind is AutoReviewKind.DURATION_ONLY and "reason=channel_not_trusted" in verdict.reason


def test_allowlist_mode_with_an_empty_list_never_approves_anything() -> None:
    """@description: The safety rule of the current mode stays exactly as it is.
    @param: none
    @return: None.
    """
    assert decide_auto_review(found(channel_id=TRUSTED), frozenset(), True).kind is AutoReviewKind.DURATION_ONLY


@pytest.mark.parametrize("require_trusted_channel,trusted", [(False, frozenset()), (True, frozenset({TRUSTED}))])
def test_a_music_video_that_was_premiered_or_streamed_before_is_approved_without_the_owner(require_trusted_channel, trusted) -> None:
    """@description: Owner ruling 2026-11-03: a past premiere or finished broadcast no longer needs a person, because live and upcoming videos are still held and the 60-720 second limit already excludes long recordings.
    @param require_trusted_channel: Allowlist or open mode.
    @param trusted: Allowlist for the mode.
    @return: None.
    """
    channel = TRUSTED if require_trusted_channel else UNTRUSTED
    verdict = decide_auto_review(found(channel_id=channel, has_live_details=True), trusted, require_trusted_channel)
    assert verdict.kind is AutoReviewKind.AUTO_APPROVE and "broadcast_history" not in verdict.reason


@pytest.mark.parametrize("fields", [{"live_broadcast_content": "live"}, {"live_broadcast_content": "upcoming"}, {"has_live_details": True, "duration_seconds": 721}])
def test_a_broadcast_that_is_still_live_or_too_long_is_still_not_auto_approved(fields) -> None:
    """@description: The relaxation covers finished premieres only; live, upcoming and over-long videos keep their existing treatment.
    @param fields: Metadata override.
    @return: None.
    """
    assert decide_open(found(channel_id=UNTRUSTED, **fields)).kind is not AutoReviewKind.AUTO_APPROVE
