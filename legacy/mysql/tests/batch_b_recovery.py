"""Inspect an existing failed harness journal and committed test rows without recovery writes.

The existing interactive entry supplies one private credential for the dedicated read-only account; reports contain only registered keys, counts and validation outcomes.
"""

import hashlib
import json
import os
import re
from tests.batch_b_assertion_guard import guarded_command
from pathlib import Path
from typing import Any, TextIO
import pymysql
from pymysql.cursors import DictCursor
from tests.batch_b_harness_contract import (RunScope, HARNESS_USER, HarnessSafetyError, PRIMARY_KEYS, TABLES, DELETE_ORDER,
    owned_rows, consistent_accounting, consistent_acceptance, expected_server_uuid)
from tests.batch_b_harness_database import (check_identity, verify_harness_grants, read_inventory, schema_fingerprints, require_baseline, version_delete_order)
from tests.batch_b_mysql_cases import validate_payloads
from tests.batch_b_acceptance_setup import reviewed_foreign_keys, query, record_error, stage_call

RECOVERY_READONLY_USER = 'jukebox101_test_readonly'  # [D-6 account]: Dedicated least-privilege inspection identity; never a write-capable fallback.
READONLY_GRANT_LINE = re.compile(r"^GRANT (SELECT|USAGE) ON (\*\.\*|`jukebox101_test`\.`(\w+)`) TO `[^`]+`@`[^`]+`$")  # [grant shape]: Exact printed form only.


def read_journal(path: Path) -> tuple[dict, str, RunScope]:
    """@description: Validate existing recovery roots without changing the original evidence.
    @param path: Explicit user-selected journal, never a credential store.
    @return: Private journal, original SHA256 and reconstructed registered scope.
    """
    if path.name != 'run.json' or not path.parent.name.startswith('jukebox101-batch-b-'):
        raise HarnessSafetyError('JOURNAL_PATH_REJECTED')
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4_000_000:
        raise HarnessSafetyError('JOURNAL_FILE_REJECTED')
    content = path.read_bytes()  # [original evidence]: This code never writes or replaces the incident journal.
    journal = json.loads(content)
    if not re.fullmatch('[0-9a-f]{32}', journal['run_id']):
        raise HarnessSafetyError('JOURNAL_RUN_ID_REJECTED')
    scope = RunScope(run_id=journal['run_id'])
    if journal['subjects'] != scope.subjects or journal['owner_id'] != scope.owner_id or set(journal['decisions']) != scope.decisions:
        raise HarnessSafetyError('JOURNAL_SCOPE_MISMATCH')
    if not all(key in journal for key in ('original_control', 'migration', 'counts', 'observed_primary_keys', 'server_uuid')):
        raise HarnessSafetyError('JOURNAL_BASELINE_MISSING')
    baseline = {name: () for name in TABLES}  # [historical empty baseline]: Validated contract, not today's database state.
    baseline.update(jb2_control=(journal['original_control'],), jb2_migrations=tuple(journal['migration']))
    require_baseline(baseline)
    if set(journal['counts']) != TABLES:
        raise HarnessSafetyError('JOURNAL_TABLE_SET_MISMATCH')
    return journal, hashlib.sha256(content).hexdigest(), scope


class ReadOnlyCursor:
    """Reject every application-issued statement outside SELECT and SHOW metadata."""

    def __init__(self, cursor: Any) -> None:
        """@description: Make write-capable harness authority unavailable to this diagnostic path.
        @param self: Read-only cursor adapter.
        @param cursor: Owned driver cursor.
        @return: None.
        """
        self.cursor = cursor

    def execute(self, sql: str, parameters: tuple = ()) -> Any:
        """@description: Fail before sending any non-read statement or multi-statement batch.
        @param self: Read-only adapter.
        @param sql: Existing static SELECT or SHOW statement.
        @param parameters: Private query predicates, never reported.
        @return: Driver execution result.
        """
        if not sql.startswith(('SELECT ', 'SHOW ')) or ';' in sql or 'FOR UPDATE' in sql.upper() or 'INTO OUTFILE' in sql.upper():
            raise HarnessSafetyError('RECOVERY_NON_READ_SQL_REJECTED')
        return self.cursor.execute(sql, parameters)

    def fetchall(self) -> Any:
        """@description: Forward only already authorized read results.
        @param self: Read-only adapter.
        @return: Private rows.
        """
        return self.cursor.fetchall()

    def fetchone(self) -> Any:
        """@description: Forward a single already authorized metadata row.
        @param self: Read-only adapter.
        @return: Private row.
        """
        return self.cursor.fetchone()


def open_read_only(password: str) -> Any:
    """@description: Connect only as the fixed local harness without init-command, writes or ambient settings.
    @param password: One masked local input supplied through private stdin.
    @return: Owned diagnostic connection; normal MySQL charset negotiation remains driver-managed.
    """
    expected_server_uuid()
    return pymysql.connect(host='127.0.0.1', port=3306, user=HARNESS_USER, password=password,
        database='jukebox101_test', charset='utf8mb4', cursorclass=DictCursor, autocommit=None,
        connect_timeout=5, read_timeout=10, write_timeout=10)


def open_recovery_readonly(account: str, password: str) -> Any:
    """@description: Connect the read-only inspection only as the dedicated account, never as a harness or application fallback.
    @param account: Account name taken from the validated header; must equal the reviewed read-only name.
    @param password: One masked local input supplied through private stdin.
    @return: Owned diagnostic connection to the fixed local test database.
    """
    if account != RECOVERY_READONLY_USER or not isinstance(password, str) or not password:
        raise HarnessSafetyError('READONLY_ACCOUNT_REJECTED')  # [pre-connection refusal]: No driver call for any other identity.
    expected_server_uuid()
    return pymysql.connect(host='127.0.0.1', port=3306, user=account, password=password,
        database='jukebox101_test', charset='utf8mb4', cursorclass=DictCursor, autocommit=None,
        connect_timeout=5, read_timeout=10, write_timeout=10)


def verify_readonly_grants(cursor: Any) -> None:
    """@description: Require the effective grants to equal the reviewed SELECT set plus usage, because the account name alone proves nothing.
    @param cursor: Authenticated read-only cursor.
    @return: None; any extra, missing, schema-wide, grant-option or active-role authority raises.
    """
    roles = [next(iter(row.values())) for row in query(cursor, 'SELECT CURRENT_ROLE() AS roles')]
    lines = [next(iter(row.values())) for row in query(cursor, 'SHOW GRANTS FOR CURRENT_USER()')]
    matches = [READONLY_GRANT_LINE.match(line) for line in lines]
    if roles != ['NONE'] or not lines or any(match is None for match in matches) or any('WITH GRANT OPTION' in line for line in lines):
        raise HarnessSafetyError('READONLY_GRANTS_REJECTED')
    usage = [match for match in matches if match.group(2) == '*.*']
    selects = [match.group(3) for match in matches if match.group(2) != '*.*']
    if len(usage) != 1 or usage[0].group(1) != 'USAGE' or any(match.group(1) != 'SELECT' for match in matches if match.group(2) != '*.*'):
        raise HarnessSafetyError('READONLY_GRANTS_REJECTED')
    if len(selects) != len(set(selects)) or set(selects) != set(TABLES):
        raise HarnessSafetyError('READONLY_GRANTS_REJECTED')


def validate_rows(rows: dict, journal: dict, scope: RunScope) -> list[str]:
    """@description: Separate ownership and consistency evidence from permission to recover.
    @param rows: Full private inventory, never copied into the report.
    @param journal: Original registered scope and baseline.
    @param scope: Validated run roots.
    @return: Fixed validation failure codes; an empty list does not authorize writes.
    """
    failures = []  # [read-only findings]: Unknown or inconsistent rows never become a deletion list.
    checks = ((owned_rows, (scope, rows)), (consistent_accounting, (rows,)), (validate_payloads, (rows,)))
    for operation, arguments in checks:
        try:
            operation(*arguments)
        except (ValueError, KeyError, TypeError, RuntimeError, AssertionError):
            failures.append(operation.__name__.upper() + '_FAILED')
    if rows['jb2_migrations'] != tuple(journal['migration']):
        failures.append('MIGRATION_CHANGED')
    return failures


def primary_key_diff(rows: dict, journal: dict) -> dict:
    """@description: Distinguish registered snapshot keys still present from missing and newly observed keys.
    @param rows: Current complete inventory.
    @param journal: Historical pre-cleanup keys, not an authorized delete list.
    @return: Per-table registered keys and counts of unregistered keys without exposing unknown identities.
    """
    differences = {}  # [historical versus current]: Missing keys do not prove which transaction deleted them.
    for table, fields in PRIMARY_KEYS.items():
        recorded = {tuple(row[field] for field in fields) for row in journal['observed_primary_keys'].get(table, ())}
        current = {tuple(row[field] for field in fields) for row in rows[table]}
        differences[table] = dict(recorded_present=[dict(zip(fields, key)) for key in sorted(recorded & current)],
            recorded_missing=[dict(zip(fields, key)) for key in sorted(recorded - current)], unregistered_count=len(current - recorded))
    return differences


def control_check(rows: dict, journal: dict) -> list[str]:
    """@description: Check original counters while allowing only the documented all-OFF emergency freeze.
    @param rows: Current private inventory.
    @param journal: Original baseline control.
    @return: Fixed inconsistency codes without changing actual control values.
    """
    from tests.batch_b_harness_contract import CONTROL_COLUMNS
    if len(rows['jb2_control']) != 1:
        return ['CONTROL_ROW_COUNT_MISMATCH']
    control = dict(rows['jb2_control'][0])  # [simulation only]: Never sent back to the database.
    if any(control[name] != journal['original_control'][name] for name in ('singleton_id', 'schema_version')):
        return ['CONTROL_IDENTITY_MISMATCH']
    if tuple(control[name] for name in CONTROL_COLUMNS[2:]) not in {(0, 0, 0), (1, 1, 1)}:
        return ['MIXED_CONTROL_FLAGS']
    for name in CONTROL_COLUMNS[2:]:
        control[name] = 1
    try:
        consistent_acceptance(dict(rows, jb2_control=(control,)), journal['original_control'])
    except (ValueError, KeyError, TypeError, RuntimeError):
        return ['CONTROL_COUNTERS_UNEXPLAINED']
    return []


def require_exact_recovery(rows: dict, journal: dict, scope: RunScope) -> dict:
    """@description: Prepare a PK-specific proposal only when no historical key or ownership evidence is missing.
    @param rows: Future fresh full inventory under the authorized recovery gate.
    @param journal: Immutable original incident evidence.
    @param scope: Validated registered run roots.
    @return: Non-authorizing manifest proposal; never a write command or unique reward claim.
    """
    if set(rows) != TABLES:
        raise HarnessSafetyError('RECOVERY_INCOMPLETE_INVENTORY')
    if validate_rows(rows, journal, scope) or control_check(rows, journal):
        raise HarnessSafetyError('RECOVERY_VALIDATION_FAILED')
    differences = primary_key_diff(rows, journal)  # [strict historical scope]: No missing or additional key is silently tolerated.
    if any(item['recorded_missing'] or item['unregistered_count'] for item in differences.values()):
        raise HarnessSafetyError('RECOVERY_PK_SET_CHANGED')
    require_frozen_control(rows['jb2_control'][0], journal)
    versions = version_delete_order(rows['jb2_versions'])
    order = (*DELETE_ORDER, 'jb2_versions')
    manifest = {table: differences[table]['recorded_present'] for table in order if rows[table]}
    return dict(run_id=scope.run_id, delete_manifest=manifest, version_order=[row['version_id'] for row in versions],
        self_roots=[row['version_id'] for row in versions if row['canonical_id'] == row['version_id']],
        control_restore=dict(journal['original_control']), recovery_writes_authorized=False,
        remaining_gates=['FRESH_UUID_SCHEMA_FK_RULES_AND_EXCLUSIVITY', 'EXPLICIT_WRITE_AUTHORIZATION', 'SELF_FK_SQL_BEHAVIOR_REVIEW'])


def require_frozen_control(control: dict, journal: dict) -> None:
    """@description: Refuse recovery when counters drift or business switches have reopened since the failed run.
    @param control: Current singleton after arithmetic consistency verification.
    @param journal: Original failed-run control snapshot.
    @return: None; no counter is written or priority changed.
    """
    if any(control[name] != 0 for name in ('snapshot_ready', 'legacy_writers_quiesced', 'settlement_enabled')):
        raise HarnessSafetyError('RECOVERY_CONTROL_NOT_FROZEN')
    observed = journal.get('observed_control', ())  # [historical watermark]: A formula alone cannot bless intervening changes.
    if len(observed) != 1 or any(control[name] != observed[0][name] for name in ('accepted_sequence', 'revision')):
        raise HarnessSafetyError('RECOVERY_CONTROL_WATERMARK_CHANGED')


def version_fk_diagnosis(rows: dict, journal: dict, definitions: dict, edges: tuple, rules: tuple) -> dict:
    """@description: Expose the missing root, alias and incoming-FK evidence using already read inventory only.
    @param rows: Full private SELECT inventory.
    @param journal: Original registered version PKs.
    @param definitions: Actual normalized SHOW CREATE mapping.
    @param edges: Actual KCU rows including constraint names.
    @param rules: Actual FK actions, never inferred from default SQL syntax.
    @return: Minimal safe evidence; it does not claim the historical blocking constraint is known.
    """
    registered = {subject['version'] for subject in journal['subjects'].values()}  # [safe identities]: Unknown row IDs are not published.
    targets = [record['version_id'] for record in journal['observed_primary_keys']['jb2_versions']]
    incoming = [edge for edge in edges if edge['REFERENCED_TABLE_SCHEMA'] == 'jukebox101_test'
                and edge['REFERENCED_TABLE_NAME'] == 'jb2_versions']
    return dict(actual_create_table=definitions['jb2_versions'], target_details=[version_target(rows, target, registered, incoming) for target in targets],
        incoming_constraints=incoming, actual_rules=[rule for rule in rules if any(
            rule['TABLE_NAME'] == edge['TABLE_NAME'] and rule['CONSTRAINT_NAME'] == edge['CONSTRAINT_NAME'] for edge in incoming)],
        historical_blocking_constraint='NOT_RECORDED', visibility='HARNESS_VISIBLE_METADATA_ONLY',
        remaining_unknowns=['HIDDEN_INCOMING_REFERENCES_NOT_EXCLUDED', 'HISTORICAL_CONSTRAINT_NAME_NOT_AVAILABLE'])


def version_target(rows: dict, target: str, registered: set, incoming: list) -> dict:
    """@description: Separate self-root, aliases and non-self incoming rows without exposing unknown identities.
    @param rows: Full private inventory.
    @param target: Original manifest version PK.
    @param registered: Registered version identifiers safe for this report.
    @param incoming: Actual version-targeting FK metadata.
    @return: Current target fields plus counts and registered aliases.
    """
    matched = [row for row in rows['jb2_versions'] if row['version_id'] == target]
    aliases = [row for row in rows['jb2_versions'] if row['canonical_id'] == target and row['version_id'] != target]
    canonical = matched[0]['canonical_id'] if len(matched) == 1 else None
    return dict(version_id=target, row_count=len(matched), canonical_id=canonical if canonical in registered or canonical is None else 'UNREGISTERED',
        is_self_root=len(matched) == 1 and canonical == target, alias_count=len(aliases),
        registered_aliases=[dict(version_id=row['version_id'], canonical_id=target) for row in aliases if row['version_id'] in registered],
        unregistered_alias_count=sum(row['version_id'] not in registered for row in aliases),
        referencing_rows=[reference_count(rows, target, edge) for edge in incoming])


def reference_count(rows: dict, target: str, edge: dict) -> dict:
    """@description: Identify non-self blockers by constraint without turning row counts into delete authority.
    @param rows: Complete visible inventory, never queried outside the allowlist.
    @param target: Original version PK.
    @param edge: Actual FK column referencing the version table.
    @return: Exact constraint/column and matching-row counts; no unknown PKs or payloads.
    """
    table, column = edge['TABLE_NAME'], edge['COLUMN_NAME']
    supported = table in rows and edge['REFERENCED_COLUMN_NAME'] == 'version_id'
    matches = [row for row in rows[table] if row[column] == target] if supported else []
    return dict(table=table, constraint=edge['CONSTRAINT_NAME'], column=column,
        matching_rows=len(matches) if supported else None,
        non_self_rows=sum(not (table == 'jb2_versions' and row['version_id'] == target) for row in matches) if supported else None,
        status='COUNTED_VISIBLE_INVENTORY' if supported else 'UNRESOLVED_REFERENCE')


def inspect_connection(connection: Any, journal: dict, scope: RunScope) -> dict:
    """@description: Compare two complete SELECT inventories and verify metadata without acquiring write locks.
    @param connection: Owned session opened as the dedicated read-only account.
    @param journal: Original private incident record.
    @param scope: Validated synthetic run scope.
    @return: Nonsecret current state and explicit limits on restoration readiness.
    """
    with connection.cursor() as raw:
        cursor = ReadOnlyCursor(raw)
        check_identity(cursor, RECOVERY_READONLY_USER)
        verify_readonly_grants(cursor)
        if query(cursor, 'SELECT @@autocommit AS enabled') != ({'enabled': 1},):
            raise HarnessSafetyError('RECOVERY_AUTOCOMMIT_REQUIRED')
        definitions = schema_fingerprints(cursor)  # [structure now]: Original journal did not preserve SHOW CREATE fingerprints.
        rows = read_inventory(cursor)
        edges = query(cursor, "SELECT CONSTRAINT_NAME,TABLE_NAME,COLUMN_NAME,ORDINAL_POSITION,REFERENCED_TABLE_SCHEMA,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME "
                      "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA='jukebox101_test' AND REFERENCED_TABLE_SCHEMA IS NOT NULL")
        rules = query(cursor, "SELECT TABLE_NAME,CONSTRAINT_NAME,UPDATE_RULE,DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA='jukebox101_test'")
        if rows != read_inventory(cursor) or definitions != schema_fingerprints(cursor):
            raise HarnessSafetyError('RECOVERY_CONCURRENT_CHANGE')
    failures = validate_rows(rows, journal, scope) + control_check(rows, journal)
    names = ('TABLE_NAME', 'COLUMN_NAME', 'ORDINAL_POSITION', 'REFERENCED_TABLE_SCHEMA', 'REFERENCED_TABLE_NAME', 'REFERENCED_COLUMN_NAME')
    if {tuple(row[name] for name in names) for row in edges} != reviewed_foreign_keys():
        failures.append('FOREIGN_KEY_DRIFT')
    return dict(counts={table: len(records) for table, records in rows.items()}, key_diff=primary_key_diff(rows, journal),
        control=rows['jb2_control'], original_control=journal['original_control'], validation_failures=failures,
        schema_fingerprint=hashlib.sha256(json.dumps(definitions, sort_keys=True).encode()).hexdigest(),
        baseline_business_nonempty=[name for name, records in rows.items() if records and name not in {'jb2_control', 'jb2_migrations'}],
        foreign_key_rules=rules, version_fk_diagnosis=version_fk_diagnosis(rows, journal, definitions, edges, rules),
        original_schema_fingerprint_available=False, recovery_writes_authorized=False)


def require_readonly_intent(header: dict) -> None:
    """@description: Accept the inspection intent only with its fixed keys and the dedicated account, before any journal or credential is read.
    @param header: Parsed first stdin line from the launcher.
    @return: None; any other shape, intent or identity raises before I/O.
    """
    if set(header) != {'authorization', 'journal', 'account'} or header['authorization'] != 'BATCH_B_RECOVERY_READONLY':
        raise HarnessSafetyError('RECOVERY_INTENT_REQUIRED')
    if header['account'] != RECOVERY_READONLY_USER:  # [no fallback]: The harness or application name is never a substitute.
        raise HarnessSafetyError('READONLY_ACCOUNT_REJECTED')


@guarded_command
def main(stream: TextIO) -> int:
    """@description: Expose only a separate read-only mode of the existing credential entry.
    @param stream: Anonymous stdin with explicit intent, the dedicated read-only account name and one private credential.
    @return: Diagnostic exit code, never a recovery or test result.
    """
    state = dict(stage='R_READ_ONLY_FAILED', a_completed=False, b_started=False, read_only=True)
    connection = None  # [owned read session]: No DBA, application test or cleanup method is invoked.
    try:
        header = json.loads(stream.readline(8192))
        require_readonly_intent(header)
        path = Path(header['journal'])
        journal, digest, scope = read_journal(path)
        os.environ['TEST_DB_SERVER_UUID'] = journal['server_uuid']
        password = json.loads(stream.readline(16384))
        if not isinstance(password, str) or not password or len(password) > 4096 or '\x00' in password:
            raise HarnessSafetyError('RECOVERY_CREDENTIAL_REJECTED')
        connection = stage_call(state, 'R_HARNESS_READ_CONNECT', open_recovery_readonly, RECOVERY_READONLY_USER, password)
        password = None
        state['recovery_report'] = stage_call(state, 'R_READ_INVENTORY', inspect_connection, connection, journal, scope)
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise HarnessSafetyError('JOURNAL_CHANGED_DURING_INSPECTION')
        state.update(stage='R_READ_ONLY_COMPLETE', run_id=scope.run_id, journal_sha256=digest)
        return 0
    except BaseException as error:
        record_error(state, error)
        return 1
    finally:
        if finish_read(connection, state):
            return 1


def finish_read(connection: Any, state: dict) -> bool:
    """@description: Close only the owned diagnostic connection and emit no private payloads.
    @param connection: Read session or None when authentication never succeeded.
    @param state: Sanitized report.
    @return: Whether closing the read session failed and must force a failing exit code.
    """
    close_failed = False  # [exit integrity]: A failed close never returns a successful diagnostic exit code.
    if connection is not None:
        try:
            connection.close()
        except Exception as error:
            state['stage'] = 'R_READ_ONLY_FAILED'
            record_error(state, error)
            close_failed = True
    os.environ.pop('TEST_DB_SERVER_UUID', None)
    print(json.dumps(state, default=str, ensure_ascii=True))
    return close_failed
