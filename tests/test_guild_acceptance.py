"""Exercise development-guild acceptance with real local registration and mocked Discord REST transport.

Synthetic credentials and fail-fast database traps keep every scenario offline and isolated.
"""

import argparse
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot import discord_acceptance as entry
from bot import guild_acceptance as acceptance
from bot.acceptance_config import DevelopmentGuildSettings, load_development_guild_settings, validate_snowflake
from bot.config import ConfigurationError, DiscordSettings
from bot.services import ServiceUnavailable

SYNTHETIC_APPLICATION_ID = 123456
SYNTHETIC_GUILD_ID = 654321


@pytest.fixture(autouse=True)
def no_external_boundaries(monkeypatch):
    """@description: Trap database, gateway, real login, HTTP requests and DM operations in every test.
    @param monkeypatch: Fail-fast boundary replacement facility.
    @return: None.
    """
    blocked = MagicMock(side_effect=AssertionError("External boundary reached"))
    monkeypatch.setattr("pymysql.connect", blocked)
    monkeypatch.setattr("discord.http.HTTPClient.request", AsyncMock(side_effect=AssertionError("Real HTTP reached")))
    monkeypatch.setattr(discord.Client, "login", AsyncMock(side_effect=AssertionError("Real login reached")))
    monkeypatch.setattr(discord.Client, "connect", AsyncMock(side_effect=AssertionError("Gateway reached")))
    monkeypatch.setattr(discord.User, "send", AsyncMock(side_effect=AssertionError("DM reached")))
    monkeypatch.setattr("bot.config.load_database_settings", blocked)


@pytest.fixture
def development_settings(monkeypatch):
    """@description: Provide synthetic secret-safe environment settings without database variables.
    @param monkeypatch: Environment isolation facility.
    @return: Explicit synthetic application and guild configuration.
    """
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-token-secret")
    monkeypatch.setenv("DISCORD_DEV_GUILD_ID", str(SYNTHETIC_GUILD_ID))
    monkeypatch.setenv("DISCORD_EXPECTED_APPLICATION_ID", str(SYNTHETIC_APPLICATION_ID))
    monkeypatch.setenv("DB_PASSWORD", "synthetic-db-secret")
    monkeypatch.setenv("DB_NAME", "jukebox101")
    monkeypatch.setenv("TEST_DB_NAME", "jukebox101_test")
    return load_development_guild_settings()


@pytest.fixture
def local_client(monkeypatch):
    """@description: Keep the actual local command tree while replacing all acceptance REST methods.
    @param monkeypatch: REST boundary injection facility.
    @return: Database-disabled client with synthetic authenticated identity.
    """
    client = acceptance.build_acceptance_client()
    client._connection.application_id = SYNTHETIC_APPLICATION_ID
    monkeypatch.setattr(client, "login", AsyncMock())
    monkeypatch.setattr(client, "application_info", AsyncMock(return_value=SimpleNamespace(id=SYNTHETIC_APPLICATION_ID)))
    monkeypatch.setattr(client, "fetch_guild", AsyncMock(return_value=SimpleNamespace(id=SYNTHETIC_GUILD_ID)))
    monkeypatch.setattr(client.tree, "fetch_commands", AsyncMock(return_value=[]))
    monkeypatch.setattr(client.tree, "sync", AsyncMock(return_value=[SimpleNamespace(name=name) for name in acceptance.EXPECTED_COMMAND_NAMES]))
    return client


def test_offline_preflight_has_no_external_side_effects(local_client, development_settings, caplog):
    """@description: Inspect exact command names and explicit targets without contacting either platform.
    @param local_client: Real local registry with mock REST methods.
    @param development_settings: Synthetic acceptance settings.
    @param caplog: Captured credential-free logs.
    @return: None.
    """
    summary = acceptance.local_preflight(local_client, development_settings)
    assert tuple(summary["commands"]) == acceptance.EXPECTED_COMMAND_NAMES
    assert summary["database"] == "disabled" and summary["automatic_sync"] is False
    assert local_client.intents.value == 0
    local_client.login.assert_not_awaited()
    local_client.tree.sync.assert_not_awaited()
    local_client.fetch_guild.assert_not_awaited()
    assert "secret" not in repr(summary) + repr(development_settings) + caplog.text


@pytest.mark.parametrize("identifier", [0, -1, "654321", True, False, 2**64, None])
def test_strict_guild_target_validation(identifier):
    """@description: Reject implicit or invalid Guild IDs before any client operation.
    @param identifier: Invalid runtime target, including strings and booleans.
    @return: None.
    """
    with pytest.raises(ConfigurationError):
        validate_snowflake(identifier, "DISCORD_DEV_GUILD_ID")


@pytest.mark.parametrize("value", ["0", "-1", "True", "abc", "１２３", "1.5", " 654321"])
def test_invalid_environment_guild_id(development_settings, monkeypatch, value):
    """@description: Distinguish environment parsing from strict typed Guild ID validation.
    @param development_settings: Synthetic baseline environment.
    @param monkeypatch: Environment injection facility.
    @param value: Malformed or nonpositive environment target.
    @return: None.
    """
    monkeypatch.setenv("DISCORD_DEV_GUILD_ID", value)
    with pytest.raises(ConfigurationError):
        load_development_guild_settings()


@pytest.mark.parametrize("setting_name", ["DISCORD_BOT_TOKEN", "DISCORD_DEV_GUILD_ID", "DISCORD_EXPECTED_APPLICATION_ID"])
def test_required_acceptance_settings(development_settings, monkeypatch, setting_name):
    """@description: Reject missing acceptance credentials or targets without using database settings.
    @param development_settings: Isolated synthetic environment.
    @param monkeypatch: Environment isolation facility.
    @param setting_name: Required setting removed for this scenario.
    @return: None.
    """
    monkeypatch.delenv(setting_name)
    with pytest.raises(ConfigurationError, match=setting_name):
        load_development_guild_settings()


def test_offline_preflight_rejects_empty_token(local_client):
    """@description: Prevent manual settings construction from bypassing the missing-token guard.
    @param local_client: Actual local command registry.
    @return: None.
    """
    settings = DevelopmentGuildSettings(DiscordSettings(""), SYNTHETIC_GUILD_ID, SYNTHETIC_APPLICATION_ID)
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.local_preflight(local_client, settings)
    local_client.login.assert_not_awaited()


def test_preflight_rejects_unexpected_command(local_client, development_settings):
    """@description: Refuse a registry that would publish an incomplete command set.
    @param local_client: Mutable local command registry.
    @param development_settings: Synthetic targets.
    @return: None.
    """
    local_client.tree.remove_command("history")
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.local_preflight(local_client, development_settings)


def test_default_entry_is_offline_and_closes_client(local_client, development_settings, monkeypatch, capsys):
    """@description: Verify the real CLI default performs preflight and closes resources without login.
    @param local_client: Real client with trapped transports.
    @param development_settings: Synthetic environment settings.
    @param monkeypatch: CLI and factory injection facility.
    @param capsys: Captured public summary output.
    @return: None.
    """
    monkeypatch.setattr(entry, "build_acceptance_client", MagicMock(return_value=local_client))
    monkeypatch.setattr("sys.argv", ["discord_acceptance"])
    entry.main()
    assert local_client.is_closed()
    assert '"automatic_sync": false' in capsys.readouterr().out
    local_client.login.assert_not_awaited()
    local_client.tree.sync.assert_not_awaited()


@pytest.mark.parametrize("arguments", [
    ("login", False, False), ("guild-sync", True, False), ("guild-sync", False, True),
    ("login", True, True), ("preflight", True, False), ("global-sync", True, True),
])
def test_unauthorized_modes_fail_before_client_creation(monkeypatch, arguments):
    """@description: Reject missing opt-ins or unsupported modes before any login or secret loading.
    @param monkeypatch: Client construction trap.
    @param arguments: Mode, login opt-in and sync opt-in combination.
    @return: None.
    """
    mode, authorize_login, authorize_guild_sync = arguments
    factory = MagicMock(side_effect=AssertionError("Client constructed before authorization"))
    monkeypatch.setattr(entry, "build_acceptance_client", factory)
    options = argparse.Namespace(mode=mode, authorize_login=authorize_login, authorize_guild_sync=authorize_guild_sync)
    with pytest.raises(acceptance.AcceptanceError):
        asyncio.run(entry.run_acceptance(options))
    factory.assert_not_called()


@pytest.mark.parametrize("option", ["--global-sync", "--token", "--db-name"])
def test_cli_has_no_global_sync_secret_or_database_override(option):
    """@description: Keep dangerous targets and credential values out of acceptance CLI arguments.
    @param option: Unsupported publishing, secret or persistence override.
    @return: None.
    """
    with pytest.raises(SystemExit):
        entry.argument_parser().parse_args([option])


def test_login_requires_authorization(local_client, development_settings):
    """@description: Require explicit permission even when the login helper is called directly.
    @param local_client: Client with mocked login.
    @param development_settings: Synthetic expected targets.
    @return: None.
    """
    with pytest.raises(acceptance.AcceptanceError):
        asyncio.run(acceptance.login_smoke(local_client, development_settings))
    local_client.login.assert_not_awaited()


def test_login_only_never_syncs(local_client, development_settings, monkeypatch):
    """@description: Authenticate and verify explicit identity without gateway, sync or database access.
    @param local_client: REST-mocked client.
    @param development_settings: Synthetic expected targets.
    @param monkeypatch: Entry client factory injection facility.
    @return: None.
    """
    monkeypatch.setattr(entry, "build_acceptance_client", MagicMock(return_value=local_client))
    options = entry.argument_parser().parse_args(["login", "--authorize-login"])
    summary = asyncio.run(entry.run_acceptance(options))
    assert summary["application_id"] == SYNTHETIC_APPLICATION_ID
    assert local_client.is_closed()
    local_client.tree.sync.assert_not_awaited()
    local_client.tree.fetch_commands.assert_not_awaited()
    local_client.connect.assert_not_awaited()


@pytest.mark.parametrize("failure_kind", ["login", "api", "timeout"])
def test_login_failures_close_and_sanitize(local_client, development_settings, monkeypatch, capsys, caplog, failure_kind):
    """@description: Sanitize expected platform failures while deterministically closing the client.
    @param local_client: REST-mocked client.
    @param development_settings: Synthetic expected targets.
    @param monkeypatch: Entry boundary injection facility.
    @param capsys: Captured command output.
    @param caplog: Captured log output.
    @param failure_kind: Expected failure category.
    @return: None.
    """
    failures = {"login": discord.LoginFailure("synthetic-token-secret"),
                "api": discord.HTTPException(SimpleNamespace(status=500, reason="Synthetic"), "synthetic-db-secret"),
                "timeout": TimeoutError("synthetic-token-secret")}
    local_client.login.side_effect = failures[failure_kind]
    monkeypatch.setattr(entry, "build_acceptance_client", MagicMock(return_value=local_client))
    monkeypatch.setattr("sys.argv", ["discord_acceptance", "login", "--authorize-login"])
    previous_level = logging.root.manager.disable
    with pytest.raises(SystemExit) as captured_error:
        entry.main()
    assert "secret" not in str(captured_error.value) + capsys.readouterr().out + caplog.text
    assert local_client.is_closed() and logging.root.manager.disable == previous_level
    local_client.tree.sync.assert_not_awaited()


@pytest.mark.parametrize("mismatch", ["application_info", "cached_application", "guild"])
def test_remote_identity_mismatch_prevents_sync(local_client, development_settings, mismatch):
    """@description: Refuse incorrect application or guild identity before confirmation or publishing.
    @param local_client: Authenticated mocked client.
    @param development_settings: Synthetic targets.
    @param mismatch: Which remote or cached identity differs.
    @return: None.
    """
    if mismatch == "application_info":
        local_client.application_info.return_value.id += 1
    elif mismatch == "cached_application":
        local_client._connection.application_id += 1
    else:
        local_client.fetch_guild.return_value.id += 1
    confirmation = MagicMock()
    with pytest.raises(acceptance.AcceptanceError):
        asyncio.run(acceptance.guild_sync(local_client, development_settings, authorized=True, confirm=confirmation))
    confirmation.assert_not_called()
    local_client.tree.sync.assert_not_awaited()


def test_sync_default_is_disabled(local_client, development_settings):
    """@description: Require independent sync permission even after login is available.
    @param local_client: Authenticated mocked client.
    @param development_settings: Expected targets.
    @return: None.
    """
    with pytest.raises(acceptance.AcceptanceError):
        asyncio.run(acceptance.guild_sync(local_client, development_settings))
    local_client.application_info.assert_not_awaited()
    local_client.tree.sync.assert_not_awaited()


def test_cancelled_confirmation_never_syncs(local_client, development_settings):
    """@description: Treat empty or mismatched typed confirmation as cancellation without changes.
    @param local_client: Authenticated mock client with empty remote registry.
    @param development_settings: Expected application and guild.
    @return: None.
    """
    confirmation = MagicMock(return_value="cancel")
    with pytest.raises(acceptance.AcceptanceError, match="cancelled"):
        asyncio.run(acceptance.guild_sync(local_client, development_settings, authorized=True, confirm=confirmation))
    prompt = confirmation.call_args.args[0]
    assert str(SYNTHETIC_APPLICATION_ID) in prompt and str(SYNTHETIC_GUILD_ID) in prompt
    assert all(name in prompt for name in acceptance.EXPECTED_COMMAND_NAMES)
    local_client.tree.sync.assert_not_awaited()


@pytest.mark.parametrize("remote_states", [[ [SimpleNamespace(name="other-command")] ], [[], [SimpleNamespace(name="new-command")]]])
def test_existing_or_changed_remote_commands_block_sync(local_client, development_settings, remote_states):
    """@description: Stop rather than overwrite commands seen before or after human confirmation.
    @param local_client: Authenticated REST-mocked client.
    @param development_settings: Expected target.
    @param remote_states: Remote responses around the manual confirmation window.
    @return: None.
    """
    local_client.tree.fetch_commands.side_effect = remote_states
    confirmation = MagicMock(return_value=acceptance.confirmation_text(development_settings))
    with pytest.raises(acceptance.AcceptanceError, match="already exist"):
        asyncio.run(acceptance.guild_sync(local_client, development_settings, authorized=True, confirm=confirmation))
    local_client.tree.sync.assert_not_awaited()


def test_confirmed_sync_is_guild_only(local_client, development_settings):
    """@description: Publish only to the verified guild after two safe-state reads and exact confirmation.
    @param local_client: Authenticated mocked client.
    @param development_settings: Explicit expected targets.
    @return: None.
    """
    confirmation = MagicMock(return_value=acceptance.confirmation_text(development_settings))
    summary = asyncio.run(acceptance.guild_sync(local_client, development_settings, authorized=True, confirm=confirmation))
    assert tuple(summary["published_commands"]) == acceptance.EXPECTED_COMMAND_NAMES
    assert local_client.tree.fetch_commands.await_count == 2
    assert local_client.application_info.await_count == 2
    local_client.tree.sync.assert_awaited_once()
    assert local_client.tree.sync.call_args.kwargs["guild"].id == SYNTHETIC_GUILD_ID
    assert len(local_client.tree.get_commands(guild=discord.Object(id=SYNTHETIC_GUILD_ID))) == 5


def test_remote_read_failure_never_publishes(local_client, development_settings):
    """@description: Refuse sync if the current remote registry cannot be inspected safely.
    @param local_client: Authenticated mocked client.
    @param development_settings: Expected targets.
    @return: None.
    """
    local_client.tree.fetch_commands.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "synthetic-secret")
    with pytest.raises(discord.Forbidden):
        asyncio.run(acceptance.guild_sync(local_client, development_settings, authorized=True, confirm=MagicMock()))
    local_client.tree.sync.assert_not_awaited()


def test_acceptance_repository_cannot_reach_database(local_client):
    """@description: Keep valid business commands from opening even a configured production database.
    @param local_client: Client whose service uses the disabled acceptance repository.
    @return: None.
    """
    service = local_client.handlers.service
    with pytest.raises(ServiceUnavailable):
        asyncio.run(service.share(123, "https://youtu.be/Abc_123-Xyz"))
    with pytest.raises(ServiceUnavailable):
        asyncio.run(service.balance(123))
    with pytest.raises(ServiceUnavailable):
        asyncio.run(service.history(123))
    with pytest.raises(ServiceUnavailable):
        asyncio.run(service.daily(123, True))
    assert "未扣點" in asyncio.run(service.exchange(123))


def test_cancelled_sync_entry_closes_client(local_client, development_settings, monkeypatch):
    """@description: Ensure user cancellation still closes the logged-in REST session.
    @param local_client: Authenticated client with trapped transport.
    @param development_settings: Synthetic target environment.
    @param monkeypatch: Entry sync boundary injection facility.
    @return: None.
    """
    monkeypatch.setattr(entry, "build_acceptance_client", MagicMock(return_value=local_client))
    monkeypatch.setattr(entry, "guild_sync", AsyncMock(side_effect=acceptance.AcceptanceError("cancelled")))
    options = entry.argument_parser().parse_args(["guild-sync", "--authorize-login", "--authorize-guild-sync"])
    with pytest.raises(acceptance.AcceptanceError):
        asyncio.run(entry.run_acceptance(options))
    assert local_client.is_closed()
    local_client.tree.sync.assert_not_awaited()


def test_sync_success_entry_closes_client(local_client, development_settings, monkeypatch):
    """@description: Close the original client after a successful explicitly authorized guild publication.
    @param local_client: Real local client with all platform operations mocked.
    @param development_settings: Synthetic expected targets.
    @param monkeypatch: CLI and manual confirmation injection facility.
    @return: None.
    """
    from functools import partial
    confirmation = MagicMock(return_value=acceptance.confirmation_text(development_settings))
    monkeypatch.setattr(entry, "build_acceptance_client", MagicMock(return_value=local_client))
    monkeypatch.setattr(entry, "guild_sync", partial(acceptance.guild_sync, confirm=confirmation))
    options = entry.argument_parser().parse_args(["guild-sync", "--authorize-login", "--authorize-guild-sync"])
    summary = asyncio.run(entry.run_acceptance(options))
    assert local_client.is_closed()
    assert tuple(summary["published_commands"]) == acceptance.EXPECTED_COMMAND_NAMES
    local_client.connect.assert_not_awaited()


def test_application_changes_during_confirmation_abort(local_client, development_settings):
    """@description: Recheck application identity after the human review window before publication.
    @param local_client: Authenticated mocked client.
    @param development_settings: Expected target identity.
    @return: None.
    """
    local_client.application_info.side_effect = [SimpleNamespace(id=SYNTHETIC_APPLICATION_ID), SimpleNamespace(id=SYNTHETIC_APPLICATION_ID + 1)]
    with pytest.raises(acceptance.AcceptanceError):
        asyncio.run(acceptance.guild_sync(local_client, development_settings, authorized=True,
                                         confirm=MagicMock(return_value=acceptance.confirmation_text(development_settings))))
    local_client.tree.sync.assert_not_awaited()


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), EOFError()])
def test_confirmation_interrupt_closes_client(local_client, development_settings, monkeypatch, failure):
    """@description: Close the logged-in client when confirmation input is interrupted or unavailable.
    @param local_client: REST-mocked local client.
    @param development_settings: Synthetic environment settings.
    @param monkeypatch: Entry and confirmation injection facility.
    @param failure: Human input interruption or unavailable stdin.
    @return: None.
    """
    from functools import partial
    monkeypatch.setattr(entry, "build_acceptance_client", MagicMock(return_value=local_client))
    monkeypatch.setattr(entry, "guild_sync", partial(acceptance.guild_sync, confirm=MagicMock(side_effect=failure)))
    options = entry.argument_parser().parse_args(["guild-sync", "--authorize-login", "--authorize-guild-sync"])
    with pytest.raises(type(failure)):
        asyncio.run(entry.run_acceptance(options))
    assert local_client.is_closed()
    local_client.tree.sync.assert_not_awaited()


def test_publish_error_closes_and_never_retries(local_client, development_settings, monkeypatch):
    """@description: Leave remote outcomes uncertain after transport failure without a destructive retry.
    @param local_client: Authenticated REST-mocked client.
    @param development_settings: Expected target.
    @param monkeypatch: Entry and manual confirmation injection facility.
    @return: None.
    """
    from functools import partial
    local_client.tree.sync.side_effect = discord.HTTPException(SimpleNamespace(status=500, reason="Synthetic"), "synthetic-secret")
    monkeypatch.setattr(entry, "build_acceptance_client", MagicMock(return_value=local_client))
    monkeypatch.setattr(entry, "guild_sync", partial(acceptance.guild_sync, confirm=MagicMock(return_value=acceptance.confirmation_text(development_settings))))
    options = entry.argument_parser().parse_args(["guild-sync", "--authorize-login", "--authorize-guild-sync"])
    with pytest.raises(discord.HTTPException):
        asyncio.run(entry.run_acceptance(options))
    assert local_client.is_closed()
    assert local_client.tree.sync.await_count == 1


def test_production_startup_error_is_sanitized(database_settings, monkeypatch, capsys):
    """@description: Keep expected login error text out of the existing production entry traceback.
    @param database_settings: Synthetic database environment, without connection use.
    @param monkeypatch: Token and client execution injection facility.
    @param capsys: Captured diagnostic output.
    @return: None.
    """
    from bot import __main__ as production_entry
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-token-secret")
    monkeypatch.setattr(production_entry, "load_database_settings", MagicMock(return_value=database_settings))
    monkeypatch.setattr(discord.Client, "run", MagicMock(side_effect=discord.LoginFailure("synthetic-token-secret")))
    with pytest.raises(SystemExit) as captured_error:
        production_entry.main()
    assert "secret" not in str(captured_error.value) + capsys.readouterr().out


def test_no_implicit_sync_or_scheduler_hooks(local_client):
    """@description: Preserve registration-only construction and framework-default no-op startup hooks.
    @param local_client: Existing client initialized without network access.
    @return: None.
    """
    assert "on_ready" not in type(local_client).__dict__
    assert "setup_hook" not in type(local_client).__dict__
    local_client.login.assert_not_awaited()
    local_client.connect.assert_not_awaited()
    local_client.tree.sync.assert_not_awaited()


@pytest.mark.parametrize("token", ["synthetic secret", "synthetic\r\nsecret", "synthetic\x00secret", "秘密token"])
def test_malformed_token_is_rejected_without_echo(development_settings, monkeypatch, token):
    """@description: Stop malformed HTTP-header credentials before transport code can echo their values.
    @param development_settings: Synthetic development environment.
    @param monkeypatch: Token input injection facility.
    @param token: Malformed synthetic credential.
    @return: None.
    """
    monkeypatch.setattr("bot.config.required_environment_value", MagicMock(return_value=token))
    with pytest.raises(ConfigurationError) as captured_error:
        load_development_guild_settings()
    assert "secret" not in str(captured_error.value) and "秘密" not in str(captured_error.value)


def test_new_acceptance_functions_follow_limits():
    """@description: Enforce function documentation and implementation limits for the new acceptance tests.
    @param: none
    @return: None.
    """
    import ast
    from pathlib import Path
    from tests.test_phase2_quality import _assert_function_documentation, _control_depth, _implementation_lines
    module_path = Path(__file__)
    source = module_path.read_text(encoding="utf-8")
    for function in ast.walk(ast.parse(source)):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert _implementation_lines(function, source.splitlines()) <= 25, function.name
            assert _control_depth(function) <= 3, function.name
            _assert_function_documentation(function, module_path)
