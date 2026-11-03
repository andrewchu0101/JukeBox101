"""Offline contract of the /daily entry wiring (issue #21): command parameters, the reviewed contract JSON, the off-by-default switch, and the service replies.

INTERFACE CONTRACT:
    Slash command: /daily mode:(on|off) [hour:int 0..23]. `mode` stays the first parameter with the same two choices; `hour` is optional.
    bot/development_command_contract.json: the "daily" command lists options [mode, hour], hour = {type 4 (integer), required false, min_value 0, max_value 23}.
    bot/discord_app.py CommandHandlers.daily(interaction, mode, hour=None):
        hour omitted -> service.daily(user_id, enabled)            (unchanged call shape, so the existing callback tests stay valid)
        hour given   -> service.daily(user_id, enabled, hour)
    bot/config.py: FORMAL_DAILY_ENV = "JUKEBOX_FORMAL_DAILY"; load_formal_daily_enabled(environ=None) -> bool, same value rules as the /exchange switch
        (absent -> False; 1/true/yes/on -> True; 0/false/no/off -> False; anything else -> ConfigurationError).
    bot/services.py: MusicService(..., formal_daily: bool = False); property formal_daily_enabled (exact True and a submission repository).
        daily(user_id, enabled, hour=None) -> str
            switch off -> the existing "尚未開放" reply, no repository call.
            switch on, invalid hour (text accepted as typed; see parse_daily_hour) -> a reply naming the valid range 0-23, repository NOT called.
            switch on, valid -> repository.save_daily_preference(user_id, enabled=<bool>, delivery_hour=<int>) where `on` without an hour stores 20
                and `off` stores no hour change (delivery_hour=None); the reply is built from the returned preference (enabled, delivery_hour) and says
                the time is Asia/Taipei (台北時間).
    SubmissionRepository.save_daily_preference(discord_user_id, *, enabled: bool | None, delivery_hour: int | None, now=None) -> DailyPreference
        (bot/database/daily_store.py: NamedTuple discord_user_id, enabled, delivery_hour); None means "leave unchanged".
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from discord import app_commands

from bot.command_contract import CONTRACT_PATH, normalize_command_payloads
from bot.config import ConfigurationError, FORMAL_DAILY_ENV, load_formal_daily_enabled
from bot.services import MusicService
from tests.test_discord_app import client_and_service, interaction  # noqa: F401  (pytest fixtures imported by name)

USER = 123  # [caller]: Matches the interaction fixture's user id.


class PreferenceRepository:
    """Record preference saves and answer with the stored result, as the real repository does."""

    def __init__(self) -> None:
        """@description: Start with no saves.
        @return: None.
        """
        self.saves = []  # [save log]: One (user, kwargs) per call.

    def save_daily_preference(self, discord_user_id: int, **fields) -> SimpleNamespace:
        """@description: Remember the call and return the preference the database would hold.
        @param discord_user_id: Caller.
        @param fields: enabled and delivery_hour keywords.
        @return: Stored preference.
        """
        self.saves.append((discord_user_id, fields))
        return SimpleNamespace(discord_user_id=discord_user_id, enabled=fields["enabled"], delivery_hour=fields["delivery_hour"] or 20)


def service_with(repository, formal_daily: bool = True) -> MusicService:
    """@description: Build the real service over the recording repository.
    @param repository: Fake submission repository.
    @param formal_daily: Whether the daily switch is on.
    @return: Service.
    """
    return MusicService(MagicMock(), submission_repository=repository, formal_daily=formal_daily)


def test_the_reviewed_contract_lists_an_optional_hour_between_0_and_23() -> None:
    """@description: The registry baseline the Bot verifies at startup knows the new parameter.
    @return: None.
    """
    commands = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["commands"]
    daily = next(item for item in normalize_command_payloads(commands) if item["name"] == "daily")
    assert [option["name"] for option in daily["options"]] == ["mode", "hour"]
    hour = daily["options"][1]
    assert (hour["type"], hour["required"], hour["min_value"], hour["max_value"]) == (4, False, 0, 23)
    assert [choice["value"] for choice in daily["options"][0]["choices"]] == ["on", "off"]


def test_the_registered_command_has_mode_first_and_an_optional_bounded_hour(client_and_service) -> None:  # noqa: F811
    """@description: The real local registry exposes mode then hour, hour optional with the documented bounds.
    @param client_and_service: Local client and mocked service.
    @return: None.
    """
    client, _service = client_and_service
    mode, hour = client.tree.get_command("daily").parameters
    assert (mode.name, hour.name, hour.required, hour.min_value, hour.max_value) == ("mode", "hour", False, 0, 23)


@pytest.mark.parametrize("hour, expected_arguments", [(None, (USER, True)), (21, (USER, True, 21))])
def test_the_handler_passes_the_hour_only_when_it_was_given(client_and_service, interaction, hour, expected_arguments) -> None:  # noqa: F811
    """@description: The handler keeps the old call shape without an hour and adds it when the user typed one.
    @param client_and_service: Local client and mocked service.
    @param interaction: Mocked Discord transport.
    @param hour: Typed hour or None.
    @param expected_arguments: Arguments the service must receive.
    @return: None.
    """
    client, service = client_and_service
    asyncio.run(client.handlers.daily(interaction, app_commands.Choice(name="on", value="on"), hour))
    service.daily.assert_awaited_once_with(*expected_arguments)


def test_the_daily_switch_is_closed_by_default_and_follows_the_exchange_switch_rules(monkeypatch) -> None:
    """@description: Absent means off; the documented values behave as for /exchange; junk refuses startup.
    @param monkeypatch: Pytest environment facility.
    @return: None.
    """
    assert FORMAL_DAILY_ENV == "JUKEBOX_FORMAL_DAILY" and load_formal_daily_enabled({}) is False
    assert [load_formal_daily_enabled({FORMAL_DAILY_ENV: value}) for value in ("1", " On ", "off", "0", "false")] == [True, True, False, False, False]
    for junk in ("", "maybe", "y", "2"):
        with pytest.raises(ConfigurationError):
            load_formal_daily_enabled({FORMAL_DAILY_ENV: junk})
    monkeypatch.delenv(FORMAL_DAILY_ENV, raising=False)
    assert load_formal_daily_enabled() is False


def test_with_the_switch_off_the_service_saves_nothing_and_says_it_is_not_open() -> None:
    """@description: Merging the code does not open /daily: the old reply, no repository call.
    @return: None.
    """
    repository = PreferenceRepository()
    reply = asyncio.run(service_with(repository, formal_daily=False).daily(USER, True, 21))
    assert "尚未開放" in reply and repository.saves == []


@pytest.mark.parametrize("enabled, hour, expected_save", [
    (True, None, {"enabled": True, "delivery_hour": 20}),
    (True, 9, {"enabled": True, "delivery_hour": 9}),
    (True, "07", {"enabled": True, "delivery_hour": 7}),
    (False, None, {"enabled": False, "delivery_hour": None}),
])
def test_a_valid_request_is_saved_and_the_reply_states_the_taipei_time(enabled, hour, expected_save) -> None:
    """@description: Defaults to 20 when turning on without an hour; turning off leaves the hour alone; the reply reads from the stored preference.
    @param enabled: Requested subscription state.
    @param hour: Typed hour or None.
    @param expected_save: Keywords the repository must receive.
    @return: None.
    """
    repository = PreferenceRepository()
    reply = asyncio.run(service_with(repository).daily(USER, enabled, hour))
    assert repository.saves == [(USER, expected_save)] and (not enabled or "台北" in reply)


@pytest.mark.parametrize("hour", [24, -1, "20:30", "abc", "", 2.5, 99])
def test_an_invalid_hour_changes_nothing_and_explains_the_range(hour) -> None:
    """@description: Bad input never reaches the repository, so the previous setting is kept, and the reply names the allowed range.
    @param hour: Rejected input.
    @return: None.
    """
    repository = PreferenceRepository()
    reply = asyncio.run(service_with(repository).daily(USER, True, hour))
    assert repository.saves == [] and "0" in reply and "23" in reply
