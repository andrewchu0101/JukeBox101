"""Verify the live-run boundary offline: hidden secrets arrive only through the reader and the process environment, nothing is read from a
dotenv file, no command synchronization runs, the formal switch stays in-process, and the synthetic candidate enters only through the
production pending intake that creates its submitter row.

Uses the SQL-protocol fake and stand-in callables. No process starts, nothing connects to MySQL or Discord, and no environment file is read.
"""

import pytest

import bot.discord_app as discord_app
from bot.config import load_database_settings
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import PersistenceNotReady
from bot.live_run_entry import LiveRunRefused, admit_synthetic_candidate, start_live_entry
from tests.submission_persistence_fake import FakeDatabase

FIXED = {"DISCORD_OWNER_USER_ID": "456708412697215018", "DISCORD_DEV_GUILD_ID": "1559749813011087392",
         "DISCORD_EXPECTED_APPLICATION_ID": "1559751168723521576", "DB_HOST": "127.0.0.1", "DB_PORT": "3307",
         "DB_NAME": "jukebox101_test", "DB_USER": "jukebox101_test_exchange"}
OWNER_ID = 456708412697215018
SYNTHETIC_ID = 900000000000000001  # [synthetic identity]: Offline value; the live value is set by the Owner.
URL = "https://youtu.be/Abc_123-Xyz"


def test_secrets_come_only_from_the_hidden_reader_and_never_from_arguments_or_dotenv(monkeypatch) -> None:
    """@description: Both secrets are read by name and placed only in the process environment; a dotenv loader that is called fails the test.
    @param monkeypatch: Pytest monkeypatch fixture.
    @return: None.
    """
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: pytest.fail("dotenv must never be read"))
    environ, asked, seen = {}, [], {}
    monkeypatch.setattr("os.environ", environ)  # [process environment]: The production loader reads os.environ, so the test uses the same mapping.

    def reader(name: str) -> str:
        """@description: Return a synthetic value for a hidden name and record which name was asked.
        @param name: Secret name.
        @return: Synthetic value.
        """
        asked.append(name)
        return "synthetic-" + name.lower()

    def production() -> None:
        """@description: Record the environment the production entry point sees and read the database settings the way it does.
        @return: None.
        """
        seen["settings"] = load_database_settings()
        seen["switch"] = environ["JUKEBOX_FORMAL_EXCHANGE"]

    start_live_entry(FIXED, reader, environ, production)
    assert asked == ["DISCORD_BOT_TOKEN", "DB_PASSWORD"]
    assert environ["DISCORD_BOT_TOKEN"] == "synthetic-discord_bot_token" and environ["DB_PASSWORD"] == "synthetic-db_password"
    assert seen["switch"] == "1" and seen["settings"].port == 3307 and seen["settings"].database == "jukebox101_test"
    assert "JUKEBOX_FORMAL_EXCHANGE" not in FIXED and "DB_PASSWORD" not in FIXED


def test_no_command_synchronization_runs_and_an_empty_secret_refuses_before_production(monkeypatch) -> None:
    """@description: The start never calls command synchronization, and an empty hidden value refuses before the production entry point runs.
    @param monkeypatch: Pytest monkeypatch fixture.
    @return: None.
    """
    monkeypatch.setattr(discord_app, "synchronize_commands", lambda *a, **k: pytest.fail("no remote sync during the live start"))
    calls = []
    with pytest.raises(LiveRunRefused, match="SECRET_EMPTY:DISCORD_BOT_TOKEN"):
        start_live_entry(FIXED, lambda name: "", {}, lambda: calls.append("production"))
    assert calls == []
    start_live_entry(FIXED, lambda name: "value", {}, lambda: calls.append("production"))
    assert calls == ["production"]


def test_a_missing_approved_identifier_refuses_before_any_secret_is_requested() -> None:
    """@description: A missing fixed identifier refuses before the hidden reader is ever called, so no secret is typed for a bad start.
    @return: None.
    """
    asked = []
    with pytest.raises(LiveRunRefused, match="FIXED_SETTING_MISSING:DB_PORT"):
        start_live_entry({**FIXED, "DB_PORT": ""}, lambda name: asked.append(name) or "x", {}, lambda: None)
    assert asked == []


@pytest.fixture
def pending_world():
    """@description: Fake database and real repository with the settlement switch off and the readiness flags set, as the approved start requires.
    @return: Tuple of database and repository.
    """
    database = FakeDatabase()
    database.state["control"].update(snapshot_ready=True, legacy_writers_quiesced=True, settlement_enabled=False)
    return database, SubmissionRepository(database.connect, OWNER_ID)


def test_the_synthetic_candidate_enters_only_through_the_pending_intake_that_creates_its_submitter(pending_world) -> None:
    """@description: The synthetic candidate is admitted by the existing pending intake; the submitter row and the event are created there, with no direct insert.
    @param pending_world: Fake database and repository.
    @return: None.
    """
    database, repository = pending_world
    admit_synthetic_candidate(repository, OWNER_ID, SYNTHETIC_ID, URL, "live-c-1")
    assert SYNTHETIC_ID in database.state["users"]
    statements = [sql for sql, _ in database.statements]
    assert any(sql.startswith("INSERT INTO jb2_events") for sql in statements)


def test_the_synthetic_identity_is_never_the_owner_or_a_non_positive_value(pending_world) -> None:
    """@description: The Owner's own identifier and non-positive identifiers are refused before the repository is touched.
    @param pending_world: Fake database and repository.
    @return: None.
    """
    database, repository = pending_world
    before = len(database.statements)
    with pytest.raises(LiveRunRefused, match="SYNTHETIC_SUBMITTER_IS_OWNER"):
        admit_synthetic_candidate(repository, OWNER_ID, OWNER_ID, URL, "live-c-2")
    with pytest.raises(LiveRunRefused, match="SYNTHETIC_SUBMITTER_INVALID"):
        admit_synthetic_candidate(repository, OWNER_ID, 0, URL, "live-c-3")
    assert len(database.statements) == before


def test_the_existing_guard_refuses_intake_while_the_settlement_switch_is_on(pending_world) -> None:
    """@description: With the switch on, the production pending intake refuses, so the synthetic candidate cannot enter during a settlement window.
    @param pending_world: Fake database and repository.
    @return: None.
    """
    database, repository = pending_world
    database.state["control"]["settlement_enabled"] = True
    with pytest.raises(PersistenceNotReady):
        admit_synthetic_candidate(repository, OWNER_ID, SYNTHETIC_ID, URL, "live-c-4")
    assert SYNTHETIC_ID not in database.state["users"]
