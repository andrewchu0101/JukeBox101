"""Admit production slash commands from any guild that installed the Bot, and refuse everything else privately.

The guild of each interaction is taken from the interaction itself and validated; there is no guild allowlist.
Refusals are ephemeral, mention nobody and reveal no configuration, other guild or other user information.
"""

import logging

import discord

from bot.acceptance_config import validate_snowflake
from bot.production_config import storable_snowflake

LOGGER = logging.getLogger(__name__)
DIRECT_MESSAGE_REFUSAL = "JukeBox101 的指令只能在已加入 Bot 的 Discord 伺服器中使用，不支援私訊；本次未執行任何操作。"
UNVERIFIED_REFUSAL = "此互動無法驗證，本次未執行任何操作。"


class MultiGuildInteractionGuard:
    """Check application, guild presence and identifier ranges for every slash command before any callback runs."""

    def __init__(self, application_id: int) -> None:
        """@description: Bind admission to the one configured Discord application.
        @param self: Admission policy.
        @param application_id: Expected application snowflake.
        @return: None.
        """
        self.application_id = validate_snowflake(application_id, "DISCORD_EXPECTED_APPLICATION_ID")  # [authorized application]

    def refusal(self, interaction: discord.Interaction) -> str | None:
        """@description: Decide whether an interaction may run, without touching the database or Discord.
        @param self: Admission policy.
        @param interaction: Incoming interaction before option parsing.
        @return: None when admitted; otherwise the private refusal text.
        """
        if interaction.type is not discord.InteractionType.application_command or interaction.application_id != self.application_id:
            return UNVERIFIED_REFUSAL
        if interaction.guild_id is None:
            return DIRECT_MESSAGE_REFUSAL  # [DM refused]: Direct messages and private channels never reach business code.
        if not storable_snowflake(interaction.guild_id) or not storable_snowflake(interaction.user.id):
            return UNVERIFIED_REFUSAL
        return None

    async def __call__(self, interaction: discord.Interaction) -> bool:
        """@description: Admit or privately refuse a command before its callback.
        @param self: Admission policy.
        @param interaction: Incoming interaction.
        @return: True when the callback may run.
        """
        refusal_text = self.refusal(interaction)
        if refusal_text is None:
            return True
        await reply_privately(interaction, refusal_text)
        return False


async def reply_privately(interaction: discord.Interaction, text: str) -> None:
    """@description: Send one ephemeral refusal, logging only the failure class if Discord rejects it.
    @param interaction: Interaction being refused.
    @param text: Fixed refusal text containing no identifiers.
    @return: None.
    """
    try:
        if interaction.response.is_done():
            return
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except (discord.HTTPException, OSError) as transport_error:
        LOGGER.warning("Interaction refusal reply failed: %s", type(transport_error).__name__)
