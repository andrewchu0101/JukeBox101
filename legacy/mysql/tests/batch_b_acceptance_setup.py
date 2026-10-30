"""Provision only the reviewed disposable harness account after complete DBA checks.

This executor consumes private in-memory credentials and reuses the existing harness contracts without running migrations.
"""

import re
import pymysql
from collections.abc import Callable
from pathlib import Path
from typing import Any
from bot.config import DatabaseSettings
from bot.database.connection import open_connection
from tests.batch_b_harness_contract import HARNESS_USER, HarnessSafetyError, harness_requirements
from tests.batch_b_harness_database import (require_baseline, read_inventory, schema_fingerprints,
    verified_connection, verify_harness_grants)

ACCOUNT = "'jukebox101_test_harness'@'127.0.0.1'"
ROOT = Path(__file__).resolve().parents[1]


def record_error(state: dict, error: BaseException) -> None:
    """@description: Preserve numeric driver diagnostics without ever serializing error messages or parameters.
    @param state: Sanitized execution report.
    @param error: Original exception, retained only in memory.
    @return: None.
    """
    if 'error_type' in state:
        return
    state['error_type'] = type(error).__name__
    state['failure_step'] = state.get('current_step', state.get('stage', 'INPUT_PROTOCOL'))
    number = mysql_error_number(error)  # [safe driver evidence]: Never coerce arbitrary error arguments into text.
    if number is not None:
        state['mysql_error_number'] = number
    if isinstance(error, HarnessSafetyError) and re.fullmatch(r'[A-Z0-9_:]+', str(error)):
        state['safety_code'] = str(error)


def mysql_error_number(error: BaseException) -> int | None:
    """@description: Accept only a genuine driver's integer error number, never its raw message.
    @param error: Exception at a meaningful execution boundary.
    @return: Bounded numeric server/client code when available.
    """
    if isinstance(error, pymysql.MySQLError) and error.args and type(error.args[0]) is int:
        if 0 <= error.args[0] <= 65535:
            return error.args[0]
    return None


def stage_call(state: dict, step: str, operation: Callable, *arguments: Any) -> Any:
    """@description: Label an existing operation without changing its safety checks or retry behavior.
    @param state: Shared sanitized stage report.
    @param step: Static internal operation label, never SQL or user input.
    @param operation: Existing connection, metadata or validation operation.
    @param arguments: Private in-memory arguments, never serialized.
    @return: Original operation result.
    """
    state['current_step'] = step  # [diagnostic position]: Captured before the failing operation begins.
    try:
        return operation(*arguments)
    except BaseException as error:
        record_error(state, error)
        raise


def query(cursor: Any, sql: str, parameters: tuple = ()) -> tuple:
    """@description: Consume metadata without sending raw server text to reports.
    @param cursor: Owned DBA cursor.
    @param sql: Static reviewed query.
    @param parameters: Bound metadata predicates.
    @return: Private metadata rows.
    """
    cursor.execute(sql, parameters)
    return tuple(cursor.fetchall())


def check_dba(cursor: Any, username: str, server_uuid: str) -> None:
    """@description: Require full metadata visibility and the independently approved local server.
    @param cursor: DBA session before any account mutation.
    @param username: Locally entered DBA name.
    @param server_uuid: Previously approved instance identifier.
    @return: None.
    """
    row = query(cursor, "SELECT DATABASE() db,CURRENT_USER() account,@@port port,@@server_uuid uuid,"
                "VERSION() version,@@mandatory_roles roles,@@foreign_key_checks fk")[0]
    if (row['db'], row['port'], row['uuid'].lower(), row['roles'], row['fk']) != ('jukebox101_test', 3306, server_uuid, '', 1):
        raise HarnessSafetyError('DBA_SERVER_REJECTED')
    if row['account'] not in {username + '@127.0.0.1', username + '@localhost'}:
        raise HarnessSafetyError('DBA_IDENTITY_REJECTED')
    version = tuple(int(part) for part in row['version'].split('-')[0].split('.')[:3])
    if version < (8, 0, 22) or version[0] != 8 or 'mariadb' in row['version'].lower():
        raise HarnessSafetyError('MYSQL_VERSION_REJECTED')
    grants = query(cursor, 'SHOW GRANTS FOR CURRENT_USER()')  # [metadata authority]: Do not infer visibility from empty results.
    global_rights = [next(iter(item.values())).replace('`', '') for item in grants]
    if not any(full_metadata_authority(text) for text in global_rights):
        raise HarnessSafetyError('FULL_DBA_METADATA_AUTHORITY_REQUIRED')


def full_metadata_authority(statement: str) -> bool:
    """@description: Accept existing DBA global visibility without granting any new administrative rights.
    @param statement: Private own-account grant text.
    @return: Whether static SELECT/TRIGGER/CREATE USER and grant authority are explicit.
    """
    match = re.fullmatch(r'GRANT (.+) ON \*\.\* TO .+ WITH GRANT OPTION', statement)
    if match is None:
        return False
    rights = {value.strip() for value in match[1].split(',')}
    return rights == {'ALL PRIVILEGES'} or {'SELECT', 'TRIGGER', 'CREATE USER'}.issubset(rights)


def reviewed_foreign_keys() -> set:
    """@description: Compare actual FK columns against the two already accepted schema artifacts.
    @param: none
    @return: Exact local and referenced table-column edges, including compound keys.
    """
    paths = ('bot/database/schema.sql', 'bot/database/migrations/002_submission_economy.sql')
    expected = set()  # [reviewed edges]: No new FK, cross-schema edge or changed compound position is allowed.
    for path in paths:
        # [statement boundaries]: Match migration_plan: comment semicolons cannot terminate CREATE TABLE.
        source = re.sub(r'--[^\n]*', '', (ROOT / path).read_text(encoding='utf-8'))
        for statement in source.split(';'):
            table = re.search(r'CREATE TABLE IF NOT EXISTS (\w+)', statement)
            if table is None:
                continue
            expected.update(statement_foreign_keys(table[1], statement))
    return expected


def statement_foreign_keys(table: str, statement: str) -> set:
    """@description: Preserve every compound FK position without deeply nested schema parsing.
    @param table: Reviewed child table.
    @param statement: Accepted local CREATE statement, never executed.
    @return: Expected table-column FK edges.
    """
    edges = set()  # [compound references]: Exact positional matching prevents partial-key approval.
    for child, parent, target in re.findall(r'FOREIGN KEY\s*\(([^)]+)\)\s*REFERENCES (\w+)\s*\(([^)]+)\)', statement):
        for position, (column, reference) in enumerate(zip(child.split(','), target.split(','), strict=True), 1):
            edges.add((table, column.strip(), position, 'jukebox101_test', parent, reference.strip()))
    return edges


def verify_metadata(cursor: Any) -> tuple[dict, dict]:
    """@description: Refuse hidden triggers, unreviewed FKs or nonempty accepted baseline before CREATE USER.
    @param cursor: Fully visible DBA metadata cursor.
    @return: Baseline inventory and structural fingerprints.
    """
    definitions = schema_fingerprints(cursor)  # [accepted structure]: Also checks exact 23 InnoDB tables and visible triggers.
    rows = read_inventory(cursor)
    require_baseline(rows)
    foreign_keys = query(cursor, "SELECT TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,REFERENCED_TABLE_SCHEMA,"
        "REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA='jukebox101_test' AND REFERENCED_TABLE_SCHEMA IS NOT NULL")
    actual = {tuple(row[key] for key in ('TABLE_NAME', 'COLUMN_NAME', 'ORDINAL_POSITION', 'REFERENCED_TABLE_SCHEMA',
              'REFERENCED_TABLE_NAME', 'REFERENCED_COLUMN_NAME')) for row in foreign_keys}
    if actual != reviewed_foreign_keys():
        raise HarnessSafetyError('FOREIGN_KEY_DRIFT')
    incoming = query(cursor, "SELECT TABLE_SCHEMA FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE REFERENCED_TABLE_SCHEMA='jukebox101_test' AND TABLE_SCHEMA<>'jukebox101_test'")
    if incoming:
        raise HarnessSafetyError('EXTERNAL_REFERENCING_SCHEMA')
    return rows, definitions


def account_snapshot(cursor: Any) -> tuple:
    """@description: Preserve application and locked migrator identities and effective grants exactly.
    @param cursor: DBA session with mysql metadata visibility.
    @return: Private immutable comparison snapshot without authentication properties.
    """
    accounts = query(cursor, "SELECT User,Host,account_locked FROM mysql.user WHERE User IN "
        "('jukebox101_test_app','jukebox101_test_migrator') ORDER BY User,Host")
    if len(accounts) != 2 or any(row['Host'] != '127.0.0.1' for row in accounts):
        raise HarnessSafetyError('EXISTING_ACCOUNT_HOSTS_REJECTED')
    if {row['User']: row['account_locked'] for row in accounts} != {'jukebox101_test_app': 'N', 'jukebox101_test_migrator': 'Y'}:
        raise HarnessSafetyError('EXISTING_ACCOUNT_LOCK_STATE_REJECTED')
    grants = tuple(query(cursor, "SHOW GRANTS FOR '" + name + "'@'127.0.0.1'")
                   for name in ('jukebox101_test_app', 'jukebox101_test_migrator'))
    return accounts, grants


def verify_extra_authority(cursor: Any) -> None:
    """@description: Reject harness authority not represented by table and column grants.
    @param cursor: DBA session while the newly created account remains locked.
    @return: None.
    """
    statements = (
        "SELECT PRIV FROM mysql.global_grants WHERE USER=%s AND HOST='127.0.0.1'",
        "SELECT FROM_USER FROM mysql.role_edges WHERE TO_USER=%s AND TO_HOST='127.0.0.1'",
        "SELECT Db FROM mysql.procs_priv WHERE User=%s AND Host='127.0.0.1'",
        "SELECT Proxied_user FROM mysql.proxies_priv WHERE User=%s AND Host='127.0.0.1'",
    )
    if any(query(cursor, sql, (HARNESS_USER,)) for sql in statements):
        raise HarnessSafetyError('HARNESS_EXTRA_AUTHORITY')


def grant_statements() -> tuple[str, ...]:
    """@description: Load only reviewed GRANT lines, never source the executable SQL document wholesale.
    @param: none
    @return: Exactly the reviewed 25 account-specific statements.
    """
    source = (ROOT / 'docs/batch-b-test-harness-grants.sql').read_text(encoding='utf-8')
    grants = tuple(line.strip() for line in source.splitlines() if line.startswith('GRANT '))
    expected = {'GRANT ' + ', '.join(sorted(rights)) + ' ON `jukebox101_test`.`' + table + '` TO ' + ACCOUNT + ';'
                for table, rights in harness_requirements().items()}
    expected.add('GRANT UPDATE (`accepted_sequence`, `revision`, `snapshot_ready`, `legacy_writers_quiesced`, `settlement_enabled`) ON `jukebox101_test`.`jb2_control` TO ' + ACCOUNT + ';')
    expected.add('GRANT UPDATE (`next_due_at`) ON `jukebox101_test`.`jb2_reminders` TO ' + ACCOUNT + ';')
    if len(grants) != 25 or set(grants) != expected:
        raise HarnessSafetyError('GRANT_ARTIFACT_REJECTED')
    return grants


def verify_locked_account(cursor: Any) -> None:
    """@description: Verify a single locked harness identity before granting or unlocking.
    @param cursor: DBA metadata cursor.
    @return: None.
    """
    rows = query(cursor, 'SELECT User,Host,account_locked FROM mysql.user WHERE User=%s', (HARNESS_USER,))
    if rows != ({'User': HARNESS_USER, 'Host': '127.0.0.1', 'account_locked': 'Y'},):
        raise HarnessSafetyError('HARNESS_LOCK_STATE_REJECTED')


def provision(connection: Any, password: str, baseline: tuple, state: dict) -> None:
    """@description: Fail closed on nontransactional account setup and verify least rights before unlock.
    @param connection: Fully prechecked DBA session.
    @param password: Private new harness credential, bound rather than interpolated.
    @param baseline: Accepted business inventory and schema definitions.
    @param state: Tracks acknowledged account creation without touching a colliding account.
    @return: None.
    """
    with connection.cursor() as cursor:
        original_accounts = stage_call(state, 'A_PROVISION_ACCOUNT_BASELINE', account_snapshot, cursor)
        stage_call(state, 'A_HARNESS_CREATE_LOCKED', cursor.execute, 'CREATE USER ' + ACCOUNT + ' IDENTIFIED BY %s ACCOUNT LOCK', (password,))
        state['account_created'] = True
        stage_call(state, 'A_HARNESS_LOCK_VERIFY', verify_locked_account, cursor)
        for index, statement in enumerate(grant_statements(), 1):
            stage_call(state, 'A_HARNESS_GRANT_' + str(index).zfill(2), cursor.execute, statement)
        state['stage'] = 'A_PRIVILEGE_VERIFY_FAILED'
        stage_call(state, 'A_HARNESS_GRANTS_VERIFY', verify_harness_grants_as_dba, cursor)
        stage_call(state, 'A_HARNESS_EXTRA_AUTHORITY', verify_extra_authority, cursor)
        if stage_call(state, 'A_ACCOUNT_BASELINE_VERIFY', account_snapshot, cursor) != original_accounts or stage_call(state, 'A_SCHEMA_BASELINE_VERIFY', verify_metadata, cursor) != baseline:
            raise HarnessSafetyError('PROVISION_BASELINE_DRIFT')
        stage_call(state, 'A_HARNESS_PRE_UNLOCK_VERIFY', verify_locked_account, cursor)
        stage_call(state, 'A_HARNESS_UNLOCK', cursor.execute, 'ALTER USER ' + ACCOUNT + ' ACCOUNT UNLOCK')


def verify_harness_grants_as_dba(cursor: Any) -> None:
    """@description: Reuse exact table-column verifier while adapting its own-account SHOW GRANTS query.
    @param cursor: DBA cursor whose grant target must remain the harness.
    @return: None.
    """
    verify_harness_grants(GrantCursor(cursor))


class GrantCursor:
    """Redirect only the own-account grant query to the locked harness account."""

    def __init__(self, cursor: Any) -> None:
        """@description: Keep existing privilege verification reusable before account unlock.
        @param self: Metadata adapter.
        @param cursor: DBA cursor.
        @return: None.
        """
        self.cursor = cursor

    def execute(self, sql: str, parameters: tuple = ()) -> Any:
        """@description: Bind the single identity-dependent query to the provisioned account.
        @param self: Metadata adapter.
        @param sql: Existing verifier statement.
        @param parameters: Existing bound values.
        @return: Driver execution result.
        """
        if sql == 'SHOW GRANTS FOR CURRENT_USER()':
            sql = 'SHOW GRANTS FOR ' + ACCOUNT
        return self.cursor.execute(sql, parameters)

    def fetchall(self) -> Any:
        """@description: Return only metadata requested by the existing verifier.
        @param self: Metadata adapter.
        @return: Cursor rows.
        """
        return self.cursor.fetchall()


def stage_a(dba: DatabaseSettings, application: DatabaseSettings, harness: DatabaseSettings, server_uuid: str, state: dict) -> tuple:
    """@description: Keep DBA credentials out of application tests and stop partial provisioning safely.
    @param dba: Private local DBA connection profile.
    @param application: Existing business identity verified before account creation.
    @param harness: Private fixed harness profile.
    @param server_uuid: Independently approved instance pin.
    @param state: Sanitized stage progress, never secret values.
    @return: Accepted baseline for independent post-test comparison.
    """
    connection = stage_call(state, 'A_DBA_CONNECT', open_connection, dba)  # [DBA lifetime]: Never shared with tests.
    try:
        baseline = stage_call(state, 'A_DBA_PRECHECK', precheck, connection, dba.user, server_uuid, state)
        stage_call(state, 'A_APPLICATION_LOGIN_AND_PRIVILEGES', verify_application_login, application)
        state['stage'] = 'A_PROVISION_FAILED'
        stage_call(state, 'A_HARNESS_PROVISION', provision, connection, harness.password, baseline, state)
        state['stage'] = 'A_PRIVILEGE_VERIFY_FAILED'
        stage_call(state, 'A_HARNESS_LOGIN_AND_PRIVILEGES', verify_harness_login, harness)
        return baseline
    except BaseException:
        relock_created_account(connection, state)
        raise
    finally:
        close_dba(connection, state)


def precheck(connection: Any, username: str, server_uuid: str, state: dict | None = None) -> tuple:
    """@description: Finish all read-only safety gates before nontransactional account creation.
    @param connection: Fixed local DBA session.
    @param username: Expected authenticated DBA.
    @param server_uuid: Independent approved instance pin.
    @param state: Optional report shared by the authorized runner.
    @return: Accepted baseline and schema fingerprints.
    """
    from tests.mysql_safety import validate_test_constraints
    state = {} if state is None else state  # [shared diagnostic scope]: Nested failures retain their specific step.
    with connection.cursor() as cursor:
        stage_call(state, 'A_DBA_IDENTITY_AND_VISIBILITY', check_dba, cursor, username, server_uuid)
        if stage_call(state, 'A_EXCLUSIVE_LOCK', query, cursor, 'SELECT GET_LOCK(%s,0) owned', ('jukebox101:batch-b-test-harness',)) != ({'owned': 1},):
            raise HarnessSafetyError('HARNESS_ALREADY_RUNNING')
        baseline = stage_call(state, 'A_SCHEMA_AND_BASELINE_METADATA', verify_metadata, cursor)
        stage_call(state, 'A_EXISTING_ACCOUNTS', account_snapshot, cursor)
        if stage_call(state, 'A_HARNESS_ABSENCE', query, cursor, 'SELECT User,Host FROM mysql.user WHERE User=%s', (HARNESS_USER,)):
            raise HarnessSafetyError('HARNESS_ALREADY_EXISTS_STOP_NO_RECREATE')
        stage_call(state, 'A_GRANT_ARTIFACT', grant_statements)
    stage_call(state, 'A_CONSTRAINT_METADATA', validate_test_constraints, connection)
    return baseline


def close_dba(connection: Any, state: dict) -> None:
    """@description: Report close failures without replacing the primary operation's safe diagnostics.
    @param connection: Owned DBA session.
    @param state: Primary failure evidence or successful progress.
    @return: None; a standalone close failure still stops execution.
    """
    try:
        connection.close()
    except BaseException as error:
        if 'error_type' not in state:
            state['current_step'] = 'A_DBA_CLOSE'
            record_error(state, error)
            raise
        state['dba_close_failed'] = True
        state['dba_close_error_type'] = type(error).__name__
        number = mysql_error_number(error)  # [secondary diagnostic]: Preserve the original failure_step and code.
        if number is not None:
            state['dba_close_mysql_error_number'] = number
def verify_harness_login(settings: DatabaseSettings) -> None:
    """@description: Require a fresh authenticated harness session before discarding DBA authority.
    @param settings: Newly provisioned harness profile.
    @return: None.
    """
    connection = verified_connection(settings, HARNESS_USER)
    try:
        with connection.cursor() as cursor:
            verify_harness_grants(cursor)
    finally:
        connection.close()


def relock_created_account(connection: Any, state: dict) -> None:
    """@description: Leave a partially provisioned account disabled without dropping or recreating it.
    @param connection: Existing DBA connection, possibly broken.
    @param state: Sanitized current stage and recovery outcome.
    @return: None.
    """
    if not state.get('account_created'):
        return
    try:
        with connection.cursor() as cursor:
            cursor.execute('ALTER USER ' + ACCOUNT + ' ACCOUNT LOCK')
        state['harness_relocked'] = True
    except Exception:
        state['harness_relocked'] = False


def verify_application_login(settings: DatabaseSettings) -> None:
    """@description: Reuse existing application ACL verification before provisioning mutations.
    @param settings: Fixed app credentials obtained through private IPC.
    @return: None.
    """
    from tests.batch_b_harness import MySQLHarness
    verifier = MySQLHarness.__new__(MySQLHarness)  # [read-only adapter]: Avoid allocating a run journal or enabling control.
    verifier.application = settings
    verifier.verify_application()
