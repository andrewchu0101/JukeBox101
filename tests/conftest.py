"""Supply isolated configuration for unit tests.

Environment fixtures prevent local credentials from affecting mock-based verification.
"""

import pytest

from bot.config import load_database_settings


@pytest.fixture
def database_settings(monkeypatch):
    """@description: Keep unit tests independent of credentials and local dotenv files.
    @param monkeypatch: Pytest environment isolation facility.
    @return: Valid settings containing a synthetic password.
    """
    # [test configuration]: Synthetic credentials must never reach a real connection.
    environment_values = {
        "DB_HOST": "127.0.0.1", "DB_PORT": "3306",
        "DB_USER": "jukebox101_app", "DB_PASSWORD": "synthetic-secret",
        "DB_NAME": "jukebox101",
    }
    for setting_name, setting_value in environment_values.items():
        monkeypatch.setenv(setting_name, setting_value)
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    return load_database_settings()
