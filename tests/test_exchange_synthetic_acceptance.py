"""Verify the labelled synthetic /exchange acceptance entry: guarded listen wiring, scripted outcomes, mode isolation and cleanup.

Offline only: fake database, scripted DM outcomes, mock Discord interactions and a monkeypatched listener; no Discord connection, network, environment file, MySQL or real points.
"""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.acceptance_config import DevelopmentGuildSettings
from bot.config import DiscordSettings
from bot.discord_app import CommandHandlers
from tests import exchange_synthetic_acceptance as entry

OWNER = 777


def settings() -> DevelopmentGuildSettings:
    """@description: Build explicit synthetic targets without reading any environment value.
    @param: none
    @return: Development guild settings with a placeholder token that is never used.
    """
    return DevelopmentGuildSettings(discord=DiscordSettings(token="synthetic-not-used"), guild_id=111, application_id=222)


def interaction() -> MagicMock:
    """@description: Build a mock Discord interaction whose transport is entirely in memory.
    @param: none
    @return: Mock interaction with awaitable defer and followup.
    """
    item = MagicMock()
    item.id = 456
    item.user.id = 101
    item.response.defer = AsyncMock()
    item.followup.send = AsyncMock()
    item.client.fetch_user = AsyncMock(side_effect=AssertionError("the real DM adapter must never be used"))
    return item


def scenarios(injection: str) -> dict:
    """@description: Run the offline scenarios for one scripted mode and index them by label.
    @param injection: Scripted outcome mode.
    @return: Summaries keyed by scenario label.
    """
    return {item["scenario"]: item for item in entry.run_offline_scenarios(injection)}


def test_the_success_scenario_delivers_and_the_zero_point_user_is_refused_without_debit() -> None:
    """@description: A requester with a point is delivered once; a zero-point user gets the reviewed insufficient-credits text.
    @param: none
    @return: None.
    """
    result = scenarios("success")
    assert result["requester_with_point"]["delivery"] == "delivered" and result["requester_with_point"]["labelled"]
    assert result["zero_point_user"]["reply"].endswith("【合成測試】點數不足，交換一首歌曲需要 1 Nickel。")


def test_an_injected_uncertain_outcome_is_not_refunded() -> None:
    """@description: The scripted uncertain outcome stays uncertain with no refund.
    @param: none
    @return: None.
    """
    result = scenarios("uncertain")["requester_with_point"]
    assert result["delivery"] == "uncertain" and result["refund"] is None


def test_an_injected_not_sent_outcome_is_refunded_and_says_so() -> None:
    """@description: The scripted proven-unsent outcome refunds the point, and the reply states that no DM was sent.
    @param: none
    @return: None.
    """
    result = scenarios("not_sent")["requester_with_point"]
    assert result["delivery"] == "failed" and result["refund"] == "refunded" and "已退回 1 點" in result["reply"]


def test_an_injected_refund_failure_is_pending_and_never_reported_as_returned() -> None:
    """@description: A failed refund transaction is reported as pending, not as returned.
    @param: none
    @return: None.
    """
    result = scenarios("refund_fail")["requester_with_point"]
    assert result["refund"] == "pending" and "已退回" not in result["reply"] and "退款待處理" in result["reply"]


def test_the_handler_routes_to_the_scripted_flow_without_touching_the_real_adapter() -> None:
    """@description: A /exchange interaction runs the scripted flow, labels its reply, states that no DM was sent, and shows no link.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("success")
    item = interaction()
    asyncio.run(CommandHandlers(service).exchange(item))
    item.response.defer.assert_awaited_once_with(ephemeral=True)
    item.client.fetch_user.assert_not_awaited()
    sent = item.followup.send.await_args.args[0]
    assert sent.startswith(entry.NOT_SENT_NOTICE) and entry.SYNTHETIC_LABEL in sent and "http" not in sent
    assert item.followup.send.await_args.kwargs == {"ephemeral": True}


def test_the_scripted_mode_is_fixed_at_startup_and_not_selectable_from_discord() -> None:
    """@description: The exchange handler accepts no mode argument, so a Discord user cannot switch the injected outcome.
    @param: none
    @return: None.
    """
    assert list(inspect.signature(CommandHandlers.exchange).parameters) == ["self", "interaction"]
    service = entry.build_exchange_service("uncertain")
    item = interaction()
    asyncio.run(CommandHandlers(service).exchange(item))
    assert "未知" in item.followup.send.await_args.args[0] or "uncertain" in item.followup.send.await_args.args[0] or "結果未知" in item.followup.send.await_args.args[0]


def test_the_scenario_mode_refuses_any_network_switch() -> None:
    """@description: Scenario mode never accepts the gateway switch.
    @param: none
    @return: None.
    """
    with pytest.raises(RuntimeError):
        entry.validate_arguments(entry.argument_parser().parse_args(["scenario", "--authorize-gateway"]))


def test_live_dm_is_refused_explicitly_in_every_mode() -> None:
    """@description: The live DM switch is refused in this round, so no entry point looks as if it could send real messages.
    @param: none
    @return: None.
    """
    with pytest.raises(RuntimeError, match="not part of this round"):
        entry.validate_arguments(entry.argument_parser().parse_args(["listen", "--authorize-gateway", "--authorize-live-dm"]))
    with pytest.raises(RuntimeError, match="not part of this round"):
        entry.validate_arguments(entry.argument_parser().parse_args(["scenario", "--authorize-live-dm"]))


def test_listen_requires_the_gateway_switch() -> None:
    """@description: Listen without its gateway switch is refused before any client is built.
    @param: none
    @return: None.
    """
    with pytest.raises(RuntimeError, match="requires --authorize-gateway"):
        entry.validate_arguments(entry.argument_parser().parse_args(["listen"]))


def test_the_guarded_client_carries_the_owner_group_and_the_labelled_exchange_route() -> None:
    """@description: The listener client holds the Owner group and the exchange command, and its exchange route is the labelled flow.
    @param: none
    @return: None.
    """
    client = entry.build_exchange_client(settings(), OWNER, "success")
    guild_names = {command.name for command in client.tree.get_commands(guild=discord.Object(id=111))}
    global_names = {command.name for command in client.tree.get_commands()}
    assert "owner" in guild_names and "exchange" in global_names
    assert client.handlers.service.synthetic_exchange_enabled is True


def test_run_exchange_listen_uses_the_shared_owner_listener_and_closes_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: The listen path reuses the reviewed Owner listener with the gateway authorization and always closes its client.
    @param monkeypatch: Replaces the reviewed listener so no connection is made.
    @return: None.
    """
    seen = {}

    async def fake_listener(client, targets, *, authorized=False):
        """@description: Record the authorized listener call without connecting.
        @param client: Client handed to the listener.
        @param targets: Explicit targets.
        @param authorized: Gateway authorization flag.
        @return: Summary without identities.
        """
        seen.update(client=client, authorized=authorized, targets=targets)
        return {"listener_stopped": True}

    monkeypatch.setattr(entry, "listen_owner_transport", fake_listener)
    arguments = entry.argument_parser().parse_args(["listen", "--authorize-gateway", "--inject", "not_sent"])
    summary = asyncio.run(entry.run_exchange_listen(arguments, settings=settings(), owner_id=OWNER))
    assert seen["authorized"] is True and seen["targets"] is not None
    assert summary["exchange_mode"] == "not_sent" and summary["listener_stopped"] is True


def test_main_listen_goes_through_the_shared_acceptance_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: The CLI listen mode delegates to the shared safe runner, so raw errors and credentials never reach the console.
    @param monkeypatch: Replaces the shared runner to observe the call.
    @return: None.
    """
    calls = []
    monkeypatch.setattr(entry, "execute_acceptance", lambda arguments, operation: calls.append((arguments.mode, operation)))
    assert entry.main(["listen", "--authorize-gateway", "--inject", "success"]) == 0
    assert calls == [("listen", entry.run_exchange_listen)]


def test_scenario_main_prints_the_synthetic_notice_and_labelled_results(capsys: pytest.CaptureFixture) -> None:
    """@description: The offline run prints the synthetic notice before any result.
    @param capsys: Captured output.
    @return: None.
    """
    assert entry.main(["scenario", "--inject", "success"]) == 0
    output = capsys.readouterr().out
    assert output.startswith(entry.SYNTHETIC_NOTICE) and "requester_with_point" in output
