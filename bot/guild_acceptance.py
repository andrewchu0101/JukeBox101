"""Prepare local command checks and explicitly authorized Discord REST acceptance steps.

The existing client and services are reused with database access disabled; no gateway or scheduler starts.
"""

import asyncio
from collections.abc import Callable

import discord

from bot.acceptance_config import DevelopmentGuildSettings, validate_snowflake
from bot.discord_app import JukeBoxClient, synchronize_commands
from bot.services import MusicService, ServiceUnavailable

EXPECTED_COMMAND_NAMES = ("balance", "daily", "exchange", "history", "share")
REST_TIMEOUT_SECONDS = 30


class AcceptanceError(RuntimeError):
    """Stop acceptance before an unverified target or potentially destructive synchronization."""


class DisabledAcceptanceRepository:
    """Refuse every persistence operation without retaining any database destination or credentials."""

    def __getattr__(self, operation_name: str):
        """@description: Fail closed if any acceptance callback attempts persistence.
        @param self: Database-disabled acceptance collaborator.
        @param operation_name: Requested repository attribute, never included in diagnostics.
        @return: No value; persistence is always disabled for this entry.
        """
        raise ServiceUnavailable("驗收入口未啟用資料庫；本次未保存或變更資料。")


def build_acceptance_client() -> JukeBoxClient:
    """@description: Reuse the production command registry with no database access path.
    @param: none
    @return: Locally initialized client, without login, gateway or remote synchronization.
    """
    return JukeBoxClient(MusicService(DisabledAcceptanceRepository()))


def local_preflight(client: JukeBoxClient, settings: DevelopmentGuildSettings) -> dict:
    """@description: Inspect local acceptance targets and command registration without network access.
    @param client: Existing client with locally registered commands.
    @param settings: Explicit development targets and secret-safe token settings.
    @return: Public target and command summary, excluding credentials.
    """
    validate_snowflake(settings.guild_id, "DISCORD_DEV_GUILD_ID")
    validate_snowflake(settings.application_id, "DISCORD_EXPECTED_APPLICATION_ID")
    if not settings.discord.token.strip():
        raise AcceptanceError("DISCORD_BOT_TOKEN is required")
    command_names = tuple(sorted(command.name for command in client.tree.get_commands()))
    if command_names != EXPECTED_COMMAND_NAMES:
        raise AcceptanceError("Local commands differ from the approved five-command registry")
    return {"application_id": settings.application_id, "guild_id": settings.guild_id,
            "commands": list(command_names), "database": "disabled", "automatic_sync": False}


async def login_smoke(client: JukeBoxClient, settings: DevelopmentGuildSettings, *, authorized: bool = False) -> dict:
    """@description: Verify one explicitly authorized REST login without receiving commands or using a database.
    @param client: Existing database-disabled client owned by the caller's async context.
    @param settings: Expected application and development guild.
    @param authorized: Explicit opt-in for a real Discord REST login.
    @return: Safe public identity summary after application and guild verification.
    """
    if authorized is not True:
        raise AcceptanceError("Discord login requires explicit authorization")
    summary = local_preflight(client, settings)
    await asyncio.wait_for(client.login(settings.discord.token), timeout=REST_TIMEOUT_SECONDS)
    await verify_remote_identity(client, settings)
    return summary


async def verify_remote_identity(client: JukeBoxClient, settings: DevelopmentGuildSettings) -> None:
    """@description: Refuse a token belonging to another application or an inaccessible guild.
    @param client: REST-authenticated client, without a running gateway.
    @param settings: Human-provided expected application and guild IDs.
    @return: None; mismatches abort before any sync.
    """
    application = await asyncio.wait_for(client.application_info(), timeout=REST_TIMEOUT_SECONDS)
    if application.id != settings.application_id or client.application_id != settings.application_id:
        raise AcceptanceError("Authenticated application differs from the expected application")
    guild = await asyncio.wait_for(client.fetch_guild(settings.guild_id), timeout=REST_TIMEOUT_SECONDS)
    if guild.id != settings.guild_id:
        raise AcceptanceError("Returned guild differs from the explicit development guild")


async def require_empty_remote_guild(client: JukeBoxClient, guild_id: int) -> None:
    """@description: Avoid bulk sync overwriting any existing application-owned guild command.
    @param client: Authenticated application client.
    @param guild_id: Explicit validated development guild identifier.
    @return: None; any existing commands or unreadable remote state stops acceptance.
    """
    guild = discord.Object(id=validate_snowflake(guild_id, "DISCORD_DEV_GUILD_ID"))
    remote_commands = await asyncio.wait_for(client.tree.fetch_commands(guild=guild), timeout=REST_TIMEOUT_SECONDS)
    if remote_commands:
        raise AcceptanceError("Remote guild commands already exist; stop without overwriting or deleting them")


def confirmation_text(settings: DevelopmentGuildSettings) -> str:
    """@description: Bind manual confirmation to one visible application and guild.
    @param settings: Explicit human-provided development targets.
    @return: Exact typed confirmation required before synchronization.
    """
    return f"SYNC {settings.application_id} {settings.guild_id}"


async def guild_sync(client: JukeBoxClient, settings: DevelopmentGuildSettings, *, authorized: bool = False,
                     confirm: Callable[[str], str] = input) -> dict:
    """@description: Permit one reviewed guild-only sync after identity, remote state and human confirmation.
    @param client: REST-authenticated database-disabled client, without gateway processing.
    @param settings: Expected application and guild IDs.
    @param authorized: Independent explicit opt-in for remote guild synchronization.
    @param confirm: Human input boundary; tests inject deterministic confirmation or cancellation.
    @return: Public synchronization summary, never tokens or database credentials.
    """
    if authorized is not True:
        raise AcceptanceError("Guild sync requires separate explicit authorization")
    summary = local_preflight(client, settings)
    await verify_remote_identity(client, settings)
    await require_empty_remote_guild(client, settings.guild_id)
    prompt = f"Application ID: {settings.application_id}\nGuild ID: {settings.guild_id}\nCommands: {', '.join(summary['commands'])}\nEnsure no other operator is changing this target.\nType {confirmation_text(settings)} to sync, or cancel: "
    if confirm(prompt) != confirmation_text(settings):
        raise AcceptanceError("Guild sync cancelled; no remote commands changed")
    if local_preflight(client, settings) != summary:
        raise AcceptanceError("Local command registry changed during confirmation")
    await verify_remote_identity(client, settings)
    await require_empty_remote_guild(client, settings.guild_id)
    published = await asyncio.wait_for(synchronize_commands(client, guild_id=settings.guild_id), timeout=REST_TIMEOUT_SECONDS)
    summary["published_commands"] = sorted(command.name for command in published)
    if tuple(summary["published_commands"]) != EXPECTED_COMMAND_NAMES:
        raise AcceptanceError("Sync response differs from expected commands; inspect remote state before any retry")
    return summary
