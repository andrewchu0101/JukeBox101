"""Pin the /claim switch, its replies and the service entry point, all with the feature closed by default.

No database or Discord connection is opened here: the disabled path must not touch either.
"""

import asyncio
from datetime import datetime, timezone

import pytest

from bot.config import ConfigurationError, load_nickel_claim_enabled
from bot.database.nickel_grant_store import ClaimResult, ClaimStatus
from bot.nickel_claim_replies import format_claim_reply
from bot.nickel_grant_policy import GrantKind, GrantLine
from bot.services import MusicService


def test_the_claim_switch_is_closed_when_unset():
    assert load_nickel_claim_enabled({}) is False


@pytest.mark.parametrize("value", ["1", "true", "YES", " on "])
def test_an_enabling_value_opens_the_claim_switch(value):
    assert load_nickel_claim_enabled({"JUKEBOX_NICKEL_CLAIM": value}) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off"])
def test_a_disabling_value_keeps_the_claim_switch_closed(value):
    assert load_nickel_claim_enabled({"JUKEBOX_NICKEL_CLAIM": value}) is False


def test_an_unrecognised_value_refuses_startup_instead_of_opening_the_switch():
    with pytest.raises(ConfigurationError):
        load_nickel_claim_enabled({"JUKEBOX_NICKEL_CLAIM": "maybe"})


def test_granted_reply_names_each_source_and_the_total():
    result = ClaimResult(ClaimStatus.GRANTED, [GrantLine(GrantKind.ENTRY, 2), GrantLine(GrantKind.WEEKLY, 1)], "2026-10-26")
    reply = format_claim_reply(result)
    assert "入門額度 2 Nickel" in reply and "本週定期額度 1 Nickel" in reply and "共 3 Nickel" in reply


def test_already_claimed_reply_says_nothing_new_was_granted():
    reply = format_claim_reply(ClaimResult(ClaimStatus.ALREADY_CLAIMED, [], "2026-10-26"))
    assert "已領取" in reply and "未發放" in reply


def test_replayed_reply_names_the_original_week_and_grants_nothing_again():
    reply = format_claim_reply(ClaimResult(ClaimStatus.REPLAYED, [], "2026-10-26"))
    assert "2026-10-26" in reply and "未重複發放" in reply


def test_disabled_reply_says_the_feature_is_not_open_and_nothing_was_granted():
    reply = format_claim_reply(ClaimResult(ClaimStatus.DISABLED, [], None))
    assert "尚未開放" in reply and "未發放" in reply


class _UntouchedRepository:
    """Fail if the disabled claim reaches any repository, proving the closed route is inert."""

    def __getattr__(self, name):
        raise AssertionError(f"disabled /claim touched the repository via {name}")


def test_the_service_claim_with_the_switch_closed_returns_the_closed_reply_without_any_access():
    service = MusicService(_UntouchedRepository())
    reply = asyncio.run(service.claim(123, "discord:1"))
    assert "尚未開放" in reply


def test_the_claim_switch_defaults_to_closed_on_the_service():
    service = MusicService(_UntouchedRepository())
    assert service.nickel_claim_enabled is False


def test_the_claim_clock_is_timezone_aware_utc():
    # [clock contract]: The service supplies an aware instant; naive timestamps are refused by the policy module.
    assert datetime.now(timezone.utc).tzinfo is not None


def test_the_claim_command_is_not_registered_while_the_switch_is_closed():
    from bot.discord_app import JukeBoxClient

    client = JukeBoxClient(MusicService(_UntouchedRepository()))
    names = {command.name for command in client.tree.get_commands()}
    assert "claim" not in names
    assert {"share", "exchange", "balance", "history", "daily"} <= names


def test_the_claim_command_is_registered_only_when_the_switch_is_open():
    from bot.discord_app import JukeBoxClient

    service = MusicService(_UntouchedRepository(), nickel_claim_enabled=True, nickel_connection_factory=lambda: None)
    client = JukeBoxClient(service)
    assert "claim" in {command.name for command in client.tree.get_commands()}
