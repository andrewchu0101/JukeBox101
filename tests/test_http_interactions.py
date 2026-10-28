"""Offline tests for HTTP interaction routing: signature gate, admission, deferral, request keys, closed exchange and background shutdown.

The service and Discord REST are fakes; no database, network or real token is used.
"""

import asyncio
import json
import logging

import pytest
from aiohttp.test_utils import TestClient, TestServer
from nacl.signing import SigningKey

from bot.config import DatabaseSettings
from bot.discord_http import RestDirectMessageSender, RestEphemeralReplier
from bot.http_config import HttpInteractionSettings
from bot.http_interactions import (BackgroundWork, DAILY_CHOICE_TEXT, EPHEMERAL_FLAG, GENERIC_FAILURE_TEXT,
                                   InteractionDispatcher, MISSING_URL_TEXT, REFUSAL_TEXT, build_app)
from bot.services import ServiceUnavailable
from tests.test_discord_http import signed_headers

GUILD_ID = 1559749813011087392
APPLICATION_ID = 1559751168723521576
USER_ID = 456708412697215018
BOT_TOKEN = "unit-test-bot-token-value"


class FakeService:
    """Records every call and returns fixed text, so routing can be asserted without any database."""

    def __init__(self, formal_exchange_enabled: bool = False, failure: Exception | None = None) -> None:
        self.formal_exchange_enabled = formal_exchange_enabled
        self.failure = failure
        self.calls: list[tuple] = []

    async def _record(self, name: str, *arguments) -> str:
        self.calls.append((name, *arguments))
        if self.failure is not None:
            raise self.failure
        return f"{name} reply"

    async def share(self, user_id: int, url: str, operation_id: str) -> str:
        return await self._record("share", user_id, url, operation_id)

    async def balance(self, user_id: int) -> str:
        return await self._record("balance", user_id)

    async def history(self, user_id: int) -> str:
        return await self._record("history", user_id)

    async def daily(self, user_id: int, enabled: bool) -> str:
        return await self._record("daily", user_id, enabled)

    async def exchange(self, user_id: int) -> str:
        return await self._record("exchange", user_id)

    async def formal_exchange(self, user_id: int, request_key: str, sender, replier) -> None:
        self.calls.append(("formal_exchange", user_id, request_key, type(sender), type(replier)))


class FakeRest:
    """Records edits of deferred responses; the DM methods are unused by these tests."""

    def __init__(self) -> None:
        self.edits: list[tuple[int, str, str]] = []

    async def edit_original_response(self, application_id: int, interaction_token: str, text: str) -> None:
        self.edits.append((application_id, interaction_token, text))


def command_payload(name: str, options: list | None = None, *, interaction_id: str = "1563787609656926209") -> dict:
    """@description: Build an admitted slash-command payload shaped like Discord's interaction object.
    @param name: Registered command name.
    @param options: Submitted options, or None for none.
    @param interaction_id: Interaction identifier used for the request key.
    @return: Parsed interaction payload.
    """
    return {"id": interaction_id, "type": 2, "token": "interaction-token-value", "guild_id": str(GUILD_ID),
            "application_id": str(APPLICATION_ID), "member": {"user": {"id": str(USER_ID)}},
            "data": {"name": name, "options": options or []}}


def dispatcher_for(service: FakeService, rest: FakeRest) -> InteractionDispatcher:
    """@description: Build a dispatcher over fakes with the private guild and application admitted.
    @param service: Fake service.
    @param rest: Fake REST client.
    @return: Dispatcher ready for one test.
    """
    return InteractionDispatcher(service, rest, guild_id=GUILD_ID, application_id=APPLICATION_ID, background=BackgroundWork())


def run_dispatch(dispatcher: InteractionDispatcher, payload: dict) -> dict:
    """@description: Dispatch one payload, let its deferred work finish, and return the synchronous response body.
    @param dispatcher: Dispatcher under test.
    @param payload: Parsed interaction payload.
    @return: Synchronous response body.
    """
    async def scenario() -> dict:
        response, work = await dispatcher.dispatch(payload)
        dispatcher.start_work(work)
        await dispatcher.drain()
        return response
    return asyncio.run(scenario())


def test_dispatch_alone_produces_no_side_effect_before_start_work() -> None:
    service, rest = FakeService(), FakeRest()
    dispatcher = dispatcher_for(service, rest)

    async def scenario() -> list:
        _, work = await dispatcher.dispatch(command_payload("balance"))
        calls_before_start = list(service.calls)
        dispatcher.start_work(work)
        await dispatcher.drain()
        return calls_before_start

    assert asyncio.run(scenario()) == []
    assert service.calls == [("balance", USER_ID)]


def test_ping_is_answered_with_pong() -> None:
    service, rest = FakeService(), FakeRest()
    response = run_dispatch(dispatcher_for(service, rest), {"type": 1})
    assert response == {"type": 1}
    assert service.calls == []


def test_command_from_another_guild_is_refused_without_service_call() -> None:
    service, rest = FakeService(), FakeRest()
    payload = command_payload("balance")
    payload["guild_id"] = "1"
    response = run_dispatch(dispatcher_for(service, rest), payload)
    assert response["data"]["content"] == REFUSAL_TEXT
    assert service.calls == [] and rest.edits == []


def test_direct_message_command_without_member_is_refused() -> None:
    service, rest = FakeService(), FakeRest()
    payload = command_payload("balance")
    del payload["member"]
    payload["guild_id"] = None
    response = run_dispatch(dispatcher_for(service, rest), payload)
    assert response["data"]["flags"] == EPHEMERAL_FLAG
    assert service.calls == []


def test_wrong_application_is_refused() -> None:
    service, rest = FakeService(), FakeRest()
    payload = command_payload("balance")
    payload["application_id"] = "2"
    run_dispatch(dispatcher_for(service, rest), payload)
    assert service.calls == []


def test_share_is_deferred_privately_and_uses_gateway_request_key() -> None:
    service, rest = FakeService(), FakeRest()
    url_option = {"name": "url", "type": 3, "value": "https://www.youtube.com/watch?v=abc"}
    response = run_dispatch(dispatcher_for(service, rest), command_payload("share", [url_option]))
    assert response == {"type": 5, "data": {"flags": EPHEMERAL_FLAG}}
    assert service.calls == [("share", USER_ID, "https://www.youtube.com/watch?v=abc", "discord:1563787609656926209")]
    assert rest.edits == [(APPLICATION_ID, "interaction-token-value", "share reply")]


def test_share_without_url_is_answered_without_calling_service() -> None:
    service, rest = FakeService(), FakeRest()
    run_dispatch(dispatcher_for(service, rest), command_payload("share"))
    assert service.calls == []
    assert rest.edits[0][2] == MISSING_URL_TEXT


def test_daily_refuses_unknown_choice_without_persisting() -> None:
    service, rest = FakeService(), FakeRest()
    option = {"name": "mode", "type": 3, "value": "weekly"}
    run_dispatch(dispatcher_for(service, rest), command_payload("daily", [option]))
    assert service.calls == []
    assert rest.edits[0][2] == DAILY_CHOICE_TEXT


def test_daily_on_passes_explicit_boolean() -> None:
    service, rest = FakeService(), FakeRest()
    option = {"name": "mode", "type": 3, "value": "on"}
    run_dispatch(dispatcher_for(service, rest), command_payload("daily", [option]))
    assert service.calls == [("daily", USER_ID, True)]


def test_exchange_stays_closed_by_default_and_never_reaches_formal_flow() -> None:
    service, rest = FakeService(formal_exchange_enabled=False), FakeRest()
    run_dispatch(dispatcher_for(service, rest), command_payload("exchange"))
    assert service.calls == [("exchange", USER_ID)]


def test_formal_exchange_uses_rest_adapters_and_the_same_request_key() -> None:
    service, rest = FakeService(formal_exchange_enabled=True), FakeRest()
    run_dispatch(dispatcher_for(service, rest), command_payload("exchange"))
    name, user_id, request_key, sender_type, replier_type = service.calls[0]
    assert (name, user_id, request_key) == ("formal_exchange", USER_ID, "discord:1563787609656926209")
    assert sender_type is RestDirectMessageSender and replier_type is RestEphemeralReplier
    assert rest.edits == []


def test_unexpected_service_failure_gives_generic_reply_and_logs_class_only(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)
    service, rest = FakeService(failure=RuntimeError("secret-detail")), FakeRest()
    run_dispatch(dispatcher_for(service, rest), command_payload("balance"))
    assert rest.edits[0][2] == GENERIC_FAILURE_TEXT
    assert "RuntimeError" in caplog.text and "secret-detail" not in caplog.text


def test_expected_service_error_text_is_shown_to_the_user() -> None:
    service, rest = FakeService(failure=ServiceUnavailable("服務暫時不可用")), FakeRest()
    run_dispatch(dispatcher_for(service, rest), command_payload("balance"))
    assert rest.edits[0][2] == "服務暫時不可用"


def test_background_failure_is_logged_by_class_name_and_released(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)
    tracker = BackgroundWork()

    async def failing_work() -> None:
        raise ValueError("payload with token")

    async def scenario() -> int:
        tracker.start(failing_work())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return await tracker.drain()

    assert asyncio.run(scenario()) == 0
    assert "ValueError" in caplog.text and "payload with token" not in caplog.text


def test_drain_cancels_work_that_outlives_the_bound() -> None:
    tracker = BackgroundWork()

    async def never_finishes() -> None:
        await asyncio.Event().wait()

    async def scenario() -> int:
        tracker.start(never_finishes())
        return await tracker.drain(timeout_seconds=0.01)

    assert asyncio.run(scenario()) == 1


def test_drain_with_no_work_returns_immediately() -> None:
    assert asyncio.run(BackgroundWork().drain()) == 0


def signing_settings(signing_key: SigningKey) -> HttpInteractionSettings:
    """@description: Build endpoint settings whose database is never contacted, because only PING and rejected requests are exercised.
    @param signing_key: Test-only key whose public half verifies requests.
    @return: Validated-looking settings for the endpoint.
    """
    database = DatabaseSettings(host="127.0.0.1", port=3306, user="jukebox101_app", password="unused-test-value", database="jukebox101")
    return HttpInteractionSettings(verify_key=signing_key.verify_key, bot_token=BOT_TOKEN, guild_id=GUILD_ID,
                                   application_id=APPLICATION_ID, owner_id=USER_ID, database=database, formal_exchange=False)


def post_interaction(app_client: TestClient, headers: dict, body: bytes):
    """@description: Send one raw interaction request to the endpoint.
    @param app_client: Test client over the application.
    @param headers: Signature headers.
    @param body: Raw request body.
    @return: Awaitable HTTP response.
    """
    return app_client.post("/interactions", data=body, headers={**headers, "Content-Type": "application/json"})


def run_endpoint_scenario(scenario) -> None:
    """@description: Start the endpoint in a test client, run one scenario, and shut it down cleanly.
    @param scenario: Async callable receiving the test client and the service fake.
    @return: None.
    """
    signing_key = SigningKey.generate()
    service = FakeService()

    async def run() -> None:
        app = build_app(service, signing_settings(signing_key))
        async with TestClient(TestServer(app)) as app_client:
            await scenario(app_client, signing_key, service)
    asyncio.run(run())


def test_unsigned_request_is_rejected_before_parsing_or_business_code() -> None:
    async def scenario(app_client, signing_key, service) -> None:
        response = await post_interaction(app_client, {"X-Signature-Ed25519": "00" * 64, "X-Signature-Timestamp": "1"}, b'{"type":1}')
        assert response.status == 401
        assert service.calls == []
    run_endpoint_scenario(scenario)


def test_signed_ping_gets_pong_over_http() -> None:
    async def scenario(app_client, signing_key, service) -> None:
        body = b'{"type":1}'
        response = await post_interaction(app_client, signed_headers(signing_key, "1700000000", body), body)
        assert response.status == 200
        assert json.loads(await response.text()) == {"type": 1}
    run_endpoint_scenario(scenario)


def test_signed_but_unparsable_body_is_rejected_with_400() -> None:
    async def scenario(app_client, signing_key, service) -> None:
        body = b"{not json"
        response = await post_interaction(app_client, signed_headers(signing_key, "1700000000", body), body)
        assert response.status == 400
    run_endpoint_scenario(scenario)


def test_signed_non_object_body_is_rejected_with_400() -> None:
    async def scenario(app_client, signing_key, service) -> None:
        body = b"[1, 2]"
        response = await post_interaction(app_client, signed_headers(signing_key, "1700000000", body), body)
        assert response.status == 400
    run_endpoint_scenario(scenario)
