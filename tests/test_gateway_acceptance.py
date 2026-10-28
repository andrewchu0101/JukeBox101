"""Verify guarded gateway dispatch and transport-only responses with no real platform access.

Actual local commands and SDK dispatch are exercised while REST, gateway, repositories and DM are trapped.
"""

import argparse
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot import gateway_acceptance as entry
from bot import gateway_listener as listener
from bot.command_contract import CONTRACT_PATH, verify_local_contract, verify_remote_payloads
from bot.config import ConfigurationError
from bot.gateway_service import GatewayAcceptanceService
from bot.guild_acceptance import AcceptanceError
from tests.test_guild_acceptance import (
    SYNTHETIC_APPLICATION_ID, SYNTHETIC_GUILD_ID, development_settings, no_external_boundaries,
)


@pytest.fixture(autouse=True)
def deny_repository_and_database_settings(monkeypatch, development_settings):
    """@description: Trap formal repository construction and any DB environment read during acceptance.
    @param monkeypatch: Fail-fast persistence and environment injection facility.
    @param development_settings: Populate synthetic DB variables before trapping all subsequent reads.
    @return: None.
    """
    import os
    original_getitem = type(os.environ).__getitem__
    monkeypatch.setattr(type(os.environ), "__getitem__", _guarded_environment_lookup(original_getitem))
    monkeypatch.setattr("bot.database.repository.MusicRepository.__init__", MagicMock(side_effect=AssertionError("Formal repository constructed")))


def _guarded_environment_lookup(original_getitem):
    """@description: Wrap only database-key reads while preserving normal environment behavior.
    @param original_getitem: Existing environment mapping lookup implementation.
    @return: Guarded environment lookup for the current offline test.
    """
    def guarded_lookup(environment, setting_name):
        """@description: Detect database settings access before any network operation is possible.
        @param environment: Process environment mapping.
        @param setting_name: Requested environment key.
        @return: Original value for non-database keys.
        """
        if setting_name.startswith(("DB_", "TEST_DB_")):
            raise AssertionError("Gateway acceptance read database configuration")
        return original_getitem(environment, setting_name)
    return guarded_lookup


@pytest.fixture
def gateway_client(development_settings, monkeypatch):
    """@description: Keep the real guarded registry while mocking every external client operation.
    @param development_settings: Explicit synthetic target configuration.
    @param monkeypatch: REST and gateway injection facility.
    @return: Real local client with mock identity, command reads and gateway connection.
    """
    client = listener.build_gateway_client(development_settings)
    client._connection.application_id = SYNTHETIC_APPLICATION_ID
    monkeypatch.setattr(client, "login", AsyncMock())
    monkeypatch.setattr(client, "connect", AsyncMock())
    monkeypatch.setattr(client, "application_info", AsyncMock(return_value=SimpleNamespace(id=SYNTHETIC_APPLICATION_ID, interactions_endpoint_url=None)))
    monkeypatch.setattr(client, "fetch_guild", AsyncMock(return_value=SimpleNamespace(id=SYNTHETIC_GUILD_ID)))
    published = [SimpleNamespace(to_dict=MagicMock(return_value=command.to_dict(client.tree))) for command in client.tree.get_commands()]
    monkeypatch.setattr(client.tree, "fetch_commands", AsyncMock(return_value=published))
    monkeypatch.setattr(client.tree, "sync", AsyncMock(side_effect=AssertionError("Unexpected sync")))
    return client


@pytest.fixture
def gateway_interaction():
    """@description: Support actual SDK slash dispatch without Discord HTTP transport.
    @param: none
    @return: Synthetic authenticated interaction with captured private responses.
    """
    return SimpleNamespace(
        id=456, guild_id=SYNTHETIC_GUILD_ID, application_id=SYNTHETIC_APPLICATION_ID,
        _state=MagicMock(), guild=None,
        type=discord.InteractionType.application_command, user=SimpleNamespace(id=123), command_failed=False,
        response=SimpleNamespace(is_done=MagicMock(return_value=False), send_message=AsyncMock(), defer=AsyncMock()),
        edit_original_response=AsyncMock(), data={"name": "balance", "type": 1, "options": []},
    )


def test_default_gateway_entry_is_offline(gateway_client, monkeypatch, capsys):
    """@description: Run the actual default CLI with synthetic settings without logging in or connecting.
    @param gateway_client: Actual local guarded client.
    @param monkeypatch: CLI and factory injection facility.
    @param capsys: Captured public preflight summary.
    @return: None.
    """
    monkeypatch.setattr(entry, "build_gateway_client", MagicMock(return_value=gateway_client))
    monkeypatch.setattr("sys.argv", ["gateway_acceptance"])
    entry.main()
    summary = json.loads(capsys.readouterr().out)
    assert summary["remote_contract"] == "not_checked_offline"
    assert summary["database"] == "disabled" and summary["gateway_connected"] is False
    assert gateway_client.is_closed()
    gateway_client.login.assert_not_awaited()
    gateway_client.connect.assert_not_awaited()
    gateway_client.tree.fetch_commands.assert_not_awaited()
    gateway_client.tree.sync.assert_not_awaited()


@pytest.mark.parametrize("mode, authorized", [("listen", False), ("preflight", True), ("guild-sync", True), ("global-sync", True)])
def test_unauthorized_modes_stop_before_settings(monkeypatch, mode, authorized):
    """@description: Reject missing authorization and sync modes before reading credentials or constructing clients.
    @param monkeypatch: Configuration and factory traps.
    @param mode: Requested unsupported or unauthorized mode.
    @param authorized: Explicit gateway opt-in state.
    @return: None.
    """
    loader = MagicMock(side_effect=AssertionError("Settings read before authorization"))
    factory = MagicMock(side_effect=AssertionError("Client constructed before authorization"))
    monkeypatch.setattr(entry, "load_development_guild_settings", loader)
    monkeypatch.setattr(entry, "build_gateway_client", factory)
    with pytest.raises(AcceptanceError):
        asyncio.run(entry.run_gateway_acceptance(argparse.Namespace(mode=mode, authorize_gateway=authorized)))
    loader.assert_not_called()
    factory.assert_not_called()


def test_gateway_helper_requires_explicit_authorization(gateway_client, development_settings):
    """@description: Preserve authorization even when the listener helper is invoked directly.
    @param gateway_client: Transport-mocked local client.
    @param development_settings: Synthetic target settings.
    @return: None.
    """
    with pytest.raises(AcceptanceError):
        asyncio.run(listener.listen_gateway(gateway_client, development_settings))
    gateway_client.login.assert_not_awaited()
    gateway_client.connect.assert_not_awaited()


@pytest.mark.parametrize("setting_name", ["DISCORD_BOT_TOKEN", "DISCORD_DEV_GUILD_ID", "DISCORD_EXPECTED_APPLICATION_ID"])
def test_gateway_missing_settings_fail_safely(development_settings, monkeypatch, setting_name):
    """@description: Fail missing explicit credentials or targets before any real client connection.
    @param development_settings: Isolated synthetic configuration.
    @param monkeypatch: Environment isolation facility.
    @param setting_name: Required setting removed for this case.
    @return: None.
    """
    monkeypatch.delenv(setting_name)
    options = entry.argument_parser().parse_args([])
    with pytest.raises(ConfigurationError, match=setting_name):
        asyncio.run(entry.run_gateway_acceptance(options))


@pytest.mark.parametrize("guild_id", [0, -1, "654321", True, None])
def test_guard_rejects_invalid_configured_guild(development_settings, guild_id):
    """@description: Reject malformed configured targets before client construction.
    @param development_settings: Explicit baseline settings.
    @param guild_id: Invalid runtime target type or value.
    @return: None.
    """
    from dataclasses import replace
    with pytest.raises(ConfigurationError):
        listener.build_gateway_client(replace(development_settings, guild_id=guild_id))


@pytest.mark.parametrize("guild_id, application_id", [(None, SYNTHETIC_APPLICATION_ID), (999, SYNTHETIC_APPLICATION_ID), (SYNTHETIC_GUILD_ID, 999)])
def test_sdk_dispatch_rejects_unauthorized_interactions(gateway_client, gateway_interaction, monkeypatch, guild_id, application_id):
    """@description: Exercise actual CommandTree dispatch to prove admission precedes callback execution.
    @param gateway_client: Real guarded command registry.
    @param gateway_interaction: Synthetic incoming request.
    @param monkeypatch: Business operation trap.
    @param guild_id: Incoming DM or foreign-guild identifier.
    @param application_id: Incoming application identity.
    @return: None.
    """
    gateway_interaction.guild_id = guild_id
    gateway_interaction.application_id = application_id
    service_call = AsyncMock(side_effect=AssertionError("Unauthorized service invocation"))
    monkeypatch.setattr(gateway_client.handlers.service, "balance", service_call)
    asyncio.run(gateway_client.tree._call(gateway_interaction))
    assert gateway_interaction.command_failed is True
    service_call.assert_not_awaited()
    gateway_interaction.response.defer.assert_not_awaited()
    rejection = gateway_interaction.response.send_message.call_args.kwargs
    assert rejection["ephemeral"] is True and rejection["allowed_mentions"].everyone is False
    gateway_interaction.edit_original_response.assert_not_awaited()


@pytest.mark.parametrize("command_name, options, expected", [
    ("share", [{"name": "url", "type": 3, "value": "https://youtu.be/Abc_123-Xyz"}], "沒有儲存"),
    ("share", [{"name": "url", "type": 3, "value": "https://evil.example/@everyone"}], "只支援"),
    ("exchange", [], "未扣除 Nickel，也未建立推薦紀錄"),("balance", [], "無法查詢餘額"), ("history", [], "無法查詢歷史"),
    ("daily", [{"name": "mode", "type": 3, "value": "on"}], "設定未保存"),
    ("daily", [{"name": "mode", "type": 3, "value": "off"}], "設定未保存"),
])
def test_real_sdk_dispatch_private_acceptance_outcomes(gateway_client, gateway_interaction, monkeypatch, command_name, options, expected):
    """@description: Route all five published command shapes through the actual SDK into private transport-only replies.
    @param gateway_client: Real local registry with guarded admission and pure services.
    @param gateway_interaction: Synthetic authenticated request.
    @param monkeypatch: Framework completion-event injection facility.
    @param command_name: Published command name.
    @param options: Structured invocation payload matching the local contract.
    @param expected: Required honest response fragment.
    @return: None.
    """
    gateway_interaction.data = {"name": command_name, "type": 1, "options": options}
    monkeypatch.setattr(gateway_client, "dispatch", MagicMock())
    asyncio.run(gateway_client.tree._call(gateway_interaction))
    gateway_interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    reply = gateway_interaction.edit_original_response.call_args.kwargs
    assert expected in reply["content"]
    assert reply["allowed_mentions"].everyone is False and reply["allowed_mentions"].users is False
    assert not any(fragment in reply["content"] for fragment in ("@everyone", "@here", "<@", "synthetic-token", "synthetic-db"))
    gateway_interaction.response.send_message.assert_not_awaited()
    assert gateway_interaction.command_failed is False


@pytest.mark.parametrize("command_name, false_result", [("balance", "0"), ("history", "沒有成功"), ("daily", "已開啟"), ("share", "已記錄投稿")])
def test_acceptance_service_never_claims_persistence(command_name, false_result):
    """@description: Keep transport-only outcomes distinct from unavailable persistent state.
    @param command_name: Operation under review.
    @param false_result: Forbidden misleading success or empty-state fragment.
    @return: None.
    """
    service = GatewayAcceptanceService()
    arguments = {"balance": (123,), "history": (123,), "daily": (123, True), "share": (123, "https://youtu.be/Abc_123-Xyz")}
    reply = asyncio.run(getattr(service, command_name)(*arguments[command_name]))
    assert false_result not in reply
    assert "Development / DB-disabled" in reply
    assert not hasattr(service, "repository")


@pytest.mark.parametrize("stage", ["defer", "edit"])
def test_reply_failure_does_not_retry_service_or_fallback(gateway_client, gateway_interaction, monkeypatch, stage, caplog):
    """@description: Preserve at-most-one callback execution when Discord response transport fails.
    @param gateway_client: Real guarded registry.
    @param gateway_interaction: Captured private response methods.
    @param monkeypatch: Service and event instrumentation facility.
    @param stage: Response transport stage to fail.
    @param caplog: Captured safe diagnostic output.
    @return: None.
    """
    failure = discord.HTTPException(SimpleNamespace(status=500, reason="Synthetic"), "synthetic-token-secret")
    target = gateway_interaction.response.defer if stage == "defer" else gateway_interaction.edit_original_response
    target.side_effect = failure
    operation = AsyncMock(return_value="Development / DB-disabled：無法查詢餘額。")
    monkeypatch.setattr(gateway_client.handlers.service, "balance", operation)
    monkeypatch.setattr(gateway_client, "dispatch", MagicMock())
    asyncio.run(gateway_client.tree._call(gateway_interaction))
    assert operation.await_count == (0 if stage == "defer" else 1)
    gateway_interaction.response.send_message.assert_not_awaited()
    assert gateway_interaction.edit_original_response.await_count == (0 if stage == "defer" else 1)
    assert "secret" not in caplog.text


def test_guard_response_failure_still_blocks_callback(gateway_client, gateway_interaction, caplog):
    """@description: Keep a denied Interaction denied even when its private rejection cannot be sent.
    @param gateway_client: Guarded client with real dispatch.
    @param gateway_interaction: Foreign-guild request to reject.
    @param caplog: Captured diagnostic output.
    @return: None.
    """
    gateway_interaction.guild_id = None
    gateway_interaction.response.send_message.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Synthetic"), "synthetic-secret")
    asyncio.run(gateway_client.tree._call(gateway_interaction))
    assert gateway_interaction.command_failed
    gateway_interaction.response.defer.assert_not_awaited()
    assert "secret" not in caplog.text


def test_local_parameter_contract_matches_baseline(gateway_client):
    """@description: Check option names, types, requiredness and choices rather than only five command names.
    @param gateway_client: Actual unchanged command registration.
    @return: None.
    """
    contract = verify_local_contract(gateway_client.tree)
    options_by_name = {command["name"]: command["options"] for command in contract}
    assert options_by_name["share"][0]["name"] == "url"
    assert options_by_name["share"][0]["required"] is True and options_by_name["share"][0]["type"] == 3
    assert options_by_name["daily"][0]["name"] == "mode"
    assert options_by_name["daily"][0]["choices"] == [{"name": "on", "value": "on"}, {"name": "off", "value": "off"}]
    assert all(not options_by_name[name] for name in ("balance", "history", "exchange"))


@pytest.mark.parametrize("field, replacement", [("name", "link"), ("type", 4), ("required", False), ("choices", [{"name": "yes", "value": "yes"}])])
def test_remote_parameter_drift_rejected(gateway_client, field, replacement):
    """@description: Detect same-name remote commands with incompatible parameter definitions.
    @param gateway_client: Reviewed local registry.
    @param field: Parameter semantics to corrupt.
    @param replacement: Deliberate incompatible option value.
    @return: None.
    """
    payloads = deepcopy(json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["commands"])
    daily_payload = next(command for command in payloads if command["name"] == "daily")
    daily_payload["options"][0][field] = replacement
    with pytest.raises(AcceptanceError):
        verify_remote_payloads(verify_local_contract(gateway_client.tree), payloads)


def test_local_contract_drift_stops_before_login(gateway_client, development_settings):
    """@description: Refuse to connect when existing handler parameter shape drifts from the local baseline.
    @param gateway_client: Actual local registry deliberately altered for this case.
    @param development_settings: Explicit expected target.
    @return: None.
    """
    gateway_client.tree.get_command("share")._params["url"].required = False
    with pytest.raises(AcceptanceError):
        asyncio.run(listener.listen_gateway(gateway_client, development_settings, authorized=True))
    gateway_client.login.assert_not_awaited()
    gateway_client.connect.assert_not_awaited()


@pytest.mark.parametrize("mismatch", ["application", "guild", "remote_parameters", "external_endpoint"])
def test_preconnection_mismatch_closes_without_gateway(gateway_client, monkeypatch, mismatch):
    """@description: Abort before Gateway on wrong identity, unreachable target contract or external interaction routing.
    @param gateway_client: REST-mocked local client.
    @param monkeypatch: Entry factory injection facility.
    @param mismatch: Remote condition incompatible with this listener.
    @return: None.
    """
    if mismatch == "application":
        gateway_client.application_info.return_value.id += 1
    elif mismatch == "guild":
        gateway_client.fetch_guild.return_value.id += 1
    elif mismatch == "remote_parameters":
        gateway_client.tree.fetch_commands.return_value = []
    else:
        gateway_client.application_info.return_value.interactions_endpoint_url = "https://example.invalid/interactions"
    monkeypatch.setattr(entry, "build_gateway_client", MagicMock(return_value=gateway_client))
    with pytest.raises(AcceptanceError):
        asyncio.run(entry.run_gateway_acceptance(entry.argument_parser().parse_args(["listen", "--authorize-gateway"])))
    assert gateway_client.is_closed()
    gateway_client.connect.assert_not_awaited()
    gateway_client.tree.sync.assert_not_awaited()


def test_authorized_listener_only_reads_and_connects(gateway_client, monkeypatch):
    """@description: Verify expected successful wiring without real login, Gateway, sync or database use.
    @param gateway_client: All external transports mocked.
    @param monkeypatch: Entry factory injection facility.
    @return: None.
    """
    monkeypatch.setattr(entry, "build_gateway_client", MagicMock(return_value=gateway_client))
    result = asyncio.run(entry.run_gateway_acceptance(entry.argument_parser().parse_args(["listen", "--authorize-gateway"])))
    assert result["listener_stopped"] is True and result["remote_contract"] == "matched_before_connection"
    assert gateway_client.is_closed()
    gateway_client.connect.assert_awaited_once_with(reconnect=False)
    gateway_client.tree.sync.assert_not_awaited()
    assert gateway_client.tree.fetch_commands.call_args.kwargs["guild"].id == SYNTHETIC_GUILD_ID
    assert gateway_client.intents.value == 0


@pytest.mark.parametrize("failure_kind", ["login", "http", "disconnect", "transport", "cancel"])
def test_failures_close_and_do_not_leak_secrets(gateway_client, monkeypatch, capsys, caplog, failure_kind):
    """@description: Close resources after login, API, network and cancellation failures without raw credentials.
    @param gateway_client: Mocked client under the real CLI boundary.
    @param monkeypatch: Entry factory and CLI injection facility.
    @param capsys: Captured public diagnostic output.
    @param caplog: Captured logs.
    @param failure_kind: Controlled external failure category.
    @return: None.
    """
    from aiohttp import ServerDisconnectedError
    failures = {"login": discord.LoginFailure("synthetic-token-secret"),
                "http": discord.HTTPException(SimpleNamespace(status=500, reason="Synthetic"), "synthetic-db-secret"),
                "disconnect": OSError("synthetic-token-secret"), "transport": ServerDisconnectedError("synthetic-token-secret"), "cancel": KeyboardInterrupt()}
    target = gateway_client.login if failure_kind == "login" else gateway_client.connect
    target.side_effect = failures[failure_kind]
    monkeypatch.setattr(entry, "build_gateway_client", MagicMock(return_value=gateway_client))
    monkeypatch.setattr("sys.argv", ["gateway_acceptance", "listen", "--authorize-gateway"])
    with pytest.raises(SystemExit) as captured_error:
        entry.main()
    assert gateway_client.is_closed()
    assert "secret" not in str(captured_error.value) + capsys.readouterr().out + caplog.text
    gateway_client.tree.sync.assert_not_awaited()


@pytest.mark.parametrize("flag", ["--global-sync", "--authorize-guild-sync", "--db-name", "--token"])
def test_gateway_cli_has_no_sync_or_persistence_flags(flag):
    """@description: Keep gateway opt-in separate from remote publishing or database configuration.
    @param flag: Forbidden command-line capability.
    @return: None.
    """
    with pytest.raises(SystemExit):
        entry.argument_parser().parse_args([flag])


def test_ready_notice_cannot_sync_or_schedule(gateway_client, capsys):
    """@description: Keep on_ready limited to a public readiness notice.
    @param gateway_client: Real local client with mocked external transport.
    @param capsys: Captured public output.
    @return: None.
    """
    asyncio.run(gateway_client.on_ready())
    assert "DB-disabled" in capsys.readouterr().out
    gateway_client.tree.sync.assert_not_awaited()
    gateway_client.login.assert_not_awaited()
    assert "setup_hook" not in type(gateway_client).__dict__


@pytest.mark.parametrize("failure_kind", ["http", "timeout"])
def test_remote_contract_read_failure_blocks_gateway(gateway_client, monkeypatch, failure_kind):
    """@description: Fail closed when remote command parameters cannot be read before connection.
    @param gateway_client: Actual guarded registry with mocked REST transport.
    @param monkeypatch: Entry factory injection facility.
    @param failure_kind: Remote-read failure category.
    @return: None.
    """
    failures = {"http": discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "synthetic-secret"),
                "timeout": TimeoutError("synthetic-secret")}
    gateway_client.tree.fetch_commands.side_effect = failures[failure_kind]
    monkeypatch.setattr(entry, "build_gateway_client", MagicMock(return_value=gateway_client))
    with pytest.raises(type(failures[failure_kind])):
        asyncio.run(entry.run_gateway_acceptance(entry.argument_parser().parse_args(["listen", "--authorize-gateway"])))
    assert gateway_client.is_closed()
    gateway_client.connect.assert_not_awaited()
    gateway_client.tree.sync.assert_not_awaited()


def test_task_cancellation_closes_gateway_client(gateway_client, monkeypatch):
    """@description: Exercise coroutine cancellation cleanup used by asyncio's Ctrl+C handling.
    @param gateway_client: REST and gateway mocked client.
    @param monkeypatch: Entry factory injection facility.
    @return: None.
    """
    gateway_client.connect.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(entry, "build_gateway_client", MagicMock(return_value=gateway_client))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(entry.run_gateway_acceptance(entry.argument_parser().parse_args(["listen", "--authorize-gateway"])))
    assert gateway_client.is_closed()
    gateway_client.tree.sync.assert_not_awaited()


def test_already_acknowledged_denial_has_no_fallback(gateway_client, gateway_interaction):
    """@description: Avoid public or duplicate replies if an unauthorized request is already acknowledged.
    @param gateway_client: Real guarded registry.
    @param gateway_interaction: Already acknowledged foreign-guild request.
    @return: None.
    """
    gateway_interaction.guild_id = None
    gateway_interaction.response.is_done.return_value = True
    asyncio.run(gateway_client.tree._call(gateway_interaction))
    gateway_interaction.response.send_message.assert_not_awaited()
    gateway_interaction.response.defer.assert_not_awaited()
    gateway_interaction.edit_original_response.assert_not_awaited()


def test_remote_contract_checks_more_than_parameter_core(gateway_client):
    """@description: Detect extra options and added autocomplete constraints beyond matching names.
    @param gateway_client: Reviewed local registry.
    @return: None.
    """
    payloads = deepcopy(json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["commands"])
    share_payload = next(command for command in payloads if command["name"] == "share")
    share_payload["options"][0]["autocomplete"] = True
    with pytest.raises(AcceptanceError):
        verify_remote_payloads(verify_local_contract(gateway_client.tree), payloads)
    share_payload["options"][0]["autocomplete"] = False
    share_payload["options"].append({"name": "unexpected", "type": 3})
    with pytest.raises(AcceptanceError):
        verify_remote_payloads(verify_local_contract(gateway_client.tree), payloads)


def test_new_gateway_test_functions_follow_limits():
    """@description: Enforce project function length, nesting and labeled documentation in new tests.
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
