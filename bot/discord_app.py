"""Register local Discord commands and translate interactions into service calls.

Construction needs no credentials or network; remote synchronization requires an explicit entry call.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from collections.abc import Awaitable, Callable

import discord
from discord import app_commands

from bot.discord_owner_review import build_owner_group
from bot.owner_review import OwnerReviewService
from bot.discord_exchange import DiscordDirectMessageSender, DiscordEphemeralReplier
from bot.services import MusicService, ServiceUnavailable
from bot.urls import InvalidVideoUrl

LOGGER = logging.getLogger(__name__)


class SafeCommandTree(app_commands.CommandTree):
    """Contain unexpected callback failures without logging private command arguments."""

    def __init__(self, client: discord.Client, *, interaction_guard: Callable[[discord.Interaction], Awaitable[bool]] | None = None) -> None:
        """@description: Allow an isolated listener to guard dispatch before any command callback.
        @param self: Client-owned command registry.
        @param client: Existing Discord client owning the registry.
        @param interaction_guard: Optional asynchronous admission policy; production defaults unchanged.
        @return: None.
        """
        super().__init__(client)
        self.interaction_guard = interaction_guard  # [dispatch admission]: Checked before framework argument parsing.

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """@description: Enforce the injected listener boundary before invoking business callbacks.
        @param self: Client-owned command registry.
        @param interaction: Incoming Discord command interaction.
        @return: Whether framework command dispatch may continue.
        """
        if self.interaction_guard is None:
            return True
        return await self.interaction_guard(interaction)

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """@description: Keep framework error logging from exposing private inputs or secrets.
        @param self: Client-owned command registry.
        @param interaction: Request whose callback failed.
        @param error: Framework-wrapped command failure, logged only by class name.
        @return: None.
        """
        LOGGER.error("Command failed: %s", type(error).__name__)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("指令暫時無法完成，請稍後再試。", ephemeral=True)
                return
            await interaction.edit_original_response(content="指令暫時無法完成，請稍後再試。")
        except (discord.HTTPException, OSError) as transport_error:
            LOGGER.warning("Error reply failed: %s", type(transport_error).__name__)


class JukeBoxClient(discord.Client):
    """Own a local command tree with no automatic remote sync or background delivery."""

    def __init__(self, service: MusicService, *, interaction_guard: Callable[[discord.Interaction], Awaitable[bool]] | None = None,
                 owner_review: "OwnerReviewWiring | None" = None) -> None:
        """@description: Build a minimal-intent client independently of login and credentials.
        @param self: Discord application instance.
        @param service: Discord-independent application operations.
        @param interaction_guard: Optional listener-specific command admission policy.
        @param owner_review: Optional Owner review wiring; when given, the guild-scoped owner group is registered locally.
        @return: None.
        """
        super().__init__(intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none())
        self.tree = SafeCommandTree(self, interaction_guard=interaction_guard)  # [command registry]: Local registration only.
        self.handlers = CommandHandlers(service)
        register_commands(self.tree, self.handlers)
        if owner_review is not None:
            self.tree.add_command(build_owner_group(owner_review.service_provider, owner_review.owner_id,
                                                    owner_review.guild_id, owner_review.now))


@dataclass(frozen=True)
class OwnerReviewWiring:
    """Carry the Owner review collaborators the client needs; the group itself is built by discord_owner_review."""

    service_provider: Callable[[], OwnerReviewService]  # [review service]: Returns the synchronous review service.
    owner_id: int  # [Owner identity]: Configured Discord user id checked on every Owner command.
    guild_id: int  # [private guild]: The only guild where the owner group is registered.
    now: Callable[[], datetime]  # [clock]: Supplies timestamps for evidence and judgements.


class CommandHandlers:
    """Route slash commands to services through a single safe interaction boundary."""

    def __init__(self, service: MusicService) -> None:
        """@description: Inject application operations without depending on Discord transport.
        @param self: Command handler instance.
        @param service: Business collaborator used by all command callbacks.
        @return: None.
        """
        self.service = service  # [application collaborator]: Handlers contain no SQL.

    async def share(self, interaction: discord.Interaction, url: str) -> None:
        """@description: Record a private YouTube submission through the application service.
        @param self: Handler routing this command.
        @param interaction: Discord request carrying the authenticated submitter.
        @param url: User-provided YouTube video URL.
        @return: None.
        """
        await _respond(interaction, self.service.share, interaction.user.id, url, f"discord:{interaction.id}")

    async def exchange(self, interaction: discord.Interaction) -> None:
        """@description: Route /exchange: the explicit synthetic flow first, then the formal flow when its switch is on, otherwise the existing fixed reply.
        @param self: Handler routing this command.
        @param interaction: Discord request carrying the recipient identity.
        @return: None.
        """
        if getattr(self.service, "synthetic_exchange_enabled", False) is True:  # [strict route]: Only an explicit boolean True selects the synthetic flow first.
            await interaction.response.defer(ephemeral=True)  # [bounded acknowledgement]: DM work may exceed the initial response window.
            await self.service.synthetic_exchange(interaction.user.id, f"discord:{interaction.id}",
                                                  DiscordDirectMessageSender(interaction.client),
                                                  DiscordEphemeralReplier(interaction))
            return
        if getattr(self.service, "formal_exchange_enabled", False) is True:  # [strict route]: Only an explicit boolean True opens the formal flow.
            await interaction.response.defer(ephemeral=True)  # [bounded acknowledgement]: Same private deferral as the synthetic route.
            await self.service.formal_exchange(interaction.user.id, f"discord:{interaction.id}",
                                               DiscordDirectMessageSender(interaction.client),
                                               DiscordEphemeralReplier(interaction))
            return
        await _respond(interaction, self.service.exchange, interaction.user.id)  # [production gate]: Unchanged fixed reply; no debit.

    async def balance(self, interaction: discord.Interaction) -> None:
        """@description: Reply privately with the requesting recipient's credit balance.
        @param self: Handler routing this command.
        @param interaction: Discord request carrying the recipient identity.
        @return: None.
        """
        await _respond(interaction, self.service.balance, interaction.user.id)

    async def history(self, interaction: discord.Interaction) -> None:
        """@description: Reply privately with the injected service's caller-scoped history.
        @param self: Handler routing this command.
        @param interaction: Discord request carrying the recipient identity.
        @return: None.
        """
        await _respond(interaction, self.service.history, interaction.user.id)

    @app_commands.choices(mode=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    async def daily(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        """@description: Persist an explicit on or off preference without scheduling work.
        @param self: Handler routing this command.
        @param interaction: Discord request carrying the recipient identity.
        @param mode: Registered on/off slash-command choice.
        @return: None.
        """
        if mode.value not in {"on", "off"}:
            await _respond(interaction, _invalid_daily_choice)
            return
        await _respond(interaction, self.service.daily, interaction.user.id, mode.value == "on")


def register_commands(tree: app_commands.CommandTree, handlers: CommandHandlers) -> None:
    """@description: Register five local commands without contacting Discord's API.
    @param tree: Empty client-owned command registry.
    @param handlers: Callback owner with an injected application service.
    @return: None.
    """
    tree.add_command(app_commands.Command(name="share", description="投稿 YouTube 影片供資格驗證", callback=handlers.share))
    tree.add_command(app_commands.Command(name="exchange", description="查詢交換功能開放狀態", callback=handlers.exchange))
    tree.add_command(app_commands.Command(name="balance", description="查詢可用交換點數", callback=handlers.balance))
    tree.add_command(app_commands.Command(name="history", description="查詢本人投稿及目前審核狀態", callback=handlers.history))
    tree.add_command(app_commands.Command(name="daily", description="設定每日推薦偏好（排程尚未啟用）", callback=handlers.daily))


async def synchronize_commands(client: JukeBoxClient, *, guild_id: int | None = None, global_sync: bool = False) -> list:
    """@description: Require an explicit target before any remote command synchronization.
    @param client: Logged-in client whose local commands should be published.
    @param guild_id: Explicit development guild snowflake, mutually exclusive with global sync.
    @param global_sync: Explicit authorization to synchronize application-wide commands.
    @return: Commands returned by Discord, or an empty list when synchronization is disabled.
    """
    if global_sync and guild_id is not None:
        raise ValueError("Choose exactly one synchronization target")
    if guild_id is not None:
        if isinstance(guild_id, bool) or not isinstance(guild_id, int) or not 0 < guild_id < 2**64:
            raise ValueError("Guild ID must be a positive Discord snowflake")
        guild = discord.Object(id=guild_id)
        client.tree.copy_global_to(guild=guild)
        return await client.tree.sync(guild=guild)
    if global_sync:
        return await client.tree.sync()
    return []


async def _respond(interaction: discord.Interaction, operation: Callable[..., Awaitable[str]], *arguments) -> None:
    """@description: Acknowledge before blocking work and keep transport failures sanitized.
    @param interaction: Request to acknowledge and reply to privately.
    @param operation: Application use case returning user-visible content.
    @param arguments: Authenticated business inputs passed to the service.
    @return: None.
    """
    try:
        await interaction.response.defer(ephemeral=True, thinking=True)
        reply = await _service_reply(operation, *arguments)
        await interaction.edit_original_response(content=reply, allowed_mentions=discord.AllowedMentions.none())
    except (discord.HTTPException, OSError) as transport_error:
        LOGGER.warning("Discord interaction failed: %s", type(transport_error).__name__)


async def _service_reply(operation: Callable[..., Awaitable[str]], *arguments) -> str:
    """@description: Convert expected validation and infrastructure errors into safe replies.
    @param operation: Awaitable service operation without Discord dependencies.
    @param arguments: Validated command inputs.
    @return: Successful content or a sanitized expected error.
    """
    try:
        return await operation(*arguments)
    except (InvalidVideoUrl, ServiceUnavailable) as expected_error:
        return str(expected_error)


async def _invalid_daily_choice() -> str:
    """@description: Reject invalid daily choices without persisting a guessed preference.
    @param: none
    @return: User-facing valid-choice guidance.
    """
    return "每日推薦設定只接受 on 或 off。"
