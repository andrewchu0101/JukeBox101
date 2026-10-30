"""Build a guild-restricted listener using the existing client, registry and private response handlers.

Only an explicitly authorized session may log in, inspect remote contracts and connect without synchronization.
"""

import asyncio
import json
import logging

import discord

from bot.acceptance_config import DevelopmentGuildSettings, validate_snowflake
from bot.command_contract import verify_local_contract, verify_remote_payloads
from bot.discord_app import JukeBoxClient
from bot.gateway_service import GatewayAcceptanceService
from bot.guild_acceptance import AcceptanceError, REST_TIMEOUT_SECONDS, local_preflight, login_smoke

LOGGER = logging.getLogger(__name__)


class GuildInteractionGuard:
    """Admit only application-command interactions from the explicitly configured guild and application."""

    def __init__(self, settings: DevelopmentGuildSettings) -> None:
        """@description: Bind dispatch admission to the same targets verified before gateway startup.
        @param self: Listener admission policy.
        @param settings: Explicit expected application and development guild.
        @return: None.
        """
        self.guild_id = validate_snowflake(settings.guild_id, "DISCORD_DEV_GUILD_ID")  # [authorized guild]: DM and other guilds fail admission.
        self.application_id = validate_snowflake(settings.application_id, "DISCORD_EXPECTED_APPLICATION_ID")  # [authorized application]: Match each Interaction.

    async def __call__(self, interaction: discord.Interaction) -> bool:
        """@description: Reject DM, foreign-guild or foreign-application dispatch before callback execution.
        @param self: Listener admission policy.
        @param interaction: Incoming framework interaction, before options are parsed.
        @return: Whether the registered slash command may execute.
        """
        if (interaction.guild_id == self.guild_id and interaction.application_id == self.application_id
                and interaction.type is discord.InteractionType.application_command):
            return True
        await _reject_interaction(interaction)
        return False


async def _reject_interaction(interaction: discord.Interaction) -> None:
    """@description: Refuse unauthorized transport privately without fallback or callback retries.
    @param interaction: Incoming request that failed the listener's admission policy.
    @return: None; response failures are logged only by safe class name.
    """
    try:
        if interaction.response.is_done():
            return
        await interaction.response.send_message(
            "此入口只接受指定私人伺服器與 Application 的指令。",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.HTTPException, OSError) as transport_error:
        LOGGER.warning("Gateway rejection reply failed: %s", type(transport_error).__name__)


def build_gateway_client(settings: DevelopmentGuildSettings) -> JukeBoxClient:
    """@description: Reuse the existing bot with transport-only services and a mandatory admission guard.
    @param settings: Explicit development target configuration.
    @return: Offline client with no repository or persistence dependencies.
    """
    client = JukeBoxClient(GatewayAcceptanceService(), interaction_guard=GuildInteractionGuard(settings))
    client.event(on_ready)
    return client


async def on_ready() -> None:
    """@description: Give the operator a safe readiness notice without synchronization or background work.
    @param: none
    @return: None.
    """
    print("Development / DB-disabled Gateway ready; Ctrl+C to close. No sync, persistence, scheduler or DM.")


def gateway_preflight(client: JukeBoxClient, settings: DevelopmentGuildSettings) -> dict:
    """@description: Check full local parameter contracts without claiming remote registry equivalence.
    @param client: Existing client with acceptance services and guarded dispatch.
    @param settings: Explicit application and guild configuration.
    @return: Public local-only summary stating the unverified remote contract status.
    """
    summary = local_preflight(client, settings)
    summary["parameter_contract"] = verify_local_contract(client.tree)
    summary["remote_contract"] = "not_checked_offline"
    summary["gateway_connected"] = False
    return summary


async def verify_gateway_remote_contract(client: JukeBoxClient, settings: DevelopmentGuildSettings, local_contract: list[dict]) -> None:
    """@description: Refuse gateway connection if remote parameters or delivery routing cannot be verified.
    @param client: REST-authenticated existing client, not yet connected to the gateway.
    @param settings: Explicit expected application and development guild.
    @param local_contract: Verified local parameter contract.
    @return: None; read failures or mismatches abort without modifying remote commands.
    """
    application = await asyncio.wait_for(client.application_info(), timeout=REST_TIMEOUT_SECONDS)
    if application.id != settings.application_id or getattr(application, "interactions_endpoint_url", None):
        raise AcceptanceError("Application identity or external Interaction endpoint prevents verified gateway routing")
    published = await asyncio.wait_for(client.tree.fetch_commands(guild=discord.Object(id=settings.guild_id)), timeout=REST_TIMEOUT_SECONDS)
    verify_remote_payloads(local_contract, [command.to_dict() for command in published])


async def listen_gateway(client: JukeBoxClient, settings: DevelopmentGuildSettings, *, authorized: bool = False) -> dict:
    """@description: Connect only after explicit opt-in, identity verification and read-only remote contract comparison.
    @param client: Database-disabled guarded client owned by the caller's async context.
    @param settings: Expected application and development guild configuration.
    @param authorized: Explicit gateway authorization, including necessary login and read-only REST checks.
    @return: Public shutdown summary; does not claim manual Interaction acceptance passed.
    """
    if authorized is not True:
        raise AcceptanceError("Gateway connection requires explicit authorization")
    summary = gateway_preflight(client, settings)
    await login_smoke(client, settings, authorized=True)
    await verify_gateway_remote_contract(client, settings, summary["parameter_contract"])
    summary["remote_contract"] = "matched_before_connection"
    print(json.dumps(summary, sort_keys=True))
    await client.connect(reconnect=False)
    summary["listener_stopped"] = True
    return summary
