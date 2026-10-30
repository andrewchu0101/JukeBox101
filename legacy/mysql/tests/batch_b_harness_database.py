"""Read and clean only an already migrated disposable MySQL schema.

No helper creates schemas, grants privileges, disables foreign keys or writes migration records.
"""

import re
from collections.abc import Callable
from typing import Any
from bot.config import DatabaseSettings
from bot.database.connection import open_connection
from bot.database.submission_migration import migration_plan, MIGRATION_VERSION
from tests.batch_b_harness_contract import (TABLES, CONTROL_COLUMNS, PRIMARY_KEYS, DELETE_ORDER,
    HARNESS_USER, HarnessSafetyError, harness_requirements, expected_server_uuid)


def verified_connection(settings: DatabaseSettings, user: str) -> Any:
    """@description: Reject configured and actual destinations before allowing any test SQL.
    @param settings: Explicit app or harness settings with redacted password representation.
    @param user: Fixed expected account for this role.
    @return: Fresh connection owned by its calling worker.
    """
    if user not in {HARNESS_USER, "jukebox101_test_app"} or (settings.host, settings.port, settings.database, settings.user) != ("127.0.0.1", 3306, "jukebox101_test", user):
        raise HarnessSafetyError("DESTINATION_REJECTED")
    expected_server_uuid()
    connection = open_connection(settings)  # [owned session]: Never shared between concurrent workers.
    try:
        with connection.cursor() as cursor:
            check_identity(cursor, user)
            cursor.execute("SET SESSION innodb_lock_wait_timeout=2")
        connection.rollback()
        return connection
    except BaseException:
        connection.close()
        raise


def check_identity(cursor: Any, user: str) -> None:
    """@description: Verify authenticated identity, MySQL version and enforced referential integrity.
    @param cursor: New connection before business writes.
    @param user: Fixed expected app or harness account.
    @return: None.
    """
    cursor.execute("SELECT DATABASE() AS db,CURRENT_USER() AS account,@@port AS port,VERSION() AS version, "
                   "CURRENT_ROLE() AS roles,@@foreign_key_checks AS fk,@@server_uuid AS server_uuid")
    row = cursor.fetchone()  # [server authority]: Client connection strings do not prove authentication identity.
    version = tuple(int(part) for part in row["version"].split("-")[0].split(".")[:3])
    if (row["db"], row["account"], row["port"], row["roles"], row["fk"]) != ("jukebox101_test", user + "@127.0.0.1", 3306, "NONE", 1):
        raise HarnessSafetyError("SERVER_IDENTITY_REJECTED")
    if version < (8, 0, 22) or version[0] != 8 or "mariadb" in row["version"].lower():
        raise HarnessSafetyError("MYSQL_8_0_22_REQUIRED")
    if row["server_uuid"].lower() != expected_server_uuid():
        raise HarnessSafetyError("SERVER_UUID_MISMATCH")
    cursor.execute("SELECT 1 FROM information_schema.APPLICABLE_ROLES LIMIT 1")
    if cursor.fetchone() is not None:
        raise HarnessSafetyError("AVAILABLE_ROLES_REJECTED")


def verify_harness_grants(cursor: Any) -> None:
    """@description: Require table-scoped cleanup and only the reviewed control/reminder column updates.
    @param cursor: Authenticated harness cursor.
    @return: None; excessive or missing authority stops before enabling any switch.
    """
    grantee = "'jukebox101_test_harness'@'127.0.0.1'"  # [fixed ACL subject]: Not supplied by test parameters.
    grants = {}  # [effective table authority]: No global or schema grant is accepted.
    for level in ("USER", "SCHEMA", "TABLE", "COLUMN"):
        cursor.execute("SELECT * FROM information_schema." + level + "_PRIVILEGES WHERE GRANTEE=%s", (grantee,))
        grants[level] = tuple(cursor.fetchall())
    reject_broad_grants(grants)
    expected = harness_requirements()
    actual = {name: set() for name in expected}
    for row in grants["TABLE"]:
        if row["TABLE_SCHEMA"] != "jukebox101_test" or row["TABLE_NAME"] not in actual:
            raise HarnessSafetyError("HARNESS_TABLE_SCOPE_REJECTED")
        actual[row["TABLE_NAME"]].add(row["PRIVILEGE_TYPE"])
    if actual != expected:
        raise HarnessSafetyError("HARNESS_TABLE_PRIVILEGES_MISMATCH")
    verify_column_grants(grants["COLUMN"])
    verify_visible_grants(cursor, HARNESS_USER)


def reject_broad_grants(grants: dict) -> None:
    """@description: Reject inherited global/schema authority and grant options before checking minimum table rights.
    @param grants: Complete own-account privilege metadata.
    @return: None.
    """
    if any(row["PRIVILEGE_TYPE"] != "USAGE" for row in grants["USER"]) or grants["SCHEMA"]:
        raise HarnessSafetyError("BROAD_HARNESS_PRIVILEGES")
    if any(row["IS_GRANTABLE"] == "YES" for rows in grants.values() for row in rows):
        raise HarnessSafetyError("HARNESS_GRANT_OPTION_REJECTED")


def verify_column_grants(rows: tuple[dict, ...]) -> None:
    """@description: Keep harness updates away from schema_version, evidence, ledger and registry fields.
    @param rows: Information-schema column grant rows.
    @return: None.
    """
    expected = {("jb2_control", name) for name in CONTROL_COLUMNS} | {("jb2_reminders", "next_due_at")}
    actual = {(row["TABLE_NAME"], row["COLUMN_NAME"]) for row in rows}  # [restricted update fields]: No whole-table UPDATE.
    if actual != expected or any(row["TABLE_SCHEMA"] != "jukebox101_test" or row["PRIVILEGE_TYPE"] != "UPDATE" for row in rows):
        raise HarnessSafetyError("HARNESS_COLUMN_PRIVILEGES_MISMATCH")


def read_inventory(cursor: Any) -> dict:
    """@description: Read every reviewed row without accepting caller-selected tables or pagination.
    @param cursor: Owned read or cleanup transaction.
    @return: Full table mapping, used internally and never dumped into diagnostics.
    """
    return {name: read_table(cursor, name) for name in sorted(TABLES)}


def read_table(cursor: Any, name: str) -> tuple[dict, ...]:
    """@description: Bound composed identifiers to the fixed reviewed 23-table set.
    @param cursor: Owned transaction cursor.
    @param name: Reviewed static table identifier.
    @return: Actual rows from the exact test database table.
    """
    if name not in TABLES:
        raise HarnessSafetyError("UNAPPROVED_TABLE")
    cursor.execute("SELECT * FROM `" + name + "`")
    return tuple(cursor.fetchall())


def schema_fingerprints(cursor: Any) -> dict:
    """@description: Verify exact InnoDB inventory and detect schema drift without treating auto-increment gaps as data.
    @param cursor: Verified harness metadata cursor.
    @return: Normalized SHOW CREATE definitions for internal baseline comparison.
    """
    cursor.execute("SELECT TABLE_NAME,TABLE_TYPE,ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE()")
    objects = tuple(cursor.fetchall())  # [visible object inventory]: DBA must attest complete schema inventory independently.
    if {row["TABLE_NAME"] for row in objects} != TABLES or any(row["TABLE_TYPE"] != "BASE TABLE" or row["ENGINE"] != "InnoDB" for row in objects):
        raise HarnessSafetyError("SCHEMA_INVENTORY_MISMATCH")
    cursor.execute("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()")
    if cursor.fetchall():
        raise HarnessSafetyError("TRIGGERS_REQUIRE_REVIEW")
    definitions = {}  # [structural baseline]: AUTO_INCREMENT counters may advance after rolled-back inserts.
    for name in sorted(TABLES):
        cursor.execute("SHOW CREATE TABLE `" + name + "`")
        definitions[name] = re.sub(r" AUTO_INCREMENT=\d+", "", cursor.fetchone()["Create Table"])
    return definitions


def require_baseline(rows: dict) -> dict:
    """@description: Preserve accepted migration state and refuse any preexisting business data.
    @param rows: Complete initial inventory.
    @return: Original control row that must be restored exactly after cleanup.
    """
    marker = rows["jb2_migrations"]  # [protected registry]: Never modified by app or harness.
    if len(marker) != 1 or marker[0] != {"version": MIGRATION_VERSION, "checksum": migration_plan()[0], "state": "applied"}:
        raise HarnessSafetyError("MIGRATION_MARKER_MISMATCH")
    if len(rows["jb2_control"]) != 1:
        raise HarnessSafetyError("CONTROL_BASELINE_MISSING")
    control = rows["jb2_control"][0]
    if control["singleton_id"] != 1 or control["schema_version"] != MIGRATION_VERSION or any(control[name] for name in CONTROL_COLUMNS[2:]):
        raise HarnessSafetyError("CONTROL_NOT_OFF")
    if any(records for name, records in rows.items() if name not in {"jb2_control", "jb2_migrations"}):
        raise HarnessSafetyError("NONEMPTY_BASELINE_REFUSED")
    return dict(control)


def set_control(cursor: Any, values: dict) -> None:
    """@description: Update only the singleton's reviewed mutable fields, never its schema identity.
    @param cursor: Harness cursor under the shared control lock.
    @param values: Exact five-field control state.
    @return: None.
    """
    if set(values) != set(CONTROL_COLUMNS):
        raise HarnessSafetyError("INVALID_CONTROL_FIELDS")
    cursor.execute("UPDATE jb2_control SET accepted_sequence=%s,revision=%s,snapshot_ready=%s,"
                   "legacy_writers_quiesced=%s,settlement_enabled=%s WHERE singleton_id=1",
                   tuple(values[name] for name in CONTROL_COLUMNS))


def lock_inventory(connection: Any) -> dict:
    """@description: Stabilize all reviewed rows and gaps before ownership checks and cleanup.
    @param connection: Dedicated harness session after all worker processes have terminated.
    @return: Full inventory under serializable transaction and control-row lock.
    """
    connection.rollback()
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        connection.begin()
        lock_control(cursor)
        return read_inventory(cursor)


def delete_owned(cursor: Any, rows: dict, observer: Callable | None = None) -> None:
    """@description: Delete only verified primary keys in foreign-key order with affected-row assertions.
    @param cursor: Locked harness cleanup cursor.
    @param rows: Full inventory already proven owned before the first DELETE.
    @param observer: Optional durable attempt observer; an observed DELETE is not a committed deletion.
    @return: None.
    """
    versions = version_delete_order(rows['jb2_versions'])  # [prevalidated graph]: Reject cycles before issuing any DELETE.
    for table in DELETE_ORDER:
        for row in rows[table]:
            observed_delete(cursor, table, row, observer)
    for row in versions:
        observed_delete(cursor, 'jb2_versions', row, observer)


def version_delete_order(rows: tuple[dict, ...]) -> tuple[dict, ...]:
    """@description: Validate the entire version graph before any child-table cleanup is attempted.
    @param rows: Complete, already owned version inventory.
    @return: Aliases before roots; self-root SQL behavior is not proved by this offline ordering.
    """
    versions = {row['version_id']: row for row in rows}  # [self-FK graph]: No out-of-inventory parent may be ignored.
    if len(versions) != len(rows) or any(row['canonical_id'] not in versions for row in rows):
        raise HarnessSafetyError('VERSION_INVENTORY_INCOMPLETE')
    ordered = []  # [explicit deletion intent]: This function never executes SQL or changes the source inventory.
    while versions:
        leaves = [name for name in versions if not any(row["canonical_id"] == name and key != name for key, row in versions.items())]
        if not leaves:
            raise HarnessSafetyError("VERSION_CYCLE_REFUSED")
        for name in leaves:
            ordered.append(versions.pop(name))
    return tuple(ordered)


def observed_delete(cursor: Any, table: str, row: dict, observer: Callable | None) -> None:
    """@description: Preserve the exact failing cleanup target without storing driver messages.
    @param cursor: Owned cleanup transaction.
    @param table: Reviewed table identifier.
    @param row: Previously verified owned row.
    @param observer: Durable nonsecret progress callback.
    @return: None.
    """
    if observer is not None:
        observer('DELETE_ATTEMPT', table, row)
    delete_primary(cursor, table, row)
    if observer is not None:
        observer('DELETE_EXECUTED_UNCOMMITTED', table, row)


def delete_primary(cursor: Any, table: str, row: dict) -> None:
    """@description: Forbid all-table cleanup and require a concrete complete primary-key predicate.
    @param cursor: Transaction-owned cleanup cursor.
    @param table: Static cleanup table with declared primary key.
    @param row: Owned actual row whose key is bound as values.
    @return: None.
    """
    if table == 'jb2_versions':
        from tests.batch_b_fk_window import require_no_version_children
        require_no_version_children(cursor, row['version_id'])
    keys = PRIMARY_KEYS[table]  # [precise cleanup identity]: Composite keys retain every component.
    cursor.execute("DELETE FROM `" + table + "` WHERE " + " AND ".join("`" + key + "`=%s" for key in keys), tuple(row[key] for key in keys))
    if cursor.rowcount != 1:
        raise HarnessSafetyError("CLEANUP_ROWCOUNT_MISMATCH:" + table)


def lock_control(cursor: Any) -> None:
    """@description: Acquire the common control row with an authorized column-level no-op UPDATE, never broader UPDATE rights.
    @param cursor: Harness transaction cursor before full stable inventory.
    @return: None; a missing or wrong-version control row blocks further management.
    """
    cursor.execute("UPDATE jb2_control SET revision=revision WHERE singleton_id=1 AND schema_version=%s", (MIGRATION_VERSION,))
    cursor.execute("SELECT schema_version FROM jb2_control WHERE singleton_id=1")
    if cursor.fetchone() != {"schema_version": MIGRATION_VERSION}:
        raise HarnessSafetyError("CONTROL_LOCK_IDENTITY_MISMATCH")


def verify_visible_grants(cursor: Any, user: str) -> None:
    """@description: Catch dynamic, role, proxy and routine grants not fully represented by table privilege metadata.
    @param cursor: Authenticated own-account session; raw grant text is never logged.
    @param user: Fixed app or harness username already verified through CURRENT_USER.
    @return: None; every visible grant must fit the same narrow reviewed scope.
    """
    cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
    for row in cursor.fetchall():
        statement = next(iter(row.values()))  # [private ACL inspection]: MySQL 8 grants only, never SHOW CREATE USER authentication data.
        if not grant_statement_allowed(statement, user):
            raise HarnessSafetyError("UNREVIEWED_EFFECTIVE_GRANT")


def grant_statement_allowed(statement: str, user: str) -> bool:
    """@description: Reject unmanaged grant forms without pretending to implement a general SQL parser.
    @param statement: Own-account SHOW GRANTS output from validated MySQL 8.
    @param user: Expected account identity.
    @return: Whether the grant has a reviewed privilege family and exact test object scope.
    """
    plain = statement.replace("`", "").replace("'", "")
    if plain == "GRANT USAGE ON *.* TO " + user + "@127.0.0.1":
        return True
    match = re.fullmatch(r"GRANT (.+) ON ([a-z0-9_\\]+)\.([a-z0-9_*]+) TO " + re.escape(user + "@127.0.0.1"), plain)
    if match is None:
        return False
    rights, schema, table = match.groups()
    if schema not in {"jukebox101_test", r"jukebox101\_test"}:
        return False
    privileges = {name.strip() for name in re.sub(r"\([^)]*\)", "", rights).split(",")}
    allowed = {"SELECT", "UPDATE", "DELETE"} | ({"INSERT"} if user == "jukebox101_test_app" else set())
    if table == "*":
        return user == "jukebox101_test_app" and schema == r"jukebox101\_test" and privileges == {"SELECT"}
    return table in TABLES and privileges.issubset(allowed)


def verify_application_columns(cursor: Any) -> None:
    """@description: Detect extra column grants that could bypass read-only application registry policy.
    @param cursor: Authenticated application session using the existing table-level minimum contract.
    @return: None; application column grants require separate review rather than silent acceptance.
    """
    cursor.execute("SELECT PRIVILEGE_TYPE FROM information_schema.COLUMN_PRIVILEGES WHERE GRANTEE=%s",
                   ("'jukebox101_test_app'@'127.0.0.1'",))
    if cursor.fetchall():
        raise HarnessSafetyError("APPLICATION_COLUMN_GRANTS_REQUIRE_REVIEW")
