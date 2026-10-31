"""Preview and, only with explicit opt-in, publish the slash-command set; the Bot itself never synchronizes at startup.

`preview` is offline. `fetch` reads remote commands over HTTP (no Gateway). `apply` overwrites the remote set for the
chosen target (Discord bulk-overwrite semantics) only when the previewed digest matches and both confirmation flags
are given. Production uses the global target and the owner-guild target, which publishes the guild-scoped Owner group
(global sync never includes it); a single development guild target is kept for testing.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Callable

import discord

from bot.acceptance_config import environment_snowflake, validate_snowflake
from bot.command_contract import normalize_command_payloads
from bot.config import ConfigurationError, load_discord_settings, load_nickel_claim_enabled
from bot.discord_app import OwnerReviewWiring
from bot.production_config import OWNER_GUILD_ENV
from bot.discord_app import JukeBoxClient

REST_TIMEOUT_SECONDS = 20  # [bounded remote call]: Each read or sync call fails instead of waiting indefinitely.
FETCH_FLAG = "--i-understand-this-calls-discord"
APPLY_FLAG = "--confirm-overwrite-remote-commands"
OWNER_GUILD_TARGET = "owner-guild"  # [owner guild target]: Publishes only the guild-scoped Owner group into DISCORD_OWNER_GUILD_ID.


class _CommandsOnlyService:
    """Stand in for the application service so the command tree can be built without any database or credential."""

    def __init__(self, nickel_claim_enabled: bool) -> None:
        """@description: Mirror only the switch that changes the registered command set.
        @param self: Placeholder service.
        @param nickel_claim_enabled: Production /claim switch value.
        @return: None.
        """
        self.nickel_claim_enabled = nickel_claim_enabled


def local_payloads(nickel_claim_enabled: bool | None = None) -> list[dict]:
    """@description: Build the exact global command payloads the production Bot registers, offline.
    @param nickel_claim_enabled: /claim switch; None reads JUKEBOX_NICKEL_CLAIM from the environment.
    @return: Discord command payload dictionaries.
    """
    claim = load_nickel_claim_enabled() if nickel_claim_enabled is None else nickel_claim_enabled
    client = JukeBoxClient(_CommandsOnlyService(claim))
    return [command.to_dict(client.tree) for command in client.tree.get_commands()]


def _unavailable_owner_service():
    """@description: Refuse any Owner service call made while only the command shape is being built offline.
    @param: none
    @return: Never returns.
    """
    raise ConfigurationError("Owner review service is not available while building command payloads")


def owner_guild_configured() -> bool:
    """@description: Tell whether an Owner guild is configured, without validating its value (validation happens on use).
    @param: none
    @return: True when DISCORD_OWNER_GUILD_ID is set to a non-blank value.
    """
    return bool(os.environ.get(OWNER_GUILD_ENV, "").strip())


def _owner_wiring(owner_guild_id: int) -> OwnerReviewWiring:
    """@description: Wire the Owner group the same way production does, with a service that is never called for registration.
    @param owner_guild_id: Owner management guild snowflake.
    @return: Owner review wiring for building the command tree.
    """
    return OwnerReviewWiring(_unavailable_owner_service, environment_snowflake("DISCORD_OWNER_USER_ID"), owner_guild_id,
                             lambda: datetime.now(timezone.utc))


def owner_guild_payloads() -> list[dict]:
    """@description: Build the guild-scoped Owner group exactly as the production Bot registers it, offline.
    @param: none
    @return: Command payloads registered only in DISCORD_OWNER_GUILD_ID.
    """
    owner_guild_id = environment_snowflake(OWNER_GUILD_ENV)
    client = JukeBoxClient(_CommandsOnlyService(False), owner_review=_owner_wiring(owner_guild_id))
    return [command.to_dict(client.tree) for command in client.tree.get_commands(guild=discord.Object(id=owner_guild_id))]


def contract_digest(payloads: list[dict]) -> str:
    """@description: Identify a command set by its invocation contract so apply can prove it publishes what was previewed.
    @param payloads: Local or remote command payloads.
    @return: Hex SHA-256 of the normalized contract.
    """
    normalized = json.dumps(normalize_command_payloads(payloads), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def contract_diff(local: list[dict], remote: list[dict]) -> dict:
    """@description: Show what a sync would add, remove or change, by command name.
    @param local: Local payloads.
    @param remote: Remote payloads (from fetch or a saved export).
    @return: Sorted lists of added, removed and changed command names.
    """
    local_by_name = {item["name"]: item for item in normalize_command_payloads(local)}
    remote_by_name = {item["name"]: item for item in normalize_command_payloads(remote)}
    return {"added": sorted(set(local_by_name) - set(remote_by_name)),
            "removed": sorted(set(remote_by_name) - set(local_by_name)),
            "changed": sorted(name for name in set(local_by_name) & set(remote_by_name) if local_by_name[name] != remote_by_name[name])}


def _owner_guild_summary() -> dict | None:
    """@description: Give the Owner guild set its own digest so it is reviewed before the owner-guild apply.
    @param: none
    @return: Digest and command names, or None when no Owner guild is configured.
    """
    if not owner_guild_configured():
        return None
    payloads = owner_guild_payloads()
    return {"digest": contract_digest(payloads), "commands": sorted(item["name"] for item in payloads)}


def preview(remote_file: Path | None) -> dict:
    """@description: Produce the offline preview an operator reviews before any remote change.
    @param remote_file: Optional saved remote export to diff against.
    @return: Preview summary with digest, command names and optional diff.
    """
    local = local_payloads()
    summary = {"mode": "preview", "local_digest": contract_digest(local), "commands": sorted(item["name"] for item in local),
               "owner_guild": _owner_guild_summary(), "remote_compared": remote_file is not None}
    if remote_file is not None:
        summary["diff"] = contract_diff(local, json.loads(remote_file.read_text(encoding="utf-8")))
    return summary


async def _remote_session(token: str, application_id: int, guild_id: int | None, action: Callable, client_factory: Callable) -> Any:
    """@description: Log in over HTTP only, verify the application, run one remote action and always close.
    @param token: Bot token (never printed).
    @param application_id: Expected application snowflake.
    @param guild_id: Development guild target, or None for global.
    @param action: Coroutine function (client, guild_object) -> result.
    @param client_factory: Builds the client; replaced in offline tests.
    @return: Action result.
    """
    client = client_factory()
    try:
        await asyncio.wait_for(client.login(token), timeout=REST_TIMEOUT_SECONDS)
        application = await asyncio.wait_for(client.application_info(), timeout=REST_TIMEOUT_SECONDS)
        if application.id != application_id:
            raise ConfigurationError("Token belongs to a different application; nothing was read or changed")
        guild = None if guild_id is None else discord.Object(id=validate_snowflake(guild_id, "guild"))
        return await asyncio.wait_for(action(client, guild), timeout=REST_TIMEOUT_SECONDS)
    finally:
        await client.close()


async def fetch_remote(client: JukeBoxClient, guild: discord.Object | None) -> list[dict]:
    """@description: Read the published commands of the target without changing them.
    @param client: Logged-in client.
    @param guild: Development guild, or None for global commands.
    @return: Remote command payloads.
    """
    return [command.to_dict() for command in await client.tree.fetch_commands(guild=guild)]


async def apply_sync(client: JukeBoxClient, guild: discord.Object | None, copy_global: bool) -> list[str]:
    """@description: Overwrite the target's remote command set with the local one (Discord bulk overwrite).
    @param client: Logged-in client built from the same production registration.
    @param guild: Target guild, or None for global commands.
    @param copy_global: True only for the development guild, which receives copies of the global commands.
    @return: Names of the commands Discord reports as published.
    """
    if copy_global and guild is not None:
        client.tree.copy_global_to(guild=guild)
    return sorted(command.name for command in await client.tree.sync(guild=guild))


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    """@description: Separate offline preview from remote reads and writes, each with its own explicit opt-in.
    @param argv: Arguments without the program name.
    @return: Parsed arguments; refuses missing confirmations before anything runs.
    """
    parser = argparse.ArgumentParser(prog="python -m bot.command_sync", description="Preview or explicitly publish slash commands")
    parser.add_argument("mode", choices=("preview", "fetch", "apply"))
    parser.add_argument("--target", choices=("global", "dev-guild", OWNER_GUILD_TARGET), default="global")
    parser.add_argument("--guild-id", type=int, help="development guild for --target dev-guild")
    parser.add_argument("--remote-file", type=Path, help="saved remote export to diff against (preview) or to write (fetch)")
    parser.add_argument("--expected-digest", help="local_digest from the reviewed preview (apply)")
    parser.add_argument(FETCH_FLAG, dest="allow_remote", action="store_true")
    parser.add_argument(APPLY_FLAG, dest="allow_overwrite", action="store_true")
    arguments = parser.parse_args(argv)
    if (arguments.target == "dev-guild") != (arguments.guild_id is not None):
        parser.error("--guild-id is required with, and only with, --target dev-guild")
    if arguments.mode in ("fetch", "apply") and not arguments.allow_remote:
        parser.error(f"{arguments.mode} contacts Discord; add {FETCH_FLAG}")
    if arguments.mode == "fetch" and arguments.remote_file is None:
        parser.error("fetch requires --remote-file to write the export")
    if arguments.mode == "apply" and (not arguments.allow_overwrite or not arguments.expected_digest):
        parser.error(f"apply overwrites remote commands; add {APPLY_FLAG} and --expected-digest from preview")
    return arguments


def _client_factory_for(is_owner: bool, owner_guild_id: int | None) -> Callable:
    """@description: Build the client with the same registration production uses for the chosen target.
    @param is_owner: True for the owner-guild target, which must also carry the Owner group.
    @param owner_guild_id: Owner guild snowflake, used only when is_owner is True.
    @return: Zero-argument client builder.
    """
    if is_owner:
        return lambda: JukeBoxClient(_CommandsOnlyService(False), owner_review=_owner_wiring(owner_guild_id))
    return lambda: JukeBoxClient(_CommandsOnlyService(load_nickel_claim_enabled()))


def run_remote(arguments: argparse.Namespace, client_factory: Callable | None = None) -> dict:
    """@description: Execute an explicitly authorized fetch or apply, refusing apply when the local set differs from the preview.
    @param arguments: Parsed and confirmed arguments.
    @param client_factory: Optional client builder for tests.
    @return: Summary of what was read or published.
    """
    is_owner = arguments.target == OWNER_GUILD_TARGET
    if is_owner and not owner_guild_configured():
        raise ConfigurationError(f"{OWNER_GUILD_ENV} is required for the owner-guild target; nothing was changed")
    guild_id = environment_snowflake(OWNER_GUILD_ENV) if is_owner else arguments.guild_id
    local = owner_guild_payloads() if is_owner else local_payloads()
    if arguments.mode == "apply" and contract_digest(local) != arguments.expected_digest:
        raise ConfigurationError("Local command set differs from the reviewed preview digest; nothing was changed")
    factory = client_factory or _client_factory_for(is_owner, guild_id)
    action = fetch_remote if arguments.mode == "fetch" else partial(apply_sync, copy_global=arguments.target == "dev-guild")
    result = asyncio.run(_remote_session(load_discord_settings().token, environment_snowflake("DISCORD_EXPECTED_APPLICATION_ID"),
                                         guild_id, action, factory))
    if arguments.mode == "fetch":
        arguments.remote_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"mode": "fetch", "target": arguments.target, "remote_digest": contract_digest(result), "diff": contract_diff(local, result)}
    return {"mode": "apply", "target": arguments.target, "published": result, "local_digest": contract_digest(local),
            "note": "Global command changes can take time to appear in clients."}


def main(argv: list[str] | None = None) -> int:
    """@description: Run one command-sync mode and print a token-free JSON summary.
    @param argv: Optional argument list for tests.
    @return: 0 on success, 2 on refusal or remote failure.
    """
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        summary = preview(arguments.remote_file) if arguments.mode == "preview" else run_remote(arguments)
    except (ConfigurationError, discord.HTTPException, asyncio.TimeoutError, OSError) as error:
        print(json.dumps({"result": "refused", "reason": type(error).__name__,
                          "detail": str(error) if isinstance(error, ConfigurationError) else None}))
        return 2
    print(json.dumps({"result": "ok", **summary}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
