"""Verify that database and Discord secrets can come from explicitly named secret files.

Covers the shared secret resolver in bot/config.py without touching Discord, MySQL or Docker.
"""

from pathlib import Path

import pytest

from bot.config import ConfigurationError, DiscordSettings, load_database_settings, load_discord_settings, required_secret_value

# [fake secret]: Deliberately contains quotes, percent signs, braces, a hash and non-ASCII text to prove it never leaks.
FAKE_DATABASE_PASSWORD = "fake-p'w\"%s{}#假值\\x"
# [fake token]: Printable ASCII only, so the Discord token check passes when the file is read correctly.
FAKE_DISCORD_TOKEN = "FAKE.token-value_123"


def write_secret_file(directory: Path, file_name: str, content: str) -> Path:
    """@description: Create a secret file inside a test-owned directory so no real credential path is touched.
    @param directory: Temporary directory provided by pytest.
    @param file_name: Name of the file to create inside that directory.
    @param content: Exact file content, including any newline the test wants to exercise.
    @return: Path to the created file.
    """
    secret_path = directory / file_name
    secret_path.write_text(content, encoding="utf-8")
    return secret_path


def test_secret_file_value_is_read_without_surrounding_newlines(tmp_path):
    """@description: Confirm a secret file supplies exactly the stored value, minus the trailing newline Docker tools add.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertion checks the resolved value.
    """
    secret_path = write_secret_file(tmp_path, "db_password", FAKE_DATABASE_PASSWORD + "\n")
    environ = {"DB_PASSWORD_FILE": str(secret_path)}

    resolved_value = required_secret_value("DB_PASSWORD", environ)

    assert resolved_value == FAKE_DATABASE_PASSWORD


def test_windows_line_ending_is_removed_from_secret_file(tmp_path):
    """@description: Confirm CRLF line endings created on Windows do not become part of the secret.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertion checks the resolved value.
    """
    secret_path = write_secret_file(tmp_path, "db_password", FAKE_DATABASE_PASSWORD + "\r\n")

    resolved_value = required_secret_value("DB_PASSWORD", {"DB_PASSWORD_FILE": str(secret_path)})

    assert resolved_value == FAKE_DATABASE_PASSWORD


def test_environment_value_is_used_when_no_file_is_configured():
    """@description: Keep the existing environment-variable source working for local development.
    @param: none
    @return: None; the assertion checks the resolved value.
    """
    resolved_value = required_secret_value("DB_PASSWORD", {"DB_PASSWORD": FAKE_DATABASE_PASSWORD})

    assert resolved_value == FAKE_DATABASE_PASSWORD


def test_setting_from_both_sources_is_refused(tmp_path):
    """@description: Refuse ambiguous configuration instead of silently choosing one secret source.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertion checks the refusal.
    """
    secret_path = write_secret_file(tmp_path, "db_password", FAKE_DATABASE_PASSWORD)
    environ = {"DB_PASSWORD": FAKE_DATABASE_PASSWORD, "DB_PASSWORD_FILE": str(secret_path)}

    with pytest.raises(ConfigurationError, match="DB_PASSWORD"):
        required_secret_value("DB_PASSWORD", environ)


@pytest.mark.parametrize("file_content", ["", "   ", "\n\n", " \t\r\n"])
def test_empty_or_blank_secret_file_is_refused(tmp_path, file_content):
    """@description: Refuse a secret file that would start the bot with an empty credential.
    @param tmp_path: Temporary directory for the secret file.
    @param file_content: Empty or whitespace-only content that must not count as a secret.
    @return: None; the assertion checks the refusal.
    """
    secret_path = write_secret_file(tmp_path, "db_password", file_content)

    with pytest.raises(ConfigurationError, match="DB_PASSWORD"):
        required_secret_value("DB_PASSWORD", {"DB_PASSWORD_FILE": str(secret_path)})


def test_missing_secret_file_is_refused_with_setting_name_only(tmp_path):
    """@description: Refuse an unreadable secret path and name only the setting, never the path contents.
    @param tmp_path: Temporary directory that does not contain the referenced file.
    @return: None; the assertion checks the refusal message.
    """
    missing_path = tmp_path / "does-not-exist"

    with pytest.raises(ConfigurationError) as refusal:
        required_secret_value("DB_PASSWORD", {"DB_PASSWORD_FILE": str(missing_path)})

    assert "DB_PASSWORD_FILE" in str(refusal.value)


def test_directory_given_as_secret_file_is_refused(tmp_path):
    """@description: Refuse a path that points at a directory rather than a readable secret file.
    @param tmp_path: Temporary directory used as the wrong secret path.
    @return: None; the assertion checks the refusal.
    """
    with pytest.raises(ConfigurationError, match="DB_PASSWORD_FILE"):
        required_secret_value("DB_PASSWORD", {"DB_PASSWORD_FILE": str(tmp_path)})


def test_non_utf8_secret_file_is_refused_without_echoing_bytes(tmp_path):
    """@description: Refuse undecodable secret files and keep the decoder's byte details out of the message.
    @param tmp_path: Temporary directory for the binary secret file.
    @return: None; the assertion checks the refusal and that no byte sample is echoed.
    """
    secret_path = tmp_path / "db_password"
    secret_path.write_bytes(b"\xff\xfe\x00fake")

    with pytest.raises(ConfigurationError) as refusal:
        required_secret_value("DB_PASSWORD", {"DB_PASSWORD_FILE": str(secret_path)})

    assert "fake" not in str(refusal.value)


def test_no_source_is_refused_without_a_default_value():
    """@description: Refuse startup when neither the environment nor a secret file supplies the credential.
    @param: none
    @return: None; the assertion checks the refusal and the setting names it reports.
    """
    with pytest.raises(ConfigurationError, match="Missing required setting: DB_PASSWORD"):
        required_secret_value("DB_PASSWORD", {})


def test_blank_environment_value_does_not_count_as_provided(tmp_path):
    """@description: Treat a blank environment variable as absent so an intended secret file is still used.
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertion checks the file value is used.
    """
    secret_path = write_secret_file(tmp_path, "db_password", FAKE_DATABASE_PASSWORD)
    environ = {"DB_PASSWORD": "   ", "DB_PASSWORD_FILE": str(secret_path)}

    resolved_value = required_secret_value("DB_PASSWORD", environ)

    assert resolved_value == FAKE_DATABASE_PASSWORD


def test_refusal_messages_never_contain_the_secret(tmp_path):
    """@description: Prove that conflicting-source refusals keep the fake secret out of str() and repr().
    @param tmp_path: Temporary directory for the secret file.
    @return: None; the assertions check both representations.
    """
    secret_path = write_secret_file(tmp_path, "db_password", FAKE_DATABASE_PASSWORD)
    environ = {"DB_PASSWORD": FAKE_DATABASE_PASSWORD, "DB_PASSWORD_FILE": str(secret_path)}

    with pytest.raises(ConfigurationError) as refusal:
        required_secret_value("DB_PASSWORD", environ)

    assert FAKE_DATABASE_PASSWORD not in str(refusal.value)
    assert FAKE_DATABASE_PASSWORD not in repr(refusal.value)


def test_database_settings_read_password_from_secret_file(tmp_path, monkeypatch):
    """@description: Confirm the application database boundary accepts a password secret file.
    @param tmp_path: Temporary directory for the secret file.
    @param monkeypatch: Pytest fixture used to set process environment values for this test only.
    @return: None; the assertions check the resolved connection settings.
    """
    secret_path = write_secret_file(tmp_path, "db_password", FAKE_DATABASE_PASSWORD + "\n")
    monkeypatch.setenv("DB_USER", "fake_app_user")
    monkeypatch.setenv("DB_HOST", "fake-host")
    monkeypatch.setenv("DB_NAME", "fake_database")
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    monkeypatch.setenv("DB_PASSWORD_FILE", str(secret_path))

    database_settings = load_database_settings()

    assert database_settings.password == FAKE_DATABASE_PASSWORD
    assert FAKE_DATABASE_PASSWORD not in repr(database_settings)


def test_discord_settings_read_token_from_secret_file(tmp_path, monkeypatch):
    """@description: Confirm the Discord boundary accepts a token secret file and still hides the value in repr().
    @param tmp_path: Temporary directory for the secret file.
    @param monkeypatch: Pytest fixture used to set process environment values for this test only.
    @return: None; the assertions check the resolved token and its representation.
    """
    secret_path = write_secret_file(tmp_path, "discord_token", FAKE_DISCORD_TOKEN + "\n")
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_BOT_TOKEN_FILE", str(secret_path))

    discord_settings = load_discord_settings()

    assert discord_settings == DiscordSettings(token=FAKE_DISCORD_TOKEN)
    assert FAKE_DISCORD_TOKEN not in repr(discord_settings)
