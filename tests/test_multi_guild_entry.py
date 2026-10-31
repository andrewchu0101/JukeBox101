"""Offline tests for the v7 multi-guild entry: admission, command definitions, Owner scoping, invite link and command sync.

Discord is never contacted: interactions are synthetic objects and every client used for sync is a recording fake.
Real "invite to a brand-new guild and use the commands" remains a manual acceptance step.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import discord
import pytest

from bot import command_sync
from bot.command_contract import verify_local_contract
from bot.discord_app import CommandHandlers, JukeBoxClient
from bot.interaction_policy import DIRECT_MESSAGE_REFUSAL, UNVERIFIED_REFUSAL, MultiGuildInteractionGuard
from bot.invite_link import build_invite_url, invite_summary
from bot.production_config import MAX_STORABLE_SNOWFLAKE

APPLICATION_ID = 900000000000000001  # [synthetic application]
OWNER_ID = 910000000000000002  # [synthetic Owner]
GUILD_A = 920000000000000003  # [first installed guild]
GUILD_B = 930000000000000004  # [second installed guild, never configured anywhere]
MEMBER_ID = 940000000000000005  # [ordinary member]: Holds no permission at all.


class _Response:
    """Record ephemeral refusals the guard sends."""

    def __init__(self) -> None:
        """@description: Start with no reply sent.
        @param self: Fake interaction response.
        @return: None.
        """
        self.sent = []  # [replies]: (text, ephemeral, allowed_mentions)

    def is_done(self) -> bool:
        """@description: Report whether a reply was already sent.
        @param self: Fake interaction response.
        @return: True after one reply.
        """
        return bool(self.sent)

    async def send_message(self, text, *, ephemeral, allowed_mentions):
        """@description: Record the refusal.
        @param self: Fake interaction response.
        @param text: Reply text.
        @param ephemeral: Privacy flag.
        @param allowed_mentions: Mention policy.
        @return: None.
        """
        self.sent.append((text, ephemeral, allowed_mentions))


def _interaction(guild_id=GUILD_A, application_id=APPLICATION_ID, user_id=MEMBER_ID,
                 interaction_type=discord.InteractionType.application_command):
    """@description: Build a synthetic interaction with only the fields the guard reads.
    @param guild_id: Guild of the interaction, None for a DM.
    @param application_id: Application the interaction targets.
    @param user_id: Invoking user.
    @param interaction_type: Interaction kind.
    @return: Synthetic interaction.
    """
    member_permissions = discord.Permissions.none()  # [ordinary member]: No role permission; the guard must not care.
    return SimpleNamespace(type=interaction_type, application_id=application_id, guild_id=guild_id,
                           user=SimpleNamespace(id=user_id, guild_permissions=member_permissions), response=_Response())


@pytest.mark.parametrize("guild_id", [GUILD_A, GUILD_B, 1])
def test_any_installed_guild_and_an_ordinary_member_are_admitted(guild_id):
    """@description: No guild allowlist exists; any guild's ordinary member passes admission without a reply.
    @param guild_id: Guild the command came from.
    @return: None.
    """
    guard = MultiGuildInteractionGuard(APPLICATION_ID)
    interaction = _interaction(guild_id=guild_id)
    assert asyncio.run(guard(interaction)) is True
    assert interaction.response.sent == []


@pytest.mark.parametrize(("interaction", "expected"), [
    (_interaction(guild_id=None), DIRECT_MESSAGE_REFUSAL),
    (_interaction(application_id=APPLICATION_ID + 1), UNVERIFIED_REFUSAL),
    (_interaction(interaction_type=discord.InteractionType.component), UNVERIFIED_REFUSAL),
    (_interaction(user_id=MAX_STORABLE_SNOWFLAKE + 1), UNVERIFIED_REFUSAL),
    (_interaction(guild_id=MAX_STORABLE_SNOWFLAKE + 1), UNVERIFIED_REFUSAL),
])
def test_dm_wrong_application_and_unstorable_ids_are_refused_privately(interaction, expected):
    """@description: Refusals are ephemeral, mention nobody and carry no identifier or configuration.
    @param interaction: Interaction to refuse.
    @param expected: Fixed refusal text.
    @return: None.
    """
    guard = MultiGuildInteractionGuard(APPLICATION_ID)
    assert asyncio.run(guard(interaction)) is False
    [(text, ephemeral, mentions)] = interaction.response.sent
    assert text == expected and ephemeral is True
    assert mentions.everyone is False and mentions.users is False and mentions.roles is False
    assert str(GUILD_A) not in text and str(APPLICATION_ID) not in text


def test_refused_interactions_never_reach_business_callbacks():
    """@description: A DM is stopped at the tree check, so no service method runs.
    @return: None.
    """
    service = MagicMock(nickel_claim_enabled=False)
    client = JukeBoxClient(service, interaction_guard=MultiGuildInteractionGuard(APPLICATION_ID))
    assert asyncio.run(client.tree.interaction_check(_interaction(guild_id=None))) is False
    assert service.method_calls == []


def test_same_user_is_the_same_account_in_every_guild():
    """@description: Handlers pass only the Discord user ID to services, so balance and history are shared across guilds.
    @return: None.
    """
    service = MagicMock()
    service.balance = AsyncMock(return_value="ok")
    handlers = CommandHandlers(service)
    for guild_id in (GUILD_A, GUILD_B):
        interaction = MagicMock(guild_id=guild_id, user=SimpleNamespace(id=MEMBER_ID))
        interaction.response.defer = AsyncMock()
        interaction.edit_original_response = AsyncMock()
        asyncio.run(handlers.balance(interaction))
    assert [call.args for call in service.balance.await_args_list] == [(MEMBER_ID,), (MEMBER_ID,)]


def test_public_commands_are_global_guild_only_and_open_to_every_member():
    """@description: Every public command is global (no guild_ids), usable only in guilds, installable only to guilds, unrestricted for members.
    @return: None.
    """
    client = JukeBoxClient(MagicMock(nickel_claim_enabled=False))
    payloads = [command.to_dict(client.tree) for command in client.tree.get_commands()]
    assert {payload["name"] for payload in payloads} == {"share", "history", "balance", "exchange", "daily"}
    for payload in payloads:
        assert payload["contexts"] == [0] and payload["integration_types"] == [0]
        assert payload["default_member_permissions"] is None
    assert all(not getattr(command, "guild_ids", None) for command in client.tree.get_commands())
    verify_local_contract(client.tree)


def test_owner_group_is_only_registered_in_the_configured_owner_guild():
    """@description: Owner commands never become global; without an Owner guild they are not registered at all.
    @return: None.
    """
    from bot.discord_app import OwnerReviewWiring
    without_owner = JukeBoxClient(MagicMock(nickel_claim_enabled=False))
    assert "owner" not in {command.name for command in without_owner.tree.get_commands()}
    wiring = OwnerReviewWiring(lambda: MagicMock(), OWNER_ID, GUILD_A, MagicMock())
    with_owner = JukeBoxClient(MagicMock(nickel_claim_enabled=False), owner_review=wiring)
    assert "owner" not in {command.name for command in with_owner.tree.get_commands()}  # [not global]
    assert "owner" in {command.name for command in with_owner.tree.get_commands(guild=discord.Object(id=GUILD_A))}
    assert "owner" not in {command.name for command in with_owner.tree.get_commands(guild=discord.Object(id=GUILD_B))}


@pytest.fixture
def production_environment(database_settings, monkeypatch):
    """@description: Provide every production setting with synthetic values and block all network access.
    @param database_settings: Synthetic database environment from conftest.
    @param monkeypatch: Environment isolation.
    @return: Recorder of Client.run calls.
    """
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-token")
    monkeypatch.setenv("DISCORD_EXPECTED_APPLICATION_ID", str(APPLICATION_ID))
    monkeypatch.setenv("DISCORD_OWNER_USER_ID", str(OWNER_ID))
    for name in ("DISCORD_DEV_GUILD_ID", "DISCORD_OWNER_GUILD_ID", "JUKEBOX_FORMAL_EXCHANGE", "JUKEBOX_NICKEL_CLAIM"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("psycopg.connect", MagicMock(side_effect=AssertionError("no database at startup")))
    run = MagicMock()
    monkeypatch.setattr(discord.Client, "run", run)
    return run


def test_production_entry_starts_without_a_development_guild_and_uses_the_multi_guild_guard(production_environment, monkeypatch):
    """@description: The entry needs only application, Owner, token and database settings; no guild ID is required.
    @param production_environment: Synthetic settings with network blocked.
    @param monkeypatch: Capture facility.
    @return: None.
    """
    from bot import __main__ as entry
    captured = {}
    monkeypatch.setattr(entry, "JukeBoxClient", lambda service, **kwargs: captured.setdefault("client", JukeBoxClient(service, **kwargs)))
    entry.main()
    production_environment.assert_called_once()
    assert production_environment.call_args.args == ("synthetic-token",)  # [login only]: The token goes to Client.run and nowhere else.
    guard = captured["client"].tree.interaction_guard._guard
    assert isinstance(guard, MultiGuildInteractionGuard) and guard.application_id == APPLICATION_ID
    assert "owner" not in {command.name for command in captured["client"].tree.get_commands()}


def test_development_guild_setting_is_ignored_by_production(production_environment, monkeypatch, caplog):
    """@description: A leftover DISCORD_DEV_GUILD_ID neither restricts admission nor stops startup.
    @param production_environment: Synthetic settings with network blocked.
    @param monkeypatch: Environment isolation.
    @param caplog: Log capture.
    @return: None.
    """
    from bot import __main__ as entry
    monkeypatch.setenv("DISCORD_DEV_GUILD_ID", str(GUILD_A))
    captured = {}
    monkeypatch.setattr(entry, "JukeBoxClient", lambda service, **kwargs: captured.setdefault("client", JukeBoxClient(service, **kwargs)))
    entry.main()
    guard = captured["client"].tree.interaction_guard._guard
    assert asyncio.run(guard(_interaction(guild_id=GUILD_B))) is True
    assert "ignored" in caplog.text


def test_invite_link_is_reproducible_minimal_and_token_free():
    """@description: The server-install link asks for bot + applications.commands, permission 0 and guild install only.
    @return: None.
    """
    url = build_invite_url(APPLICATION_ID)
    assert url == build_invite_url(APPLICATION_ID)
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert (parts.scheme, parts.netloc, parts.path) == ("https", "discord.com", "/oauth2/authorize")
    assert query == {"client_id": [str(APPLICATION_ID)], "permissions": ["0"], "integration_type": ["0"],
                     "scope": ["bot applications.commands"]}
    summary = invite_summary(APPLICATION_ID)
    assert summary["administrator_requested"] is False and summary["contains_token"] is False
    assert any("Public Bot" in step for step in summary["owner_portal_steps"])
    with pytest.raises(Exception):
        build_invite_url(0)


def test_preview_is_offline_stable_and_reports_a_diff(tmp_path, monkeypatch):
    """@description: Preview needs no token, yields a stable digest and shows added/removed/changed commands.
    @param tmp_path: Temporary directory for a saved remote export.
    @param monkeypatch: Environment isolation.
    @return: None.
    """
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("JUKEBOX_NICKEL_CLAIM", raising=False)
    local = command_sync.local_payloads()
    remote = [payload for payload in local if payload["name"] != "daily"] + [{"name": "legacy", "type": 1, "options": []}]
    remote[0] = dict(remote[0], contexts=[0, 1])
    export = tmp_path / "remote.json"
    export.write_text(json.dumps(remote), encoding="utf-8")
    summary = command_sync.preview(export)
    assert summary["local_digest"] == command_sync.contract_digest(command_sync.local_payloads())
    assert summary["diff"] == {"added": ["daily"], "removed": ["legacy"], "changed": [remote[0]["name"]]}


@pytest.mark.parametrize("argv", [["apply"], ["apply", "--i-understand-this-calls-discord"],
                                  ["apply", "--i-understand-this-calls-discord", "--confirm-overwrite-remote-commands"],
                                  ["fetch"], ["fetch", "--i-understand-this-calls-discord"],
                                  ["preview", "--target", "dev-guild"]])
def test_remote_modes_require_explicit_opt_in(argv):
    """@description: Any remote read or write without all confirmations stops at argument parsing.
    @param argv: Incomplete command line.
    @return: None.
    """
    with pytest.raises(SystemExit):
        command_sync.parse_arguments(argv)


def _apply_arguments(digest: str):
    """@description: Build fully confirmed apply arguments.
    @param digest: Expected digest.
    @return: Parsed arguments.
    """
    return command_sync.parse_arguments(["apply", "--i-understand-this-calls-discord", "--confirm-overwrite-remote-commands",
                                         "--expected-digest", digest])


def _fake_client(application_id: int = APPLICATION_ID):
    """@description: Build a recording client for remote sessions; nothing reaches Discord.
    @param application_id: Application the fake token belongs to.
    @return: Fake client.
    """
    client = MagicMock()
    client.login = AsyncMock()
    client.close = AsyncMock()
    client.application_info = AsyncMock(return_value=SimpleNamespace(id=application_id))
    client.tree.sync = AsyncMock(return_value=[SimpleNamespace(name="share")])
    return client


def test_apply_with_a_digest_that_differs_from_the_preview_changes_nothing(production_environment):
    """@description: A mismatching digest refuses before any client is created or any login happens.
    @param production_environment: Synthetic settings.
    @return: None.
    """
    factory = MagicMock()
    with pytest.raises(command_sync.ConfigurationError, match="differs"):
        command_sync.run_remote(_apply_arguments("0" * 64), client_factory=factory)
    factory.assert_not_called()


def test_confirmed_apply_syncs_globally_and_a_foreign_token_is_refused(production_environment):
    """@description: A confirmed global apply calls sync once; a token of another application changes nothing.
    @param production_environment: Synthetic settings.
    @return: None.
    """
    digest = command_sync.contract_digest(command_sync.local_payloads())
    client = _fake_client()
    summary = command_sync.run_remote(_apply_arguments(digest), client_factory=lambda: client)
    client.tree.sync.assert_awaited_once_with(guild=None)
    assert summary["published"] == ["share"] and client.close.await_count == 1
    foreign = _fake_client(APPLICATION_ID + 1)
    with pytest.raises(command_sync.ConfigurationError, match="different application"):
        command_sync.run_remote(_apply_arguments(digest), client_factory=lambda: foreign)
    foreign.tree.sync.assert_not_called()
    assert foreign.close.await_count == 1


def test_owner_guild_preview_reports_the_owner_group_separately_from_the_global_set(production_environment, monkeypatch):
    """@description: The Owner group is guild-scoped, so the preview reports it under its own digest and keeps the global digest unchanged.
    @param production_environment: Synthetic settings with network blocked.
    @param monkeypatch: Environment isolation for the Owner guild setting.
    @return: None.
    """
    global_digest = command_sync.contract_digest(command_sync.local_payloads())
    monkeypatch.setenv("DISCORD_OWNER_GUILD_ID", str(GUILD_A))
    summary = command_sync.preview(None)
    assert summary["local_digest"] == global_digest
    assert "owner" not in summary["commands"]
    assert summary["owner_guild"]["commands"] == ["owner"]
    assert summary["owner_guild"]["digest"] == command_sync.contract_digest(command_sync.owner_guild_payloads())


def test_owner_guild_preview_is_none_when_no_owner_guild_is_configured(production_environment):
    """@description: Without DISCORD_OWNER_GUILD_ID there is no Owner set to preview and nothing is invented.
    @param production_environment: Synthetic settings with the Owner guild removed.
    @return: None.
    """
    assert command_sync.preview(None)["owner_guild"] is None


def test_confirmed_owner_guild_apply_publishes_only_the_owner_group(production_environment, monkeypatch):
    """@description: The owner-guild target syncs the guild set without copying the global commands into the Owner guild.
    @param production_environment: Synthetic settings.
    @param monkeypatch: Environment isolation for the Owner guild setting.
    @return: None.
    """
    monkeypatch.setenv("DISCORD_OWNER_GUILD_ID", str(GUILD_A))
    digest = command_sync.contract_digest(command_sync.owner_guild_payloads())
    client = _fake_client()
    arguments = command_sync.parse_arguments(["apply", "--target", "owner-guild", "--i-understand-this-calls-discord",
                                              "--confirm-overwrite-remote-commands", "--expected-digest", digest])
    command_sync.run_remote(arguments, client_factory=lambda: client)
    client.tree.copy_global_to.assert_not_called()
    client.tree.sync.assert_awaited_once()
    assert client.tree.sync.await_args.kwargs["guild"].id == GUILD_A


def test_owner_guild_apply_without_an_owner_guild_changes_nothing(production_environment):
    """@description: The owner-guild target refuses before any client is created when no Owner guild is configured.
    @param production_environment: Synthetic settings with the Owner guild removed.
    @return: None.
    """
    arguments = command_sync.parse_arguments(["apply", "--target", "owner-guild", "--i-understand-this-calls-discord",
                                              "--confirm-overwrite-remote-commands", "--expected-digest", "0" * 64])
    factory = MagicMock()
    with pytest.raises(command_sync.ConfigurationError, match="DISCORD_OWNER_GUILD_ID"):
        command_sync.run_remote(arguments, client_factory=factory)
    factory.assert_not_called()
