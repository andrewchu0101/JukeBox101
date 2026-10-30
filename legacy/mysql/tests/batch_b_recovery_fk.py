"""Coordinate one separately authorized incident recovery across two explicit FK DDL boundaries.

DBA authority stays outside the harness transaction; unknown outcomes remain blocked and never trigger SQL replay.
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
from bot.database.connection import open_connection
from tests import batch_b_recovery_write as recovery
from tests.batch_b_acceptance_setup import check_dba, query, record_error, mysql_error_number, stage_call
from tests.batch_b_fk_lab import existing_dba_authority
from tests.batch_b_fk_window import build_window, require_schema, digest, apply_switch
from tests.batch_b_harness_contract import HARNESS_USER, HarnessSafetyError
from tests.batch_b_harness_database import verified_connection, schema_fingerprints, read_inventory, lock_inventory

SERVER_UUID = 'ab73bd54-c3c3-11f1-bc47-005056c00001'
ORIGINAL_SCHEMA = '5f201eabfb5828f80029497e3eca81432ed99a81aebbd15f2a23515cc93c62a8'
FAILED_JOURNAL = Path(r'C:\Users\Yume\AppData\Local\Temp\jukebox101-recovery-8odr8mus\recovery.json')
FAILED_HASH = 'b4b728e4de2e136dcb83b0222cd3a997c0f94e7066fbc83dbdbf92e586d669ee'
DDL_FAILURE_JOURNAL = Path(r'C:\Users\Yume\AppData\Local\Temp\jukebox101-fk-recovery-kx8ef4k_\recovery.json')
DDL_FAILURE_HASH = 'c91b55f7fd929968d8bb44daf8bcf9efa8865176e5e1bcf9de9c59f7361fdeb4'


def receive(stream: TextIO) -> tuple:
    """@description: Pin the only permitted incident and explicit DDL authorization before credentials or connections.
    @param stream: Anonymous stdin from the existing launcher.
    @return: Validated header, immutable evidence and two private credentials.
    """
    header = json.loads(stream.readline(8192))  # [single-operation authority]: No general FK modification interface.
    expected = {'authorization', 'journal', 'schema_fingerprint', 'exclusive_no_ddl', 'dba_user', 'server_uuid'}
    if set(header) != expected or header['authorization'] != 'BATCH_B_RECOVERY_FK_REPAIR' or header['exclusive_no_ddl'] is not True:
        raise HarnessSafetyError('FK_REPAIR_AUTHORIZATION_REQUIRED')
    if header['schema_fingerprint'] != ORIGINAL_SCHEMA or header['server_uuid'] != SERVER_UUID:
        raise HarnessSafetyError('FK_REPAIR_PIN_REJECTED')
    if not isinstance(header['dba_user'], str) or not re.fullmatch('[A-Za-z0-9_]{1,32}', header['dba_user']) or header['dba_user'].startswith('jukebox101_'):
        raise HarnessSafetyError('FK_REPAIR_DBA_REJECTED')
    legacy = dict(authorization='BATCH_B_RECOVERY_WRITE', journal=header['journal'], confirmation='RESTORE ' + recovery.RUN_ID,
                  schema_fingerprint=ORIGINAL_SCHEMA, exclusive_no_ddl=True)
    journal, scope, manifest = recovery.load_contract(legacy)
    if journal['server_uuid'] != SERVER_UUID or hashlib.sha256(FAILED_JOURNAL.read_bytes()).hexdigest() != FAILED_HASH:
        raise HarnessSafetyError('FK_REPAIR_EVIDENCE_CHANGED')
    if hashlib.sha256(DDL_FAILURE_JOURNAL.read_bytes()).hexdigest() != DDL_FAILURE_HASH:
        raise HarnessSafetyError('FK_REPAIR_DDL_EVIDENCE_CHANGED')
    credentials = [json.loads(stream.readline(16384)) for _ in range(2)]  # [private memory]: DBA then existing harness; never environment or files.
    if any(not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value for value in credentials):
        raise HarnessSafetyError('FK_REPAIR_CREDENTIAL_REJECTED')
    return header, journal, scope, manifest, credentials


def acquire_connections(context: dict, credentials: list[str], state: dict) -> None:
    """@description: Keep existing DBA and least-privileged harness connections separate and owned.
    @param context: Private orchestration state, never serialized.
    @param credentials: Two short-lived in-memory passwords.
    @param state: Sanitized failure phase without credential or driver message contents.
    @return: None; outer ownership boundary closes even partially opened connections.
    """
    context['dba'] = stage_call(state, 'R_FK_DBA_CONNECT', open_connection,
                              DatabaseSettings('127.0.0.1', 3306, context['header']['dba_user'], credentials[0], 'jukebox101_test'))
    context['harness'] = stage_call(state, 'R_FK_HARNESS_CONNECT', verified_connection,
                                  DatabaseSettings('127.0.0.1', 3306, HARNESS_USER, credentials[1], 'jukebox101_test'), HARNESS_USER)
    for name, lock in (('dba', 'jukebox101:batch-b-fk-window'), ('harness', 'jukebox101:batch-b-test-harness')):
        context[name + '_id'] = stage_call(state, 'R_FK_' + name.upper() + '_SESSION_SETUP', prepare_session, context[name], lock)
    state.pop('current_step', None)


def prepare_session(connection: Any, lock: str) -> int:
    """@description: Attribute lock and session failures separately from credential authentication.
    @param connection: Already authenticated owned connection.
    @param lock: Existing exclusive advisory lock name.
    @return: Owned MySQL connection identifier.
    """
    with connection.cursor() as cursor:
        if query(cursor, 'SELECT GET_LOCK(%s,0) owned', (lock,)) != ({'owned': 1},):
            raise HarnessSafetyError('FK_REPAIR_EXCLUSIVE_LOCK_UNAVAILABLE')
        cursor.execute('SET SESSION lock_wait_timeout=5')
        cursor.execute('SET SESSION innodb_lock_wait_timeout=2')
        return query(cursor, 'SELECT CONNECTION_ID() id')[0]['id']


def require_exclusive(cursor: Any, context: dict) -> None:
    """@description: Refuse other foreground sessions, scheduled events and hidden DBA metadata restrictions.
    @param cursor: Existing DBA cursor with full instance metadata visibility.
    @param context: Exact two owned connection IDs and fixed approved header.
    @return: None; operator quiescence is additionally required because advisory locks cannot restrain administrators.
    """
    check_dba(cursor, context['header']['dba_user'], SERVER_UUID)
    if not existing_dba_authority(query(cursor, 'SHOW GRANTS FOR CURRENT_USER()')):
        raise HarnessSafetyError('FK_REPAIR_DBA_AUTHORITY_REJECTED')
    others = query(cursor, "SELECT ID FROM information_schema.PROCESSLIST WHERE ID NOT IN (%s,%s) AND USER NOT IN ('system user','event_scheduler')",
                   (context['dba_id'], context['harness_id']))
    if others or query(cursor, "SELECT EVENT_NAME FROM information_schema.EVENTS WHERE EVENT_SCHEMA='jukebox101_test'"):
        raise HarnessSafetyError('FK_REPAIR_OTHER_WRITER_POSSIBLE')


def check_evidence() -> None:
    """@description: Preserve all original source artifacts throughout both independent DDL phases.
    @param: none
    @return: None; modified manifests or historical journals poison the entire attempt.
    """
    recovery.require_journal_hash()
    if hashlib.sha256(recovery.MANIFEST_FILE.read_bytes()).hexdigest() != recovery.MANIFEST_HASH:
        raise HarnessSafetyError('FK_REPAIR_MANIFEST_CHANGED')
    if hashlib.sha256(FAILED_JOURNAL.read_bytes()).hexdigest() != FAILED_HASH:
        raise HarnessSafetyError('FK_REPAIR_FAILED_JOURNAL_CHANGED')
    if hashlib.sha256(DDL_FAILURE_JOURNAL.read_bytes()).hexdigest() != DDL_FAILURE_HASH:
        raise HarnessSafetyError('FK_REPAIR_DDL_JOURNAL_CHANGED')


def inspect_original(context: dict, state: dict) -> None:
    """@description: Prove exact 15-PK ownership under locks before releasing transactions for the first ALTER.
    @param context: Owned connections, original journal, scope and pinned manifest.
    @param state: New attempt journal state; no credentials.
    @return: None; independent original and temporary hashes are derived only after exact original approval.
    """
    check_evidence()
    with context['dba'].cursor() as cursor:
        require_exclusive(cursor, context)
        recovery.verify_fk_metadata(cursor)
        definitions = schema_fingerprints(cursor)
        context['window'] = build_window(definitions, ORIGINAL_SCHEMA)
    rows = lock_inventory(context['harness'])  # [stable exact manifest]: Not row counts or inferred run prefixes.
    with context['harness'].cursor() as cursor:
        recovery.guard_metadata(cursor, ORIGINAL_SCHEMA)
        recovery.check_manifest(rows, context['journal'], context['scope'], context['manifest'])
    context['before'] = rows
    state.update(original_schema=ORIGINAL_SCHEMA, cascade_schema=digest(dict(context['window'].cascade)))
    context['harness'].rollback()
    context['dba'].rollback()
    recovery.save(state, 'R_FK_ORIGINAL_VERIFIED')


def boundary(context: dict, cascade: bool | str, expected_rows: dict) -> None:
    """@description: Recheck exact schema, all FK edges and whole inventory on the DBA side of every ALTER.
    @param context: Existing exclusively attested connections and immutable schema pair.
    @param cascade: Exact original, temporary or dual-FK bridge state; never arbitrary metadata.
    @param expected_rows: Exact before-data or fully restored baseline according to acknowledged commit outcome.
    @return: None; unknown data, metadata or sessions forbid further writes.
    """
    check_evidence()
    context['dba'].rollback()
    with context['dba'].cursor() as cursor:
        require_exclusive(cursor, context)
        require_schema(schema_fingerprints(cursor), context['window'], cascade)
        recovery.verify_fk_metadata(cursor, cascade)
        if read_inventory(cursor) != expected_rows:
            raise HarnessSafetyError('FK_REPAIR_BOUNDARY_DATA_CHANGED')
    context['dba'].rollback()


def transition(context: dict, cascade: bool, expected_rows: dict, state: dict) -> None:
    """@description: Execute one exact ALTER once with full checks before and after its implicit commit.
    @param context: Fixed DBA connection and precomputed original/CASCADE schema pair.
    @param cascade: Direction of the single constraint change.
    @param expected_rows: Data that DDL is forbidden to alter.
    @param state: Durable outcome for each separately acknowledged DDL.
    @return: None; restoration is complete only after exact metadata verification.
    """
    apply_switch(context['dba'], context['window'], cascade, state, recovery.save,
                 lambda phase: boundary(context, phase, expected_rows))
    state['cascade_verified'] = cascade
    state['fk_restoration_pending'] = cascade
    recovery.save(state, 'R_FK_CASCADE_VERIFIED' if cascade else 'R_FK_ORIGINAL_RESTORED')


def settle(context: dict, password: str, state: dict) -> None:
    """@description: Reuse existing single harness transaction and unknown-commit behavior inside the exact temporary window.
    @param context: Original incident data and already verified temporary FK scope.
    @param password: Private harness credential for independent SELECT/SHOW post-verification.
    @param state: Shared DML outcome, distinct from both DDL outcomes.
    @return: None; original FK restoration and final verification still must follow.
    """
    try:
        recovery.restore_transaction(context['harness'], context['journal'], context['scope'], context['manifest'], ORIGINAL_SCHEMA, state, context['window'])
        recovery.commit_once(context['harness'], state)
    except BaseException:
        recovery.rollback_failure(context['harness'], state)
        raise
    recovery.verify_after(password, context['journal'], ORIGINAL_SCHEMA, state, context['window'])
    state['data_baseline_verified'] = state['baseline_restored']
    state['baseline_restored'] = False  # [whole recovery outcome]: Data-only success cannot hide an unrestored FK.


def restore_after_failure(context: dict, state: dict) -> None:
    """@description: Restore the original FK only when DML outcome and unchanged data are both proven.
    @param context: Still-owned original connections and evidence.
    @param state: Original failure and independent DDL/commit status.
    @return: None; unknown outcomes or any restoration error retain a blocked pending obligation.
    """
    if not state.get('cascade_verified') or state.get('restore_ddl_outcome') is not None or state.get('restore_phase_started'):
        return
    if state.get('commit_outcome') == 'UNKNOWN' or state.get('error_type') == 'HarnessSafetyError':
        return
    if state.get('rollback_outcome') == 'ACKNOWLEDGED' and state.get('commit_outcome') != 'ACKNOWLEDGED':
        expected = context['before']  # [proven rollback target]: Never reset counters on a failed DML transaction.
    elif state.get('commit_outcome') == 'ACKNOWLEDGED' and state.get('read_only_baseline_matches') is True:
        expected = recovery.expected_baseline(context['journal'])
    else:
        return
    try:
        transition(context, False, expected, state)
    except BaseException as error:
        state['fk_restore_error_type'] = type(error).__name__
        state['fk_restore_mysql_error_number'] = mysql_error_number(error)
        state['fk_restoration_pending'] = True


def execute(context: dict, password: str, state: dict) -> None:
    """@description: Preserve a strict original-to-temporary-to-original lifecycle without replay or automatic unblocking.
    @param context: Two owned connections and exact immutable incident data.
    @param password: Private harness credential for fresh final reads.
    @param state: New journal retaining every independent outcome.
    @return: None; failed recovery stays failed even when FK restoration succeeds.
    """
    inspect_original(context, state)
    try:
        transition(context, True, context['before'], state)
        settle(context, password, state)
        state['restore_phase_started'] = True
        transition(context, False, recovery.expected_baseline(context['journal']), state)
        recovery.verify_after(password, context['journal'], ORIGINAL_SCHEMA, state)
    except BaseException as error:
        record_error(state, error)
        state['baseline_restored'] = False
        inspect_unknown(context, password, state)
        restore_after_failure(context, state)
        raise
    if not state.get('baseline_restored') or state.get('fk_restoration_pending'):
        raise HarnessSafetyError('FK_REPAIR_FINAL_VERIFICATION_REQUIRED')
    recovery.save(state, 'R_FK_RESTORED_VERIFIED_BLOCKED')


def inspect_unknown(context: dict, password: str, state: dict) -> None:
    """@description: Restrict a lost DML commit acknowledgement to a fresh read-only investigation.
    @param context: Owned connections and immutable temporary schema.
    @param password: Private harness password for a fresh SELECT/SHOW connection.
    @param state: UNKNOWN is preserved even when the baseline is observed.
    @return: None; no ALTER or DELETE is retried.
    """
    if state.get('commit_outcome') != 'UNKNOWN':
        return
    try:
        connection = context.pop('harness')
        connection.close()
        recovery.verify_after(password, context['journal'], ORIGINAL_SCHEMA, state, context['window'])
    except BaseException as error:
        state['read_only_investigation_error_type'] = type(error).__name__


def close_connections(context: dict, state: dict) -> None:
    """@description: Release only owned sessions without hiding post-commit close failures.
    @param context: Possibly partially opened connection map.
    @param state: Safe attempt outcome, never promoted after a close error.
    @return: None.
    """
    for name in ('harness', 'dba'):
        connection = context.pop(name, None)
        if connection is None:
            continue
        try:
            connection.close()
        except BaseException as error:
            record_error(state, error)
            state.update(baseline_restored=False, stage='R_FK_FAILED_BLOCKED', connection_close_failed=True)


def run_received(received: tuple, state: dict) -> int:
    """@description: Bound credential and connection lifetimes around the explicit repair executor.
    @param received: Validated protocol, evidence and private credentials.
    @param state: Durable nonsecret attempt result.
    @return: Zero only after both original data baseline and original FK are independently verified.
    """
    header, journal, scope, manifest, credentials = received
    context = dict(header=header, journal=journal, scope=scope, manifest=manifest)  # [private orchestration]: Never serialize this map.
    try:
        acquire_connections(context, credentials, state)
        execute(context, credentials[1], state)
    finally:
        credentials.clear()
        close_connections(context, state)
    return int(state.get('baseline_restored') is not True)


@guarded_command
def main(stream: TextIO) -> int:
    """@description: Expose a separate default-off repair mode while preserving all original artifacts and the block.
    @param stream: Existing launcher's anonymous private protocol.
    @return: Nonzero on any DDL, DML, verification or persistence uncertainty.
    """
    state = dict(stage='R_FK_FAILED_BLOCKED', session_blocked=True, baseline_restored=False, fk_restoration_pending=False,
                 cleanup_progress=[], run_id=recovery.RUN_ID, journal_sha256=recovery.JOURNAL_HASH)  # [independent attempt]: Never the original journal.
    try:
        received = receive(stream)
        os.environ['TEST_DB_SERVER_UUID'] = SERVER_UUID
        state['recovery_journal'] = str(Path(tempfile.mkdtemp(prefix='jukebox101-fk-recovery-')) / 'recovery.json')
        recovery.save(state, 'R_FK_PRECHECK')
        if run_received(received, state):
            raise HarnessSafetyError('FK_REPAIR_FINALIZATION_FAILED')
    except BaseException as error:
        record_error(state, error)
        state.update(stage='R_FK_FAILED_BLOCKED', baseline_restored=False)
    finally:
        os.environ.pop('TEST_DB_SERVER_UUID', None)
    return emit(state)


def emit(state: dict) -> int:
    """@description: Never report success if the final durable journal cannot be retained.
    @param state: Sanitized blocked outcome with original errors preserved.
    @return: Zero only when original schema, data and final journal are all verified.
    """
    try:
        if 'recovery_journal' in state:
            recovery.save(state, state['stage'])
    except BaseException as error:
        state.update(stage='R_FK_FAILED_BLOCKED', baseline_restored=False, journal_error_type=type(error).__name__)
    print(json.dumps(state, ensure_ascii=True))
    return int(state.get('baseline_restored') is not True)
