"""Fetch the minimum video metadata from the official YouTube Data API v3 videos.list endpoint.

Inputs are a video ID, an API key and an injectable transport; the output is a typed lookup that never turns an API failure into a decision.
No page is scraped, no audio is downloaded, and calls run on worker threads outside every database transaction.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

VIDEOS_ENDPOINT = "https://www.googleapis.com/youtube/v3/videos"  # [official source]: The only host this module contacts.
REQUEST_PARTS = "snippet,contentDetails,status,liveStreamingDetails"  # [minimum parts]: Costs one quota unit per call.
RESPONSE_FIELDS = ("items(id,snippet(channelId,categoryId,title,liveBroadcastContent),contentDetails(duration),"
                   "status(uploadStatus,privacyStatus),liveStreamingDetails(scheduledStartTime))")  # [minimum fields]: Nothing else is requested.
REQUEST_TIMEOUT_SECONDS = 5.0  # [attempt bound]: One blocked call never holds a worker for long.
MAX_ATTEMPTS = 3  # [retry bound]: Temporary failures are attempted at most this many times per lookup.
RETRY_BASE_SECONDS = 0.5  # [backoff base]: Doubled after each failed attempt.
MIN_REQUEST_INTERVAL_SECONDS = 0.2  # [local rate limit]: At most five calls per second from this process.
QUOTA_COOLDOWN = timedelta(hours=1)  # [quota pause]: Daily quota resets at midnight Pacific; hourly probes cost one unit each.
MAX_RESPONSE_BYTES = 1_000_000  # [response bound]: A one-video response is far smaller than this.
SOURCE_LABEL = "youtube-data-api-v3:videos.list"  # [provenance]: Stored with the retrieval time as duration evidence.
QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})  # [quota family]: Retrying before the cooldown only burns more quota.
RETRYABLE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})  # [rate family]: Safe to retry after backoff.
ISO8601_DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", re.ASCII)  # [duration grammar]: Whole units only.
SECONDS_PER_DAY, SECONDS_PER_HOUR, SECONDS_PER_MINUTE = 86400, 3600, 60


class LookupStatus(Enum):
    """Separate a real answer from failures that say nothing about the video."""

    FOUND = "found"
    NOT_FOUND = "not_found"
    TEMPORARY_FAILURE = "temporary_failure"
    QUOTA_EXHAUSTED = "quota_exhausted"


class TransportFailure(RuntimeError):
    """Report one failed HTTP attempt by status and API reason, never by URL, header or key."""

    def __init__(self, http_status: int | None, api_reason: str) -> None:
        """@description: Keep only the facts retry handling needs.
        @param self: New failure.
        @param http_status: HTTP status, or None for a network failure or timeout.
        @param api_reason: First error reason in the API response, or an empty string.
        @return: None.
        """
        super().__init__(f"YouTube API attempt failed: {http_status} {api_reason}")
        self.http_status = http_status  # [retry input]: None means the request never completed.
        self.api_reason = api_reason  # [retry input]: Official error reason text.


@dataclass(frozen=True)
class VideoMetadata:
    """Carry the official facts the trust policy reads; only channel, duration and time are ever persisted."""

    video_id: str  # [subject]: Verified equal to the requested video.
    channel_id: str | None  # [stable channel]: Compared to the allowlist; never a display name.
    category_id: str | None  # [music signal]: YouTube category of the upload.
    title: str | None  # [negative signal only]: Read in memory to withhold trust; never stored and never proves a version.
    live_broadcast_content: str | None  # [live state]: none, live or upcoming.
    has_live_details: bool  # [broadcast history]: True for upcoming, live or completed broadcasts.
    upload_status: str | None  # [processing state]: Only processed has a final duration.
    privacy_status: str | None  # [visibility]: Only public qualifies for automatic approval.
    duration_seconds: int | None  # [full-video length]: None when absent, malformed or zero.
    fetched_at: datetime  # [retrieval time]: Timezone-aware instant the response was parsed.


@dataclass(frozen=True)
class MetadataLookup:
    """Return one lookup outcome; metadata exists only when the status is FOUND."""

    status: LookupStatus  # [outcome]: Drives the policy decision.
    metadata: VideoMetadata | None  # [facts]: None for every non-FOUND outcome.


def parse_iso8601_duration(text: object) -> int | None:
    """@description: Convert the official duration to exact seconds without rounding, refusing anything the grammar does not cover.
    @param text: contentDetails.duration value from the API.
    @return: Positive whole seconds, or None when absent, malformed or zero, as for a broadcast with no final length.
    """
    if not isinstance(text, str) or text in {"P", ""} or text.endswith("T"):
        return None
    match = ISO8601_DURATION.fullmatch(text)
    if match is None:
        return None
    days, hours, minutes, seconds = (int(group or 0) for group in match.groups())
    total = days * SECONDS_PER_DAY + hours * SECONDS_PER_HOUR + minutes * SECONDS_PER_MINUTE + seconds
    return total if total > 0 else None


def http_transport(video_id: str, api_key: str, timeout_seconds: float) -> dict:
    """@description: Perform one videos.list request, sending the key in a header so it never appears in a URL or an error.
    @param video_id: Parser-validated video identifier.
    @param api_key: API key, never logged.
    @param timeout_seconds: Connect and read timeout.
    @return: Decoded JSON response.
    """
    query = urllib.parse.urlencode({"part": REQUEST_PARTS, "id": video_id, "fields": RESPONSE_FIELDS})
    request = urllib.request.Request(f"{VIDEOS_ENDPOINT}?{query}", headers={"X-Goog-Api-Key": api_key, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read(MAX_RESPONSE_BYTES))
    except urllib.error.HTTPError as error:
        raise TransportFailure(error.code, _error_reason(error)) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise TransportFailure(None, "") from None


def _error_reason(error: urllib.error.HTTPError) -> str:
    """@description: Read the first official error reason from a failed response body.
    @param error: HTTP error holding the response body.
    @return: Reason text, or an empty string when the body is not the documented shape.
    """
    try:
        return json.loads(error.read(MAX_RESPONSE_BYTES))["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError, OSError):
        return ""


class YouTubeMetadataClient:
    """Own timeout, bounded retry, local rate limit and quota cooldown around one official endpoint."""

    def __init__(self, api_key: str, *, transport: Callable[[str, str, float], dict] = http_transport,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 sleep: Callable[[float], None] = time.sleep) -> None:
        """@description: Bind the credential and injectable boundaries without any network activity.
        @param self: New client.
        @param api_key: API key held only in memory.
        @param transport: Performs one HTTP attempt.
        @param clock: Timezone-aware current-instant provider.
        @param sleep: Blocking delay; called only on worker threads.
        @return: None.
        """
        self._api_key = api_key  # [credential]: Never placed in a message or representation.
        self._transport = transport  # [HTTP boundary]: Faked in tests.
        self._clock = clock  # [time source]: Stamps evidence and the quota cooldown.
        self._sleep = sleep  # [delay]: Backoff and rate limit.
        self._lock = threading.Lock()  # [rate and cooldown state]: Workers share one client.
        self._next_request_at = 0.0  # [rate limit]: Monotonic instant the next call may start.
        self._quota_blocked_until: datetime | None = None  # [cooldown]: No network call before this instant.

    def __repr__(self) -> str:
        """@description: Keep the credential out of every representation.
        @param self: Client.
        @return: Fixed text.
        """
        return "YouTubeMetadataClient()"

    def lookup(self, video_id: str) -> MetadataLookup:
        """@description: Ask the official API about one video, classifying every failure as something other than a judgement about the video.
        @param self: Client.
        @param video_id: Parser-validated video identifier.
        @return: Found metadata, not found, a temporary failure or exhausted quota.
        """
        if self._cooling_down():
            return MetadataLookup(LookupStatus.QUOTA_EXHAUSTED, None)
        for attempt in range(MAX_ATTEMPTS):
            self._respect_rate_limit()
            try:
                return self._interpret(video_id, self._transport(video_id, self._api_key, REQUEST_TIMEOUT_SECONDS))
            except TransportFailure as failure:
                terminal = self._terminal_outcome(failure)
            if terminal is not None:
                return terminal
            if attempt + 1 < MAX_ATTEMPTS:
                self._sleep(RETRY_BASE_SECONDS * 2 ** attempt)
        return MetadataLookup(LookupStatus.TEMPORARY_FAILURE, None)

    def _cooling_down(self) -> bool:
        """@description: Tell whether the quota cooldown still forbids network calls.
        @param self: Client.
        @return: True before the cooldown ends.
        """
        with self._lock:
            return self._quota_blocked_until is not None and self._clock() < self._quota_blocked_until

    def _respect_rate_limit(self) -> None:
        """@description: Space consecutive calls from all workers, sleeping outside the state lock.
        @param self: Client.
        @return: None.
        """
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + MIN_REQUEST_INTERVAL_SECONDS
        if delay > 0:
            self._sleep(delay)

    def _terminal_outcome(self, failure: TransportFailure) -> MetadataLookup | None:
        """@description: Stop retrying when another attempt cannot help, starting the cooldown for quota exhaustion.
        @param self: Client.
        @param failure: Failed HTTP attempt.
        @return: Final lookup, or None when the attempt may be retried.
        """
        if failure.api_reason in QUOTA_REASONS:
            with self._lock:
                self._quota_blocked_until = self._clock() + QUOTA_COOLDOWN
            return MetadataLookup(LookupStatus.QUOTA_EXHAUSTED, None)
        retryable = failure.http_status is None or failure.http_status == 429 or failure.http_status >= 500
        if retryable or failure.api_reason in RETRYABLE_REASONS:
            return None
        return MetadataLookup(LookupStatus.TEMPORARY_FAILURE, None)

    def _interpret(self, video_id: str, response: object) -> MetadataLookup:
        """@description: Accept only a response about the requested video; an unexpected shape is a failure, not a fact.
        @param self: Client.
        @param video_id: Requested video.
        @param response: Decoded JSON response.
        @return: Lookup outcome.
        """
        items = response.get("items") if isinstance(response, dict) else None
        if not isinstance(items, list) or len(items) > 1:
            return MetadataLookup(LookupStatus.TEMPORARY_FAILURE, None)
        if not items:
            return MetadataLookup(LookupStatus.NOT_FOUND, None)
        if not isinstance(items[0], dict) or items[0].get("id") != video_id:
            return MetadataLookup(LookupStatus.TEMPORARY_FAILURE, None)
        return MetadataLookup(LookupStatus.FOUND, self._metadata(video_id, items[0]))

    def _metadata(self, video_id: str, item: dict) -> VideoMetadata:
        """@description: Copy the documented fields, leaving anything missing as None so the policy can hold it.
        @param self: Client.
        @param video_id: Verified video identifier.
        @param item: One videos.list item.
        @return: Metadata stamped with the retrieval time.
        """
        snippet, details, status = (_section(item, name) for name in ("snippet", "contentDetails", "status"))
        return VideoMetadata(video_id, snippet.get("channelId"), snippet.get("categoryId"), snippet.get("title"),
                             snippet.get("liveBroadcastContent"), "liveStreamingDetails" in item, status.get("uploadStatus"),
                             status.get("privacyStatus"), parse_iso8601_duration(details.get("duration")), self._clock())


def _section(item: dict, name: str) -> dict:
    """@description: Read one response section, treating a missing or malformed one as empty.
    @param item: One videos.list item.
    @param name: Section name.
    @return: Section dictionary.
    """
    section = item.get(name)
    return section if isinstance(section, dict) else {}
