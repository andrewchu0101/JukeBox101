"""Verify the official metadata provider and the trusted-channel policy without any network call.

The transport is a scripted fake at the HTTP boundary; retries, rate limits and quota handling are observed through the public lookup result.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.auto_review import AutoReviewKind, decide_auto_review
from bot.eligibility import DurationEligibility, assess_duration
from bot.youtube_metadata import (LookupStatus, MetadataLookup, TransportFailure, VideoMetadata, YouTubeMetadataClient,
                                  parse_iso8601_duration)

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
VIDEO_ID = "cUfDOS2SINM"
TRUSTED = "UC" + "a" * 22
UNTRUSTED = "UC" + "b" * 22


def api_item(**overrides) -> dict:
    """@description: Build one videos.list item shaped like the official response.
    @param overrides: Top-level sections replaced by a test.
    @return: Item dictionary.
    """
    item = {"id": VIDEO_ID, "snippet": {"channelId": TRUSTED, "categoryId": "10", "title": "Song", "liveBroadcastContent": "none"},
            "contentDetails": {"duration": "PT3M9S"}, "status": {"uploadStatus": "processed", "privacyStatus": "public"}}
    item.update(overrides)
    return item


class ScriptedTransport:
    """Return scripted outcomes for each HTTP attempt and count how many attempts reached the network."""

    def __init__(self, *outcomes) -> None:
        """@description: Store the outcomes in call order.
        @param self: Transport.
        @param outcomes: Response dictionaries or exceptions.
        @return: None.
        """
        self.outcomes = list(outcomes)
        self.calls = []

    def __call__(self, video_id: str, api_key: str, timeout_seconds: float) -> dict:
        """@description: Play the next outcome.
        @param self: Transport.
        @param video_id: Requested video.
        @param api_key: Credential passed by the client.
        @param timeout_seconds: Per-attempt timeout.
        @return: Scripted response.
        """
        self.calls.append((video_id, api_key, timeout_seconds))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def client_for(transport, now=lambda: NOW, sleeps=None) -> YouTubeMetadataClient:
    """@description: Build a client whose sleeping is recorded instead of performed.
    @param transport: Scripted transport.
    @param now: Clock.
    @param sleeps: Optional list collecting requested delays.
    @return: Client under test.
    """
    recorded = sleeps if sleeps is not None else []
    return YouTubeMetadataClient("fake-key", transport=transport, clock=now, sleep=recorded.append)


@pytest.mark.parametrize("text,seconds", [("PT3M9S", 189), ("PT1M", 60), ("PT12M", 720), ("PT1H", 3600), ("P1DT1S", 86401), ("PT59S", 59)])
def test_iso8601_durations_become_exact_seconds(text, seconds) -> None:
    assert parse_iso8601_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "P", "PT", "3M9S", "PT1.5S", "PT-1S", "P1W", None, 189, "P0D", "PT0S"])
def test_unusable_durations_are_not_guessed(text) -> None:
    assert parse_iso8601_duration(text) is None


def test_a_found_video_keeps_only_the_fields_the_policy_needs() -> None:
    lookup = client_for(ScriptedTransport({"items": [api_item()]})).lookup(VIDEO_ID)
    assert lookup.status is LookupStatus.FOUND
    assert lookup.metadata.duration_seconds == 189
    assert lookup.metadata.channel_id == TRUSTED
    assert lookup.metadata.fetched_at == NOW


def test_an_empty_result_is_not_found_without_retrying() -> None:
    transport = ScriptedTransport({"items": []})
    assert client_for(transport).lookup(VIDEO_ID).status is LookupStatus.NOT_FOUND
    assert len(transport.calls) == 1


def test_a_response_for_another_video_is_not_trusted() -> None:
    lookup = client_for(ScriptedTransport({"items": [api_item(id="AAAAAAAAAAA")]})).lookup(VIDEO_ID)
    assert lookup.status is LookupStatus.TEMPORARY_FAILURE


def test_a_temporary_failure_retries_a_bounded_number_of_times_with_backoff() -> None:
    sleeps = []
    transport = ScriptedTransport(TransportFailure(503, ""), TransportFailure(429, ""), TransportFailure(None, ""))
    assert client_for(transport, sleeps=sleeps).lookup(VIDEO_ID).status is LookupStatus.TEMPORARY_FAILURE
    assert len(transport.calls) == 3
    assert 0.5 in sleeps and 1.0 in sleeps  # [doubling backoff]: Rate-limit delays are interleaved.


def test_a_retry_that_succeeds_returns_the_metadata() -> None:
    transport = ScriptedTransport(TransportFailure(500, ""), {"items": [api_item()]})
    assert client_for(transport).lookup(VIDEO_ID).status is LookupStatus.FOUND


def test_quota_exhaustion_stops_retrying_and_cools_down_without_calling_the_network() -> None:
    moments = [NOW]
    transport = ScriptedTransport(TransportFailure(403, "quotaExceeded"), {"items": [api_item()]})
    client = client_for(transport, now=lambda: moments[0])
    assert client.lookup(VIDEO_ID).status is LookupStatus.QUOTA_EXHAUSTED
    assert client.lookup(VIDEO_ID).status is LookupStatus.QUOTA_EXHAUSTED
    assert len(transport.calls) == 1
    moments[0] = NOW + timedelta(hours=2)
    assert client.lookup(VIDEO_ID).status is LookupStatus.FOUND


def test_a_refused_credential_is_a_temporary_failure_and_never_retried() -> None:
    transport = ScriptedTransport(TransportFailure(400, "keyInvalid"))
    assert client_for(transport).lookup(VIDEO_ID).status is LookupStatus.TEMPORARY_FAILURE
    assert len(transport.calls) == 1


def test_consecutive_lookups_are_spaced_by_the_rate_limit() -> None:
    sleeps = []
    client = client_for(ScriptedTransport({"items": [api_item()]}, {"items": [api_item()]}), sleeps=sleeps)
    client.lookup(VIDEO_ID)
    client.lookup(VIDEO_ID)
    assert sleeps and sleeps[0] > 0


def found(**fields) -> MetadataLookup:
    """@description: Build a found lookup with overridable metadata.
    @param fields: Metadata fields replaced by a test.
    @return: Lookup result.
    """
    values = {"video_id": VIDEO_ID, "channel_id": TRUSTED, "category_id": "10", "title": "Song", "live_broadcast_content": "none",
              "has_live_details": False, "upload_status": "processed", "privacy_status": "public", "duration_seconds": 189, "fetched_at": NOW}
    values.update(fields)
    return MetadataLookup(LookupStatus.FOUND, VideoMetadata(**values))


@pytest.mark.parametrize("seconds", [60, 189, 720])
def test_a_trusted_channel_music_video_within_limits_is_auto_approved(seconds) -> None:
    verdict = decide_auto_review(found(duration_seconds=seconds), frozenset({TRUSTED}))
    assert verdict.kind is AutoReviewKind.AUTO_APPROVE
    assert assess_duration(verdict.duration) is DurationEligibility.PASS
    assert verdict.duration.seconds == seconds
    assert TRUSTED in verdict.reason and "2026-10-31" in verdict.reason


@pytest.mark.parametrize("seconds", [59, 721])
def test_an_official_out_of_range_duration_rejects_even_on_a_trusted_channel(seconds) -> None:
    verdict = decide_auto_review(found(duration_seconds=seconds), frozenset({TRUSTED}))
    assert verdict.kind is AutoReviewKind.DURATION_REJECT
    assert assess_duration(verdict.duration) is DurationEligibility.REJECT


@pytest.mark.parametrize("fields", [{"channel_id": UNTRUSTED}, {"category_id": "24"}, {"title": "New Song Official Trailer"},
                                    {"title": "新歌預告"}, {"title": "副歌片段 Clip"}, {"privacy_status": "unlisted"}, {"has_live_details": True}])
def test_anything_short_of_every_trust_condition_records_duration_and_waits_for_the_owner(fields) -> None:
    verdict = decide_auto_review(found(**fields), frozenset({TRUSTED}))
    assert verdict.kind is AutoReviewKind.DURATION_ONLY
    assert verdict.duration.seconds == 189


def test_an_empty_allowlist_never_auto_approves() -> None:
    assert decide_auto_review(found(), frozenset()).kind is AutoReviewKind.DURATION_ONLY


@pytest.mark.parametrize("fields", [{"live_broadcast_content": "live"}, {"live_broadcast_content": "upcoming"}, {"duration_seconds": None},
                                    {"upload_status": "uploaded"}])
def test_live_upcoming_or_undetermined_videos_are_held_without_any_evidence(fields) -> None:
    verdict = decide_auto_review(found(**fields), frozenset({TRUSTED}))
    assert verdict.kind is AutoReviewKind.HOLD
    assert verdict.duration is None


@pytest.mark.parametrize("lookup", [MetadataLookup(LookupStatus.NOT_FOUND, None), found(privacy_status="private"),
                                    found(upload_status="deleted"), found(upload_status="rejected")])
def test_a_missing_or_private_video_is_a_retryable_validation_failure_not_a_rejection(lookup) -> None:
    assert decide_auto_review(lookup, frozenset({TRUSTED})).kind is AutoReviewKind.UNAVAILABLE


@pytest.mark.parametrize("status", [LookupStatus.TEMPORARY_FAILURE, LookupStatus.QUOTA_EXHAUSTED])
def test_api_failures_write_nothing_and_stay_retryable(status) -> None:
    assert decide_auto_review(MetadataLookup(status, None), frozenset({TRUSTED})).kind is AutoReviewKind.RETRY
