"""Verify that the compensation tables match the reviewed specification before any compensation transaction runs.

The specification names every column, every unique key and every named CHECK constraint the code relies on. A missing table, a missing
column, a missing unique key or a missing CHECK refuses explicitly with a code-only error. The feature is off by default; when it is off no
compensation table is read. Only a missing-table error is translated; every other database error propagates unchanged.
"""

from typing import Any

SCHEMA_SPEC = {
    "exchange_incidents": {
        "columns": ("incident_id", "recommendation_id", "debit_ledger_id", "discord_user_id", "song_id", "reason_code", "approved_by",
                    "approved_at", "state", "refund_status", "refund_ledger_id", "redelivery_decision", "settled_at", "closure", "closed_at"),
        "unique": (("recommendation_id",), ("debit_ledger_id",), ("refund_ledger_id",)),
        "checks": ("chk_incidents_refund", "chk_incidents_settled", "chk_incidents_non_fault", "chk_incidents_closed"),
    },
    "exchange_redelivery_grants": {
        "columns": ("grant_id", "incident_id", "discord_user_id", "song_id", "granted_at", "state", "claim_token", "claimed_at",
                    "delivery_fact", "message_id", "delivered_at", "owner_disposition", "disposition_by", "disposition_at",
                    "closed_at", "holds_song"),
        "unique": (("incident_id",),),
        "checks": ("chk_grants_claim", "chk_grants_sent", "chk_grants_holds"),
    },
    "exchange_incident_events": {
        "columns": ("event_id", "incident_id", "action", "actor", "at", "detail"),
        "unique": (),
        "checks": (),
    },
}  # [reviewed specification]: Changing it requires changing the candidate DDL and the offline SQLite mirror in the same change.
COMPENSATION_TABLES = tuple(SCHEMA_SPEC)
MISSING_TABLE_CODE = 1146  # [MySQL]: ER_NO_SUCH_TABLE.


class CompensationDisabled(RuntimeError):
    """Refuse a compensation entry point while the feature switch is off; the message names the switch only."""


class CompensationSchemaMissing(RuntimeError):
    """Refuse a compensation transaction whose table is absent; the message names the table only."""


class CompensationSchemaMismatch(RuntimeError):
    """Refuse a compensation transaction whose table lacks a required column, unique key or CHECK; the message names the element only."""


def _is_missing_table(error: Exception) -> bool:
    """@description: Recognise only the missing-table error of MySQL or of the offline stand-in.
    @param error: Exception raised by a probe statement.
    @return: True only when the error says the table does not exist.
    """
    code = error.args[0] if error.args else None
    return code == MISSING_TABLE_CODE or (isinstance(code, str) and "no such table" in code)


def _columns(cursor: Any, table: str) -> set:
    """@description: Read the column names of one table from an empty probe, without fetching any row.
    @param cursor: Cursor inside the gated transaction.
    @param table: Constant table name.
    @return: Column names as the engine reports them.
    """
    cursor.execute(f"SELECT * FROM {table} LIMIT 0")  # [probe]: Constant names only; no data is read.
    return {description[0] for description in cursor.description}


def _unique_keys(cursor: Any, table: str, dialect: str) -> set:
    """@description: Read the unique keys of one table as sets of column names, using the dialect's catalogue.
    @param cursor: Cursor inside the gated transaction.
    @param table: Constant table name.
    @param dialect: Either mysql or sqlite.
    @return: Set of frozensets of column names, one per unique key.
    """
    if dialect == "sqlite":
        keys = set()
        cursor.execute(f"PRAGMA index_list({table})")
        for index in cursor.fetchall():
            if index[2]:  # [unique flag]: Column two of PRAGMA index_list.
                cursor.execute(f"PRAGMA index_info({index[1]})")
                keys.add(frozenset(column[2] for column in cursor.fetchall()))
        return keys
    cursor.execute("SELECT INDEX_NAME AS index_name, COLUMN_NAME AS column_name FROM information_schema.STATISTICS "
                   "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND NON_UNIQUE = 0", (table,))
    grouped: dict = {}
    for row in cursor.fetchall():
        grouped.setdefault(row["index_name"], set()).add(row["column_name"])
    return {frozenset(columns) for columns in grouped.values()}


def _checks(cursor: Any, table: str, dialect: str) -> set:
    """@description: Read the names of the CHECK constraints of one table, using the dialect's catalogue.
    @param cursor: Cursor inside the gated transaction.
    @param table: Constant table name.
    @param dialect: Either mysql or sqlite.
    @return: Set of CHECK constraint names present on the table.
    """
    if dialect == "sqlite":
        cursor.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,))
        row = cursor.fetchone()
        text = row["sql"] if row is not None else ""
        return {name for name in SCHEMA_SPEC[table]["checks"] if name in text}
    cursor.execute("SELECT CONSTRAINT_NAME AS name FROM information_schema.TABLE_CONSTRAINTS "
                   "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND CONSTRAINT_TYPE = 'CHECK'", (table,))
    return {row["name"] for row in cursor.fetchall()}


def _dialect(cursor: Any) -> str:
    """@description: Name the catalogue family of a cursor; the offline stand-in declares itself, every production cursor is MySQL.
    @param cursor: Cursor inside the gated transaction.
    @return: sqlite or mysql.
    """
    return getattr(cursor, "catalogue_dialect", "mysql")


def _check_table(cursor: Any, table: str, spec: dict, dialect: str) -> None:
    """@description: Compare one table with its specification and refuse the first missing element.
    @param cursor: Cursor inside the gated transaction.
    @param table: Constant table name.
    @param spec: Specification entry of the table.
    @param dialect: Catalogue family, mysql or sqlite.
    @return: None; the first mismatch raises a code-only error.
    """
    try:
        present = _columns(cursor, table)
    except Exception as error:
        if not _is_missing_table(error):
            raise
        raise CompensationSchemaMissing(f"compensation table missing: {table}") from error
    missing_columns = set(spec["columns"]) - present
    if missing_columns:
        raise CompensationSchemaMismatch(f"{table} lacks columns: {', '.join(sorted(missing_columns))}")
    keys = _unique_keys(cursor, table, dialect)
    for required in spec["unique"]:
        if frozenset(required) not in keys:
            raise CompensationSchemaMismatch(f"{table} lacks unique key on: {', '.join(required)}")
    found = _checks(cursor, table, dialect)
    for name in spec["checks"]:
        if name not in found:
            raise CompensationSchemaMismatch(f"{table} lacks CHECK constraint: {name}")


def require_schema(cursor: Any) -> None:
    """@description: Confirm every compensation table, column, unique key and named CHECK matches the reviewed specification before any compensation transaction proceeds.
    @param cursor: Cursor inside the gated transaction.
    @return: None; the first mismatch raises a code-only error and nothing is written.
    """
    dialect = _dialect(cursor)
    for table, spec in SCHEMA_SPEC.items():
        _check_table(cursor, table, spec, dialect)
