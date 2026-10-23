"""Adapt discord.py direct messages and interaction replies to the exchange delivery protocols.

Only a refusal with Discord error code 50007 (cannot send messages to this user) or a failure before any send is classed as proven unsent; every other HTTP, server, rate-limit, timeout or connection failure is uncertain, because discord.py retries some requests internally and a request may still have been accepted.
"""

import logging
from typing import Any

import discord

from bot.exchange_delivery import DmResult

LOGGER = logging.getLogger(__name__)
CANNOT_SEND_TO_USER = 50007  # [Discord error code]: The DM was refused before a message was created.


class DiscordDirectMessageSender:
    """Send one DM through discord.py on the event loop and classify its outcome by what it proves."""

    def __init__(self, client: Any) -> None:
        """@description: Keep the connected client used to look up recipients.
        @param self: Adapter instance.
        @param client: Connected discord.py client.
        @return: None.
        """
        self._client = client

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Look up the recipient, send one DM, and classify the result without ever resending.
        @param self: Adapter instance.
        @param discord_user_id: Reserved requester.
        @param text: Message body.
        @return: Delivered, not-sent or unknown result.
        """
        try:
            user = await self._client.fetch_user(discord_user_id)  # [GET only]: Nothing has been sent if this fails.
        except Exception as error:
            LOGGER.warning("Recipient lookup failed: %s", type(error).__name__)
            return DmResult.not_sent("recipient_lookup_failed")
        try:
            message = await user.send(text)
        except discord.Forbidden as error:
            if getattr(error, "code", None) == CANNOT_SEND_TO_USER:
                return DmResult.not_sent("dm_closed_50007")  # [documented refusal]: No message exists for this attempt.
            return DmResult.unknown("forbidden_other")
        except Exception as error:
            LOGGER.warning("Direct message outcome unknown: %s", type(error).__name__)
            return DmResult.unknown(type(error).__name__)
        return DmResult.delivered(message.id)


class DiscordEphemeralReplier:
    """Send a requester-only follow-up on an already deferred interaction."""

    def __init__(self, interaction: Any) -> None:
        """@description: Keep the interaction whose follow-up carries the reply.
        @param self: Adapter instance.
        @param interaction: Deferred Discord interaction.
        @return: None.
        """
        self._interaction = interaction

    async def send_ephemeral(self, text: str) -> None:
        """@description: Show one private follow-up that never contains a song link.
        @param self: Adapter instance.
        @param text: Link-free reply text.
        @return: None.
        """
        await self._interaction.followup.send(text, ephemeral=True)
