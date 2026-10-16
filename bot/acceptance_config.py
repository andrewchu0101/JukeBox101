"""Read explicit development-guild acceptance settings without loading database credentials.

Environment values identify one expected application and guild; token representations remain secret-safe.
"""

from dataclasses import dataclass

from bot.config import ConfigurationError, DiscordSettings, load_discord_settings, required_environment_value

MAX_DISCORD_SNOWFLAKE = 2**64


@dataclass(frozen=True)
class DevelopmentGuildSettings:
    """Carry one explicitly named application and guild with a secret-safe Discord token."""

    discord: DiscordSettings
    guild_id: int
    application_id: int


def validate_snowflake(identifier: int, setting_name: str) -> int:
    """@description: Reject implicit boolean or string targets before Discord operations.
    @param identifier: Explicit numeric application or guild identifier.
    @param setting_name: Public setting label used in diagnostics.
    @return: Validated positive Discord snowflake.
    """
    if type(identifier) is not int or not 0 < identifier < MAX_DISCORD_SNOWFLAKE:
        raise ConfigurationError(f"{setting_name} must be a positive Discord snowflake")
    return identifier


def environment_snowflake(setting_name: str) -> int:
    """@description: Parse environment IDs without accepting signs, booleans or non-ASCII digits.
    @param setting_name: Explicit development application or guild environment key.
    @return: Positive integer ID suitable for strict target validation.
    """
    configured_identifier = required_environment_value(setting_name)
    if not configured_identifier.isascii() or not configured_identifier.isdecimal():
        raise ConfigurationError(f"{setting_name} must contain ASCII decimal digits")
    return validate_snowflake(int(configured_identifier), setting_name)


def load_development_guild_settings() -> DevelopmentGuildSettings:
    """@description: Keep acceptance independent of both production and test database environments.
    @param: none
    @return: Explicit process-environment settings, without dotenv loading or external access.
    """
    return DevelopmentGuildSettings(
        discord=load_discord_settings(),
        guild_id=environment_snowflake("DISCORD_DEV_GUILD_ID"),
        application_id=environment_snowflake("DISCORD_EXPECTED_APPLICATION_ID"),
    )
