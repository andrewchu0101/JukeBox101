"""Inspect narrowly scoped disposable database capabilities without changing grants.

Effective table permissions combine schema and table grants while rejecting global scope, grant options and active-role ambiguity.
"""

from typing import Any
from bot.database.test_profiles import APPLICATION_USER, MIGRATION_USER

ORIGINAL_TABLES = frozenset({"users", "songs", "submissions", "recommendations", "credit_ledger", "daily_dispatches"})
NEW_TABLES = frozenset({"jb2_control", "jb2_versions", "jb2_videos", "jb2_legacy", "jb2_events", "jb2_receipts",
                        "jb2_claims", "jb2_settlements", "jb2_review_history", "jb2_evidence", "jb2_decisions",
                        "jb2_exclusions", "jb2_barriers", "jb2_exceptions", "jb2_reminders", "jb2_reminder_batches", "jb2_migrations"})
DML = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"})  # [DML vocabulary]: Allowed rights are further restricted per table.


def migration_requirements() -> dict[str, set[str]]:
    """@description: Include parent FK references, read-only inventory and transactional bootstrap rights.
    @param: none
    @return: Minimum effective permissions for every original and future table.
    """
    requirements = {name: {"SELECT"} for name in ORIGINAL_TABLES}  # [minimal rights]: Never mutate original rows during migration.
    requirements.update({name: {"CREATE", "SELECT"} for name in NEW_TABLES})
    for name in ("users", "songs", "credit_ledger", "jb2_versions", "jb2_videos", "jb2_events"):
        requirements[name].add("REFERENCES")
    for name in ("jb2_migrations", "jb2_control", "jb2_legacy"):
        requirements[name].add("INSERT")
    requirements["jb2_migrations"].add("UPDATE")
    return requirements


def application_requirements() -> dict[str, set[str]]:
    """@description: Preserve existing repositories while keeping ledger and migration history immutable.
    @param: none
    @return: Minimum application rights including read-only inventory on all reviewed tables.
    """
    requirements = {name: {"SELECT"} for name in ORIGINAL_TABLES | NEW_TABLES}  # [per-table rights]: Cleanup is not runtime authority.
    for name in ("users", "songs", "jb2_versions", "jb2_videos", "jb2_legacy", "jb2_events",
                 "jb2_reminders", "jb2_barriers", "jb2_exceptions"):
        requirements[name].update({"INSERT", "UPDATE"})
    requirements["jb2_control"].add("UPDATE")
    for name in ("submissions", "credit_ledger", "jb2_receipts", "jb2_claims", "jb2_settlements",
                 "jb2_review_history", "jb2_evidence", "jb2_decisions", "jb2_exclusions", "jb2_reminder_batches"):
        requirements[name].add("INSERT")
    return requirements


def _excess_table_rights(schema: set[str], tables: dict, allowed: dict[str, set[str]]) -> bool:
    """@description: Detect additive schema rights that would bypass a narrower table grant.
    @param schema: Effective schema-level rights.
    @param tables: Explicit table-level capabilities.
    @param allowed: Reviewed maximum rights for every known table.
    @return: Whether rights exceed any table contract, including unknown grant targets.
    """
    return bool(set(tables) - set(allowed)) or any((schema | set(tables.get(name, ()))) - rights
                                                 for name, rights in allowed.items())


def _local_schema(name: str) -> bool:
    """@description: Accept the exact schema and its escaped literal grant spelling.
    @param name: Schema scope returned by information_schema.
    @return: Whether this scope names the disposable schema.
    """
    return name in {"jukebox101_test", r"jukebox101\_test"}


def collect_privileges(cursor: Any, migration: bool) -> dict:
    """@description: Read only privilege metadata for the independently authenticated account.
    @param cursor: Verified session without active roles.
    @param migration: Whether the dedicated migrator is selected.
    @return: Sanitized schema/table privileges and unsafe scope indicators.
    """
    user = MIGRATION_USER if migration else APPLICATION_USER  # [authenticated grantee]: Never caller supplied SQL.
    grantee = "'" + user + "'@'127.0.0.1'"
    cursor.execute("SELECT PRIVILEGE_TYPE,IS_GRANTABLE FROM information_schema.USER_PRIVILEGES WHERE GRANTEE=%s", (grantee,))
    global_rows = tuple(cursor.fetchall())
    cursor.execute("SELECT TABLE_SCHEMA,PRIVILEGE_TYPE,IS_GRANTABLE FROM information_schema.SCHEMA_PRIVILEGES WHERE GRANTEE=%s", (grantee,))
    schema_rows = tuple(cursor.fetchall())
    cursor.execute("SELECT TABLE_SCHEMA,TABLE_NAME,PRIVILEGE_TYPE,IS_GRANTABLE FROM information_schema.TABLE_PRIVILEGES WHERE GRANTEE=%s", (grantee,))
    table_rows = tuple(cursor.fetchall())
    tables = {}  # [effective table grant sources]: Schema grants are combined later, not substituted for table grants.
    for row in table_rows:
        if _local_schema(row["TABLE_SCHEMA"]):
            tables.setdefault(row["TABLE_NAME"], []).append(row["PRIVILEGE_TYPE"])
    return {"global_privileges": sorted(row["PRIVILEGE_TYPE"] for row in global_rows if row["PRIVILEGE_TYPE"] != "USAGE"),
            "cross_schema_grants": any(not _local_schema(row["TABLE_SCHEMA"]) for row in (*schema_rows, *table_rows)),
            "grant_option": any(row.get("IS_GRANTABLE") == "YES" for row in (*global_rows, *schema_rows, *table_rows)),
            "test_schema_privileges": sorted({row["PRIVILEGE_TYPE"] for row in schema_rows if _local_schema(row["TABLE_SCHEMA"])}),
            "table_privileges": tables, "table_grants_present": bool(table_rows)}


def privilege_blockers(grants: dict, present: set[str], migration: bool) -> list[str]:
    """@description: Enforce effective per-table capabilities and keep application DDL forbidden.
    @param grants: Sanitized metadata from the current account only.
    @param present: Actual known tables inspected by preflight.
    @param migration: Select the migration requirement map.
    @return: Missing rights or unsafe privileges, without secret values.
    """
    schema = set(grants["test_schema_privileges"])  # [schema capability]: Inherited by every table in this schema.
    table_grants = grants.get("table_privileges", {})
    allowed = migration_requirements() if migration else application_requirements()  # [maximum authority]: Applies even to future table grants.
    required = allowed if migration else {name: allowed[name] for name in ORIGINAL_TABLES | (present & set(allowed))}
    missing = any(not rights.issubset(schema | set(table_grants.get(name, ()))) for name, rights in required.items())
    supplied = schema | {right for rights in table_grants.values() for right in rights}
    blockers = ["MIGRATION_PRIVILEGES_MISSING" if migration else "APPLICATION_DML_PRIVILEGES_MISSING"] if missing else []
    if grants.get("grant_option") or (not migration and supplied - DML):
        blockers.append("EXCESS_ACCOUNT_PRIVILEGES")
    if _excess_table_rights(schema, table_grants, allowed):
        blockers.append("EXCESS_MIGRATION_PRIVILEGES" if migration else "EXCESS_APPLICATION_PRIVILEGES")
    return blockers
