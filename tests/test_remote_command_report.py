"""Verify the read-only remote command report offline: it names missing and unapproved commands and differing options, takes the approved
options from the locally registered tree, and never calls a write operation.

Command objects are stand-ins; the local tree is built offline without a login. No Discord request, network or environment file is involved.
"""

import asyncio
from types import SimpleNamespace

import pytest

from bot.discord_app import JukeBoxClient
from bot.guild_acceptance import EXPECTED_COMMAND_NAMES, approved_option_names, read_only_remote_report, remote_command_report

APPROVED = {"balance": (), "daily": ("mode",), "exchange": (), "history": (), "share": ("url",)}


def remote(name: str, *options: str):
    """@description: Stand in for one remote command with its option names.
    @param name: Command name.
    @param options: Option names.
    @return: Object exposing name and options.
    """
    return SimpleNamespace(name=name, options=[SimpleNamespace(name=option) for option in options])


def test_the_approved_names_and_options_come_from_the_local_tree_and_match_the_expected_names() -> None:
    """@description: The local registration, built offline, has exactly the approved five names and the expected options.
    @param: none
    @return: None.
    """
    approved = approved_option_names(JukeBoxClient(SimpleNamespace()))
    assert tuple(sorted(approved)) == tuple(sorted(EXPECTED_COMMAND_NAMES))
    assert approved["share"] == ("url",) and approved["daily"] == ("mode",)


def test_matching_names_and_options_report_no_difference() -> None:
    """@description: A remote guild that holds exactly the approved commands and options reports nothing to change.
    @param: none
    @return: None.
    """
    commands = [remote("balance"), remote("daily", "mode"), remote("exchange"), remote("history"), remote("share", "url")]
    assert remote_command_report(commands, APPROVED) == {"missing_remotely": [], "not_approved_remotely": [], "option_differences": {}}


def test_missing_unapproved_and_differing_options_are_all_named_and_the_input_is_untouched() -> None:
    """@description: Missing and unapproved names and option differences are reported in sorted order without mutating the fetched objects.
    @param: none
    @return: None.
    """
    commands = [remote("share"), remote("owner"), remote("balance"), remote("daily")]
    before = [(command.name, tuple(option.name for option in command.options)) for command in commands]
    report = remote_command_report(commands, APPROVED)
    assert report == {
        "missing_remotely": ["exchange", "history"],
        "not_approved_remotely": ["owner"],
        "option_differences": {"daily": {"remote": (), "approved": ("mode",)}, "share": {"remote": (), "approved": ("url",)}},
    }
    assert [(command.name, tuple(option.name for option in command.options)) for command in commands] == before


class ReadOnlyTree:
    """@description: Command tree stand-in that answers fetch_commands and fails loudly on any write.
    @param remote_commands: Commands the fetch returns.
    @return: Tree stand-in.
    """

    def __init__(self, remote_commands: list) -> None:
        """@description: Store the commands the fetch will return and record calls.
        @param self: Tree stand-in.
        @param remote_commands: Commands the fetch returns.
        @return: None.
        """
        self.remote_commands = remote_commands
        self.fetches = 0

    async def fetch_commands(self, *, guild) -> list:
        """@description: Record one read-only fetch and return the stored commands.
        @param self: Tree stand-in.
        @param guild: Guild object the fetch names.
        @return: Stored remote commands.
        """
        self.fetches += 1
        return self.remote_commands

    async def sync(self, *args, **kwargs) -> None:
        """@description: Any synchronization is a write and must never run during the report.
        @param self: Tree stand-in.
        @return: Never returns.
        """
        raise AssertionError("the read-only report must never synchronize commands")


def test_the_read_only_report_fetches_once_and_never_synchronizes(monkeypatch) -> None:
    """@description: The remote check reads the guild once, compares against the local tree, and performs no write call.
    @param monkeypatch: Pytest monkeypatch fixture.
    @return: None.
    """
    tree = ReadOnlyTree([remote("share", "url"), remote("owner")])
    client = SimpleNamespace(tree=tree)
    monkeypatch.setattr("bot.guild_acceptance.approved_option_names", lambda _client: APPROVED)
    report = asyncio.run(read_only_remote_report(client, 1559749813011087392))
    assert report["not_approved_remotely"] == ["owner"] and tree.fetches == 1
