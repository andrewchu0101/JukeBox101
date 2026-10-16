"""Read database and Discord execution settings from environment and optional dotenv files.

Validated settings feed explicit application boundaries without exposing credentials in representations.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MYSQL_PORT = 3306
MAX_TCP_PORT = 65535


class ConfigurationError(ValueError):
    """Report missing or invalid setting names without including secret values."""


@dataclass(frozen=True)
class DatabaseSettings:
    """Carry validated connection settings; exclude credentials from representations."""

    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str


@dataclass(frozen=True)
class DiscordSettings:
    """Carry secret-safe execution settings with remote synchronization disabled by default."""

    token: str = field(repr=False)


def required_environment_value(setting_name: str) -> str:
    """@description: Fail early when a required database setting is absent.
    @param setting_name: Environment key needed to establish the connection.
    @return: Configured value without including it in diagnostic messages.
    """
    setting_value = os.environ.get(setting_name)
    if setting_value is None or not setting_value.strip():
        raise ConfigurationError(f"Missing required setting: {setting_name}")
    return setting_value


def configured_database_port() -> int:
    """@description: Reject malformed ports before contacting MySQL.
    @param: none
    @return: TCP port within the valid network range.
    """
    try:
        database_port = int(os.environ.get("DB_PORT", str(DEFAULT_MYSQL_PORT)))
    except ValueError:
        raise ConfigurationError("DB_PORT must be an integer") from None
    if not 1 <= database_port <= MAX_TCP_PORT:
        raise ConfigurationError("DB_PORT must be between 1 and 65535")
    return database_port


def load_database_settings(dotenv_path: Path | None = None) -> DatabaseSettings:
    """@description: Support local files while keeping platform environment values authoritative.
    @param dotenv_path: Explicit optional dotenv file; None reads only environment variables.
    @return: Database settings independent of any Discord token.
    """
    if dotenv_path is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False)
    # [application account]: Administrative accounts are forbidden for application access.
    database_user = required_environment_value("DB_USER")
    if database_user.casefold() == "root":
        raise ConfigurationError("DB_USER must be a non-administrative application account")
    return DatabaseSettings(
        host=required_environment_value("DB_HOST"),
        port=configured_database_port(),
        user=database_user,
        password=required_environment_value("DB_PASSWORD"),
        database=required_environment_value("DB_NAME"),
    )


def load_discord_settings(dotenv_path: Path | None = None) -> DiscordSettings:
    """@description: Require a real configured token only at the explicit execution boundary.
    @param dotenv_path: Optional local settings file, without overriding platform environment.
    @return: Secret-safe Discord execution configuration, without logging in.
    """
    if dotenv_path is not None:
        load_dotenv(dotenv_path=dotenv_path, override=False)
    # [execution credential]: Reject header-control input without exposing its contents.
    token = required_environment_value("DISCORD_BOT_TOKEN").strip()
    if not token.isascii() or any(not 33 <= ord(character) <= 126 for character in token):
        raise ConfigurationError("DISCORD_BOT_TOKEN must contain only printable ASCII without internal whitespace")
    return DiscordSettings(token=token)
