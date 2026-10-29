"""Verify the formal Gateway entry stops before Discord login or any database connection when a required secret is unusable.

Every external boundary is replaced by a recorder: Discord login, the MySQL connector and the database settings loader. Nothing connects to Discord or MySQL.
"""

import importlib
import os
from pathlib import Path

import discord
import pytest

SYNTHETIC_TOKEN = "SYNTHETIC-TOKEN-0123456789"  # [fake credential]: Recognisable so a leak would be caught by assertion.
SYNTHETIC_DB_PASSWORD = "SYNTHETIC-DB-PASSWORD-9876"  # [fake credential]: Never reaches a connection in these tests.
ENTRY_SETTINGS = {
    "DISCORD_DEV_GUILD_ID": "456",
    "DISCORD_EXPECTED_APPLICATION_ID": "789",
    "DISCORD_OWNER_USER_ID": "123",
    "DB_HOST": "db.synthetic.invalid",
    "DB_PORT": "3306",
    "DB_NAME": "jukebox_synthetic",
    "DB_USER": "jukebox_app",
}
CREDENTIAL_ENVIRONMENT = ("DISCORD_BOT_TOKEN", "DISCORD_BOT_TOKEN_FILE", "DB_PASSWORD", "DB_PASSWORD_FILE", "SECRET_EXPECTED_UID", "SECRET_EXPECTED_GID")


@pytest.fixture
def entry():
    """@description: Import the production entry module, which is inert until main() runs.
    @param: none
    @return: The bot.__main__ module.
    """
    return importlib.import_module("bot.__main__")


@pytest.fixture
def isolated(monkeypatch):
    """@description: Start every test with the entry settings present and no credentials, then record login and connection attempts.
    @param monkeypatch: Environment and boundary isolation facility.
    @return: Dictionary of recorders for login and connection attempts.
    """
    for name, value in ENTRY_SETTINGS.items():
        monkeypatch.setenv(name, value)
    for name in CREDENTIAL_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    recorders = {"logins": [], "connections": []}

    def fake_run(self, token, **kwargs):
        """@description: Record a login attempt instead of contacting Discord.
        @param self: Client that would log in.
        @param token: Credential passed to login.
        @param kwargs: Ignored keyword options.
        @return: None.
        """
        recorders["logins"].append(token)

    def fake_connect(*args, **kwargs):
        """@description: Record a database connection attempt instead of contacting MySQL.
        @param args: Ignored positional arguments.
        @param kwargs: Ignored keyword arguments.
        @return: Never returns; a connection attempt fails the test.
        """
        recorders["connections"].append(args)
        raise AssertionError("A database connection must not be opened on refusal")

    monkeypatch.setattr(discord.Client, "run", fake_run)
    monkeypatch.setattr("pymysql.connect", fake_connect)
    return recorders


def _refuse(entry) -> SystemExit:
    """@description: Run the production entry and return the refusal it raises.
    @param entry: Production entry module.
    @return: The SystemExit raised by main().
    """
    with pytest.raises(SystemExit) as refusal:
        entry.main()
    return refusal.value


def test_missing_discord_token_stops_before_login_and_database(entry, isolated):
    """@description: Without any Discord token the entry stops before login and never opens a database connection.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @return: None.
    """
    refusal = _refuse(entry)
    assert "DISCORD_BOT_TOKEN" in str(refusal.code)
    assert isolated["logins"] == []
    assert isolated["connections"] == []


def test_missing_database_password_stops_before_login_and_connection(entry, isolated, monkeypatch):
    """@description: Without a database password the entry stops before login and never connects to MySQL.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    monkeypatch.setenv("DISCORD_BOT_TOKEN", SYNTHETIC_TOKEN)
    refusal = _refuse(entry)
    assert "DB_PASSWORD" in str(refusal.code)
    assert isolated["logins"] == []
    assert isolated["connections"] == []


def test_blank_secret_file_stops_before_login(entry, isolated, monkeypatch, tmp_path: Path):
    """@description: A secret file that holds only whitespace is an empty credential and stops the entry before login.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @param monkeypatch: Environment isolation facility.
    @param tmp_path: Temporary directory for the synthetic blank file.
    @return: None.
    """
    blank_file = tmp_path / "token.txt"
    blank_file.write_text("   \n", encoding="utf-8")
    monkeypatch.setenv("DISCORD_BOT_TOKEN_FILE", str(blank_file))
    monkeypatch.setenv("DB_PASSWORD", SYNTHETIC_DB_PASSWORD)
    refusal = _refuse(entry)
    assert "DISCORD_BOT_TOKEN" in str(refusal.code)
    assert isolated["logins"] == []
    assert isolated["connections"] == []


def test_refusal_exit_is_nonzero_and_names_only_the_setting(entry, isolated):
    """@description: The refusal carries a nonzero exit status and a message that names the setting without a value.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @return: None.
    """
    refusal = _refuse(entry)
    assert refusal.code not in (None, 0, "")
    assert SYNTHETIC_TOKEN not in str(refusal.code)
    assert SYNTHETIC_DB_PASSWORD not in str(refusal.code)


def test_refusal_output_contains_no_secret_values(entry, isolated, monkeypatch, capsys):
    """@description: A malformed token that is present is never echoed on stdout, stderr or the refusal message.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @param monkeypatch: Environment isolation facility.
    @param capsys: Captured standard output and error.
    @return: None.
    """
    malformed_token = SYNTHETIC_TOKEN + " with space"
    monkeypatch.setenv("DISCORD_BOT_TOKEN", malformed_token)
    monkeypatch.setenv("DB_PASSWORD", SYNTHETIC_DB_PASSWORD)
    refusal = _refuse(entry)
    captured = capsys.readouterr()
    visible_text = str(refusal.code) + captured.out + captured.err
    assert malformed_token not in visible_text
    assert SYNTHETIC_DB_PASSWORD not in visible_text
    assert isolated["logins"] == []


# --- mounted secret files: expected identity and permission judgement ----------------------------------------------------------


@pytest.fixture
def mounted_token(monkeypatch, unix_permission_dir: Path) -> Path:
    """@description: Provide a synthetic token file owned by this process with owner-only permissions, and a database password from the environment.
    @param monkeypatch: Environment isolation facility.
    @param unix_permission_dir: Linux-filesystem directory for the synthetic token file, so its 0400 mode persists.
    @return: Path of the synthetic token file.
    """
    token_file = unix_permission_dir / "discord_bot_token"
    token_file.write_text(SYNTHETIC_TOKEN + "\n", encoding="utf-8")
    token_file.chmod(0o400)  # [sanctioned mode]: Owner-only 0400, the approved scheme for mounted secrets.
    monkeypatch.setenv("DISCORD_BOT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("DB_PASSWORD", SYNTHETIC_DB_PASSWORD)
    return token_file


def _expect_identity(monkeypatch, uid: int, gid: int) -> None:
    """@description: Declare the numeric owner and group the mounted secret must carry.
    @param monkeypatch: Environment isolation facility.
    @param uid: Expected numeric owner.
    @param gid: Expected numeric group.
    @return: None.
    """
    monkeypatch.setenv("SECRET_EXPECTED_UID", str(uid))
    monkeypatch.setenv("SECRET_EXPECTED_GID", str(gid))


def test_file_secret_without_expected_identity_stops_before_login(entry, isolated, mounted_token):
    """@description: A file-mounted secret without declared expected UID and GID is refused before login.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @param mounted_token: Synthetic token file owned by this process.
    @return: None.
    """
    refusal = _refuse(entry)
    assert "SECRET_EXPECTED_UID" in str(refusal.code)
    assert isolated["logins"] == []


@pytest.mark.parametrize("raw_identity", ["abc", "-1", " 1000", "１０００", ""])
def test_malformed_expected_identity_stops_before_login(entry, isolated, mounted_token, monkeypatch, raw_identity):
    """@description: A non-integer, negative or blank expected UID is refused rather than guessed.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @param mounted_token: Synthetic token file owned by this process.
    @param monkeypatch: Environment isolation facility.
    @param raw_identity: Malformed expected UID text.
    @return: None.
    """
    monkeypatch.setenv("SECRET_EXPECTED_UID", raw_identity)
    monkeypatch.setenv("SECRET_EXPECTED_GID", str(os.getegid()))
    refusal = _refuse(entry)
    assert "SECRET_EXPECTED_UID" in str(refusal.code)
    assert isolated["logins"] == []


def test_world_readable_mounted_secret_is_refused(entry, isolated, mounted_token, monkeypatch):
    """@description: A mounted secret readable by other accounts is refused even when the identity matches.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @param mounted_token: Synthetic token file owned by this process.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    mounted_token.chmod(0o644)
    _expect_identity(monkeypatch, os.geteuid(), os.getegid())
    refusal = _refuse(entry)
    assert "DISCORD_BOT_TOKEN" in str(refusal.code)
    assert isolated["logins"] == []


def test_mounted_secret_owner_mismatch_is_refused(entry, isolated, mounted_token, monkeypatch):
    """@description: A mounted secret whose numeric owner differs from the declared UID is refused before login.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @param mounted_token: Synthetic token file owned by this process.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    _expect_identity(monkeypatch, os.geteuid() + 1, os.getegid())
    refusal = _refuse(entry)
    assert "DISCORD_BOT_TOKEN" in str(refusal.code)
    assert isolated["logins"] == []


def test_correctly_mounted_secret_proceeds_to_login(entry, isolated, mounted_token, monkeypatch):
    """@description: A mounted secret with the declared numeric identity and owner-only permissions reaches login exactly once.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @param mounted_token: Synthetic token file owned by this process.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    _expect_identity(monkeypatch, os.geteuid(), os.getegid())
    entry.main()
    assert isolated["logins"] == [SYNTHETIC_TOKEN]
    assert isolated["connections"] == []


def test_group_readable_0440_mounted_secret_is_refused_because_0400_is_the_only_sanctioned_mode(entry, isolated, mounted_token, monkeypatch):
    """@description: A mounted secret with a matching group but mode 0440 is refused; 0400 is the only sanctioned mode.
    @param entry: Production entry module.
    @param isolated: Login and connection recorders.
    @param mounted_token: Synthetic token file owned by this process.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    mounted_token.chmod(0o440)
    _expect_identity(monkeypatch, os.geteuid(), os.getegid())
    refusal = _refuse(entry)
    assert "DISCORD_BOT_TOKEN" in str(refusal.code)
    assert isolated["logins"] == []

