"""Define an explicitly invoked, checksum-guarded additive migration runner.

No startup path invokes this module; interrupted MySQL DDL requires manual review rather than a misleading transaction rollback.
"""

import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple
from bot.config import DatabaseSettings
from bot.database.test_profiles import require_migration_execution

MIGRATION_VERSION = "2b2-b1"  # [schema identity]: Bound to the exact reviewed migration digest.
MIGRATION_AUTHORIZATION = "REVIEWED_BATCH_B_ISOLATED_MIGRATION"  # [invocation guard]: Never substitutes for human authorization.
MIGRATION_FILE = Path(__file__).with_name("migrations") / "002_submission_economy.sql"  # [reviewed artifact]: Not user-selected SQL.


def migration_plan() -> tuple[str, tuple[str, ...]]:
    """@description: Read reviewed static statements without connecting to a database or executing SQL.
    @param: none
    @return: File checksum and additive CREATE TABLE statements.
    """
    # [reviewed bytes]: Digest binds the version to the exact migration artifact.
    source = MIGRATION_FILE.read_text(encoding="utf-8")
    statements = tuple(part.strip() for part in re.sub(r"--[^\n]*", "", source).split(";") if part.strip())
    if not all(statement.startswith("CREATE TABLE IF NOT EXISTS jb2_") for statement in statements):
        raise ValueError("Migration contains an unapproved statement")
    return hashlib.sha256(source.encode("utf-8")).hexdigest(), statements


class MigrationGuards(NamedTuple):
    """Pair the destination check run before connecting with the identity check run after connecting; callers choose the pair explicitly."""

    settings_guard: Callable[[DatabaseSettings], None]  # [pre-connect destination]: Refuses before any socket is opened.
    session_guard: Callable[[Any], None]  # [post-connect identity]: Refuses on the live cursor before any DDL.


def apply_migration(factory: Callable[[], Any], authorization: str, *, settings: DatabaseSettings,
                    guards: MigrationGuards | None = None) -> bool:
    """@description: Require an explicit future authorization before any migration connection can be opened.
    @param factory: Explicit worker-local connection creator, never implicit environment loading.
    @param authorization: Deliberate reviewed invocation token, not proof of human approval.
    @param settings: Explicit dedicated migrator profile checked before factory invocation.
    @param guards: Optional explicit destination pair; None keeps the local fixed test guards exactly as before.
    @return: True when newly applied, False when the identical applied migration is already present.
    """
    if authorization != MIGRATION_AUTHORIZATION:
        raise PermissionError("Separate reviewed migration authorization is required")
    settings_guard, session_guard = (require_migration_execution, _require_isolated_destination) if guards is None else guards
    settings_guard(settings)
    # [owned migration session]: DDL cannot be made atomic by wrapping it in a normal transaction.
    connection = factory()
    try:
        return _run_session(connection, session_guard)
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def _run_session(connection: Any, session_guard: Callable[[Any], None] | None = None) -> bool:
    """@description: Verify authenticated identity before acquiring the dedicated migration advisory lock.
    @param connection: Owned dedicated migrator connection.
    @param session_guard: Identity check for the live cursor; None keeps the local fixed test identity.
    @return: Whether the reviewed migration was newly applied.
    """
    with connection.cursor() as cursor:
        (session_guard or _require_isolated_destination)(cursor)
        cursor.execute("SET SESSION sql_mode = 'STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION'")
        cursor.execute("SELECT GET_LOCK(%s,%s) AS acquired", ("jukebox101:2b2-migration", 5))
        if cursor.fetchone()["acquired"] != 1:
            raise RuntimeError("Migration lock is unavailable")
        return _apply_locked(connection, cursor)


def _require_isolated_destination(cursor: Any) -> None:
    """@description: Keep this preparation-stage migration runner confined to the fixed disposable test identity.
    @param cursor: Future explicitly authorized migration session before any DDL.
    @return: None.
    """
    cursor.execute("SELECT DATABASE() AS database_name, CURRENT_USER() AS account, @@port AS server_port, "
                   "VERSION() AS mysql_version, CURRENT_ROLE() AS active_roles")
    identity = cursor.fetchone()  # [actual destination]: Never trust the factory's advertised settings alone.
    if (identity["database_name"], identity["account"], identity["server_port"], identity["active_roles"]) != (
            "jukebox101_test", "jukebox101_test_migrator@127.0.0.1", 3306, "NONE") or not identity["mysql_version"].startswith("8."):
        raise PermissionError("Migration preparation is restricted to the fixed disposable test identity")


def _apply_locked(connection: Any, cursor: Any) -> bool:
    """@description: Reject checksum drift and partially applied DDL before touching protected data.
    @param connection: Exclusive migration session holding the advisory lock.
    @param cursor: Session cursor.
    @return: Whether this invocation applied the reviewed plan.
    """
    cursor.execute("CREATE TABLE IF NOT EXISTS jb2_migrations (version VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin "
                   "PRIMARY KEY, checksum CHAR(64) NOT NULL, state VARCHAR(16) NOT NULL) ENGINE=InnoDB")
    checksum, statements = migration_plan()  # [versioned plan]: Never substitute runtime-generated DDL.
    cursor.execute("SELECT checksum,state FROM jb2_migrations WHERE version=%s", (MIGRATION_VERSION,))
    previous = cursor.fetchone()  # [resume guard]: An applying marker means manual recovery is required.
    if previous is not None:
        if previous["checksum"] != checksum or previous["state"] != "applied":
            raise RuntimeError("Migration drift or interrupted DDL requires manual recovery")
        _verify_tables(cursor, statements)
        return False
    _reject_unowned_tables(cursor, statements)
    cursor.execute("INSERT INTO jb2_migrations (version,checksum,state) VALUES (%s,%s,%s)",
                   (MIGRATION_VERSION, checksum, "applying"))
    connection.commit()
    for statement in statements:
        cursor.execute(statement)
    _finish_migration(connection, cursor, statements)
    return True


def _table_names(statements: tuple[str, ...]) -> tuple[str, ...]:
    """@description: Extract only reviewed static table names for metadata verification.
    @param statements: Validated additive migration plan.
    @return: Fixed expected table names.
    """
    return tuple(statement.split()[5] for statement in statements)


def _metadata(cursor: Any, statements: tuple[str, ...]) -> tuple[dict, ...]:
    """@description: Inspect expected table existence and engines without accepting user SQL identifiers.
    @param cursor: Migration session cursor.
    @param statements: Reviewed static table declarations.
    @return: Information-schema rows for those fixed tables.
    """
    # [bound metadata names]: Only placeholder count is composed; names remain parameterized values.
    names = _table_names(statements)
    placeholders = ",".join("%s" for _ in names)
    cursor.execute("SELECT TABLE_NAME AS table_name, ENGINE AS engine FROM information_schema.TABLES "
                   "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN (" + placeholders + ")", names)
    return tuple(cursor.fetchall())


def _reject_unowned_tables(cursor: Any, statements: tuple[str, ...]) -> None:
    """@description: Stop on unknown preexisting new tables rather than silently masking schema drift.
    @param cursor: Migration session cursor.
    @param statements: Reviewed declarations.
    @return: None.
    """
    if _metadata(cursor, statements):
        raise RuntimeError("Unversioned jb2 tables require manual verification")


def _verify_tables(cursor: Any, statements: tuple[str, ...]) -> None:
    """@description: Refuse missing or nontransactional migration tables.
    @param cursor: Migration session cursor.
    @param statements: Reviewed declarations.
    @return: None.
    """
    # [metadata evidence]: Full column/FK drift still requires the documented schema inspection.
    rows = _metadata(cursor, statements)
    if {row["table_name"] for row in rows} != set(_table_names(statements)) or any(row["engine"] != "InnoDB" for row in rows):
        raise RuntimeError("Incomplete or nontransactional migration schema")


def _finish_migration(connection: Any, cursor: Any, statements: tuple[str, ...]) -> None:
    """@description: Bootstrap legacy catalog membership without fabricating sequences or modifying old balances.
    @param connection: Explicit migration session.
    @param cursor: Session cursor after all additive DDL succeeded.
    @param statements: Reviewed plan whose tables must exist.
    @return: None.
    """
    _verify_tables(cursor, statements)
    connection.begin()
    cursor.execute("INSERT INTO jb2_legacy (song_id) SELECT song_id FROM songs")
    cursor.execute("INSERT INTO jb2_control (singleton_id,schema_version) VALUES (%s,%s)", (1, MIGRATION_VERSION))
    cursor.execute("UPDATE jb2_migrations SET state=%s WHERE version=%s", ("applied", MIGRATION_VERSION))
    connection.commit()
