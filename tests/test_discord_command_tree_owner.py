"""Verify the owner group is registered only when wired, scoped to the private guild, and dispatches through the real tree.

Local command-tree objects and fake interactions only; no login, no Gateway, no remote sync.
"""

from datetime import datetime, timezone

import discord

from bot.discord_app import JukeBoxClient, OwnerReviewWiring
from tests.test_discord_owner_review import FakeInteraction, FakeOwnerReviewService, _run

OWNER_ID = 777
GUILD_ID = 4242
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def client(wiring=None) -> JukeBoxClient:
    """@description: Build the real client with a placeholder application service and optional owner wiring.
    @param wiring: Optional owner review wiring.
    @return: Client whose local command tree is inspected.
    """
    return JukeBoxClient(object(), owner_review=wiring)


def wiring(service: FakeOwnerReviewService) -> OwnerReviewWiring:
    """@description: Provide a fake service through the real wiring dataclass.
    @param service: Fake review service.
    @return: Wiring with the configured Owner and private guild.
    """
    return OwnerReviewWiring(lambda: service, OWNER_ID, GUILD_ID, lambda: NOW)


def test_owner_group_is_absent_when_not_wired_so_existing_commands_are_unchanged():
    """@description: Without owner wiring the client registers no owner command.
    @param: none
    @return: None.
    """
    tree = client().tree
    assert tree.get_command("owner", guild=discord.Object(id=GUILD_ID)) is None


def test_owner_group_is_registered_only_for_the_private_guild_and_not_globally():
    """@description: When wired, the owner group exists for the private guild and is absent from global commands.
    @param: none
    @return: None.
    """
    tree = client(wiring(FakeOwnerReviewService())).tree
    assert tree.get_command("owner", guild=discord.Object(id=GUILD_ID)) is not None
    assert tree.get_command("owner") is None


def test_dispatch_through_the_tree_refuses_a_wrong_guild_before_any_service_call():
    """@description: Dispatching a subcommand from the tree enforces the guild gate before the service is touched.
    @param: none
    @return: None.
    """
    service = FakeOwnerReviewService()
    tree = client(wiring(service)).tree
    group = tree.get_command("owner", guild=discord.Object(id=GUILD_ID))
    command = group.get_command("list")
    interaction = FakeInteraction(user_id=OWNER_ID, guild_id=GUILD_ID + 1)
    _run(command.callback(command.binding, interaction))
    assert service.calls == []


def test_dispatch_through_the_tree_runs_list_for_the_owner_in_the_private_guild():
    """@description: The owner in the private guild reaches the real service through the registered tree command.
    @param: none
    @return: None.
    """
    service = FakeOwnerReviewService()
    tree = client(wiring(service)).tree
    group = tree.get_command("owner", guild=discord.Object(id=GUILD_ID))
    command = group.get_command("list")
    interaction = FakeInteraction(user_id=OWNER_ID, guild_id=GUILD_ID)
    _run(command.callback(command.binding, interaction))
    assert any(call[0] == "list_pending" for call in service.calls)
