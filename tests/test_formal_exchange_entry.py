"""Verify the formal /exchange entry wiring offline: the switch is off by default and accepts only explicit values, the synthetic route keeps priority,
the formal route runs the same delivery program without the synthetic label, the interaction replies are deferred and private, and the reply never exposes the link.

The formal route is wired to the production delivery program and a real repository over the offline fake; only the direct-message and reply
adapters are scripted. Nothing connects to MySQL, Discord or the environment files.
"""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot import discord_app
from bot.config import ConfigurationError, FORMAL_EXCHANGE_ENV, load_formal_exchange_enabled
from bot.discord_exchange import DiscordDirectMessageSender, DiscordEphemeralReplier
from bot.exchange_delivery import DeliveryStatus, DmResult
from bot.services import MusicService, ServiceUnavailable, SYNTHETIC_LABEL
from tests.test_exchange_reservation import balance, two_submitters  # noqa: F401  (fixture imported by name)
from tests.test_exchange_synthetic_path import Dm, LINK, Reply, row  # noqa: F401  (offline recorders and helpers)
from tests.test_identity_service_path import world, video_of  # noqa: F401  (world is the pytest fixture behind two_submitters)


def formal_service(repository) -> MusicService:
    """@description: Build the real service with the formal switch enabled over the injected repository, and the synthetic route left off.
    @param repository: Offline repository.
    @return: Service whose formal /exchange route runs the production delivery program.
    """
    return MusicService(MagicMock(), submission_repository=repository, formal_exchange=True)


def run_formal(service, sender, replier, user_id: int, key: str):
    """@description: Run one formal exchange request through the service entry point.
    @param service: Formal-enabled service.
    @param sender: Scripted direct-message adapter.
    @param replier: Recording reply adapter.
    @param user_id: Requester.
    @param key: Request key.
    @return: Flow result.
    """
    return asyncio.run(service.formal_exchange(user_id, key, sender, replier))


# --- configuration: explicit values only, never read from .env ---------------------------------------------------------------


def test_the_formal_switch_is_off_when_the_setting_is_absent() -> None:
    """@description: With no setting at all, the formal route stays closed.
    @return: None.
    """
    assert load_formal_exchange_enabled({}) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_only_explicit_enabling_values_open_the_formal_switch(value) -> None:
    """@description: The documented enabling values, in any letter case and with surrounding spaces, open the switch.
    @param value: Enabling setting text.
    @return: None.
    """
    assert load_formal_exchange_enabled({FORMAL_EXCHANGE_ENV: value}) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_the_documented_disabling_values_keep_the_switch_closed(value) -> None:
    """@description: The documented disabling values keep the switch closed.
    @param value: Disabling setting text.
    @return: None.
    """
    assert load_formal_exchange_enabled({FORMAL_EXCHANGE_ENV: value}) is False


@pytest.mark.parametrize("value", ["", "maybe", "enabled", "y", "2"])
def test_any_other_text_is_refused_and_never_opens_the_switch(value) -> None:
    """@description: Empty or unrecognised text is a configuration error, so a mistyped value can never open the route.
    @param value: Unrecognised setting text.
    @return: None.
    """
    with pytest.raises(ConfigurationError):
        load_formal_exchange_enabled({FORMAL_EXCHANGE_ENV: value})


def test_reading_the_switch_never_loads_a_dotenv_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: The switch is read from the given environment mapping only; the function has no dotenv path and does not touch os.environ when a mapping is supplied.
    @param monkeypatch: Pytest environment facility.
    @return: None.
    """
    monkeypatch.setenv(FORMAL_EXCHANGE_ENV, "1")
    assert load_formal_exchange_enabled({}) is False
    assert os.environ.get(FORMAL_EXCHANGE_ENV) == "1"


# --- service: default off, explicit on, shared program, no label ------------------------------------------------------------


def test_the_formal_route_is_closed_by_default_and_refuses_before_any_work() -> None:
    """@description: A service built without the formal switch refuses the formal entry before any database or DM work.
    @return: None.
    """
    service = MusicService(MagicMock(), submission_repository=MagicMock())
    assert service.formal_exchange_enabled is False
    with pytest.raises(ServiceUnavailable):
        asyncio.run(service.formal_exchange(101, "discord:1", Dm(DmResult.delivered(1)), Reply()))


def test_the_formal_switch_needs_a_repository_and_only_an_exact_true(two_submitters) -> None:
    """@description: Without a repository the formal route stays closed, and a non-boolean truthy value does not open it.
    @param two_submitters: Offline world.
    @return: None.
    """
    _, repository, _ = two_submitters
    assert MusicService(MagicMock(), formal_exchange=True).formal_exchange_enabled is False
    service = MusicService(MagicMock(), submission_repository=repository, formal_exchange=True)
    assert service.formal_exchange_enabled is True
    service._formal_exchange = "false"  # [strict]: Only an exact boolean True opens the route.
    assert service.formal_exchange_enabled is False


def test_a_formal_success_is_unlabelled_delivers_the_link_only_by_direct_message(two_submitters) -> None:
    """@description: The formal route uses the production delivery program: one direct message carries the link, the reply carries neither the link nor the synthetic label, and one point is debited.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender, replier = Dm(DmResult.delivered(8101)), Reply()
    flow = run_formal(formal_service(repository), sender, replier, 101, "discord:formal-1")
    assert flow.delivery_status is DeliveryStatus.DELIVERED and flow.refund_status is None
    assert len(sender.attempts) == 1 and LINK + video_of(2).video_id in sender.attempts[0][1]
    assert SYNTHETIC_LABEL not in sender.attempts[0][1]
    assert replier.sent and all(SYNTHETIC_LABEL not in text and LINK not in text for text in replier.sent)
    assert flow.reply_text and SYNTHETIC_LABEL not in flow.reply_text and LINK not in flow.reply_text
    assert row(database, flow.recommendation_id)["discord_message_id"] == 8101
    assert balance(database, 101) == 0


def test_the_synthetic_route_keeps_its_label_when_both_routes_are_enabled(two_submitters) -> None:
    """@description: Enabling the formal route does not change the synthetic route: its messages and replies remain labelled.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    _, repository, _ = two_submitters
    service = MusicService(MagicMock(), submission_repository=repository, synthetic_exchange=True, formal_exchange=True)
    sender, replier = Dm(DmResult.delivered(8201)), Reply()
    flow = asyncio.run(service.synthetic_exchange(101, "discord:synthetic-1", sender, replier))
    assert sender.attempts[0][1].startswith(SYNTHETIC_LABEL)
    assert flow.reply_text.startswith(SYNTHETIC_LABEL)


# --- interaction: priority, defer and private replies, closed fallback ------------------------------------------------------


def interaction_for(user_id: int) -> MagicMock:
    """@description: A mock Discord interaction with an awaitable deferral and followup, recording the calls the handler makes.
    @param user_id: Requesting user.
    @return: Mock interaction.
    """
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.id = 42
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def test_with_both_switches_on_the_synthetic_route_is_chosen_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: The handler keeps synthetic priority: with both routes available only the synthetic flow runs.
    @param monkeypatch: Replaces the respond helper to prove the fixed reply is not used.
    @return: None.
    """
    fixed = []

    async def respond(interaction, operation, *args):
        fixed.append(args)

    monkeypatch.setattr(discord_app, "_respond", respond)
    service = MagicMock()
    service.synthetic_exchange_enabled = True
    service.formal_exchange_enabled = True
    service.synthetic_exchange = AsyncMock()
    service.formal_exchange = AsyncMock()
    asyncio.run(discord_app.CommandHandlers(service).exchange(interaction_for(101)))
    service.synthetic_exchange.assert_awaited_once()
    service.formal_exchange.assert_not_awaited()
    assert fixed == []


def test_with_only_the_formal_switch_on_the_handler_defers_privately_and_uses_the_discord_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: With only the formal switch on, the handler defers ephemerally and runs the formal route with the real Discord adapters; the fixed reply is not used.
    @param monkeypatch: Replaces the respond helper to prove the fixed reply is not used.
    @return: None.
    """
    fixed = []

    async def respond(interaction, operation, *args):
        fixed.append(args)

    monkeypatch.setattr(discord_app, "_respond", respond)
    interaction = interaction_for(101)
    service = MagicMock()
    service.synthetic_exchange_enabled = False
    service.formal_exchange_enabled = True
    service.formal_exchange = AsyncMock()
    asyncio.run(discord_app.CommandHandlers(service).exchange(interaction))
    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    args = service.formal_exchange.await_args.args
    assert args[0] == 101 and args[1] == "discord:42"
    assert isinstance(args[2], DiscordDirectMessageSender) and isinstance(args[3], DiscordEphemeralReplier)
    assert fixed == []


@pytest.mark.parametrize("formal_value", [False, None, "true", 1])
def test_with_the_formal_switch_not_exactly_true_the_handler_keeps_the_fixed_reply(monkeypatch: pytest.MonkeyPatch, formal_value) -> None:
    """@description: Only an exact boolean True selects the formal route; any other value, and an absent attribute, keeps the existing fixed reply and debits nothing.
    @param monkeypatch: Replaces the respond helper to observe the fixed path.
    @param formal_value: Attribute value under test.
    @return: None.
    """
    fixed = []

    async def respond(interaction, operation, *args):
        fixed.append(args)

    monkeypatch.setattr(discord_app, "_respond", respond)
    service = MagicMock(spec=["exchange", "synthetic_exchange_enabled", "formal_exchange_enabled", "formal_exchange"])
    service.synthetic_exchange_enabled = False
    service.formal_exchange_enabled = formal_value
    service.formal_exchange = AsyncMock()
    interaction = interaction_for(101)
    asyncio.run(discord_app.CommandHandlers(service).exchange(interaction))
    assert fixed == [(101,)]
    service.formal_exchange.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()


def test_the_absent_attribute_of_an_older_service_keeps_the_fixed_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: A service object without the formal attribute keeps the existing fixed reply.
    @param monkeypatch: Replaces the respond helper to observe the fixed path.
    @return: None.
    """
    fixed = []

    async def respond(interaction, operation, *args):
        fixed.append(args)

    monkeypatch.setattr(discord_app, "_respond", respond)
    service = MagicMock(spec=["exchange"])
    service.exchange = MagicMock()
    asyncio.run(discord_app.CommandHandlers(service).exchange(interaction_for(101)))
    assert fixed == [(101,)]
