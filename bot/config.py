"""Read database and Discord execution settings from environment and optional dotenv files.

Validated settings feed explicit application boundaries without exposing credentials in representations.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MYSQL_PORT = 3306
MAX_TCP_PORT = 65535


FORMAL_EXCHANGE_ENV = "JUKEBOX_FORMAL_EXCHANGE"  # [formal /exchange switch]: Absent or a disabling value keeps the route closed; anything else refuses startup.
_ENABLING_VALUES = frozenset({"1", "true", "yes", "on"})  # [explicit enable]: The only values that open the formal route.
_DISABLING_VALUES = frozenset({"0", "false", "no", "off"})  # [explicit disable]: Stated closed, not merely unrecognised.


def load_formal_exchange_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """@description: Read the formal /exchange switch so an unrecognised value can never open the route by accident.
    @param environ: Mapping to read; None reads the process environment. No dotenv file is ever consulted here.
    @return: True only for an explicit enabling value; False when absent or explicitly disabled.
    """
    source = os.environ if environ is None else environ
    raw = source.get(FORMAL_EXCHANGE_ENV)
    if raw is None:
        return False  # [default closed]: The absence of the setting is the closed state.
    value = raw.strip().lower()
    if value in _ENABLING_VALUES:
        return True
    if value in _DISABLING_VALUES:
        return False
    raise ConfigurationError(f"{FORMAL_EXCHANGE_ENV} must be one of 1, true, yes, on, 0, false, no, off")


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


def required_environment_value(setting_name: str, environ: Mapping[str, str] | None = None) -> str:
    """@description: Fail early when a required database setting is absent.
    @param setting_name: Environment key needed to establish the connection.
    @param environ: Mapping to read; None reads the process environment.
    @return: Configured value without including it in diagnostic messages.
    """
    source = os.environ if environ is None else environ
    setting_value = source.get(setting_name)
    if setting_value is None or not setting_value.strip():
        raise ConfigurationError(f"Missing required setting: {setting_name}")
    return setting_value


SECRET_FILE_SUFFIX = "_FILE"  # [secret file pointer suffix]: Environment key naming the file that holds a secret; the path itself is not secret.


def required_secret_value(setting_name: str, environ: Mapping[str, str] | None = None) -> str:
    """@description: Resolve a credential from exactly one explicit source so startup never guesses between secrets.
    @param setting_name: Setting whose value is a secret, such as DB_PASSWORD or DISCORD_BOT_TOKEN.
    @param environ: Mapping to read; None reads the process environment. Dotenv loading stays with the caller.
    @return: Secret value from the environment or from the file named by the matching _FILE setting.
    """
    source = os.environ if environ is None else environ
    file_setting_name = setting_name + SECRET_FILE_SUFFIX
    secret_file_path = source.get(file_setting_name, "")
    if not secret_file_path.strip():
        return required_environment_value(setting_name, source)
    if source.get(setting_name, "").strip():
        raise ConfigurationError(f"{setting_name} and {file_setting_name} must not both be set")
    return read_secret_file(setting_name, secret_file_path)


def read_secret_file(setting_name: str, secret_file_path: str) -> str:
    """@description: Load a secret file once at startup and refuse anything that would leave an empty credential.
    @param setting_name: Setting the file supplies; used in refusal messages instead of any file content.
    @param secret_file_path: Path given by the operator's _FILE setting.
    @return: File content with surrounding line endings removed and non-blank content guaranteed.
    """
    file_setting_name = setting_name + SECRET_FILE_SUFFIX
    try:
        secret_text = Path(secret_file_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigurationError(f"{file_setting_name} must point to a readable UTF-8 secret file") from None
    secret_value = secret_text.strip("\r\n")  # [line ending only]: Docker tools add one trailing newline; any other edge whitespace is refused below.
    if not secret_value.strip():
        raise ConfigurationError(f"{setting_name} secret file must not be empty")
    if secret_value != secret_value.strip():
        raise ConfigurationError(f"{setting_name} secret file must not have spaces or tabs around the value")
    return secret_value


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
        password=required_secret_value("DB_PASSWORD"),
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
    token = required_secret_value("DISCORD_BOT_TOKEN").strip()
    if not token.isascii() or any(not 33 <= ord(character) <= 126 for character in token):
        raise ConfigurationError("DISCORD_BOT_TOKEN must contain only printable ASCII without internal whitespace")
    return DiscordSettings(token=token)


NICKEL_CLAIM_ENV = "JUKEBOX_NICKEL_CLAIM"  # [/claim switch]: Absent or a disabling value keeps Nickel grants closed; anything else refuses startup.


def load_nickel_claim_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """@description: Read the /claim switch with the same strict rules as the formal exchange switch, closed unless explicitly enabled.
    @param environ: Mapping to read; None reads the process environment. No dotenv file is ever consulted here.
    @return: True only for an explicit enabling value; False when absent or explicitly disabled.
    """
    source = os.environ if environ is None else environ
    raw = source.get(NICKEL_CLAIM_ENV)
    if raw is None:
        return False  # [default closed]: The absence of the setting is the closed state.
    value = raw.strip().lower()
    if value in _ENABLING_VALUES:
        return True
    if value in _DISABLING_VALUES:
        return False
    raise ConfigurationError(f"{NICKEL_CLAIM_ENV} must be one of 1, true, yes, on, 0, false, no, off")
