"""Apply the versioned PostgreSQL schema files in order, each in one transaction, under an advisory lock.

Inputs are the numbered SQL files in bot/database/postgres and a schema-owner connection; the output is the
jb_schema_migrations ledger. Checksums of applied versions are verified on every run; any drift stops the run.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bot.database.connection import APPLICATION_SCHEMA

MIGRATIONS_DIRECTORY = Path(__file__).with_name("postgres")  # [versioned DDL]: Shipped inside the image with the code.
MIGRATION_FILE_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")  # [file naming]: Four-digit version, lowercase name.
ADVISORY_LOCK_KEY = 7_101_000_001  # [single runner]: Fixed key; a second concurrent runner refuses instead of waiting.
LEDGER_DDL = ("CREATE TABLE IF NOT EXISTS jb_schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
              "sha256 CHAR(64) NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(), "
              "applied_by TEXT NOT NULL DEFAULT current_user)")


class MigrationRefused(RuntimeError):
    """Stop before any schema change because the database or the files are not in a state this runner may touch."""


class MigrationFailed(RuntimeError):
    """Report that one migration failed and was rolled back; earlier committed versions stay applied."""


@dataclass(frozen=True)
class Migration:
    """Carry one versioned schema file with the digest that identifies its exact text."""

    version: int  # [order]: Applied strictly ascending, starting at 1, without gaps.
    name: str  # [label]: File name stem after the version.
    sql: str  # [statement text]: Executed as one transaction.
    sha256: str  # [identity]: Recorded on apply and compared on every later run.


def discover_migrations(directory: Path = MIGRATIONS_DIRECTORY) -> list[Migration]:
    """@description: Read the shipped schema files so the runner applies exactly what was reviewed, in order.
    @param directory: Folder holding NNNN_name.sql files.
    @return: Migrations ordered by version; raises MigrationRefused on bad names or gaps.
    """
    migrations = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_FILE_PATTERN.match(path.name)
        if match is None:
            raise MigrationRefused(f"unexpected migration file name: {path.name}")
        raw = path.read_bytes()
        migrations.append(Migration(int(match.group(1)), match.group(2), raw.decode("utf-8"), hashlib.sha256(raw).hexdigest()))
    if [migration.version for migration in migrations] != list(range(1, len(migrations) + 1)):
        raise MigrationRefused("migration versions must start at 1 and have no gaps")
    return migrations


def pending_migrations(available: list[Migration], applied: dict[int, dict]) -> list[Migration]:
    """@description: Decide what to apply while refusing edited, unknown or out-of-order history.
    @param available: Shipped migrations in order.
    @param applied: Ledger rows keyed by version.
    @return: Migrations not yet applied, in order.
    """
    by_version = {migration.version: migration for migration in available}
    for version, row in applied.items():
        if version not in by_version:
            raise MigrationRefused(f"database has version {version} that this release does not ship")
        if row["sha256"] != by_version[version].sha256:
            raise MigrationRefused(f"version {version} was applied with different content; refusing to continue")
    if sorted(applied) != list(range(1, len(applied) + 1)):
        raise MigrationRefused("applied versions are not a contiguous prefix")
    return [migration for migration in available if migration.version not in applied]


def verify_runner_identity(cursor: Any) -> dict:
    """@description: Refuse superusers and non-owners so schema changes come only from the dedicated migrator role.
    @param cursor: Cursor on the migrator connection.
    @return: Identity facts (role, database, server version).
    """
    cursor.execute("SELECT current_user AS account, current_database() AS database_name, "
                   "current_setting('server_version') AS server_version, "
                   "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_superuser, "
                   "(SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = %s) AS schema_owner",
                   (APPLICATION_SCHEMA,))
    identity = cursor.fetchone()
    if identity["is_superuser"]:
        raise MigrationRefused("refusing to migrate as a superuser; use the schema-owner migrator role")
    if identity["schema_owner"] != identity["account"]:
        raise MigrationRefused(f"schema {APPLICATION_SCHEMA} must exist and be owned by the connecting role")
    return identity


def read_applied(cursor: Any) -> dict[int, dict]:
    """@description: Load the migration ledger so checksums and order can be verified before any change.
    @param cursor: Cursor on the migrator connection.
    @return: Ledger rows keyed by version.
    """
    cursor.execute("SELECT version, name, sha256 FROM jb_schema_migrations ORDER BY version")
    return {row["version"]: row for row in cursor.fetchall()}


def apply_migration(connection: Any, migration: Migration) -> None:
    """@description: Apply one schema file and its ledger row atomically, rolling both back on any failure.
    @param connection: Migrator connection with no open work.
    @param migration: Next pending migration.
    @return: None; raises MigrationFailed naming the version and SQLSTATE only.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(migration.sql)
            cursor.execute("INSERT INTO jb_schema_migrations (version, name, sha256) VALUES (%s, %s, %s)",
                           (migration.version, migration.name, migration.sha256))
        connection.commit()
    except Exception as error:
        connection.rollback()
        raise MigrationFailed(f"version {migration.version} ({migration.name}) rolled back: "
                              f"{type(error).__name__} {getattr(error, 'sqlstate', None)}") from None


def run_migrations(connection: Any, *, apply: bool, available: list[Migration] | None = None) -> dict:
    """@description: Verify identity, take the single-runner lock, check history and optionally apply pending files.
    @param connection: Schema-owner connection owned by the caller, closed by the caller.
    @param apply: False reports the plan only; True applies pending migrations one transaction at a time.
    @param available: Migrations to use; None reads the shipped directory.
    @return: Summary with identity, applied versions, pending versions and what this run applied.
    """
    migrations = discover_migrations() if available is None else available
    with connection.cursor() as cursor:
        identity = verify_runner_identity(cursor)
        cursor.execute("SELECT pg_try_advisory_lock(%s) AS locked", (ADVISORY_LOCK_KEY,))
        if not cursor.fetchone()["locked"]:
            raise MigrationRefused("another migration run holds the lock")
        cursor.execute(LEDGER_DDL)
    connection.commit()
    with connection.cursor() as cursor:
        pending = pending_migrations(migrations, read_applied(cursor))
    connection.commit()
    applied_now = []
    for migration in pending if apply else []:
        apply_migration(connection, migration)
        applied_now.append(migration.version)
    return {"account": identity["account"], "database": identity["database_name"], "server_version": identity["server_version"],
            "pending_before": [migration.version for migration in pending], "applied_now": applied_now,
            "latest_shipped": migrations[-1].version if migrations else 0}
