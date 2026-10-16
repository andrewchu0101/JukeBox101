"""Run an explicitly authorized, read-only local Batch B database inventory.

Only the user's PowerShell process consumes TEST_DB credentials; reports never include secrets or raw driver exceptions.
"""

import hashlib
import json
import re
from typing import Any

import pymysql

from bot.config import ConfigurationError, DatabaseSettings
from bot.database.connection import open_connection
from bot.database.test_profiles import profile_settings, validate_profile, MIGRATION_USER
from bot.database.test_privileges import privilege_blockers
from bot.database.submission_migration import migration_plan
from tests.mysql_safety import EXPECTED_TABLES, validate_test_constraints

PREFLIGHT_SWITCH = "JUKEBOX_RUN_BATCH_B_PREFLIGHT"  # [read authorization]: Does not authorize migration or test writes.


def preflight_settings() -> DatabaseSettings:
    """@description: Require explicit TEST_DB values without loading dotenv or falling back to production settings.
    @param: none
    @return: Fixed local test settings whose password is never printed.
    """
    return profile_settings(PREFLIGHT_SWITCH)


def collect_preflight(settings: DatabaseSettings, migration: bool = False) -> dict:
    """@description: Inventory only after verifying the fixed authenticated MySQL 8 identity.
    @param settings: Validated explicit local test account and database settings.
    @param migration: Require the independent migration identity and privilege contract.
    @return: Nonsecret inventory plus conservative stop or empty-baseline status.
    """
    validate_profile(settings, migration)
    # [owned read-only connection]: Created only by the user's explicit CLI invocation.
    connection = open_connection(settings)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            connection.begin()
            identity = _verify_identity(cursor, migration)
            grants = _privileges(cursor, migration)
            report = _inventory(cursor, identity, grants, migration)
        validate_test_constraints(connection)
        return report
    finally:
        try:
            connection.rollback()
        finally:
            connection.close()


def _verify_identity(cursor: Any, migration: bool = False) -> dict:
    """@description: Refuse another database, account, server port, version or active role before inventory reads.
    @param cursor: Read-only transaction cursor.
    @param migration: Select the dedicated migrator instead of the application identity.
    @return: Fixed identity and nonsecret MySQL version evidence.
    """
    cursor.execute("SELECT DATABASE() AS database_name,CURRENT_USER() AS account,VERSION() AS mysql_version, "
                   "@@port AS server_port,CURRENT_ROLE() AS active_roles")
    identity = cursor.fetchone()  # [actual server identity]: Factory settings alone are insufficient.
    if (identity["database_name"], identity["account"], identity["server_port"]) != (
            "jukebox101_test", (MIGRATION_USER if migration else "jukebox101_test_app") + "@127.0.0.1", 3306):
        raise ConfigurationError("ACTUAL_TEST_IDENTITY_MISMATCH")
    if not identity["mysql_version"].startswith("8.") or "mariadb" in identity["mysql_version"].lower():
        raise ConfigurationError("MYSQL_8_REQUIRED")
    if identity["active_roles"] != "NONE":
        raise ConfigurationError("ACTIVE_ROLE_PRIVILEGES_REQUIRE_REVIEW")
    return {key: value for key, value in identity.items() if key != "active_roles"}


def _privileges(cursor: Any, migration: bool = False) -> dict:
    """@description: Inspect effective schema and table grants without authentication text.
    @param cursor: Verified local read-only session.
    @param migration: Select the authenticated dedicated migrator grantee.
    @return: Sanitized grants and scope blockers.
    """
    from bot.database.test_privileges import collect_privileges
    return collect_privileges(cursor, migration)


def _inventory(cursor: Any, identity: dict, privileges: dict, migration: bool = False) -> dict:
    """@description: Stop on unknown assets or any nonempty table rather than assuming a disposable database.
    @param cursor: Verified read-only cursor.
    @param identity: Nonsecret fixed server identity.
    @param privileges: Inspected account scope.
    @param migration: Evaluate only this account profile, never infer the other account readiness.
    @return: Comparable table counts, structure digests and migration state without changing any rows.
    """
    cursor.execute("SELECT TABLE_NAME,TABLE_TYPE,ENGINE,TABLE_COLLATION FROM information_schema.TABLES "
                   "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME", ("jukebox101_test",))
    tables = tuple(cursor.fetchall())  # [existing assets]: An existing test database is not automatically disposable.
    # [reviewed migration scope]: Only fixed original or declared Batch B tables may enter composed identifiers.
    checksum, statements = migration_plan()
    allowed = EXPECTED_TABLES | {statement.split()[5] for statement in statements} | {"jb2_migrations"}
    unknown = sorted(row["TABLE_NAME"] for row in tables if row["TABLE_NAME"] not in allowed)
    known = {row["TABLE_NAME"] for row in tables if row["TABLE_TYPE"] == "BASE TABLE" and row["ENGINE"] == "InnoDB"} & allowed
    baseline = {name: _table_baseline(cursor, name) for name in sorted(known)}
    # [sanitized inventory]: No application rows, password values or raw authentication errors enter the report.
    report = {"identity": identity, "privileges": privileges, "tables": tables, "unknown_tables": unknown,
              "baseline": baseline, "structure": _schema_structure(cursor),
              "migration_file_checksum": checksum, "migration": _migration_state(cursor, known)}
    report["stop_reasons"] = _stop_reasons(tables, unknown, baseline, privileges, migration)
    report["status"] = "STOP" if report["stop_reasons"] else "READONLY_EMPTY_BASELINE_REVIEW_REQUIRED"
    report["application_readiness"] = "NOT_CHECKED_SEPARATE_ACCOUNT" if migration else ("BLOCKED" if report["stop_reasons"] else "READY")
    report["migration_readiness"] = ("BLOCKED" if report["stop_reasons"] else "READY_FOR_MANUAL_REVIEW") if migration else "NOT_CHECKED_SEPARATE_ACCOUNT"
    report["migration_authorized"] = False
    return report


def _table_baseline(cursor: Any, table_name: str) -> dict:
    """@description: Count only allowlisted tables and fingerprint their actual structure without dumping rows.
    @param cursor: Read-only verified cursor.
    @param table_name: Static allowlisted table identifier, never arbitrary user text.
    @return: Exact row count and actual SHOW CREATE digest.
    """
    if table_name not in EXPECTED_TABLES | {statement.split()[5] for statement in migration_plan()[1]} | {"jb2_migrations"}:
        raise ConfigurationError("UNAPPROVED_TABLE_IDENTIFIER")
    cursor.execute("SELECT COUNT(*) AS row_count FROM `" + table_name + "`")
    count = cursor.fetchone()["row_count"]  # [exact baseline]: Estimates from information_schema are insufficient.
    cursor.execute("SHOW CREATE TABLE `" + table_name + "`")
    definition = cursor.fetchone()  # [structure evidence]: Raw definitions are not printed as diagnostic text.
    ddl = next(value for key, value in definition.items() if key.startswith("Create "))
    return {"row_count": count, "create_sha256": hashlib.sha256(ddl.encode("utf-8")).hexdigest()}


def _schema_structure(cursor: Any) -> dict:
    """@description: Record nonsecret column and index structure without dumping defaults, comments or application rows.
    @param cursor: Verified read-only cursor.
    @return: Ordered column and index metadata for the selected test schema.
    """
    cursor.execute("SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLLATION_NAME FROM information_schema.COLUMNS "
                   "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,ORDINAL_POSITION", ("jukebox101_test",))
    columns = tuple(cursor.fetchall())  # [structure baseline]: Exclude defaults that could contain unrelated sensitive text.
    cursor.execute("SELECT TABLE_NAME,INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME FROM information_schema.STATISTICS "
                   "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX", ("jukebox101_test",))
    return {"columns": columns, "indexes": tuple(cursor.fetchall())}


def _migration_state(cursor: Any, known: set[str]) -> tuple[dict, ...]:
    """@description: Inspect existing migration markers without creating the registry table.
    @param cursor: Verified read-only cursor.
    @param known: Actual allowlisted tables present in the database.
    @return: Existing version/checksum/state rows or an empty tuple.
    """
    if "jb2_migrations" not in known:
        return ()
    cursor.execute("SELECT version,checksum,state FROM jb2_migrations ORDER BY version")
    rows = tuple(cursor.fetchall())  # [known marker shape]: Do not dump arbitrary content from an unrecognized existing table.
    if any(not re.fullmatch(r"2b2-b\d+", row["version"]) or not re.fullmatch(r"[a-f0-9]{64}", row["checksum"])
           or row["state"] not in {"applying", "applied"} for row in rows):
        return ({"status": "UNRECOGNIZED_MARKER_REVIEW_REQUIRED"},)
    return rows


def _stop_reasons(tables: tuple[dict, ...], unknown: list[str], baseline: dict, privileges: dict, migration: bool = False) -> list[str]:
    """@description: Require manual review instead of treating readable metadata as write authorization.
    @param tables: Actual objects in the selected test schema.
    @param unknown: Objects outside the known six-table and Batch B scope.
    @param baseline: Exact known-table counts and fingerprints.
    @param privileges: Nonsecret account scope evidence.
    @param migration: Select application DML or migration DDL requirements.
    @return: Conservative blockers; an empty result still requires human review before migration.
    """
    # [preflight blockers]: None of these conditions may trigger cleanup or privilege expansion.
    checks = {
        "UNKNOWN_OBJECTS": bool(unknown),
        "SIX_TABLES_MISSING": not EXPECTED_TABLES.issubset(baseline),
        "NON_INNODB_OR_VIEW": any(row["ENGINE"] != "InnoDB" or row["TABLE_TYPE"] != "BASE TABLE" for row in tables),
        "NONEMPTY_ASSETS_REQUIRE_REVIEW": any(row["row_count"] != 0 for row in baseline.values()),
        "EXISTING_BATCH_B_SCHEMA_REQUIRES_REVIEW": any(name.startswith("jb2_") for name in baseline),
        "BROAD_ACCOUNT_PRIVILEGES": bool(privileges["global_privileges"] or privileges["cross_schema_grants"]),
    }
    return [reason for reason, blocked in checks.items() if blocked] + privilege_blockers(privileges, set(baseline), migration)


def run_report(collect) -> None:
    """@description: Print only sanitized preflight evidence from the user's explicitly authorized local process.
    @param collect: Explicit profile-specific report collector.
    @return: None; any failure stops without migration or cleanup.
    """
    try:
        report = collect()
    except (ConfigurationError, ValueError, TypeError, KeyError, StopIteration, RuntimeError, PermissionError, pymysql.MySQLError) as error:
        # [safe failure]: Raw driver text may contain account or credential details and is never printed.
        code = error.args[0] if isinstance(error, pymysql.MySQLError) and error.args and type(error.args[0]) is int else None
        reason = str(error) if isinstance(error, ConfigurationError) and re.fullmatch(r"[A-Z][A-Z0-9_]+", str(error)) else "PREFLIGHT_CHECK_FAILED"
        print(json.dumps({"status": "STOP", "error_type": type(error).__name__, "mysql_error_number": code, "reason": reason}))
        raise SystemExit(1) from None
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] == "STOP":
        raise SystemExit(1)


def main() -> None:
    """@description: Keep the application entry point separate from migration credentials.
    @param: none
    @return: None.
    """
    run_report(lambda: collect_preflight(preflight_settings()))


if __name__ == "__main__":
    main()
