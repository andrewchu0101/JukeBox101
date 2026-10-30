"""Read the HTTP interaction deployment settings from the process environment only.

Every value is validated before a socket opens; the bot token and database credentials stay out of representations.
"""

from dataclasses import dataclass, field

from nacl.signing import VerifyKey

from bot.acceptance_config import environment_snowflake
from bot.config import (ConfigurationError, DatabaseSettings, load_database_settings, load_discord_settings,
                        load_formal_exchange_enabled, required_environment_value)

PUBLIC_KEY_ENV = "DISCORD_PUBLIC_KEY"  # [interaction verification]: Application public key from the Developer Portal; not secret, but required.
PUBLIC_KEY_HEX_LENGTH = 64  # [Ed25519 public key]: 32 bytes encoded as hexadecimal.
LISTEN_HOST = "127.0.0.1"  # [loopback only]: The HTTPS reverse proxy on the same host is the sole ingress.
LISTEN_PORT = 8080  # [local port]: Forwarded only by the reverse proxy.


@dataclass(frozen=True)
class HttpInteractionSettings:
    """Carry every validated setting the HTTP endpoint needs; the bot token and database password are excluded from representations."""

    verify_key: VerifyKey
    bot_token: str = field(repr=False)
    guild_id: int
    application_id: int
    owner_id: int
    database: DatabaseSettings
    formal_exchange: bool  # [formal /exchange]: False keeps the existing fixed reply; no debit happens.
    listen_host: str = LISTEN_HOST
    listen_port: int = LISTEN_PORT


def load_verify_key() -> VerifyKey:
    """@description: Parse the application public key once so a malformed value stops startup before any request is served.
    @param: none
    @return: Ed25519 verification key for interaction signatures.
    """
    public_key_hex = required_environment_value(PUBLIC_KEY_ENV).strip()
    if len(public_key_hex) != PUBLIC_KEY_HEX_LENGTH:
        raise ConfigurationError(f"{PUBLIC_KEY_ENV} must be {PUBLIC_KEY_HEX_LENGTH} hexadecimal characters")
    try:
        return VerifyKey(bytes.fromhex(public_key_hex))
    except ValueError:
        raise ConfigurationError(f"{PUBLIC_KEY_ENV} must be hexadecimal") from None


def load_http_interaction_settings() -> HttpInteractionSettings:
    """@description: Collect the HTTP endpoint's settings from the environment, failing before any socket or database connection.
    @param: none
    @return: Validated settings for the HTTP interaction entry point.
    """
    discord_settings = load_discord_settings()
    return HttpInteractionSettings(
        verify_key=load_verify_key(),
        bot_token=discord_settings.token,
        guild_id=environment_snowflake("DISCORD_DEV_GUILD_ID"),
        application_id=environment_snowflake("DISCORD_EXPECTED_APPLICATION_ID"),
        owner_id=environment_snowflake("DISCORD_OWNER_USER_ID"),
        database=load_database_settings(),
        formal_exchange=load_formal_exchange_enabled(),
    )
