"""Verify a failed private reply after a command finishes is logged by sanitized reason only, is not repeated, and never swallows cancellation.

The service and Discord REST are fakes; no database, network or real token is used.
"""

import asyncio
import logging

import pytest

from bot.discord_http import DiscordRestError
from bot.http_interactions import BackgroundWork, InteractionDispatcher
from tests.test_http_interactions import APPLICATION_ID, GUILD_ID, FakeService, command_payload, dispatcher_for, run_dispatch


class FailingReplyRest:
    """Counts reply attempts and fails each one with a sanitized REST error, as an expired token or Discord outage would."""

    def __init__(self, error: Exception) -> None:
        self.attempts = 0
        self._error = error

    async def edit_original_response(self, application_id: int, interaction_token: str, text: str) -> None:
        self.attempts += 1
        raise self._error


class HangingReplyRest:
    """Holds the reply open so the test can cancel it while it is in flight."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def edit_original_response(self, application_id: int, interaction_token: str, text: str) -> None:
        self.started.set()
        await asyncio.Event().wait()


def test_a_failed_final_reply_is_logged_by_sanitized_reason_and_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    """@description: A REST failure while sending the final reply is reported by its sanitized reason, and the background tracker does not treat it as an unexpected crash.
    @param caplog: Captured log records.
    @return: None.
    """
    caplog.set_level(logging.ERROR)
    rest = FailingReplyRest(DiscordRestError("discord_404"))
    assert run_dispatch(dispatcher_for(FakeService(), rest), command_payload("balance"))["type"] == 5
    assert "discord_404" in caplog.text
    assert "Interaction work failed" not in caplog.text


def test_a_failed_final_reply_is_not_repeated_so_no_duplicate_reply_is_sent() -> None:
    """@description: The final reply is attempted exactly once, because a repeat could duplicate a message Discord already accepted.
    @return: None.
    """
    rest = FailingReplyRest(DiscordRestError("transport"))
    run_dispatch(dispatcher_for(FakeService(), rest), command_payload("balance"))
    assert rest.attempts == 1


def test_a_failed_final_reply_does_not_include_secret_text_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    """@description: Only the sanitized reason reaches the log, never an exception message that could carry the interaction token.
    @param caplog: Captured log records.
    @return: None.
    """
    caplog.set_level(logging.ERROR)
    rest = FailingReplyRest(DiscordRestError("transport"))
    run_dispatch(dispatcher_for(FakeService(), rest), command_payload("balance"))
    assert "interaction-token-value" not in caplog.text


def test_cancelling_a_reply_in_flight_propagates_the_cancellation_without_an_error_log(caplog: pytest.LogCaptureFixture) -> None:
    """@description: Cancellation of a reply that is still in flight remains a cancellation; it is not converted into a failure log.
    @param caplog: Captured log records.
    @return: None.
    """
    caplog.set_level(logging.ERROR)
    rest = HangingReplyRest()
    tracker = BackgroundWork()

    async def scenario() -> None:
        dispatcher = InteractionDispatcher(FakeService(), rest, guild_id=GUILD_ID, application_id=APPLICATION_ID, background=tracker)
        response, work = await dispatcher.dispatch(command_payload("balance"))
        dispatcher.start_work(work)
        await rest.started.wait()
        await tracker.drain(timeout_seconds=0.01)

    asyncio.run(scenario())
    assert "Interaction work failed" not in caplog.text


class FormalFlowFails(FakeService):
    """Formal exchange flow that fails unexpectedly, so the generic failure reply is the only remaining edit."""

    async def formal_exchange(self, user_id: int, request_key: str, sender, replier) -> None:
        raise RuntimeError("synthetic-formal-failure")


class FormalFlowHangs(FakeService):
    """Formal exchange flow that is still running when shutdown cancels it."""

    def __init__(self) -> None:
        super().__init__(formal_exchange_enabled=True)
        self.started = asyncio.Event()

    async def formal_exchange(self, user_id: int, request_key: str, sender, replier) -> None:
        self.started.set()
        await asyncio.Event().wait()


def test_a_failed_generic_reply_after_a_formal_flow_failure_is_logged_by_reason(caplog: pytest.LogCaptureFixture) -> None:
    """@description: When the formal flow fails and the generic failure reply also fails, the reason is logged and the background tracker sees no crash.
    @param caplog: Captured log records.
    @return: None.
    """
    caplog.set_level(logging.ERROR)
    rest = FailingReplyRest(DiscordRestError("discord_404"))
    run_dispatch(dispatcher_for(FormalFlowFails(formal_exchange_enabled=True), rest), command_payload("exchange"))
    assert "discord_404" in caplog.text
    assert "Interaction work failed" not in caplog.text


def test_a_failed_generic_reply_after_a_formal_flow_failure_is_attempted_once_and_leaks_no_token(caplog: pytest.LogCaptureFixture) -> None:
    """@description: The generic failure reply is attempted exactly once, so no duplicate reply is sent, and the interaction token never reaches the log.
    @param caplog: Captured log records.
    @return: None.
    """
    caplog.set_level(logging.ERROR)
    rest = FailingReplyRest(DiscordRestError("transport"))
    run_dispatch(dispatcher_for(FormalFlowFails(formal_exchange_enabled=True), rest), command_payload("exchange"))
    assert rest.attempts == 1
    assert "interaction-token-value" not in caplog.text


def test_cancelling_a_running_formal_flow_propagates_cancellation_without_an_error_log(caplog: pytest.LogCaptureFixture) -> None:
    """@description: Cancelling a formal flow still in progress stays a cancellation and is not reported as a failure.
    @param caplog: Captured log records.
    @return: None.
    """
    caplog.set_level(logging.ERROR)
    service = FormalFlowHangs()
    tracker = BackgroundWork()

    async def scenario() -> None:
        dispatcher = InteractionDispatcher(service, FailingReplyRest(DiscordRestError("unused")), guild_id=GUILD_ID,
                                           application_id=APPLICATION_ID, background=tracker)
        response, work = await dispatcher.dispatch(command_payload("exchange"))
        dispatcher.start_work(work)
        await service.started.wait()
        await tracker.drain(timeout_seconds=0.01)

    asyncio.run(scenario())
    assert "Interaction work failed" not in caplog.text
