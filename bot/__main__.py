"""Start the explicitly configured Discord application without remote command synchronization.

Only executing this module loads credentials and logs in; imports remain offline and inert.
"""

import logging

import discord

from bot.config import ConfigurationError, load_database_settings, load_discord_settings
from bot.database.repository import MusicRepository
from bot.discord_app import JukeBoxClient
from bot.services import MusicService


def main() -> None:
    """@description: Separate explicit login from independently testable client construction.
    @param: none
    @return: None; configuration failures terminate before attempting login.
    """
    logging.basicConfig(level=logging.INFO)
    try:
        discord_settings = load_discord_settings()
        database_settings = load_database_settings()
    except ConfigurationError as configuration_error:
        raise SystemExit(str(configuration_error)) from None
    client = JukeBoxClient(MusicService(MusicRepository(database_settings)))
    try:
        client.run(discord_settings.token, log_handler=None)
    except (discord.LoginFailure, discord.HTTPException, OSError) as startup_error:
        raise SystemExit("Discord startup failed: " + type(startup_error).__name__) from None


if __name__ == "__main__":
    main()
