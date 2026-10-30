"""Verify Discord HTTP interaction signatures and call Discord REST for direct messages and private replies.

This is the only module that sends bot-authorized or interaction-token requests over HTTP, so tokens stay here and never reach logs or exception text.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any

import aiohttp
from nacl.exceptions import CryptoError
from nacl.signing import VerifyKey

from bot.exchange_delivery import DmResult

LOGGER = logging.getLogger(__name__)
DISCORD_API_BASE_URL = "https://discord.com/api/v10"  # [API version]: Pinned here; confirm against Discord's current version before deployment.
MAX_REST_ATTEMPTS = 3  # [429 budget]: Total attempts per REST call, including the first; only HTTP 429 is retried.
MAX_RETRY_WAIT_SECONDS = 5.0  # [429 budget]: A Retry-After longer than this fails at once instead of being shortened.
REST_RETRY_BUDGET_SECONDS = 15.0  # [overall deadline]: Covers every attempt and wait of one REST call; below DM_TIMEOUT_SECONDS so retries finish before the delivery timeout.
REST_TIMEOUT_SECONDS = 10.0  # [bounded request]: Shorter than the 20-second DM attempt so a stalled call returns before the delivery timeout.
SIGNATURE_HEADER = "X-Signature-Ed25519"
TIMESTAMP_HEADER = "X-Signature-Timestamp"
NO_MENTIONS = {"parse": []}  # [mentions]: Replies and DMs never ping anyone.
TRANSPORT_FAILURE = "transport"  # [sanitized reason]: Used for network errors and timeouts, which carry URLs with tokens.


class _RateLimited(Exception):
    """Carry a 429 from one attempt to the retry loop; the advised wait is None when Discord's advice is missing or unreadable."""

    def __init__(self, wait_seconds: float | None) -> None:
        """@description: Keep the advised wait so the retry loop decides whether the budget allows another attempt.
        @param self: Signal instance.
        @param wait_seconds: Non-negative seconds Discord advised, or None when it could not be read.
        @return: None.
        """
        super().__init__("rate limited")
        self.wait_seconds = wait_seconds  # [advised wait]: Honoured in full or not retried at all.


class DiscordRestError(Exception):
    """Report a failed Discord REST call by a sanitized reason only; URLs contain interaction tokens and are never kept."""

    def __init__(self, reason: str) -> None:
        """@description: Keep only the classified reason so logs and results never carry the request URL.
        @param self: Error instance.
        @param reason: Sanitized category such as discord_403 or transport.
        @return: None.
        """
        super().__init__(reason)
        self.reason = reason  # [sanitized reason]: Safe to log and to store as an uncertain-outcome reason.


def verify_interaction_signature(verify_key: VerifyKey, headers: Mapping[str, str], raw_body: bytes) -> bool:
    """@description: Accept an interaction only when Discord's Ed25519 signature covers its timestamp and the exact raw body.
    @param verify_key: Application public key parsed once at startup.
    @param headers: Incoming request headers.
    @param raw_body: Unparsed request body, the exact bytes Discord signed.
    @return: True only for a valid signature; missing or malformed headers count as failed verification.
    """
    signature_hex = headers.get(SIGNATURE_HEADER)
    timestamp = headers.get(TIMESTAMP_HEADER)
    if signature_hex is None or timestamp is None:
        return False
    try:
        verify_key.verify(timestamp.encode() + raw_body, bytes.fromhex(signature_hex))
    except (CryptoError, ValueError):
        return False
    return True


class DiscordRestClient:
    """Call Discord's HTTP API: bot-authorized routes for direct messages, interaction-token routes for private replies."""

    def __init__(self, session: aiohttp.ClientSession, bot_token: str, sleep=None, clock=None) -> None:
        """@description: Keep one HTTP session and the bot credential used only for bot-authorized routes.
        @param self: Client instance.
        @param session: Shared aiohttp session owned by the application lifecycle.
        @param bot_token: Bot credential; never logged or placed in exception text.
        @param sleep: Awaitable wait used between 429 attempts; None uses asyncio.sleep.
        @param clock: Monotonic time source for the overall REST budget; None uses time.monotonic.
        @return: None.
        """
        self._session = session
        self._sleep = sleep or asyncio.sleep  # [injectable wait]: Tests replace it; production waits on the event loop.
        self._clock = clock or time.monotonic  # [injectable time]: Measures the overall REST budget; tests replace it.
        self._bot_headers = {"Authorization": f"Bot {bot_token}"}  # [bot credential]: Sent only on bot-authorized routes.

    async def create_dm_channel(self, discord_user_id: int) -> str:
        """@description: Open or fetch the bot's direct-message channel with one recipient; no message is sent here.
        @param self: Client instance.
        @param discord_user_id: Recipient identifier.
        @return: Channel identifier as Discord returns it.
        """
        # [retry allowed]: Get-or-create; a 429 is repeated and the call sends no message, so a repeat cannot duplicate anything.
        body = await self._send("POST", "/users/@me/channels", {"recipient_id": str(discord_user_id)}, self._bot_headers,
                                retry_on_rate_limit=True)
        return str(body["id"])

    async def create_message(self, channel_id: str, text: str) -> int:
        """@description: Post one message to a channel and return the identifier Discord accepted.
        @param self: Client instance.
        @param channel_id: Direct-message channel identifier.
        @param text: Message body without mentions.
        @return: Accepted message identifier.
        """
        payload = {"content": text, "allowed_mentions": NO_MENTIONS}
        # [retry refused]: Each accepted POST posts a message, so a 429 is reported and never repeated by this client.
        body = await self._send("POST", f"/channels/{channel_id}/messages", payload, self._bot_headers,
                                retry_on_rate_limit=False)
        return int(body["id"])

    async def edit_original_response(self, application_id: int, interaction_token: str, text: str) -> None:
        """@description: Replace the deferred private response of one interaction using its interaction token.
        @param self: Client instance.
        @param application_id: Application that owns the interaction.
        @param interaction_token: Token from the interaction payload; valid for 15 minutes and never logged.
        @param text: Reply body without mentions.
        @return: None.
        """
        path = f"/webhooks/{application_id}/{interaction_token}/messages/@original"
        # [retry allowed]: Replacing the original response with the same content is idempotent.
        await self._send("PATCH", path, {"content": text, "allowed_mentions": NO_MENTIONS}, {}, retry_on_rate_limit=True)

    async def _send(self, method: str, path: str, payload: dict, headers: Mapping[str, str], *, retry_on_rate_limit: bool) -> Any:
        """@description: Perform one REST call within one overall budget, repeating an advised 429 only when the endpoint declares it safe; every failure becomes a sanitized DiscordRestError.
        @param self: Client instance.
        @param method: HTTP method.
        @param path: Path below the Discord API base.
        @param payload: JSON body.
        @param headers: Extra headers for this route.
        @param retry_on_rate_limit: Explicit per-endpoint declaration; False means a 429 is reported at once.
        @return: Decoded JSON response body.
        """
        url = DISCORD_API_BASE_URL + path
        started_at = self._clock()  # [overall deadline]: Attempts and waits are all charged against REST_RETRY_BUDGET_SECONDS.
        for attempt in range(1, MAX_REST_ATTEMPTS + 1):
            remaining_seconds = REST_RETRY_BUDGET_SECONDS - (self._clock() - started_at)
            if remaining_seconds <= 0:
                raise DiscordRestError("discord_429")  # [budget spent]: Never send with a zero timeout, which aiohttp reads as no timeout.
            try:
                return await self._attempt(method, url, payload, headers, min(REST_TIMEOUT_SECONDS, remaining_seconds))
            except _RateLimited as limited:
                if not retry_on_rate_limit:
                    raise DiscordRestError("discord_429") from None  # [endpoint refuses repeat]: The 429 is reported, never repeated.
                wait_seconds = limited.wait_seconds
            spent_seconds = self._clock() - started_at
            if wait_seconds is None or attempt == MAX_REST_ATTEMPTS or wait_seconds > MAX_RETRY_WAIT_SECONDS:
                raise DiscordRestError("discord_429")  # [budget spent]: Stop instead of shortening the advised wait.
            if spent_seconds + wait_seconds >= REST_RETRY_BUDGET_SECONDS:
                raise DiscordRestError("discord_429")  # [overall deadline]: Fail now rather than sleep past the budget.
            await self._sleep(wait_seconds)  # [advised wait]: Discord's Retry-After is honoured in full before the next attempt.
        raise DiscordRestError("discord_429")  # [unreachable guard]: The loop always returns or raises above.

    async def _attempt(self, method: str, url: str, payload: dict, headers: Mapping[str, str], timeout_seconds: float) -> Any:
        """@description: Make one HTTP request, returning its JSON body or signalling a 429 with the wait Discord advised.
        @param self: Client instance.
        @param method: HTTP method.
        @param url: Full request URL; never placed in exception text.
        @param payload: JSON body.
        @param headers: Extra headers for this route.
        @param timeout_seconds: Upper bound for this single request, capped by the remaining overall budget.
        @return: Decoded JSON response body.
        """
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        try:
            async with self._session.request(method, url, json=payload, headers=dict(headers), timeout=timeout) as response:
                if response.status == 429:
                    raise _RateLimited(await _retry_after_seconds(response))
                if response.status >= 400:
                    raise DiscordRestError(f"discord_{response.status}")
                return await response.json()
        except (aiohttp.ClientError, TimeoutError):
            raise DiscordRestError(TRANSPORT_FAILURE) from None  # [no chaining]: The original message embeds the URL with the token.


async def _retry_after_seconds(response) -> float | None:
    """@description: Read the wait Discord advised for a 429, from the header first and then the JSON body; never guess.
    @param response: Response object with optional headers and an optional JSON body.
    @return: Non-negative seconds to wait, or None when the advice is missing or unreadable.
    """
    headers = getattr(response, "headers", None) or {}
    raw = headers.get("Retry-After")
    if raw is None:
        try:
            body = await response.json()
        except (aiohttp.ClientError, ValueError):
            return None
        raw = body.get("retry_after") if isinstance(body, dict) else None
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


class RestDirectMessageSender:
    """Send one direct message through Discord REST and classify the outcome by what the attempt proves."""

    def __init__(self, rest: DiscordRestClient) -> None:
        """@description: Keep the REST client used for recipient lookup and message creation.
        @param self: Adapter instance.
        @param rest: Shared REST client.
        @return: None.
        """
        self._rest = rest

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Open the DM channel, post one message, and never resend; a failure before posting proves nothing was sent.
        @param self: Adapter instance.
        @param discord_user_id: Reserved requester.
        @param text: Message body carrying the verified link.
        @return: Delivered, not-sent or unknown result.
        """
        try:
            channel_id = await self._rest.create_dm_channel(discord_user_id)
        except DiscordRestError as error:
            LOGGER.warning("Recipient lookup failed: %s", error.reason)
            return DmResult.not_sent("recipient_lookup_failed")
        try:
            message_id = await self._rest.create_message(channel_id, text)
        except DiscordRestError as error:
            LOGGER.warning("Direct message outcome unknown: %s", error.reason)  # [last response only]: Earlier attempts may have been accepted.
            return DmResult.unknown(error.reason)
        return DmResult.delivered(message_id)


class RestEphemeralReplier:
    """Send a requester-only reply by editing the deferred response of one interaction."""

    def __init__(self, rest: DiscordRestClient, application_id: int, interaction_token: str) -> None:
        """@description: Keep the REST client and the interaction whose deferred response carries the reply.
        @param self: Adapter instance.
        @param rest: Shared REST client.
        @param application_id: Application that owns the interaction.
        @param interaction_token: Interaction token; kept in memory only for this request.
        @return: None.
        """
        self._rest = rest
        self._application_id = application_id
        self._interaction_token = interaction_token

    async def send_ephemeral(self, text: str) -> None:
        """@description: Replace the deferred response with one link-free private reply.
        @param self: Adapter instance.
        @param text: Link-free reply text.
        @return: None.
        """
        await self._rest.edit_original_response(self._application_id, self._interaction_token, text)
