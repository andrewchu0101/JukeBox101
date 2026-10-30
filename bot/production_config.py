"""Read the production multi-guild Discord settings: one application, one Owner, any guild that installed the Bot.

Application and Owner IDs are mandatory and fixed; an optional Owner management guild hosts the Owner commands.
No guild allowlist exists: the guild of each interaction is validated per request by the interaction policy.
"""

import logging
import os
from dataclasses import dataclass

from bot.acceptance_config import environment_snowflake
from bot.config import DiscordSettings, load_discord_settings

LOGGER = logging.getLogger(__name__)
MAX_STORABLE_SNOWFLAKE = 2**63 - 1  # [PostgreSQL BIGINT]: Discord IDs above this cannot be stored and are refused, never truncated.
OWNER_GUILD_ENV = "DISCORD_OWNER_GUILD_ID"  # [Owner commands]: Optional; when absent the Owner group is not registered.
DEV_GUILD_ENV = "DISCORD_DEV_GUILD_ID"  # [development only]: Ignored by the production entry; used by the dev acceptance tools.


@dataclass(frozen=True)
class ProductionDiscordSettings:
    """Carry the fixed application and Owner identities with a secret-safe token for the multi-guild Gateway."""

    discord: DiscordSettings
    application_id: int  # [authorized application]: Every interaction must carry this application ID.
    owner_id: int  # [Owner identity]: Owner commands and CLI decisions are bound to this user only.
    owner_guild_id: int | None  # [Owner management guild]: Only guild where Owner commands are registered; None disables them.


def storable_snowflake(identifier: int) -> bool:
    """@description: Tell whether a Discord ID fits the database column, so oversized IDs are refused instead of truncated.
    @param identifier: Discord snowflake from an interaction or setting.
    @return: True for a positive integer that fits PostgreSQL BIGINT.
    """
    return type(identifier) is int and 0 < identifier <= MAX_STORABLE_SNOWFLAKE


def load_production_discord_settings() -> ProductionDiscordSettings:
    """@description: Load the multi-guild entry settings without requiring or trusting any single development guild.
    @param: none
    @return: Validated settings; raises ConfigurationError naming the setting only.
    """
    owner_guild_text = os.environ.get(OWNER_GUILD_ENV, "").strip()
    if os.environ.get(DEV_GUILD_ENV, "").strip():
        LOGGER.warning("%s is set but ignored by the production entry; it only targets development tools", DEV_GUILD_ENV)
    return ProductionDiscordSettings(
        discord=load_discord_settings(),
        application_id=environment_snowflake("DISCORD_EXPECTED_APPLICATION_ID"),
        owner_id=environment_snowflake("DISCORD_OWNER_USER_ID"),
        owner_guild_id=environment_snowflake(OWNER_GUILD_ENV) if owner_guild_text else None,
    )
