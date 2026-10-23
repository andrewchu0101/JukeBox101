"""Verify the discord.py exchange adapters classify each concrete call result by what it proves, and that the /exchange route is selected explicitly.

Mocked client, user and interaction objects with real discord.py exception types; no network, no Discord session, no message is ever sent.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot import discord_app
from bot.discord_exchange import DiscordDirectMessageSender, DiscordEphemeralReplier
from bot.exchange_delivery import DmKind


def http_error(cls, code: int, status: int = 403):
    """@description: Build a real discord.py HTTP exception with the given status and Discord error code.
    @param cls: discord.py exception class.
    @param code: Discord JSON error code.
    @param status: HTTP status.
    @return: Exception instance.
    """
    response = MagicMock(status=status, reason="synthetic")
    return cls(response, {"code": code, "message": "synthetic"})


def client_with(user: MagicMock | None = None, lookup_error: BaseException | None = None) -> MagicMock:
    """@description: Build a mock client whose recipient lookup either returns a user or fails.
    @param user: Mock user whose send is configured by the test.
    @param lookup_error: Exception raised by the lookup, if any.
    @return: Mock client.
    """
    client = MagicMock()
    client.fetch_user = AsyncMock(side_effect=lookup_error, return_value=user)
    return client


def user_whose_send_raises(error: BaseException) -> MagicMock:
    """@description: Build a mock user whose send raises the given error.
    @param error: Exception raised by send.
    @return: Mock user.
    """
    user = MagicMock()
    user.send = AsyncMock(side_effect=error)
    return user


def send(client, text: str = "synthetic body"):
    """@description: Run the real adapter once against the mock client.
    @param client: Mock client.
    @param text: Message body.
    @return: Classified DM result.
    """
    return asyncio.run(DiscordDirectMessageSender(client).send(42, text))


def test_an_accepted_dm_is_delivered_with_discord_message_identifier() -> None:
    """@description: Only an accepted send returns delivered, carrying the identifier Discord returned.
    @param: none
    @return: None.
    """
    user = MagicMock()
    user.send = AsyncMock(return_value=MagicMock(id=555))
    result = send(client_with(user))
    assert result.kind is DmKind.DELIVERED and result.message_id == 555


def test_a_lookup_failure_is_proven_unsent_because_no_message_was_attempted() -> None:
    """@description: A failed recipient lookup is a GET only, so it proves no DM was sent and may be refunded.
    @param: none
    @return: None.
    """
    result = send(client_with(lookup_error=http_error(discord.HTTPException, 0, 500)))
    assert result.kind is DmKind.NOT_SENT and result.reason == "recipient_lookup_failed"


def test_a_refusal_at_send_time_is_uncertain_even_with_error_code_50007() -> None:
    """@description: A final 403 at send time is not proof of non-delivery, because discord.py may have retried earlier responses in the same call.
    @param: none
    @return: None.
    """
    result = send(client_with(user_whose_send_raises(http_error(discord.Forbidden, 50007))))
    assert result.kind is DmKind.UNKNOWN


def test_other_forbidden_errors_are_uncertain_and_never_refunded() -> None:
    """@description: Any other 403 is not proof of non-delivery, so it stays uncertain.
    @param: none
    @return: None.
    """
    result = send(client_with(user_whose_send_raises(http_error(discord.Forbidden, 50001))))
    assert result.kind is DmKind.UNKNOWN


@pytest.mark.parametrize("error", [
    lambda: http_error(discord.DiscordServerError, 0, 500),
    lambda: http_error(discord.HTTPException, 0, 429),
    lambda: TimeoutError("synthetic"),
    lambda: ConnectionResetError("synthetic"),
])
def test_server_rate_limit_timeout_and_connection_failures_are_uncertain(error) -> None:
    """@description: Server errors, rate limits, timeouts and disconnects may still have been accepted, so they are uncertain.
    @param error: Factory for the raised exception.
    @return: None.
    """
    result = send(client_with(user_whose_send_raises(error())))
    assert result.kind is DmKind.UNKNOWN


def test_the_adapter_never_sends_twice_for_one_call() -> None:
    """@description: One adapter call performs at most one send, so discord.py's own retries are the only repetition and are classed uncertain.
    @param: none
    @return: None.
    """
    user = user_whose_send_raises(http_error(discord.DiscordServerError, 0, 500))
    send(client_with(user))
    assert user.send.await_count == 1


def test_the_ephemeral_reply_uses_the_deferred_followup_and_is_private() -> None:
    """@description: The reply goes out as an ephemeral follow-up on the interaction.
    @param: none
    @return: None.
    """
    interaction = MagicMock()
    interaction.followup.send = AsyncMock()
    asyncio.run(DiscordEphemeralReplier(interaction).send_ephemeral("reply"))
    interaction.followup.send.assert_awaited_once_with("reply", ephemeral=True)


def test_the_exchange_route_is_selected_only_by_the_explicit_synthetic_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Without the explicit synthetic flag the existing gate answers /exchange; with it the labelled flow runs.
    @param monkeypatch: Replaces the respond helper to observe the route.
    @return: None.
    """
    gate_calls = []

    async def respond(interaction, operation, *args):
        gate_calls.append(args)

    monkeypatch.setattr(discord_app, "_respond", respond)
    interaction = MagicMock()
    interaction.user.id = 101
    interaction.response.defer = AsyncMock()
    gate_service = MagicMock(spec=["exchange"])
    gate_service.exchange = MagicMock()
    asyncio.run(discord_app.CommandHandlers(gate_service).exchange(interaction))
    assert gate_calls == [(101,)]
    interaction.response.defer.assert_not_awaited()

    synthetic_service = MagicMock()
    synthetic_service.synthetic_exchange_enabled = True
    synthetic_service.synthetic_exchange = AsyncMock()
    asyncio.run(discord_app.CommandHandlers(synthetic_service).exchange(interaction))
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    synthetic_service.synthetic_exchange.assert_awaited_once()
    assert len(gate_calls) == 1


def test_the_dm_bound_holds_only_when_the_attempt_honours_cancellation() -> None:
    """@description: asyncio.wait_for waits for a cancelled attempt to finish, so an attempt that ignores cancellation is not bounded; the flow therefore relies on the SDK honouring cancellation.
    @param: none
    @return: None.
    """
    import time

    async def stubborn():
        """@description: Ignore one cancellation and keep running briefly, as an attempt that does not honour cancel would.
        @param: none
        @return: None.
        """
        try:
            await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            await asyncio.sleep(0.3)
            raise

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(asyncio.wait_for(stubborn(), 0.01))
    assert time.monotonic() - started >= 0.3  # [measured limit]: The local bound is not absolute for uncooperative coroutines.
