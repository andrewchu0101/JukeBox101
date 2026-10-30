"""Restore only the reviewed incident through an explicitly selected recovery transaction.

This module reuses existing ownership, FK ordering and baseline checks; it never provisions accounts or clears the original session block.
"""

import hashlib
import json
import os
import re
import tempfile
from tests.batch_b_assertion_guard import guarded_command
from pathlib import Path
from typing import Any, TextIO
from bot.config import DatabaseSettings
from tests.batch_b_acceptance_setup import ROOT, query, record_error, reviewed_foreign_keys
from tests.batch_b_harness import persist_manifest
from tests.batch_b_harness_contract import HARNESS_USER, HarnessSafetyError, TABLES, CONTROL_COLUMNS
from tests.batch_b_harness_database import (verified_connection, verify_harness_grants, schema_fingerprints,
    read_inventory, lock_inventory, delete_owned, set_control)
from tests.batch_b_recovery import read_journal, require_exact_recovery, open_read_only, ReadOnlyCursor
from tests.batch_b_harness_database import check_identity
from tests.batch_b_fk_window import FkWindow, require_schema, digest, TEMP_ROOT_NAME, constraint_names

RUN_ID = '14d8aee30b034a9883e9f71cdec8146e'
JOURNAL_HASH = '6a75a0bd379a08fa1f57992187b039f75f19bde1c51f7e457bc61dd0fcbfd71d'
MANIFEST_HASH = '10cf5cac90d8fa47ccfd6a15ae9ed82e2bf365c6bbb2855cbbe8ae968b52a3a2'
MANIFEST_FILE = ROOT / 'docs/batch-b-recovery-manifest.json'
JOURNAL_FILE = Path(r'C:\Users\Yume\AppData\Local\Temp\jukebox101-batch-b-hst16g0d\run.json')


def fingerprint(definitions: dict) -> str:
    """@description: Bind fresh metadata to the operator-reviewed read-only schema fingerprint.
    @param definitions: Existing normalized SHOW CREATE mapping.
    @return: Stable SHA256 without SQL or row values.
    """
    return digest(definitions)


def load_contract(header: dict) -> tuple:
    """@description: Refuse any different incident, modified evidence or missing execution confirmation before connecting.
    @param header: Nonsecret private input protocol with explicit operator authorization.
    @return: Validated journal, scope and pinned manifest.
    """
    if set(header) != {'authorization', 'journal', 'confirmation', 'schema_fingerprint', 'exclusive_no_ddl'}:
        raise HarnessSafetyError('RECOVERY_WRITE_PROTOCOL_REJECTED')
    if header['authorization'] != 'BATCH_B_RECOVERY_WRITE' or header['confirmation'] != 'RESTORE ' + RUN_ID or header['exclusive_no_ddl'] is not True:
        raise HarnessSafetyError('RECOVERY_WRITE_CONFIRMATION_REQUIRED')
    if not isinstance(header['schema_fingerprint'], str) or not re.fullmatch('[0-9a-f]{64}', header['schema_fingerprint']):
        raise HarnessSafetyError('APPROVED_SCHEMA_FINGERPRINT_REQUIRED')
    if Path(header['journal']).resolve() != JOURNAL_FILE.resolve():
        raise HarnessSafetyError('RECOVERY_INCIDENT_PATH_REJECTED')
    journal, digest, scope = read_journal(JOURNAL_FILE)
    content = MANIFEST_FILE.read_bytes()  # [reviewed authorization boundary]: No caller-selected cleanup manifest.
    if digest != JOURNAL_HASH or scope.run_id != RUN_ID or hashlib.sha256(content).hexdigest() != MANIFEST_HASH:
        raise HarnessSafetyError('RECOVERY_EVIDENCE_HASH_MISMATCH')
    manifest = json.loads(content)
    if manifest['run_id'] != RUN_ID or manifest['journal_sha256'] != digest:
        raise HarnessSafetyError('RECOVERY_MANIFEST_INCIDENT_MISMATCH')
    return journal, scope, manifest


def expected_baseline(journal: dict) -> dict:
    """@description: Reconstruct only the original accepted empty business baseline.
    @param journal: Validated immutable original incident journal.
    @return: All 23 expected tables, original Control and untouched Registry.
    """
    rows = {table: () for table in TABLES}
    rows.update(jb2_control=(journal['original_control'],), jb2_migrations=tuple(journal['migration']))
    return rows


def verify_fk_metadata(cursor: Any, cascade_window: bool | str = False) -> None:
    """@description: Reject changed, cascading or cross-schema references before precise deletion.
    @param cursor: Verified harness metadata cursor inside the recovery transaction.
    @param cascade_window: Internal original, temporary-name CASCADE or dual-FK bridge scope.
    @return: None; no FK behavior is weakened or dynamically repaired.
    """
    names = ('TABLE_NAME', 'COLUMN_NAME', 'ORDINAL_POSITION', 'REFERENCED_TABLE_SCHEMA', 'REFERENCED_TABLE_NAME', 'REFERENCED_COLUMN_NAME')
    rows = query(cursor, 'SELECT ' + ','.join(names) + " FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA='jukebox101_test' AND REFERENCED_TABLE_SCHEMA IS NOT NULL")
    if {tuple(row[name] for name in names) for row in rows} != reviewed_foreign_keys():
        raise HarnessSafetyError('RECOVERY_FOREIGN_KEY_DRIFT')
    incoming = query(cursor, "SELECT TABLE_SCHEMA FROM information_schema.KEY_COLUMN_USAGE WHERE REFERENCED_TABLE_SCHEMA='jukebox101_test' AND TABLE_SCHEMA<>'jukebox101_test'")
    if incoming:
        raise HarnessSafetyError('RECOVERY_EXTERNAL_REFERENCE')
    rules = query(cursor, "SELECT TABLE_NAME,CONSTRAINT_NAME,UPDATE_RULE,DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA='jukebox101_test'")
    expected = constraint_names(reviewed_constraint_names(), cascade_window)
    if {(row['TABLE_NAME'], row['CONSTRAINT_NAME']) for row in rules} != expected:
        raise HarnessSafetyError('RECOVERY_FK_RULE_SET_CHANGED')
    for row in rules:
        allowed = {'CASCADE'} if cascade_window and (row['TABLE_NAME'], row['CONSTRAINT_NAME']) == ('jb2_versions', TEMP_ROOT_NAME) else {'RESTRICT', 'NO ACTION'}
        if row['UPDATE_RULE'] not in {'RESTRICT', 'NO ACTION'} or row['DELETE_RULE'] not in allowed:
            raise HarnessSafetyError('RECOVERY_CASCADING_ACTION_REFUSED')


def reviewed_constraint_names() -> set:
    """@description: Require every reviewed named FK, including the root self-reference, to be visible.
    @param: none
    @return: Accepted local table and FK names.
    """
    constraints = set()  # [reviewed FK inventory]: Explicit actions still come from live metadata.
    for relative in ('bot/database/schema.sql', 'bot/database/migrations/002_submission_economy.sql'):
        source = re.sub(r'--[^\n]*', '', (ROOT / relative).read_text(encoding='utf-8'))
        for statement in source.split(';'):
            table = re.search(r'CREATE TABLE IF NOT EXISTS (\w+)', statement)
            if table:
                constraints.update((table[1], name) for name in re.findall(r'CONSTRAINT (\w+) FOREIGN KEY', statement))
    return constraints


def guard_metadata(cursor: Any, approved_schema: str) -> dict:
    """@description: Repeat identity, minimum grants and approved structure checks at transaction boundaries.
    @param cursor: Recovery-owned cursor.
    @param approved_schema: Previously reviewed nonsecret schema fingerprint.
    @return: Normalized fresh definitions.
    """
    check_identity(cursor, HARNESS_USER)
    verify_harness_grants(cursor)
    definitions = schema_fingerprints(cursor)
    if fingerprint(definitions) != approved_schema:
        raise HarnessSafetyError('RECOVERY_SCHEMA_FINGERPRINT_CHANGED')
    verify_fk_metadata(cursor)
    require_journal_hash()
    return definitions


def require_journal_hash() -> None:
    """@description: Preserve the original incident evidence before and after every recovery attempt.
    @param: none
    @return: None.
    """
    if hashlib.sha256(JOURNAL_FILE.read_bytes()).hexdigest() != JOURNAL_HASH:
        raise HarnessSafetyError('RECOVERY_JOURNAL_CHANGED')
    if hashlib.sha256(MANIFEST_FILE.read_bytes()).hexdigest() != MANIFEST_HASH:
        raise HarnessSafetyError('RECOVERY_MANIFEST_CHANGED')


def guard_cleanup_metadata(cursor: Any, approved: str, window: FkWindow | None) -> None:
    """@description: Preserve default restrictive recovery while permitting one fully pinned coordinator scope.
    @param cursor: Existing verified harness cursor.
    @param approved: Original approved schema fingerprint.
    @param window: Immutable exact variant, never an ambient environment switch.
    @return: None; only the precomputed root action may differ.
    """
    if window is None:
        guard_metadata(cursor, approved)
        return
    if digest(dict(window.original)) != approved:
        raise HarnessSafetyError('RECOVERY_WINDOW_APPROVAL_MISMATCH')
    check_identity(cursor, HARNESS_USER)
    verify_harness_grants(cursor)
    require_schema(schema_fingerprints(cursor), window, True)
    verify_fk_metadata(cursor, True)
    require_journal_hash()


def check_manifest(rows: dict, journal: dict, scope: Any, manifest: dict) -> dict:
    """@description: Prove full ownership and exact approved keys, not merely table counts or prefixes.
    @param rows: Complete locked current inventory.
    @param journal: Original validated registration.
    @param scope: Original synthetic roots.
    @param manifest: Pinned reviewed 15-PK artifact.
    @return: Verified dependency plan; self-root DELETE may still be refused by MySQL and must roll back.
    """
    proposal = require_exact_recovery(rows, journal, scope)
    if proposal['delete_manifest'] != manifest['delete_manifest'] or rows['jb2_control'] != (manifest['control_precondition'],):
        raise HarnessSafetyError('RECOVERY_APPROVED_MANIFEST_CHANGED')
    if proposal['control_restore'] != manifest['control_restore']:
        raise HarnessSafetyError('RECOVERY_RESTORE_TARGET_CHANGED')
    # [self-FK safety]: Complete ownership plus explicit alias-before-root guards prevent hidden cascade deletion.
    # Legacy NO ACTION remains default; only the separate coordinator may supply an exact temporary schema.
    if len(proposal['version_order']) != len(rows['jb2_versions']):
        raise HarnessSafetyError('RECOVERY_SELF_FK_UNPROVEN')
    return proposal


def save(state: dict, phase: str) -> None:
    """@description: Persist only this new recovery attempt, never overwrite the original journal.
    @param state: Nonsecret recovery progress with a unique owned report path.
    @param phase: Fixed stage name.
    @return: None.
    """
    state['stage'] = phase
    destination = Path(state['recovery_journal'])
    if destination.resolve() == JOURNAL_FILE.resolve():
        raise HarnessSafetyError('ORIGINAL_JOURNAL_WRITE_REFUSED')
    persist_manifest(destination, state)


def observe(state: dict, phase: str, table: str, row: dict) -> None:
    """@description: Keep precise delete attempts distinct from committed cleanup.
    @param state: This attempt's nonsecret progress.
    @param phase: Existing delete observer phase.
    @param table: Reviewed table.
    @param row: Verified row; only its complete PK is journaled.
    @return: None.
    """
    from tests.batch_b_harness_contract import PRIMARY_KEYS
    state['cleanup_progress'].append(dict(phase=phase, table=table, primary_key={key: row[key] for key in PRIMARY_KEYS[table]}))
    save(state, 'R_RECOVERY_TRANSACTION')


def restore_transaction(connection: Any, journal: dict, scope: Any, manifest: dict, approved: str, state: dict, window: FkWindow | None = None) -> None:
    """@description: Delete and restore counters atomically, after every gate is rechecked under the control lock.
    @param connection: Verified harness connection.
    @param journal: Original incident evidence.
    @param scope: Registered roots.
    @param manifest: Exact pinned cleanup artifact.
    @param approved: Operator-reviewed schema hash.
    @param state: Private attempt progress, without credentials.
    @param window: Explicit coordinator-only exact temporary schema, absent for legacy recovery.
    @return: None; commit is deliberately outside this function.
    """
    rows = lock_inventory(connection)  # [one serializable transaction]: Locks rows/gaps before fresh ownership checks.
    with connection.cursor() as cursor:
        guard_cleanup_metadata(cursor, approved, window)
        proof = check_manifest(rows, journal, scope, manifest)  # [isolation proof]: No unknown target or cascading FK is accepted.
        state['self_fk_plan'] = dict(version_order=proof['version_order'], self_roots=proof['self_roots'], restrictive_rules_verified=window is None, cascade_window=window is not None)
        save(state, 'R_RECOVERY_VALIDATED')
        delete_owned(cursor, rows, lambda *event: observe(state, *event))
        cursor.execute('SELECT * FROM jb2_control WHERE singleton_id=1 FOR UPDATE')
        if cursor.fetchone() != manifest['control_precondition']:
            raise HarnessSafetyError('RECOVERY_CONTROL_CHANGED_BEFORE_RESTORE')
        set_control(cursor, {name: journal['original_control'][name] for name in CONTROL_COLUMNS})
        if cursor.rowcount != 1:
            raise HarnessSafetyError('RECOVERY_CONTROL_ROWCOUNT_MISMATCH')
        if read_inventory(cursor) != expected_baseline(journal):
            raise HarnessSafetyError('RECOVERY_PRECOMMIT_BASELINE_MISMATCH')
        guard_cleanup_metadata(cursor, approved, window)
        save(state, 'R_RECOVERY_PRECOMMIT_VERIFIED')


def commit_once(connection: Any, state: dict) -> None:
    """@description: Never retry a commit whose acknowledgement may have been lost.
    @param connection: Owned transaction that passed all precommit checks.
    @param state: Recovery progress; UNKNOWN always forces read-only investigation.
    @return: None.
    """
    state['commit_outcome'] = 'PENDING'
    save(state, 'R_RECOVERY_COMMITTING')
    try:
        connection.commit()
    except BaseException:
        state['commit_outcome'] = 'UNKNOWN'
        raise
    state['commit_outcome'] = 'ACKNOWLEDGED'


def rollback_failure(connection: Any, state: dict) -> None:
    """@description: Roll back only precommit failures without pretending a committed transaction can be undone.
    @param connection: Failed owned session.
    @param state: Original safe diagnostics and commit status.
    @return: None.
    """
    if state.get('commit_outcome') in {'UNKNOWN', 'ACKNOWLEDGED'}:
        state['rollback_outcome'] = 'NOT_ATTEMPTED_AFTER_COMMIT_BOUNDARY'
        return
    try:
        connection.rollback()
        state['rollback_outcome'] = 'ACKNOWLEDGED'
    except Exception as error:
        state['rollback_outcome'] = 'UNKNOWN'
        state['rollback_error_type'] = type(error).__name__


def verify_after(password: str, journal: dict, approved: str, state: dict, window: FkWindow | None = None) -> None:
    """@description: Use only SELECT/SHOW on a fresh connection after commit or unknown acknowledgement.
    @param password: Private existing harness credential, never journaled.
    @param journal: Original accepted baseline.
    @param approved: Reviewed schema hash.
    @param state: Sanitized result with commit status preserved.
    @param window: Explicit temporary variant during the coordinator window, otherwise original schema.
    @return: None.
    """
    connection = open_read_only(password)
    try:
        with connection.cursor() as raw:
            cursor = ReadOnlyCursor(raw)
            guard_cleanup_metadata(cursor, approved, window)
            rows = read_inventory(cursor)
            state['final_counts'] = {table: len(records) for table, records in rows.items()}
            if rows != read_inventory(cursor):
                raise HarnessSafetyError('RECOVERY_POSTVERIFY_CHANGED')
            guard_cleanup_metadata(cursor, approved, window)
            matches = rows == expected_baseline(journal)
            state['read_only_baseline_matches'] = matches
            if state.get('commit_outcome') == 'UNKNOWN':
                return
            if not matches:
                raise HarnessSafetyError('RECOVERY_POSTCOMMIT_BASELINE_MISMATCH')
            state['baseline_restored'] = True
    finally:
        connection.close()


def execute(password: str, journal: dict, scope: Any, manifest: dict, approved: str, state: dict) -> None:
    """@description: Hold one attempt lock, make at most one commit and leave every failure blocked.
    @param password: Existing harness credential from anonymous stdin.
    @param journal: Validated original journal.
    @param scope: Original run roots.
    @param manifest: Pinned manifest.
    @param approved: Reviewed schema hash.
    @param state: Nonsecret attempt report.
    @return: None.
    """
    settings = DatabaseSettings('127.0.0.1', 3306, HARNESS_USER, password, 'jukebox101_test')
    connection = verified_connection(settings, HARNESS_USER)
    try:
        with connection.cursor() as cursor:
            if query(cursor, 'SELECT GET_LOCK(%s,0) AS owned', ('jukebox101:batch-b-test-harness',)) != ({'owned': 1},):
                raise HarnessSafetyError('RECOVERY_ALREADY_RUNNING')
            guard_metadata(cursor, approved)
            if read_inventory(cursor) == expected_baseline(journal):
                raise HarnessSafetyError('RECOVERY_ALREADY_BASELINE_NO_REPLAY')
        restore_transaction(connection, journal, scope, manifest, approved, state)
        commit_once(connection, state)
    except BaseException as error:
        record_error(state, error)
        rollback_failure(connection, state)
        raise
    finally:
        connection.close()
    verify_after(password, journal, approved, state)


def receive(stream: TextIO) -> tuple:
    """@description: Require explicit restore intent and checked artifacts before consuming one private credential.
    @param stream: Owned anonymous input pipe.
    @return: Validated header, evidence and private credential.
    """
    header = json.loads(stream.readline(8192))
    journal, scope, manifest = load_contract(header)
    password = json.loads(stream.readline(16384))  # [private process memory]: Never enters environment, argv or report.
    if not isinstance(password, str) or not password or len(password) > 4096 or '\x00' in password:
        raise HarnessSafetyError('RECOVERY_CREDENTIAL_REJECTED')
    return header, journal, scope, manifest, password


@guarded_command
def main(stream: TextIO) -> int:
    """@description: Provide a separate opt-in write mode while preserving the original incident and block.
    @param stream: Private operator protocol, never ambient environment authorization.
    @return: Zero only for independently verified restoration, with manual acceptance still pending.
    """
    state = dict(stage='R_RECOVERY_FAILED_BLOCKED', session_blocked=True, baseline_restored=False,
        a_completed=False, b_started=False, cleanup_progress=[], run_id=RUN_ID, journal_sha256=JOURNAL_HASH)
    password = None
    try:
        header, journal, scope, manifest, password = receive(stream)
        state.update(manifest_sha256=MANIFEST_HASH, approved_schema_fingerprint=header['schema_fingerprint'])
        os.environ['TEST_DB_SERVER_UUID'] = journal['server_uuid']
        state['recovery_journal'] = str(Path(tempfile.mkdtemp(prefix='jukebox101-recovery-')) / 'recovery.json')
        save(state, 'R_RECOVERY_PRECHECK')
        execute(password, journal, scope, manifest, header['schema_fingerprint'], state)
        if state['baseline_restored'] is not True:
            raise HarnessSafetyError('RECOVERY_VERIFICATION_NOT_CONFIRMED')
        save(state, 'R_RECOVERY_RESTORED_VERIFIED_BLOCKED')
        return 0
    except BaseException as error:
        return finish_failure(error, password, locals().get('journal'), locals().get('header'), state)
    finally:
        password = None
        os.environ.pop('TEST_DB_SERVER_UUID', None)
        print(json.dumps(state, ensure_ascii=True))


def finish_failure(error: BaseException, password: str | None, journal: dict | None, header: dict | None, state: dict) -> int:
    """@description: Keep every failure blocked even if final close or diagnostic persistence also fails.
    @param error: Original exception, never serialized as message text.
    @param password: Private credential or None before input completed.
    @param journal: Validated original evidence or None.
    @param header: Validated operator intent or None.
    @param state: Safe progress evidence retaining the original failure.
    @return: Nonzero; no failure is promoted to successful restoration.
    """
    record_error(state, error)
    state['baseline_restored'] = False
    investigate_unknown(password, journal, header, state)
    state['stage'] = 'R_RECOVERY_COMMIT_UNKNOWN_BLOCKED' if state.get('commit_outcome') == 'UNKNOWN' else 'R_RECOVERY_FAILED_BLOCKED'
    try:
        if 'recovery_journal' in state:
            save(state, state['stage'])
    except Exception as diagnostic_error:
        state['recovery_journal_error_type'] = type(diagnostic_error).__name__
    return 1


def investigate_unknown(password: str | None, journal: dict | None, header: dict | None, state: dict) -> None:
    """@description: Investigate an ambiguous commit using reads only, without promoting it to success.
    @param password: Existing private credential or None before input completed.
    @param journal: Validated original baseline or None.
    @param header: Validated execution header or None.
    @param state: Original error and UNKNOWN commit evidence.
    @return: None.
    """
    if state.get('commit_outcome') != 'UNKNOWN':
        return
    try:
        verify_after(password, journal, header['schema_fingerprint'], state)
    except Exception as error:
        state['read_only_investigation_error_type'] = type(error).__name__
