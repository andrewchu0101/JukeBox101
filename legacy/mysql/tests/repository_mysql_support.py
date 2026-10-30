"""Contain repository acceptance SQL in an explicitly approved rollback-only test session.

The test adapter replaces repository transaction ownership with savepoints, never committing fixture rows.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from threading import get_ident
from unittest.mock import patch
from uuid import uuid4

from bot.config import ConfigurationError, DatabaseSettings
from bot.database.connection import open_connection
from bot.database.repository import MusicRepository
from bot.urls import YouTubeVideo, parse_youtube_url
from tests.mysql_safety import (
    EXPECTED_TABLES, load_test_database_settings, require_disposable_authorization,
    validate_connected_test_database, validate_test_destination,
)

LOCK_WAIT_TIMEOUT_SECONDS = 2
COUNTS_SQL = (
    "SELECT 'users' AS table_name, COUNT(*) AS row_count FROM users UNION ALL "
    "SELECT 'songs', COUNT(*) FROM songs UNION ALL "
    "SELECT 'submissions', COUNT(*) FROM submissions UNION ALL "
    "SELECT 'recommendations', COUNT(*) FROM recommendations UNION ALL "
    "SELECT 'credit_ledger', COUNT(*) FROM credit_ledger UNION ALL "
    "SELECT 'daily_dispatches', COUNT(*) FROM daily_dispatches"
)


def authorized_repository_settings() -> DatabaseSettings:
    """@description: Demand both existing integration authorization and a repository-specific opt-in.
    @param: none
    @return: Dedicated TEST_DB settings, never production or dotenv fallbacks.
    """
    if os.environ.get("JUKEBOX_RUN_MYSQL_TEST") != "1" or os.environ.get("JUKEBOX_RUN_REPOSITORY_MYSQL") != "1":
        raise ConfigurationError("Repository integration requires both explicit test opt-in switches")
    return load_test_database_settings()


def table_counts(connection) -> dict[str, int]:
    """@description: Compare every accepted table without interpolating table names into SQL.
    @param connection: Preflight-validated isolated test connection.
    @return: Exact six-table row counts in the current transaction snapshot.
    """
    with connection.cursor() as cursor:
        cursor.execute(COUNTS_SQL)
        return {row["table_name"]: row["row_count"] for row in cursor.fetchall()}


def require_empty_tables(counts: dict[str, int]) -> None:
    """@description: Refuse preexisting data rather than letting upserts touch unknown rows.
    @param counts: All six isolated test table counts captured before fixture writes.
    @return: None; rejects incomplete counts or any nonempty table.
    """
    if set(counts) != EXPECTED_TABLES or any(counts.values()):
        raise ConfigurationError("Repository acceptance requires all six test tables to be empty; never clear them automatically")


def rollback_and_close(connection) -> None:
    """@description: Release the owned connection even when server rollback fails.
    @param connection: Connection owned solely by this test session.
    @return: None; rollback failures propagate instead of claiming clean completion.
    """
    try:
        connection.rollback()
    finally:
        connection.close()


def verified_counts(settings: DatabaseSettings) -> dict[str, int]:
    """@description: Inspect committed row counts through a fresh identity-validated connection.
    @param settings: Explicit approved test configuration.
    @return: Six-table counts after fresh server identity and schema preflight.
    """
    validate_test_destination(settings)
    if settings != authorized_repository_settings():
        raise ConfigurationError("Repository count settings differ from current authorized settings")
    connection = open_connection(settings)
    try:
        validate_connected_test_database(connection)
        return table_counts(connection)
    finally:
        rollback_and_close(connection)


class RollbackRepositorySession:
    """Keep one worker-owned outer transaction and replace operation commits with savepoint release."""

    def __init__(self, settings: DatabaseSettings, connection) -> None:
        """@description: Bind test-only transaction ownership to one validated connection and thread.
        @param self: Rollback-only session instance.
        @param settings: Dedicated authorized test destination.
        @param connection: Exclusively owned uncommitted outer transaction.
        @return: None.
        """
        self.settings = settings  # [test destination]: Credentials remain outside representations.
        self.connection = connection  # [outer transaction]: All fixture rows remain uncommitted.
        self.owner_thread_id = get_ident()
        self.repository = MusicRepository(settings)
        self.discord_user_id = uuid4().int >> 64  # [isolated identity]: Synthetic recipient only.
        self.alternate_user_id = uuid4().int >> 64
        self.video = new_video()

    def check_owner(self, settings: DatabaseSettings) -> None:
        """@description: Reject connection sharing and authorization changes before repository SQL.
        @param self: Test session owning the connection.
        @param settings: Configuration supplied by the repository operation.
        @return: None.
        """
        authorized_settings = authorized_repository_settings()
        validate_test_destination(settings)
        if settings != self.settings or settings != authorized_settings:
            raise ConfigurationError("Repository operation settings differ from authorized test settings")
        if get_ident() != self.owner_thread_id:
            raise RuntimeError("Rollback-only connections cannot cross threads")

    @contextmanager
    def transaction(self, settings: DatabaseSettings) -> Iterator:
        """@description: Execute unchanged repository SQL without committing the outer test transaction.
        @param self: Worker-owned rollback-only session.
        @param settings: Configuration supplied by the repository.
        @return: Context yielding the owned connection for one savepoint-scoped operation.
        """
        self.check_owner(settings)
        execute_statement(self.connection, "SAVEPOINT repository_operation")
        try:
            yield self.connection
        except BaseException:
            execute_statement(self.connection, "ROLLBACK TO SAVEPOINT repository_operation")
            raise
        finally:
            execute_statement(self.connection, "RELEASE SAVEPOINT repository_operation")


def execute_statement(connection, statement: str, parameters: tuple = ()) -> None:
    """@description: Execute reviewed test statements with separately bound business values.
    @param connection: Owned preflight-validated connection.
    @param statement: Fixed SQL provided by test code, never user input.
    @param parameters: Bound fixture identifiers or synthetic values.
    @return: None.
    """
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)


def select_rows(connection, statement: str, parameters: tuple = ()) -> list[dict]:
    """@description: Read assertions through the transaction that owns the uncommitted rows.
    @param connection: Current worker's validated connection.
    @param statement: Fixed SELECT from the acceptance code.
    @param parameters: Fixture-specific values bound separately from SQL.
    @return: Dictionary rows visible to the test transaction.
    """
    if not statement.lstrip().startswith("SELECT ") or ";" in statement:
        raise ValueError("Acceptance assertion reads require one SELECT")
    with connection.cursor() as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchall()


def new_video() -> YouTubeVideo:
    """@description: Generate synthetic case-sensitive identifiers without retrieving media.
    @param: none
    @return: Parsed synthetic source URL, not evidence of a real musical recording.
    """
    return parse_youtube_url("https://youtu.be/" + uuid4().hex[:11])


def require_savepoint_timeout_mode(connection) -> None:
    """@description: Refuse server timeout settings that discard the outer savepoint transaction.
    @param connection: Preflight-validated connection before fixture writes.
    @return: None; rejects full transaction rollback on a lock-wait timeout.
    """
    rows = select_rows(connection, "SELECT @@GLOBAL.innodb_rollback_on_timeout AS rollback_on_timeout")
    if rows != [{"rollback_on_timeout": 0}]:
        raise ConfigurationError("Rollback-only acceptance requires innodb_rollback_on_timeout=OFF; do not change server settings automatically")


@contextmanager
def rollback_repository_session(settings: DatabaseSettings) -> Iterator[RollbackRepositorySession]:
    """@description: Own preflight, savepoint injection and unconditional rollback for one worker.
    @param settings: Explicit TEST_DB credentials validated before connection creation.
    @return: Test session whose repository operations cannot commit fixture rows.
    """
    if settings != authorized_repository_settings():
        raise ConfigurationError("Session settings differ from authorized repository test settings")
    validate_test_destination(settings)
    require_disposable_authorization()
    connection = open_connection(settings)
    try:
        validate_connected_test_database(connection)
        require_empty_tables(table_counts(connection))
        require_savepoint_timeout_mode(connection)
        connection.rollback()
        connection.begin()
        execute_statement(connection, "SET SESSION innodb_lock_wait_timeout = %s", (LOCK_WAIT_TIMEOUT_SECONDS,))
        session = RollbackRepositorySession(settings, connection)
        with patch("bot.database.repository.transaction", session.transaction):
            yield session
    finally:
        rollback_and_close(connection)
