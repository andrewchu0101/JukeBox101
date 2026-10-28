"""Verify the Owner transport acceptance client: guild-only Owner group, guarded tree admission, synthetic service and owner-only sync.

Synthetic environment values, fake interactions, mocked Discord REST calls and an in-memory fake database only; no login,
Gateway, remote synchronization or real database connection is made.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.acceptance_config import load_development_guild_settings
from bot.command_contract import verify_remote_payloads
from bot.guild_acceptance import AcceptanceError
from tests.owner_transport_acceptance import (
    argument_parser,
    build_synthetic_owner_client,
    listen_owner_transport,
    owner_group_contract,
    owner_guild_sync,
    inspect_remote_commands,
    run_owner_transport,
    sync_owner_group,
    validate_transport_mode,
)
from tests.test_discord_owner_review import FakeInteraction, OWNER_ID, _run

SYNTHETIC_GUILD_ID = 2002
SYNTHETIC_APPLICATION_ID = 5005
OTHER_GUILD_ID = 3003
OTHER_APPLICATION_ID = 6006


@pytest.fixture
def settings(monkeypatch):
    """@description: Provide synthetic, secret-safe development targets without any database variable.
    @param monkeypatch: Environment isolation facility.
    @return: Explicit synthetic development guild settings.
    """
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-token-secret")
    monkeypatch.setenv("DISCORD_DEV_GUILD_ID", str(SYNTHETIC_GUILD_ID))
    monkeypatch.setenv("DISCORD_EXPECTED_APPLICATION_ID", str(SYNTHETIC_APPLICATION_ID))
    return load_development_guild_settings()


@pytest.fixture
def client(settings):
    """@description: Build the transport acceptance client that owns a synthetic in-memory Owner service.
    @param settings: Synthetic development targets.
    @return: Client with the guarded command tree and the Owner group for the private guild.
    """
    return build_synthetic_owner_client(settings, OWNER_ID)


def guarded_interaction(user_id: int, guild_id: int, application_id: int) -> FakeInteraction:
    """@description: Build a fake application-command interaction carrying the fields the listener admission reads.
    @param user_id: Discord user snowflake of the caller.
    @param guild_id: Guild where the interaction happened.
    @param application_id: Application that received the interaction.
    @return: Fake interaction with the admission fields set.
    """
    interaction = FakeInteraction(user_id, guild_id)
    interaction.application_id = application_id
    interaction.type = discord.InteractionType.application_command
    interaction.response.is_done = lambda: False  # [listener rejection]: The real response reports whether a reply already happened.
    return interaction


def test_owner_group_is_registered_for_the_private_guild_only(client):
    """@description: The synthetic client exposes the Owner group in the private guild and never globally.
    @param client: Transport acceptance client.
    @return: None.
    """
    assert client.tree.get_command("owner", guild=discord.Object(id=SYNTHETIC_GUILD_ID)) is not None
    assert client.tree.get_command("owner") is None


def test_tree_admission_refuses_a_foreign_guild_or_application_and_admits_the_private_target(client):
    """@description: Real tree admission refuses foreign guild or application dispatch and admits only the configured targets.
    @param client: Transport acceptance client.
    @return: None.
    """
    foreign_guild = guarded_interaction(OWNER_ID, OTHER_GUILD_ID, SYNTHETIC_APPLICATION_ID)
    foreign_application = guarded_interaction(OWNER_ID, SYNTHETIC_GUILD_ID, OTHER_APPLICATION_ID)
    target = guarded_interaction(OWNER_ID, SYNTHETIC_GUILD_ID, SYNTHETIC_APPLICATION_ID)
    assert _run(client.tree.interaction_check(foreign_guild)) is False
    assert _run(client.tree.interaction_check(foreign_application)) is False
    assert _run(client.tree.interaction_check(target)) is True


def test_owner_list_reaches_the_synthetic_service_only_for_the_owner_in_the_private_guild(client):
    """@description: The Owner's list command answers from the synthetic service, and a stranger in the same guild gets only a refusal.
    @param client: Transport acceptance client.
    @return: None.
    """
    group = client.tree.get_command("owner", guild=discord.Object(id=SYNTHETIC_GUILD_ID))
    command = group.get_command("list")
    owner_call = FakeInteraction(OWNER_ID, SYNTHETIC_GUILD_ID)
    stranger_call = FakeInteraction(OWNER_ID + 1, SYNTHETIC_GUILD_ID)
    _run(command.callback(command.binding, owner_call))
    _run(command.callback(command.binding, stranger_call))
    owner_texts = " ".join(content or "" for _method, content, _options in owner_call.replies())
    stranger_texts = " ".join(content or "" for _method, content, _options in stranger_call.replies())
    assert "op-1" in owner_texts
    assert "op-1" not in stranger_texts


def test_owner_contract_is_the_owner_group_alone_and_accepts_its_own_remote_payload(client, settings):
    """@description: The remote contract for the transport client covers the Owner group only and accepts an identical remote payload.
    @param client: Transport acceptance client.
    @param settings: Synthetic development targets.
    @return: None.
    """
    contract = owner_group_contract(client, settings.guild_id)
    assert [command["name"] for command in contract] == ["owner"]
    verify_remote_payloads(contract, [dict(command) for command in contract])


def test_owner_contract_refuses_a_remote_group_with_a_missing_subcommand(client, settings):
    """@description: A remote Owner group that lost one subcommand is refused before any connection.
    @param client: Transport acceptance client.
    @param settings: Synthetic development targets.
    @return: None.
    """
    contract = owner_group_contract(client, settings.guild_id)
    remote = [dict(command, options=command["options"][1:]) for command in contract]
    with pytest.raises(AcceptanceError):
        verify_remote_payloads(contract, remote)


def test_owner_only_sync_publishes_to_the_guild_without_copying_global_commands(client, settings, monkeypatch):
    """@description: Owner-only sync targets the private guild and never copies the five global commands into it.
    @param client: Transport acceptance client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    monkeypatch.setattr(client.tree, "copy_global_to", MagicMock(side_effect=AssertionError("global commands must not be copied")))
    sync = AsyncMock(return_value=[])
    monkeypatch.setattr(client.tree, "sync", sync)
    _run(sync_owner_group(client, settings.guild_id))
    sync.assert_awaited_once()
    assert sync.await_args.kwargs["guild"].id == settings.guild_id


def stub_remote(client, *, remote_guild_commands: list, monkeypatch) -> AsyncMock:
    """@description: Replace the REST reads that identity and emptiness checks use, so no Discord request is made.
    @param client: Transport acceptance client.
    @param remote_guild_commands: Commands the stubbed guild currently reports.
    @param monkeypatch: Attribute replacement facility.
    @return: Mock recording the synchronization call.
    """
    # [login result]: discord.Client exposes application_id as a read-only property, so the class property is replaced for this test.
    monkeypatch.setattr(type(client), "application_id", property(lambda _self: SYNTHETIC_APPLICATION_ID))
    monkeypatch.setattr(client, "application_info", AsyncMock(return_value=SimpleNamespace(id=SYNTHETIC_APPLICATION_ID)))
    monkeypatch.setattr(client, "fetch_guild", AsyncMock(return_value=SimpleNamespace(id=SYNTHETIC_GUILD_ID)))
    monkeypatch.setattr(client.tree, "fetch_commands", AsyncMock(return_value=remote_guild_commands))
    sync = AsyncMock(return_value=[])
    monkeypatch.setattr(client.tree, "sync", sync)
    return sync


def test_owner_sync_refuses_without_explicit_authorization_before_any_request(client, settings, monkeypatch):
    """@description: Owner sync stops before any REST read unless the separate sync authorization is given.
    @param client: Transport acceptance client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    sync = stub_remote(client, remote_guild_commands=[], monkeypatch=monkeypatch)
    with pytest.raises(AcceptanceError):
        _run(owner_guild_sync(client, settings, authorized=False, confirm=lambda prompt: ""))
    sync.assert_not_awaited()


def test_owner_sync_refuses_when_the_guild_already_has_application_commands(client, settings, monkeypatch):
    """@description: Owner sync never overwrites a guild that already has application-owned commands.
    @param client: Transport acceptance client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    sync = stub_remote(client, remote_guild_commands=[SimpleNamespace(name="share")], monkeypatch=monkeypatch)
    with pytest.raises(AcceptanceError):
        _run(owner_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "SYNC"))
    sync.assert_not_awaited()


def test_owner_sync_refuses_a_wrong_typed_confirmation_before_publishing(client, settings, monkeypatch):
    """@description: A confirmation that does not name this application and guild cancels the sync without publishing.
    @param client: Transport acceptance client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    sync = stub_remote(client, remote_guild_commands=[], monkeypatch=monkeypatch)
    with pytest.raises(AcceptanceError):
        _run(owner_guild_sync(client, settings, authorized=True, confirm=lambda prompt: "SYNC wrong"))
    sync.assert_not_awaited()


def test_preflight_mode_reports_the_owner_group_and_the_five_global_commands_without_any_network_or_secret(settings, monkeypatch):
    """@description: The default preflight passes the five-command local check with the Owner group wired and never echoes the token.
    @param settings: Synthetic development targets, which also set the synthetic token.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    monkeypatch.setenv("DISCORD_OWNER_USER_ID", str(OWNER_ID))
    arguments = argument_parser().parse_args([])
    summary = _run(run_owner_transport(arguments))
    assert summary["owner_group"] == ["owner"]
    assert "synthetic-token-secret" not in repr(summary)


def test_listen_owner_transport_requires_explicit_gateway_authorization_before_any_login(client, settings, monkeypatch):
    """@description: The transport listener refuses to log in or connect unless the gateway authorization is given.
    @param client: Transport acceptance client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    login = AsyncMock()
    connect = AsyncMock()
    monkeypatch.setattr(client, "login", login)
    monkeypatch.setattr(client, "connect", connect)
    with pytest.raises(AcceptanceError):
        _run(listen_owner_transport(client, settings, authorized=False))
    login.assert_not_awaited()
    connect.assert_not_awaited()


@pytest.mark.parametrize(
    "arguments",
    [
        ["sync", "--authorize-login", "--authorize-sync", "--authorize-gateway"],
        ["listen", "--authorize-gateway", "--authorize-sync"],
        ["listen", "--authorize-gateway", "--authorize-login"],
        ["preflight", "--authorize-sync"],
    ],
)
def test_a_switch_that_does_not_belong_to_the_mode_is_refused_before_any_network_or_secret_read(arguments, monkeypatch):
    """@description: Each network switch authorizes only its own mode, so a foreign switch is refused instead of silently ignored.
    @param arguments: Command-line arguments mixing a mode with a switch it does not own.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    monkeypatch.setenv("DISCORD_OWNER_USER_ID", str(OWNER_ID))
    with pytest.raises(AcceptanceError):
        validate_transport_mode(argument_parser().parse_args(arguments))


def test_owner_sync_rechecks_the_empty_guild_after_the_typed_confirmation(client, settings, monkeypatch):
    """@description: A remote command created while the operator is typing the confirmation stops the sync before publishing.
    @param client: Transport acceptance client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    remote_commands: list = []
    sync = stub_remote(client, remote_guild_commands=remote_commands, monkeypatch=monkeypatch)

    def confirm_while_another_operator_creates_a_command(prompt: str) -> str:
        """@description: Simulate a concurrent guild command appearing during the confirmation prompt.
        @param prompt: Confirmation prompt text, unused by the fake.
        @return: The correct typed confirmation.
        """
        remote_commands.append(SimpleNamespace(name="share"))
        return f"SYNC {settings.application_id} {settings.guild_id}"

    with pytest.raises(AcceptanceError):
        _run(owner_guild_sync(client, settings, authorized=True, confirm=confirm_while_another_operator_creates_a_command))
    sync.assert_not_awaited()


def test_inspect_mode_requires_only_the_login_switch(monkeypatch):
    """@description: The read-only inspect mode accepts exactly the login authorization and nothing that publishes or connects.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    validate_transport_mode(argument_parser().parse_args(["inspect", "--authorize-login"]))
    with pytest.raises(AcceptanceError):
        validate_transport_mode(argument_parser().parse_args(["inspect"]))
    with pytest.raises(AcceptanceError):
        validate_transport_mode(argument_parser().parse_args(["inspect", "--authorize-login", "--authorize-sync"]))


def test_inspect_lists_global_and_guild_command_names_without_publishing_or_connecting(client, settings, monkeypatch):
    """@description: Inspection reads global and guild command names separately, and never calls sync or connect.
    @param client: Transport acceptance client.
    @param settings: Synthetic development targets.
    @param monkeypatch: Attribute replacement facility.
    @return: None.
    """
    def fake_fetch(guild=None):
        """@description: Return different command sets for the global scope and the guild scope.
        @param guild: Guild object when reading guild commands, otherwise None.
        @return: Awaitable of fake command objects.
        """
        names = ["share"] if guild is None else ["owner", "share"]
        return _async_value([SimpleNamespace(name=name, type=1, to_dict=lambda n=name: {"name": n}) for name in names])

    stub_remote(client, remote_guild_commands=[], monkeypatch=monkeypatch)
    monkeypatch.setattr(client.tree, "fetch_commands", fake_fetch)
    sync = AsyncMock()
    connect = AsyncMock()
    monkeypatch.setattr(client.tree, "sync", sync)
    monkeypatch.setattr(client, "connect", connect)
    summary = _run(inspect_remote_commands(client, settings))
    assert summary["global_commands"] == ["share"]
    assert summary["guild_commands"] == ["owner", "share"]
    assert summary["read_only"] is True
    sync.assert_not_awaited()
    connect.assert_not_awaited()


async def _async_value(value):
    """@description: Wrap one value as an awaitable so fake REST reads match the real coroutine interface.
    @param value: Value to return.
    @return: The value.
    """
    return value
