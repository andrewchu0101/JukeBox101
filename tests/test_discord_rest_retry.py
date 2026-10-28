"""Pin bounded retry for HTTP 429 on the outbound REST client.

Only 429 is retried, only up to MAX_REST_ATTEMPTS total attempts, and never sooner than Discord's Retry-After.
A wait longer than the budget fails at once instead of shortening the wait, and every attempt and wait shares one overall deadline.
No real Discord call is made; time is a fake clock advanced by the scripted session and the injected sleep.
"""

import asyncio

import pytest

from bot.discord_http import (
    MAX_REST_ATTEMPTS,
    MAX_RETRY_WAIT_SECONDS,
    REST_RETRY_BUDGET_SECONDS,
    DiscordRestClient,
    DiscordRestError,
    RestDirectMessageSender,
)
from bot.exchange_delivery import DmKind
from bot import discord_http
from bot.exchange_delivery import DM_TIMEOUT_SECONDS

BOT_TOKEN = "test-bot-token-not-real"


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Response:
    def __init__(self, status: int, headers: dict | None = None, body: dict | None = None, took: float = 0.0) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = body or {}
        self.took = took  # [simulated duration]: Seconds the fake clock advances while this response is awaited.

    async def json(self) -> dict:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False


class _ScriptedSession:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.attempts = 0
        self.timeouts: list[float] = []
        self.clock = _FakeClock()

    def request(self, method: str, url: str, **kwargs):
        self.attempts += 1
        self.timeouts.append(kwargs["timeout"].total)
        response = self._responses.pop(0)
        self.clock.now += response.took
        return response


def _client(session, sleeps: list) -> DiscordRestClient:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        session.clock.now += seconds
    return DiscordRestClient(session, BOT_TOKEN, sleep=fake_sleep, clock=session.clock)


def test_a_429_with_retry_after_is_retried_after_the_advised_wait_and_then_succeeds():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, {"Retry-After": "0.5"}), _Response(200, body={"id": "1"})])
    result = asyncio.run(_client(session, sleeps)._send("POST", "/x", {}, {}, retry_on_rate_limit=True))
    assert result == {"id": "1"}
    assert sleeps == [0.5]
    assert session.attempts == 2


def test_retry_after_in_the_json_body_is_used_when_the_header_is_missing():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, body={"retry_after": 0.25}), _Response(200, body={})])
    asyncio.run(_client(session, sleeps)._send("POST", "/x", {}, {}, retry_on_rate_limit=True))
    assert sleeps == [0.25]


def test_persistent_429_stops_at_the_attempt_limit_and_never_retries_forever():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, {"Retry-After": "0.1"}) for _ in range(MAX_REST_ATTEMPTS)])
    with pytest.raises(DiscordRestError) as refused:
        asyncio.run(_client(session, sleeps)._send("POST", "/x", {}, {}, retry_on_rate_limit=True))
    assert refused.value.reason == "discord_429"
    assert session.attempts == MAX_REST_ATTEMPTS
    assert len(sleeps) == MAX_REST_ATTEMPTS - 1


def test_a_wait_longer_than_the_budget_fails_at_once_without_sleeping_or_shortening_it():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, {"Retry-After": str(MAX_RETRY_WAIT_SECONDS + 60)})])
    with pytest.raises(DiscordRestError):
        asyncio.run(_client(session, sleeps)._send("POST", "/x", {}, {}, retry_on_rate_limit=True))
    assert sleeps == []
    assert session.attempts == 1


def test_an_unreadable_retry_after_is_not_guessed_and_fails_without_retry():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, {"Retry-After": "soon"})])
    with pytest.raises(DiscordRestError):
        asyncio.run(_client(session, sleeps)._send("POST", "/x", {}, {}, retry_on_rate_limit=True))
    assert sleeps == [] and session.attempts == 1


@pytest.mark.parametrize("status", [404, 500, 503])
def test_other_error_statuses_are_never_retried(status):
    sleeps: list = []
    session = _ScriptedSession([_Response(status)])
    with pytest.raises(DiscordRestError) as refused:
        asyncio.run(_client(session, sleeps)._send("POST", "/x", {}, {}, retry_on_rate_limit=True))
    assert refused.value.reason == f"discord_{status}"
    assert session.attempts == 1 and sleeps == []


def test_the_budget_constants_are_bounded_and_small():
    assert MAX_REST_ATTEMPTS == 3
    assert 0 < MAX_RETRY_WAIT_SECONDS <= 10


def test_the_overall_rest_budget_finishes_before_the_direct_message_timeout():
    assert REST_RETRY_BUDGET_SECONDS < DM_TIMEOUT_SECONDS


def test_slow_attempts_and_waits_share_one_overall_budget_and_the_next_attempt_is_capped_to_what_remains():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, {"Retry-After": "5"}, took=9.0), _Response(429, {"Retry-After": "5"})])
    with pytest.raises(DiscordRestError) as refused:
        asyncio.run(_client(session, sleeps)._send("POST", "/x", {}, {}, retry_on_rate_limit=True))
    assert refused.value.reason == "discord_429"
    assert sleeps == [5.0]
    assert session.attempts == 2
    assert session.timeouts[1] == pytest.approx(REST_RETRY_BUDGET_SECONDS - 14.0)


def test_a_retry_after_that_would_end_past_the_overall_budget_fails_before_sleeping():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, {"Retry-After": "4"}, took=12.0), _Response(200, body={})])
    with pytest.raises(DiscordRestError):
        asyncio.run(_client(session, sleeps)._send("POST", "/x", {}, {}, retry_on_rate_limit=True))
    assert sleeps == []
    assert session.attempts == 1


def test_a_rate_limited_direct_message_post_is_not_repeated_and_its_outcome_is_uncertain():
    sleeps: list = []
    session = _ScriptedSession([_Response(200, body={"id": "c1"}), _Response(429, {"Retry-After": "1"}), _Response(200, body={"id": "987"})])
    sender = RestDirectMessageSender(_client(session, sleeps))
    result = asyncio.run(sender.send(42, "link"))
    assert result.message_id is None
    assert result.kind == DmKind.UNKNOWN
    assert session.attempts == 2 and sleeps == []


def test_a_rate_limited_message_post_is_never_repeated_even_with_a_short_advised_wait():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, {"Retry-After": "0.1"}), _Response(200, body={"id": "987"})])
    with pytest.raises(DiscordRestError) as refused:
        asyncio.run(_client(session, sleeps).create_message("c1", "link"))
    assert refused.value.reason == "discord_429"
    assert session.attempts == 1 and sleeps == []


def test_a_rate_limited_channel_lookup_is_repeated_because_it_returns_the_existing_channel():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, {"Retry-After": "0.5"}), _Response(200, body={"id": "c1"})])
    assert asyncio.run(_client(session, sleeps).create_dm_channel(42)) == "c1"
    assert sleeps == [0.5] and session.attempts == 2


def test_a_rate_limited_original_response_edit_is_repeated_because_the_edit_is_idempotent():
    sleeps: list = []
    session = _ScriptedSession([_Response(429, {"Retry-After": "0.5"}), _Response(200, body={})])
    asyncio.run(_client(session, sleeps).edit_original_response(1, "token", "reply"))
    assert sleeps == [0.5] and session.attempts == 2


class _TimeoutSession:
    """Fails every request with a timeout before any response exists."""

    def __init__(self) -> None:
        self.attempts = 0

    def request(self, method: str, url: str, **kwargs):
        self.attempts += 1
        raise TimeoutError()


def test_a_timeout_is_never_retried_even_for_an_endpoint_that_allows_retry():
    session = _TimeoutSession()
    client = DiscordRestClient(session, BOT_TOKEN, sleep=None, clock=_FakeClock())
    with pytest.raises(DiscordRestError) as refused:
        asyncio.run(client.create_dm_channel(42))
    assert refused.value.reason == "transport"
    assert session.attempts == 1


def test_cancelling_during_a_retry_wait_stops_the_call_without_another_attempt():
    session = _ScriptedSession([_Response(429, {"Retry-After": "1"}), _Response(200, body={})])
    waiting = asyncio.Event()

    async def blocked_sleep(seconds: float) -> None:
        waiting.set()
        await asyncio.Event().wait()

    async def scenario() -> None:
        client = DiscordRestClient(session, BOT_TOKEN, sleep=blocked_sleep, clock=session.clock)
        task = asyncio.ensure_future(client._send("POST", "/x", {}, {}, retry_on_rate_limit=True))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert session.attempts == 1


def test_the_module_docstring_is_the_first_statement_so_the_module_stays_documented():
    assert discord_http.__doc__ is not None and discord_http.__doc__.startswith("Verify Discord")
