"""Separate disposable application and migration identities before database I/O.

Only explicit profile-specific environment keys are consumed; passwords remain excluded from settings representations.
"""

import os
from bot.config import ConfigurationError, DatabaseSettings, required_environment_value

APPLICATION_USER = "jukebox101_test_app"  # [application identity]: Never authorized for schema changes.
MIGRATION_USER = "jukebox101_test_migrator"  # [DDL identity]: Separate local account only.
MIGRATION_SWITCH = "JUKEBOX_RUN_BATCH_B_MIGRATION"  # [write authorization]: Disabled unless explicitly enabled.


def validate_profile(settings: DatabaseSettings, migration: bool = False) -> None:
    """@description: Reject an incorrect credential profile before opening a socket.
    @param settings: Explicit connection destination with a redacted password representation.
    @param migration: Whether the dedicated DDL identity is required.
    @return: None.
    """
    expected_user = MIGRATION_USER if migration else APPLICATION_USER  # [required account]: Profiles cannot substitute for each other.
    if (settings.host, settings.port, settings.user, settings.database) != ("127.0.0.1", 3306, expected_user, "jukebox101_test"):
        raise ConfigurationError("TEST_PROFILE_DESTINATION_MISMATCH")
    if not settings.password:
        raise ConfigurationError("TEST_PROFILE_PASSWORD_MISSING")


def profile_settings(switch: str, migration: bool = False) -> DatabaseSettings:
    """@description: Load exactly one explicitly authorized profile without dotenv or fallback.
    @param switch: Exact authorization switch for this entry point.
    @param migration: Select only TEST_MIGRATION_DB keys when true.
    @return: Validated fixed local credentials.
    """
    if os.environ.get(switch) != "1":
        raise ConfigurationError("PREFLIGHT_OR_MIGRATION_NOT_AUTHORIZED")
    prefix = "TEST_MIGRATION_DB_" if migration else "TEST_DB_"  # [exclusive namespace]: No other settings are consulted.
    try:
        port = int(required_environment_value(prefix + "PORT"))
    except ValueError:
        raise ConfigurationError("TEST_PROFILE_PORT_INVALID") from None
    settings = DatabaseSettings(required_environment_value(prefix + "HOST"), port,
                                required_environment_value(prefix + "USER"), required_environment_value(prefix + "PASSWORD"),
                                required_environment_value(prefix + "NAME"))
    validate_profile(settings, migration)
    return settings


def require_migration_execution(settings: DatabaseSettings) -> None:
    """@description: Keep direct runner callers behind the same closed-by-default destination and execution gates.
    @param settings: Dedicated migration credentials supplied by a trusted caller.
    @return: None.
    """
    validate_profile(settings, True)
    if os.environ.get(MIGRATION_SWITCH) != "1":
        raise ConfigurationError("MIGRATION_EXECUTION_NOT_AUTHORIZED")
