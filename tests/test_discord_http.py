"""Offline tests for Ed25519 interaction verification and REST-based direct-message classification.

Discord and the HTTP session are replaced by fakes; no network, token or database is touched.
"""

import asyncio
import logging

import aiohttp
import pytest
from nacl.signing import SigningKey

from bot.discord_http import (DiscordRestClient, DiscordRestError, RestDirectMessageSender, TRANSPORT_FAILURE,
                              verify_interaction_signature)
from bot.exchange_delivery import DmKind

BOT_TOKEN = "unit-test-bot-token-value"


def signed_headers(signing_key: SigningKey, timestamp: str, body: bytes) -> dict:
    """@description: Sign timestamp plus body exactly as Discord does, so tests exercise the real verification path.
    @param signing_key: Test-only Ed25519 key.
    @param timestamp: Timestamp header value.
    @param body: Raw request body.
    @return: Headers carrying the signature and timestamp.
    """
    signature = signing_key.sign(timestamp.encode() + body).signature
    return {"X-Signature-Ed25519": signature.hex(), "X-Signature-Timestamp": timestamp}


def test_valid_signature_is_accepted() -> None:
    signing_key = SigningKey.generate()
    body = b'{"type":1}'
    headers = signed_headers(signing_key, "1700000000", body)
    assert verify_interaction_signature(signing_key.verify_key, headers, body) is True


def test_tampered_body_is_rejected() -> None:
    signing_key = SigningKey.generate()
    headers = signed_headers(signing_key, "1700000000", b'{"type":1}')
    assert verify_interaction_signature(signing_key.verify_key, headers, b'{"type":2}') is False


def test_tampered_timestamp_is_rejected() -> None:
    signing_key = SigningKey.generate()
    headers = signed_headers(signing_key, "1700000000", b"{}")
    headers["X-Signature-Timestamp"] = "1700000001"
    assert verify_interaction_signature(signing_key.verify_key, headers, b"{}") is False


def test_other_application_key_is_rejected() -> None:
    signing_key = SigningKey.generate()
    other_key = SigningKey.generate()
    headers = signed_headers(signing_key, "1700000000", b"{}")
    assert verify_interaction_signature(other_key.verify_key, headers, b"{}") is False


@pytest.mark.parametrize("malformed_signature", ["not-hex", "00" * 10, ""])
def test_malformed_or_short_signature_is_rejected(malformed_signature: str) -> None:
    signing_key = SigningKey.generate()
    headers = {"X-Signature-Ed25519": malformed_signature, "X-Signature-Timestamp": "1700000000"}
    assert verify_interaction_signature(signing_key.verify_key, headers, b"{}") is False


def test_missing_headers_are_rejected() -> None:
    signing_key = SigningKey.generate()
    assert verify_interaction_signature(signing_key.verify_key, {}, b"{}") is False


class FakeRest:
    """Stand-in for DiscordRestClient that records calls and raises scripted failures."""

    def __init__(self, dm_error: str | None = None, message_error: str | None = None, message_id: int = 555) -> None:
        self.dm_error = dm_error
        self.message_error = message_error
        self.message_id = message_id
        self.messages: list[tuple[str, str]] = []

    async def create_dm_channel(self, discord_user_id: int) -> str:
        if self.dm_error is not None:
            raise DiscordRestError(self.dm_error)
        return "channel-1"

    async def create_message(self, channel_id: str, text: str) -> int:
        if self.message_error is not None:
            raise DiscordRestError(self.message_error)
        self.messages.append((channel_id, text))
        return self.message_id


def classify(rest: FakeRest):
    """@description: Run one direct-message attempt through the REST sender synchronously for assertion.
    @param rest: Fake REST client with scripted failures.
    @return: Classified DmResult.
    """
    return asyncio.run(RestDirectMessageSender(rest).send(42, "你的交換推薦：https://youtu.be/example"))


def test_successful_dm_is_delivered_with_accepted_message_id() -> None:
    result = classify(FakeRest(message_id=987))
    assert result.kind is DmKind.DELIVERED
    assert result.message_id == 987


def test_failed_channel_lookup_is_proven_unsent() -> None:
    result = classify(FakeRest(dm_error="discord_403"))
    assert result.kind is DmKind.NOT_SENT
    assert result.reason == "recipient_lookup_failed"


def test_failed_message_post_is_uncertain_and_keeps_its_sanitized_reason() -> None:
    result = classify(FakeRest(message_error="discord_403"))
    assert result.kind is DmKind.UNKNOWN
    assert result.reason == "discord_403"


class FakeResponse:
    """Minimal async response with a status and a JSON body."""

    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status = status
        self._body = body or {}

    async def json(self) -> dict:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False


class FakeSession:
    """Stand-in for aiohttp.ClientSession that returns scripted responses or raises transport errors."""

    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, url: str, **kwargs) -> FakeResponse:
        self.calls.append((method, url))
        if self._error is not None:
            raise self._error
        return self._response


def test_http_error_status_maps_to_sanitized_reason() -> None:
    client = DiscordRestClient(FakeSession(response=FakeResponse(429)), BOT_TOKEN)
    with pytest.raises(DiscordRestError) as raised:
        asyncio.run(client.create_dm_channel(42))
    assert raised.value.reason == "discord_429"


def test_transport_error_hides_url_and_token() -> None:
    interaction_token = "interaction-token-secret"
    transport_error = aiohttp.ClientConnectionError(f"connect failed for /webhooks/1/{interaction_token}")
    client = DiscordRestClient(FakeSession(error=transport_error), BOT_TOKEN)
    with pytest.raises(DiscordRestError) as raised:
        asyncio.run(client.edit_original_response(1, interaction_token, "reply"))
    assert raised.value.reason == TRANSPORT_FAILURE
    assert interaction_token not in str(raised.value)
    assert BOT_TOKEN not in str(raised.value)


def test_successful_message_returns_accepted_identifier() -> None:
    client = DiscordRestClient(FakeSession(response=FakeResponse(200, {"id": "1234"})), BOT_TOKEN)
    assert asyncio.run(client.create_message("channel-1", "hello")) == 1234


def test_sanitized_reason_never_includes_bot_token_in_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    classify(FakeRest(message_error="discord_500"))
    assert BOT_TOKEN not in caplog.text
