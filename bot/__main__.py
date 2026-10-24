"""Start the explicitly configured Discord application without remote command synchronization.

Only executing this module loads credentials and logs in; imports remain offline and inert.
"""

import logging
from functools import partial

import discord

from bot.config import ConfigurationError, load_database_settings, load_formal_exchange_enabled
from bot.database.repository import MusicRepository
from bot.database.connection import open_connection
from bot.database.submission_repository import SubmissionRepository
from bot.acceptance_config import environment_snowflake, load_development_guild_settings
from bot.gateway_listener import GuildInteractionGuard
from bot.discord_app import JukeBoxClient
from bot.services import MusicService


def main() -> None:
    """@description: Separate explicit login from independently testable client construction.
    @param: none
    @return: None; configuration failures terminate before attempting login.
    """
    logging.basicConfig(level=logging.INFO)
    try:
        guild_settings = load_development_guild_settings()
        owner_id = environment_snowflake("DISCORD_OWNER_USER_ID")
        database_settings = load_database_settings()
        formal_exchange = load_formal_exchange_enabled()  # [formal /exchange]: Off unless the setting is explicitly enabled.
    except ConfigurationError as configuration_error:
        raise SystemExit(str(configuration_error)) from None
    service = MusicService(MusicRepository(database_settings),
                           SubmissionRepository(partial(open_connection, database_settings), owner_id),
                           formal_exchange=formal_exchange)
    client = JukeBoxClient(service, interaction_guard=GuildInteractionGuard(guild_settings))
    try:
        client.run(guild_settings.discord.token, log_handler=None)
    except (discord.LoginFailure, discord.HTTPException, OSError) as startup_error:
        raise SystemExit("Discord startup failed: " + type(startup_error).__name__) from None


if __name__ == "__main__":
    main()
