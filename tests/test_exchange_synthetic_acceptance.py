"""Verify the labelled synthetic /exchange acceptance: participant mapping, /balance and /history, scripted outcomes, mode isolation and listen wiring.

Offline only: fake database, scripted DM outcomes, mock Discord interactions and a monkeypatched listener; no Discord connection, network, environment file, MySQL or real points.
The participant identifiers below are obviously synthetic test values, not Discord accounts.
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
from tests.exchange_synthetic_acceptance import SyntheticAccounts

OWNER = 900000000000000001  # [synthetic test identity]: Stands in for DISCORD_OWNER_USER_ID in this test only.
PARTNER = 900000000000000002  # [synthetic test identity]: Optional second participant.
ZERO = 900000000000000003  # [synthetic test identity]: Optional zero-point participant.
STRANGER = 900000000000000009  # [synthetic test identity]: Not configured; must never receive anything.
ACCOUNTS = SyntheticAccounts(owner=OWNER, partner=PARTNER, zero=ZERO)


def settings() -> DevelopmentGuildSettings:
    """@description: Build explicit synthetic targets without reading any environment value.
    @param: none
    @return: Development guild settings with a placeholder token that is never used.
    """
    return DevelopmentGuildSettings(discord=DiscordSettings(token="synthetic-not-used"), guild_id=111, application_id=222)


_INTERACTION_IDS = iter(range(10_000, 10_000_000))


def interaction(user_id: int) -> MagicMock:
    """@description: Build a mock Discord interaction whose transport is entirely in memory.
    @param user_id: Synthetic invoking user.
    @return: Mock interaction with awaitable defer and followup; the real DM adapter must never be used.
    """
    item = MagicMock()
    item.id = next(_INTERACTION_IDS)  # [new Discord interaction]: Each /exchange call is a new request, not a replay.
    item.user.id = user_id
    item.response.defer = AsyncMock()
    item.followup.send = AsyncMock()
    item.client.fetch_user = AsyncMock(side_effect=AssertionError("the real DM adapter must never be used"))
    return item


def run_exchange(service, user_id: int) -> str:
    """@description: Send one /exchange interaction through the handler and return the ephemeral text.
    @param service: Composite service under test.
    @param user_id: Invoking synthetic user.
    @return: Text shown to the invoker.
    """
    item = interaction(user_id)
    asyncio.run(CommandHandlers(service).exchange(item))
    item.response.defer.assert_awaited_once_with(ephemeral=True)
    item.client.fetch_user.assert_not_awaited()
    return item.followup.send.await_args.args[0]


def balance(service, user_id: int) -> str:
    """@description: Read the caller's own synthetic balance through the composite service.
    @param service: Composite service under test.
    @param user_id: Invoking synthetic user.
    @return: Balance text.
    """
    return asyncio.run(service.balance(user_id))


def history(service, user_id: int) -> str:
    """@description: Read the caller's own synthetic history through the composite service.
    @param service: Composite service under test.
    @param user_id: Invoking synthetic user.
    @return: History text.
    """
    return asyncio.run(service.history(user_id))


def test_owner_first_exchange_succeeds_then_the_balance_and_history_show_the_same_result() -> None:
    """@description: The owner's first exchange is delivered, one point is spent, history shows the operation, and a second request is refused for lack of points.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("success", ACCOUNTS)
    assert balance(service, OWNER) == f"{entry.SYNTHETIC_LABEL}假餘額：1 點（合成資料，未動真實點數）。"
    first = run_exchange(service, OWNER)
    assert first.startswith(entry.SYNTHETIC_LABEL) and "合成交換成功；未實際發送 DM。操作 1" in first
    assert "http" not in first
    assert balance(service, OWNER).endswith("假餘額：0 點（合成資料，未動真實點數）。")
    assert "操作 1｜合成歌曲 #" in history(service, OWNER) and "已交付（合成）" in history(service, OWNER)
    second = run_exchange(service, OWNER)
    assert "點數不足" in second and "操作 2" not in second
    assert balance(service, OWNER).endswith("假餘額：0 點（合成資料，未動真實點數）。")


def test_the_owner_never_receives_their_own_version_and_the_partner_receives_the_owners() -> None:
    """@description: The owner's own version is excluded; the partner's exchange draws the owner's version and not the owner's own, and the partner keeps their own point until used.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("success", ACCOUNTS)
    database = service._database
    run_exchange(service, OWNER)
    delivered = next(row for row in database.state["recommendations"].values() if row["discord_user_id"] == OWNER)
    owner_song = database.state["songs"]
    assert delivered["song_id"] == next(row["song_id"] for row in owner_song.values() if row["platform_track_id"] == "Synth0002X2")
    assert balance(service, PARTNER).endswith("假餘額：1 點（合成資料，未動真實點數）。")


def test_a_zero_point_participant_is_refused_without_debit() -> None:
    """@description: The configured zero-point participant gets the reviewed insufficient-credits text and no reservation.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("success", ACCOUNTS)
    assert run_exchange(service, ZERO).endswith("點數不足，交換一首歌曲需要 1 Nickel。")
    assert balance(service, ZERO).endswith("假餘額：0 點（合成資料，未動真實點數）。")
    assert service._database.state["recommendations"] == {}


def test_an_unconfigured_user_gets_nothing_and_no_seed_is_reachable() -> None:
    """@description: A user who is not configured cannot exchange, see a balance or see history, and no point is created for them.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("success", ACCOUNTS)
    assert run_exchange(service, STRANGER).endswith(entry.UNCONFIGURED_TEXT)
    assert balance(service, STRANGER) == entry.UNCONFIGURED_TEXT
    assert history(service, STRANGER) == entry.UNCONFIGURED_TEXT
    assert service._database.state["recommendations"] == {}
    assert balance(service, OWNER).endswith("假餘額：1 點（合成資料，未動真實點數）。")


def test_scripted_not_sent_is_refunded_and_visible_in_balance_and_history() -> None:
    """@description: The scripted not-sent outcome refunds the point, and the balance and history show the refund.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("not_sent", ACCOUNTS)
    reply = run_exchange(service, OWNER)
    assert "模擬未送出（注入），已退回 1 點" in reply and "未實際發送 DM" in reply
    assert balance(service, OWNER).endswith("假餘額：1 點（合成資料，未動真實點數）。")
    assert "未送出（注入）｜已退回 1 點" in history(service, OWNER)


def test_scripted_refund_failure_is_pending_and_never_reported_as_returned() -> None:
    """@description: A failed refund leaves the point spent and the history says the refund is pending.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("refund_fail", ACCOUNTS)
    reply = run_exchange(service, OWNER)
    assert "退款待處理" in reply and "已退回" not in reply
    assert balance(service, OWNER).endswith("假餘額：0 點（合成資料，未動真實點數）。")
    assert "退款待處理" in history(service, OWNER)


def test_scripted_refund_failure_is_injected_once_so_the_next_request_retries_and_refunds() -> None:
    """@description: The refund failure fires only on the first request; the next request retries the pending refund and returns the point.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("refund_fail", ACCOUNTS)
    run_exchange(service, OWNER)
    assert balance(service, OWNER).endswith("假餘額：0 點（合成資料，未動真實點數）。")
    run_exchange(service, OWNER)
    assert balance(service, OWNER).endswith("假餘額：1 點（合成資料，未動真實點數）。")
    assert "已退回 1 點" in history(service, OWNER) and "退款待處理" not in history(service, OWNER)


def test_scripted_uncertain_outcome_keeps_its_code_and_never_refunds() -> None:
    """@description: The scripted uncertain outcome shows its operation code, keeps the debit, and never refunds or resends.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("uncertain", ACCOUNTS)
    reply = run_exchange(service, OWNER)
    assert "模擬結果未知（注入）" in reply and "操作 1" in reply and "不退款、不重送" in reply
    assert balance(service, OWNER).endswith("假餘額：0 點（合成資料，未動真實點數）。")
    assert "結果未知（注入，保留）" in history(service, OWNER)


def test_the_scripted_mode_is_fixed_at_startup_and_the_exchange_handler_takes_no_mode() -> None:
    """@description: The handler has no mode parameter, so a Discord user cannot change the scripted outcome.
    @param: none
    @return: None.
    """
    assert list(inspect.signature(CommandHandlers.exchange).parameters) == ["self", "interaction"]


def test_participant_identifiers_must_be_positive_distinct_integers() -> None:
    """@description: Duplicate or non-positive participant identities are refused before any seed is written.
    @param: none
    @return: None.
    """
    with pytest.raises(ValueError):
        SyntheticAccounts(owner=OWNER, partner=OWNER, zero=None)
    with pytest.raises(ValueError):
        SyntheticAccounts(owner=0, partner=None, zero=None)


def test_optional_environment_participants_are_absent_unless_set_and_strictly_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Optional participants are None when unset, and malformed values are refused.
    @param monkeypatch: Controls only the two synthetic environment keys for this test.
    @return: None.
    """
    monkeypatch.delenv(entry.PARTNER_ENV, raising=False)
    monkeypatch.delenv(entry.ZERO_ENV, raising=False)
    assert entry.accounts_from_environment(OWNER) == SyntheticAccounts(owner=OWNER, partner=None, zero=None)
    monkeypatch.setenv(entry.PARTNER_ENV, "0900")
    with pytest.raises(ValueError):
        entry.accounts_from_environment(OWNER)
    monkeypatch.setenv(entry.PARTNER_ENV, "+900000000000000002")
    with pytest.raises(ValueError):
        entry.accounts_from_environment(OWNER)


def test_live_dm_is_refused_explicitly_in_every_mode() -> None:
    """@description: The live DM switch is refused in this round, so no entry point looks as if it could send real messages.
    @param: none
    @return: None.
    """
    with pytest.raises(RuntimeError, match="not part of this round"):
        entry.validate_arguments(entry.argument_parser().parse_args(["listen", "--authorize-gateway", "--authorize-live-dm"]))
    with pytest.raises(RuntimeError, match="not part of this round"):
        entry.validate_arguments(entry.argument_parser().parse_args(["scenario", "--authorize-live-dm"]))


def test_listen_requires_the_gateway_switch_and_scenario_refuses_it() -> None:
    """@description: Listen without its gateway switch is refused, and scenario mode never accepts it.
    @param: none
    @return: None.
    """
    with pytest.raises(RuntimeError, match="requires --authorize-gateway"):
        entry.validate_arguments(entry.argument_parser().parse_args(["listen"]))
    with pytest.raises(RuntimeError, match="never connects"):
        entry.validate_arguments(entry.argument_parser().parse_args(["scenario", "--authorize-gateway"]))


def test_the_guarded_client_carries_the_owner_group_and_the_labelled_exchange_route() -> None:
    """@description: The listener client holds the Owner group guild-scoped and the exchange command globally, and its exchange route is the labelled flow.
    @param: none
    @return: None.
    """
    client = entry.build_exchange_client(settings(), OWNER, "success", ACCOUNTS)
    guild_names = {command.name for command in client.tree.get_commands(guild=discord.Object(id=111))}
    global_names = {command.name for command in client.tree.get_commands()}
    assert "owner" in guild_names and "exchange" in global_names
    assert client.handlers.service.synthetic_exchange_enabled is True


def test_run_exchange_listen_uses_the_shared_owner_listener_with_the_configured_participants(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: The listen path reuses the reviewed Owner listener with the gateway authorization and the configured participants, and never connects in tests.
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
    monkeypatch.setenv(entry.PARTNER_ENV, str(PARTNER))
    monkeypatch.delenv(entry.ZERO_ENV, raising=False)
    arguments = entry.argument_parser().parse_args(["listen", "--authorize-gateway", "--inject", "not_sent"])
    summary = asyncio.run(entry.run_exchange_listen(arguments, settings=settings(), owner_id=OWNER))
    assert seen["authorized"] is True and seen["targets"] is not None
    assert summary["exchange_mode"] == "not_sent" and summary["listener_stopped"] is True
    assert balance(seen["client"].handlers.service, PARTNER).endswith("假餘額：1 點（合成資料，未動真實點數）。")


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
    assert output.startswith(entry.SYNTHETIC_NOTICE) and "owner_with_point" in output


def test_a_retry_of_the_same_interaction_is_a_replay_with_no_second_debit() -> None:
    """@description: Discord retrying the same interaction reuses its request key, so the original result is returned and no second point is spent.
    @param: none
    @return: None.
    """
    service = entry.build_exchange_service("success", ACCOUNTS)
    first, retry = interaction(OWNER), interaction(OWNER)
    retry.id = first.id  # [same Discord interaction]: The retry carries the identical identifier.
    asyncio.run(CommandHandlers(service).exchange(first))
    asyncio.run(CommandHandlers(service).exchange(retry))
    assert "操作 1" in retry.followup.send.await_args.args[0]
    assert balance(service, OWNER).endswith("假餘額：0 點（合成資料，未動真實點數）。")
    assert sum(row["reason"] == "exchange_debit" for row in service._database.state["credit_ledger"].values()) == 1
