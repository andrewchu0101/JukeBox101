"""Verify real local discord.py registration and mocked interaction transport.

The standard asyncio runner exercises callbacks and explicit sync without authenticating or sending messages.
"""

import asyncio
import importlib
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands
import pytest

from bot.config import ConfigurationError, load_discord_settings
from bot.discord_app import JukeBoxClient, synchronize_commands
from bot.services import MusicService, ServiceUnavailable


@pytest.fixture
def client_and_service():
    """@description: Build the actual local registry with an entirely mocked application service.
    @param: none
    @return: Local Discord client and injected async service.
    """
    service = MagicMock(spec=MusicService)
    for command_name in ("share", "exchange", "balance", "history", "daily"):
        getattr(service, command_name).return_value = "synthetic safe reply"
    return JukeBoxClient(service), service


@pytest.fixture
def interaction():
    """@description: Capture private acknowledgement and replies without Discord transport.
    @param: none
    @return: Synthetic interaction with asynchronous response methods.
    """
    return SimpleNamespace(
        id=456, user=SimpleNamespace(id=123),
        response=SimpleNamespace(defer=AsyncMock(), is_done=MagicMock(return_value=True), send_message=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def test_local_registry_and_minimal_permissions(client_and_service):
    """@description: Verify five local slash commands with explicit daily choices and minimal intents.
    @param client_and_service: Real local client with mocked service.
    @return: None.
    """
    client, service = client_and_service
    assert {command.name for command in client.tree.get_commands()} == {"share", "exchange", "balance", "history", "daily"}
    assert client.intents.value == 0
    assert client.allowed_mentions.everyone is False
    assert client.allowed_mentions.users is False
    assert client.tree.get_command("share").parameters[0].name == "url"
    assert [choice.value for choice in client.tree.get_command("daily").parameters[0].choices] == ["on", "off"]
    assert service.mock_calls == []


@pytest.mark.parametrize("command_name, extra_arguments, expected_arguments", [
    ("share", ("https://youtu.be/Abc_123-Xyz",), (123, "https://youtu.be/Abc_123-Xyz", "discord:456")),
    ("exchange", (), (123,)), ("balance", (), (123,)), ("history", (), (123,)),
    ("daily", (app_commands.Choice(name="on", value="on"),), (123, True)),
    ("daily", (app_commands.Choice(name="off", value="off"),), (123, False)),
])
def test_callbacks_acknowledge_and_reply_privately(client_and_service, interaction, command_name, extra_arguments, expected_arguments):
    """@description: Route actual registered callbacks through the authenticated private interaction.
    @param client_and_service: Real local client and mocked application service.
    @param interaction: Mocked Discord transport.
    @param command_name: Slash command to invoke.
    @param extra_arguments: Registered command options.
    @param expected_arguments: Required business arguments after identity extraction.
    @return: None.
    """
    client, service = client_and_service
    command = client.tree.get_command(command_name)
    asyncio.run(command.callback(command.binding, interaction, *extra_arguments))
    getattr(service, command_name).assert_awaited_once_with(*expected_arguments)
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert interaction.edit_original_response.call_args.kwargs["content"] == "synthetic safe reply"
    assert interaction.edit_original_response.call_args.kwargs["allowed_mentions"].everyone is False


def test_database_error_gets_safe_reply(client_and_service, interaction):
    """@description: Display infrastructure failure without falsely reporting zero credits.
    @param client_and_service: Local client and mocked service.
    @param interaction: Captured private response.
    @return: None.
    """
    client, service = client_and_service
    service.balance.side_effect = ServiceUnavailable("資料服務暫時無法使用，請稍後再試。")
    asyncio.run(client.handlers.balance(interaction))
    reply = interaction.edit_original_response.call_args.kwargs["content"]
    assert "暫時無法使用" in reply and "0 點" not in reply


@pytest.mark.parametrize("stage", ["defer", "reply"])
def test_discord_failures_are_sanitized(client_and_service, interaction, caplog, stage):
    """@description: Avoid leaking transport response bodies or command arguments into logs.
    @param client_and_service: Local client and mocked service.
    @param interaction: Mock transport to fail.
    @param caplog: Captured safe diagnostic output.
    @param stage: Transport stage raising a Discord HTTP failure.
    @return: None.
    """
    client, service = client_and_service
    response = SimpleNamespace(status=403, reason="Forbidden")
    failure = discord.Forbidden(response, "synthetic-token-secret")
    target = interaction.response.defer if stage == "defer" else interaction.edit_original_response
    target.side_effect = failure
    asyncio.run(client.handlers.balance(interaction))
    assert "secret" not in caplog.text and "Forbidden" in caplog.text
    if stage == "defer":
        service.balance.assert_not_awaited()


def test_unexpected_callback_error_is_sanitized(client_and_service, interaction, caplog):
    """@description: Replace framework traceback logging with a safe user-facing failure.
    @param client_and_service: Client owning the custom error boundary.
    @param interaction: Already acknowledged mock request.
    @param caplog: Captured logging output.
    @return: None.
    """
    client, service = client_and_service
    failure = app_commands.CommandInvokeError(client.tree.get_command("share"), RuntimeError("synthetic-password-secret"))
    asyncio.run(client.tree.on_error(interaction, failure))
    assert "secret" not in caplog.text
    assert "暫時無法完成" in interaction.edit_original_response.call_args.kwargs["content"]


def test_invalid_daily_choice_is_not_persisted(client_and_service, interaction):
    """@description: Avoid guessing daily preferences for malformed direct callback inputs.
    @param client_and_service: Client and mocked service.
    @param interaction: Mocked authenticated request.
    @return: None.
    """
    client, service = client_and_service
    asyncio.run(client.handlers.daily(interaction, app_commands.Choice(name="invalid", value="invalid")))
    service.daily.assert_not_awaited()


def test_sync_default_is_disabled(client_and_service, monkeypatch):
    """@description: Require an opt-in target before contacting Discord's command API.
    @param client_and_service: Locally registered client.
    @param monkeypatch: Remote method injection facility.
    @return: None.
    """
    client, service = client_and_service
    sync_mock = AsyncMock()
    monkeypatch.setattr(client.tree, "sync", sync_mock)
    assert asyncio.run(synchronize_commands(client)) == []
    sync_mock.assert_not_awaited()


@pytest.mark.parametrize("options", [{"guild_id": 456}, {"global_sync": True}])
def test_explicit_sync_target(client_and_service, monkeypatch, options):
    """@description: Keep development-guild and global publishing explicit and independently testable.
    @param client_and_service: Local application client.
    @param monkeypatch: Remote method injection facility.
    @param options: Explicit synchronization target.
    @return: None.
    """
    client, service = client_and_service
    sync_mock = AsyncMock(return_value=["mock published command"])
    monkeypatch.setattr(client.tree, "sync", sync_mock)
    assert asyncio.run(synchronize_commands(client, **options)) == ["mock published command"]
    if "guild_id" in options:
        assert sync_mock.call_args.kwargs["guild"].id == 456
    else:
        sync_mock.assert_awaited_once_with()


@pytest.mark.parametrize("options", [{"guild_id": 0}, {"guild_id": True}, {"guild_id": "456"}, {"guild_id": 456, "global_sync": True}])
def test_sync_rejects_invalid_or_conflicting_targets(client_and_service, monkeypatch, options):
    """@description: Reject guessed or conflicting sync targets before remote API access.
    @param client_and_service: Local application client.
    @param monkeypatch: Remote method injection facility.
    @param options: Invalid explicit target configuration.
    @return: None.
    """
    client, service = client_and_service
    sync_mock = AsyncMock()
    monkeypatch.setattr(client.tree, "sync", sync_mock)
    with pytest.raises(ValueError):
        asyncio.run(synchronize_commands(client, **options))
    sync_mock.assert_not_awaited()


def test_token_required_and_secret_safe(monkeypatch):
    """@description: Fail missing token configuration before login without exposing present tokens.
    @param monkeypatch: Isolated environment facility.
    @return: None.
    """
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="DISCORD_BOT_TOKEN"):
        load_discord_settings()
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-token-secret")
    assert "secret" not in repr(load_discord_settings())


def test_missing_token_stops_entry_before_login(monkeypatch):
    """@description: Ensure explicit startup fails before database setup or Discord login.
    @param monkeypatch: Credential and login isolation facility.
    @return: None.
    """
    entry = importlib.import_module("bot.__main__")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    login_mock = MagicMock()
    database_loader = MagicMock()
    monkeypatch.setattr(discord.Client, "run", login_mock)
    monkeypatch.setattr(entry, "load_database_settings", database_loader)
    with pytest.raises(SystemExit, match="DISCORD_BOT_TOKEN"):
        entry.main()
    login_mock.assert_not_called()
    database_loader.assert_not_called()


def test_explicit_entry_passes_token_only_to_login(database_settings, monkeypatch):
    """@description: Verify explicit execution wiring without performing login or command sync.
    @param database_settings: Synthetic production-shaped database environment.
    @param monkeypatch: Runtime boundary isolation facility.
    @return: None.
    """
    entry = importlib.import_module("bot.__main__")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-token-secret")
    for setting_name in ("DISCORD_DEV_GUILD_ID", "DISCORD_EXPECTED_APPLICATION_ID", "DISCORD_OWNER_USER_ID"):
        monkeypatch.setenv(setting_name, "456")
    login_mock = MagicMock()
    sync_mock = AsyncMock()
    connect_mock = MagicMock(side_effect=AssertionError("Unexpected connection"))
    monkeypatch.setattr(discord.Client, "run", login_mock)
    monkeypatch.setattr(app_commands.CommandTree, "sync", sync_mock)
    monkeypatch.setattr("pymysql.connect", connect_mock)
    entry.main()
    login_mock.assert_called_once_with("synthetic-token-secret", log_handler=None)
    sync_mock.assert_not_awaited()
    connect_mock.assert_not_called()


def test_imports_do_not_load_credentials_or_connect():
    """@description: Verify module imports remain inert with credential and network boundaries trapped.
    @param: none
    @return: None.
    """
    script = """
import importlib
from unittest.mock import MagicMock
import bot.config, pymysql, discord
from discord import app_commands
blocked = MagicMock(side_effect=AssertionError('External boundary reached'))
bot.config.load_database_settings = blocked
bot.config.load_discord_settings = blocked
pymysql.connect = blocked
discord.Client.run = blocked
app_commands.CommandTree.sync = blocked
for module_name in ('bot.urls', 'bot.models', 'bot.database.repository', 'bot.services', 'bot.discord_app', 'bot.__main__', 'bot.acceptance_config', 'bot.guild_acceptance', 'bot.discord_acceptance', 'bot.acceptance_runtime', 'bot.command_contract', 'bot.gateway_service', 'bot.gateway_listener', 'bot.gateway_acceptance'):
    importlib.import_module(module_name)
blocked.assert_not_called()
"""
    process = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=15)
    assert process.returncode == 0, "Import safety subprocess failed"
