"""Receive single-use credentials through an anonymous pipe and run authorized Batch B acceptance.

Only sanitized progress and counts leave this process; real repository tests retain their existing cleanup and isolation gates.
"""

from contextlib import redirect_stdout, redirect_stderr
import io
import importlib
import json
import os
import re
import sys
from typing import TextIO
from uuid import UUID
from pathlib import Path
import tempfile
from bot.config import DatabaseSettings
from tests.batch_b_harness_contract import HARNESS_USER, HarnessSafetyError, RUN_SWITCH, HARNESS_SWITCH
from tests.batch_b_harness_database import verified_connection, read_inventory, schema_fingerprints
from tests.batch_b_acceptance_setup import stage_a, record_error, stage_call


def receive(stream: TextIO) -> tuple[dict, list[str]]:
    """@description: Accept one bounded private protocol without consulting credential stores.
    @param stream: Owned anonymous standard-input pipe.
    @return: Validated nonsecret header and three private credentials.
    """
    header = json.loads(stream.readline(4096))  # [explicit execution intent]: No ambient environment can authorize this runner.
    if set(header) != {'dba_user', 'server_uuid', 'authorization'} or header['authorization'] != 'BATCH_B_A_PLUS_B':
        raise HarnessSafetyError('INPUT_AUTHORIZATION_REJECTED')
    if not isinstance(header['dba_user'], str) or not re.fullmatch(r'[a-zA-Z0-9_]{1,32}', header['dba_user']):
        raise HarnessSafetyError('DBA_USERNAME_REJECTED')
    if header['dba_user'] in {HARNESS_USER, 'jukebox101_test_app', 'jukebox101_test_migrator'}:
        raise HarnessSafetyError('DBA_ROLE_REJECTED')
    if str(UUID(header['server_uuid'])) != header['server_uuid'].lower():
        raise HarnessSafetyError('SERVER_UUID_REJECTED')
    return header, receive_private_credentials(stream)


def settings(user: str, password: str) -> DatabaseSettings:
    """@description: Prevent credential input from changing the sole approved destination.
    @param user: Validated DBA name or fixed application/harness account.
    @param password: Private process-memory credential.
    @return: Redacted fixed-target connection settings.
    """
    return DatabaseSettings('127.0.0.1', 3306, user, password, 'jukebox101_test')


def close_gates() -> None:
    """@description: Keep inherited migration and unrelated real-test entry points disabled.
    @param: none
    @return: None.
    """
    for key in tuple(os.environ):
        if key.startswith('JUKEBOX_RUN_'):
            os.environ[key] = '0'


def install_profiles(application: DatabaseSettings, harness: DatabaseSettings) -> None:
    """@description: Adapt private IPC to existing process-scoped pytest profile loaders only during Stage B.
    @param application: Fixed business account, never DBA.
    @param harness: Independent setup/cleanup account.
    @return: None.
    """
    for prefix, profile in (('TEST_DB_', application), ('TEST_HARNESS_DB_', harness)):
        for name, value in dict(HOST=profile.host, PORT=str(profile.port), USER=profile.user,
                                PASSWORD=profile.password, NAME=profile.database).items():
            os.environ[prefix + name] = value
    os.environ[RUN_SWITCH] = os.environ[HARNESS_SWITCH] = '1'
    os.environ['JUKEBOX_BATCH_B_EXCLUSIVE'] = 'jukebox101_test'
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    os.environ['PYTEST_ADDOPTS'] = ''


class TestCounts:
    """Count actual pytest reports without exposing assertion values, local variables or driver text."""

    def __init__(self, supervision: dict | None = None) -> None:
        """@description: Initialize separate genuine test outcomes.
        @param self: Private pytest report observer.
        @param supervision: Optional new-session metadata and parent-only preflight callbacks.
        @return: None.
        """
        self.supervision = supervision  # [parent-only context]: No DBA connection is sent to application workers.
        self.counts = dict(passed=0, failed=0, skipped=0)  # [real results]: Offline tests never enter this runner.
        self.reports = []  # [pytest phase evidence]: Distinguish a passed call from a failed teardown without raw traceback.

    def pytest_configure(self, config: object) -> None:
        """@description: Bind this one pytest invocation to its independently created supervised session.
        @param self: Observer with optional new-session context.
        @param config: Current pytest configuration, never inherited environment authority.
        @return: None.
        """
        config.stage_b_supervision = self.supervision

    def pytest_runtest_logreport(self, report: object) -> None:
        """@description: Count calls and setup/teardown failures without printing exception payloads.
        @param self: Private observer.
        @param report: Actual pytest TestReport.
        @return: None.
        """
        self.reports.append({'nodeid': report.nodeid, 'when': report.when, 'outcome': report.outcome})
        if report.failed:
            self.counts['failed'] += 1
        elif report.skipped:
            self.counts['skipped'] += 1
        elif report.when == 'call' and report.passed:
            self.counts['passed'] += 1


def run_tests(state: dict, supervision: dict | None = None) -> int:
    """@description: Run only the existing real suite while suppressing potentially sensitive traceback output.
    @param state: Sanitized counts and journal references.
    @param supervision: Optional exact schema, new-session scope and before/after run guards.
    @return: Original pytest exit code.
    """
    import pytest
    observer = TestCounts(supervision)  # [actual result accounting]: Never substitute mock success for MySQL behavior.
    output = io.StringIO()
    try:
        with redirect_stdout(output), redirect_stderr(output):
            result = pytest.main(['tests/test_batch_b_mysql.py', '-p', 'no:cacheprovider', '-p', 'no:logging',
                '-o', 'addopts=', '-q', '-s', '--tb=no', '--maxfail=1'], plugins=[observer])
        state['tests'] = observer.counts
        state['test_reports'] = observer.reports
        state['journals'] = journal_reports(output.getvalue())
        return int(result)
    finally:
        output.close()


def journal_reports(output: str) -> list[dict]:
    """@description: Publish only existing nonsecret harness summary fields instead of raw captured pytest output.
    @param output: Private captured terminal stream, possibly including exceptions.
    @return: Validated harness journal references and restoration state.
    """
    reports = []  # [diagnostic allowlist]: Never serialize arbitrary captured objects.
    for line in output.splitlines():
        start = line.find('{"run_id":')
        if start < 0:
            continue
        try:
            report = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if set(report) == {'run_id', 'journal', 'restoration_pending', 'counts', 'session_blocked'}:
            reports.append(report)
    return reports


def verify_restoration(harness: DatabaseSettings, baseline: tuple, state: dict | None = None) -> None:
    """@description: Independently re-read all 23 tables and protected registry after pytest teardown.
    @param harness: Fixed cleanup identity, with no registry write or DDL permission.
    @param baseline: Full accepted inventory and definitions recorded before provisioning.
    @param state: Optional sanitized diagnostic report for concrete baseline differences.
    @return: None; any difference blocks acceptance.
    """
    connection = verified_connection(harness, HARNESS_USER)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            connection.begin()
            actual = read_inventory(cursor), schema_fingerprints(cursor)
            require_restoration(actual, baseline, state)
    finally:
        connection.close()


def require_restoration(actual: tuple, baseline: tuple, state: dict | None) -> None:
    """@description: Preserve strict baseline equality while reporting concrete safe differences.
    @param actual: Private observed inventory and schema.
    @param baseline: Original protected inventory and schema.
    @param state: Optional sanitized report.
    @return: None; any difference retains the existing stop condition.
    """
    if actual == baseline:
        return
    if state is not None:
        state['baseline_diff'] = inventory_difference(actual, baseline)
    raise HarnessSafetyError('FINAL_BASELINE_MISMATCH')


def inventory_difference(actual: tuple, baseline: tuple) -> dict:
    """@description: Make restoration mismatches concrete without dumping row payloads or unknown identities.
    @param actual: Private current inventory and schema definitions.
    @param baseline: Protected original inventory and definitions.
    @return: Per-table counts/content-change flags, counter differences and schema drift names.
    """
    rows, definitions = actual
    original, original_definitions = baseline
    tables = {name: dict(expected_count=len(original[name]), actual_count=len(rows[name]), content_changed=rows[name] != original[name])
              for name in sorted(original) if rows[name] != original[name]}
    counters = {}  # [safe control detail]: Only exact integer values are emitted, never arbitrary stored strings.
    if len(rows['jb2_control']) == len(original['jb2_control']) == 1:
        for name in ('accepted_sequence', 'revision', 'snapshot_ready', 'legacy_writers_quiesced', 'settlement_enabled'):
            expected, observed = original['jb2_control'][0][name], rows['jb2_control'][0][name]
            if expected != observed:
                counters[name] = dict(expected=expected, actual=observed if type(observed) is int else 'INVALID_TYPE')
    return dict(tables=tables, control=counters, schema_changed=[name for name in sorted(original_definitions)
                if definitions.get(name) != original_definitions[name]])


def execute(header: dict, credentials: list[str], state: dict) -> int:
    """@description: Discard DBA references before enabling the existing genuine test workflow.
    @param header: Validated nonsecret intent and server pin.
    @param credentials: Private single-use secrets, cleared after profile construction.
    @param state: Sanitized progress and results.
    @return: Original pytest code or safety failure code.
    """
    os.environ['TEST_DB_SERVER_UUID'] = header['server_uuid'].lower()
    dba = settings(header['dba_user'], credentials[0])  # [DBA-only authority]: Never installed in environment or test workers.
    application = settings('jukebox101_test_app', credentials[1])
    harness = settings(HARNESS_USER, credentials[2])
    credentials.clear()
    baseline = stage_a(dba, application, harness, os.environ['TEST_DB_SERVER_UUID'], state)
    dba = None
    state['a_completed'] = True
    state.pop('current_step', None)
    state['stage'] = 'B_BASELINE_FAILED'
    verify_restoration(harness, baseline)
    install_profiles(application, harness)
    state['b_started'] = True
    state['stage'] = 'B_TEST_FAILED'
    result = run_tests(state)
    state['pytest_exit_code'] = result
    close_gates()
    return conclude(harness, baseline, state, result)


def conclude(harness: DatabaseSettings, baseline: tuple, state: dict, result: int) -> int:
    """@description: Preserve cleanup failure classification even if an independent read also fails.
    @param harness: Private fixed cleanup account.
    @param baseline: Pre-provisioning protected baseline.
    @param state: Actual pytest counts and sanitized teardown summaries.
    @param result: Original pytest exit code.
    @return: Original failing code or one when verification cannot justify success.
    """
    pending = any(report['restoration_pending'] for report in state.get('journals', ()))
    state['stage'] = 'B_CLEANUP_FAILED' if pending else 'B_VERIFY_FAILED'
    verify_restoration(harness, baseline, state)
    state['baseline_restored'] = True
    if pending:
        return result or 1
    complete = result == 0 and state['tests'] == dict(passed=23, failed=0, skipped=0)
    state['stage'] = 'COMPLETE_MANUAL_ACCEPTANCE_PENDING' if complete else 'B_TEST_FAILED'
    return result or int(not complete)


def main() -> int:
    """@description: Stop safely with fixed error categories and erase process-scoped credential adapters.
    @param: none
    @return: Real runner exit status; no raw exception text escapes.
    """
    state = dict(stage='A_PRECHECK_FAILED', a_completed=False, b_started=False, baseline_restored=False, current_step='INPUT_PROTOCOL')
    credentials = []  # [private receive buffer]: Never include in sanitized output.
    try:
        close_gates()
        header, credentials = receive(sys.stdin)
        return execute(header, credentials, state)
    except BaseException as error:
        record_error(state, error)
        return 1
    finally:
        credentials.clear()
        close_gates()
        for key in tuple(os.environ):
            if key.startswith(('TEST_DB_', 'TEST_HARNESS_DB_', 'TEST_DBA_DB_')) or key == 'JUKEBOX_BATCH_B_EXCLUSIVE':
                os.environ.pop(key, None)
        print(json.dumps(state, ensure_ascii=True))


def entry() -> int:
    """@description: Keep read-only incident inspection separate from the authorized write workflow.
    @param: none
    @return: Selected mode's exit code; unknown arguments never fall back to writes.
    """
    if not sys.argv[1:]:
        return main()
    if sys.argv[1:] == ['--stage-b-only']:
        return stage_b_main(sys.stdin)
    modes = {'--inspect-recovery': 'batch_b_recovery', '--restore-recovery': 'batch_b_recovery_write',
             '--fk-lab': 'batch_b_fk_lab', '--repair-recovery-fk': 'batch_b_recovery_fk'}  # [mutually exclusive entry]: Never fall through to provisioning.
    if len(sys.argv) != 2 or sys.argv[1] not in modes:
        print('{"stage":"R_READ_ONLY_FAILED","safety_code":"UNKNOWN_ENTRY_ARGUMENT"}')
        return 1
    try:
        close_gates()
        return importlib.import_module('tests.' + modes[sys.argv[1]]).main(sys.stdin)
    finally:
        close_gates()


def receive_stage_b(stream: TextIO) -> tuple[dict, list[str]]:
    """@description: Require a new session and the pinned Stage-B-only scope before any credentials or I/O.
    @param stream: Existing anonymous stdin protocol, never a command-line secret.
    @return: Validated header and DBA, application and existing harness credentials.
    """
    header = json.loads(stream.readline(8192))  # [new authority]: Distinct from A+B and recovery modes.
    require_stage_b_identity(header)
    require_stage_b_authority(header)
    return header, receive_private_credentials(stream)


def receive_private_credentials(stream: TextIO) -> list[str]:
    """@description: Reuse one bounded three-role credential protocol across A+B and Stage-B-only.
    @param stream: Existing anonymous pipe; values never appear in reports or command arguments.
    @return: DBA, application and existing harness secrets for the owning orchestration boundary.
    """
    credentials = [json.loads(stream.readline(16384)) for _ in range(3)]  # [private lifetime]: Cleared by the caller's finally boundary.
    if any(not isinstance(secret, str) or not secret or len(secret) > 4096 or '\x00' in secret for secret in credentials):
        raise HarnessSafetyError('CREDENTIAL_PROTOCOL_REJECTED')
    return credentials


def require_stage_b_identity(header: dict) -> None:
    """@description: Reject malformed session and DBA identities without accepting a caller-controlled path.
    @param header: Parsed private protocol header with no credentials.
    @return: None; missing fields fail before authorization or network access.
    """
    if not isinstance(header.get('session_id'), str) or str(UUID(header['session_id'])) != header['session_id']:
        raise HarnessSafetyError('STAGE_B_SESSION_REJECTED')
    username = header.get('dba_user')  # [existing DBA identity]: Never an Application/Migrator/Harness account.
    if not isinstance(username, str) or not re.fullmatch('[A-Za-z0-9_]{1,32}', username) or username.startswith('jukebox101_'):
        raise HarnessSafetyError('STAGE_B_DBA_REJECTED')


def require_stage_b_authority(header: dict) -> None:
    """@description: Bind Stage B to the previously approved instance, schema and one new confirmation.
    @param header: Shape-checked identities and explicit operation intent, never ambient switches.
    @return: None; foreign pins, extra fields and old-mode authorization are refused.
    """
    from tests.batch_b_recovery_fk import SERVER_UUID, ORIGINAL_SCHEMA
    expected = {'authorization', 'dba_user', 'server_uuid', 'schema_fingerprint', 'session_id', 'exclusive_no_ddl', 'confirmation'}
    if set(header) != expected or header['authorization'] != 'BATCH_B_STAGE_B_ONLY' or header['exclusive_no_ddl'] is not True:
        raise HarnessSafetyError('STAGE_B_AUTHORIZATION_REQUIRED')
    if header['server_uuid'] != SERVER_UUID or header['schema_fingerprint'] != ORIGINAL_SCHEMA:
        raise HarnessSafetyError('STAGE_B_PIN_REJECTED')
    if header['confirmation'] != 'STAGE_B_ONLY ' + header['session_id']:
        raise HarnessSafetyError('STAGE_B_CONFIRMATION_REQUIRED')


def stage_b_save(state: dict, phase: str) -> None:
    """@description: Keep a durable new-session journal without touching any recovery evidence.
    @param state: Nonsecret parent session facts and child manifest references.
    @param phase: Fixed orchestration phase, distinct from the FK helper's subphases.
    @return: None; failed persistence cannot be reported as successful validation.
    """
    from tests.batch_b_harness import persist_manifest
    state['stage'] = phase
    persist_manifest(Path(state['session_journal']), state)


def reserve_stage_b_session(header: dict, state: dict) -> Path:
    """@description: Refuse reuse of the same session even after an earlier process crashes or fails preflight.
    @param header: Validated UUID, never a caller-selected filesystem path.
    @param state: New session audit output, not an old journal.
    @return: Fresh exclusively created local directory for this invocation and its manifests.
    """
    directory = Path(tempfile.gettempdir()) / ('jukebox101-stage-b-' + header['session_id'])
    directory.mkdir()  # [single-use boundary]: Existing directory forbids resume and retry before database access.
    state.update(session_id=header['session_id'], session_journal=str(directory / 'session.json'), runs=[],
                 previous_session_blocked=True, session_blocked=True, a_completed=False, b_started=False,
                 baseline_restored=False, original_schema=header['schema_fingerprint'])
    stage_b_save(state, 'B_ONLY_RESERVED')
    return directory


def connect_stage_b(context: dict, credentials: list[str], state: dict) -> None:
    """@description: Reuse existing accounts and exclusive-session checks without Stage A provisioning.
    @param context: Private parent-owned connection and profile map, never serialized.
    @param credentials: DBA, application and existing harness secrets, kept out of worker environments.
    @param state: Safe failure attribution and journal.
    @return: None; outer ownership boundary closes partial connections.
    """
    from bot.database.connection import open_connection
    from tests.batch_b_recovery_fk import prepare_session
    context['application'] = settings('jukebox101_test_app', credentials[1])
    context['management'] = settings(HARNESS_USER, credentials[2])
    context['dba'] = stage_call(state, 'B_ONLY_DBA_CONNECT', open_connection, settings(context['header']['dba_user'], credentials[0]))
    context['harness'] = stage_call(state, 'B_ONLY_HARNESS_CONNECT', verified_connection, context['management'], HARNESS_USER)
    context['dba_id'] = stage_call(state, 'B_ONLY_DBA_SESSION', prepare_session, context['dba'], 'jukebox101:batch-b-fk-window')
    context['harness_id'] = stage_call(state, 'B_ONLY_HARNESS_SESSION', prepare_session, context['harness'], 'jukebox101:batch-b-stage-b-supervisor')
    credentials.clear()
    state.pop('current_step', None)


def inspect_stage_b(context: dict, state: dict) -> None:
    """@description: Prove the pinned original schema, zero business rows and accepted Registry before DDL.
    @param context: Owned parent sessions and explicit fixed-target profiles.
    @param state: New session audit, with no raw table payloads.
    @return: None; all reads complete before the first temporary FK is attempted.
    """
    from tests.batch_b_recovery_fk import require_exclusive, ORIGINAL_SCHEMA
    from tests.batch_b_fk_window import build_window, digest
    from tests.batch_b_acceptance_setup import verify_metadata, verify_application_login, verify_harness_login
    from tests.batch_b_harness_database import CONTROL_COLUMNS, require_baseline
    from tests.mysql_safety import validate_test_constraints
    with context['dba'].cursor() as cursor:
        require_exclusive(cursor, context)
        baseline = verify_metadata(cursor)
        control = require_baseline(baseline[0])
        if any(control[name] != 0 for name in CONTROL_COLUMNS):
            raise HarnessSafetyError('STAGE_B_ORIGINAL_CONTROL_NOT_ZERO')
        context['window'] = build_window(baseline[1], ORIGINAL_SCHEMA)
    validate_test_constraints(context['dba'])
    verify_application_login(context['application'])
    verify_harness_login(context['management'])
    context['baseline'] = baseline
    state['cascade_schema'] = digest(dict(context['window'].cascade))
    stage_b_boundary(context, False)
    stage_b_save(state, 'B_ONLY_PREFLIGHT_VERIFIED')


def stage_b_boundary(context: dict, phase: bool | str) -> None:
    """@description: Recheck full DBA visibility, exclusivity, FK rules and unchanged data at every schema boundary.
    @param context: Private supervisor state and immutable preflight baseline.
    @param phase: Exact original, dual-FK bridge or CASCADE state.
    @return: None; no unknown data is accepted as a new baseline.
    """
    from tests.batch_b_recovery_fk import require_exclusive
    from tests.batch_b_recovery_write import verify_fk_metadata
    from tests.batch_b_fk_window import require_schema
    context['harness'].rollback()
    context['dba'].rollback()
    with context['dba'].cursor() as cursor:
        require_exclusive(cursor, context)
        require_stage_b_events_disabled(cursor)
        require_schema(schema_fingerprints(cursor), context['window'], phase)
        verify_fk_metadata(cursor, phase)
        if read_inventory(cursor) != context['baseline'][0]:
            raise HarnessSafetyError('STAGE_B_BOUNDARY_DATA_CHANGED')
    context['dba'].rollback()


def require_stage_b_events_disabled(cursor: object) -> None:
    """@description: Reject enabled events in any schema that could write into the disposable test database.
    @param cursor: DBA cursor with already-proved global metadata visibility.
    @return: None; scheduled background writers invalidate exclusive-access attestation.
    """
    from tests.batch_b_acceptance_setup import query
    if query(cursor, "SELECT EVENT_SCHEMA,EVENT_NAME FROM information_schema.EVENTS WHERE STATUS='ENABLED'"):
        raise HarnessSafetyError('STAGE_B_ENABLED_EVENT_REFUSED')


def stage_b_transition(context: dict, cascade: bool, state: dict) -> None:
    """@description: Reuse the reviewed four-step FK helper with fresh data checks and no DDL replay.
    @param context: Parent-only DBA session and approved immutable window.
    @param cascade: Open CASCADE or restore the exact original NO ACTION constraint.
    @param state: Durable per-DDL progress, including partial and unknown outcomes.
    @return: None; helper failure never starts tests or another DDL direction.
    """
    from tests.batch_b_fk_window import apply_switch
    label = 'cascade_ddl_outcome' if cascade else 'restore_ddl_outcome'
    if label in state:
        raise HarnessSafetyError('STAGE_B_DDL_RETRY_REFUSED')
    if not cascade and not state.get('tests_verified'):
        raise HarnessSafetyError('STAGE_B_RESTORE_REQUIRES_COMPLETE_TESTS')
    apply_switch(context['dba'], context['window'], cascade, state, stage_b_save,
                 lambda phase: stage_b_boundary(context, phase))
    state['fk_restoration_pending'] = cascade
    stage_b_save(state, 'B_ONLY_CASCADE_VERIFIED' if cascade else 'B_ONLY_ORIGINAL_VERIFIED')


def stage_b_run(context: dict, state: dict) -> None:
    """@description: Run exactly the existing suite only within the verified temporary FK state.
    @param context: Parent connections, immutable baseline and session directory.
    @param state: Sanitized result counts and registered run manifests.
    @return: None; any failed or skipped test leaves the session blocked without restoration writes.
    """
    if state.get('b_started'):
        raise HarnessSafetyError('STAGE_B_PYTEST_RETRY_REFUSED')
    stage_b_boundary(context, True)
    supervision = dict(session_id=state['session_id'], directory=context['directory'], state=state,
                       definitions=dict(context['window'].cascade), baseline=context['baseline'][0], before_run=lambda: stage_b_boundary(context, True))
    install_profiles(context['application'], context['management'])
    state['b_started'] = True
    stage_b_save(state, 'B_ONLY_TESTS_STARTED')
    try:
        result = run_tests(state, supervision)
    finally:
        close_gates()
    state['pytest_exit_code'] = result
    stage_b_save(state, 'B_ONLY_TESTS_FINISHED')
    require_stage_b_results(state)
    stage_b_boundary(context, True)
    state['tests_verified'] = True


def require_stage_b_results(state: dict) -> None:
    """@description: Require genuine complete test counts and all registered manifests before restoring schema.
    @param state: Actual observer output, never a caller assertion of success.
    @return: None; missing journals, uncertainty or pytest failure retain the block.
    """
    if state.get('pytest_exit_code') != 0 or state.get('tests') != dict(passed=23, failed=0, skipped=0):
        raise HarnessSafetyError('STAGE_B_TESTS_INCOMPLETE')
    if len(state['runs']) != 23 or len(state.get('journals', [])) != 23:
        raise HarnessSafetyError('STAGE_B_MANIFESTS_INCOMPLETE')
    for run, summary in zip(state['runs'], state['journals'], strict=True):
        require_stage_b_run(run, summary, state['session_id'])


def require_stage_b_run(run: dict, summary: dict, session_id: str) -> None:
    """@description: Prevent old or sibling-session manifests from authorizing this supervisor's restoration.
    @param run: Run ID and journal path registered before any child database work.
    @param summary: Existing sanitized pytest teardown summary.
    @param session_id: Parent's independently reserved single-use UUID.
    @return: None; only fully restored, correctly attributed child evidence is accepted.
    """
    report = json.loads(Path(run['journal']).read_text(encoding='utf-8'))
    if report['run_id'] != run['run_id'] or report['parent_session_id'] != session_id or report['status'] != 'RESTORED_VERIFIED':
        raise HarnessSafetyError('STAGE_B_MANIFEST_NOT_RESTORED')
    if summary['run_id'] != run['run_id'] or summary['journal'] != run['journal'] or summary['restoration_pending'] or summary['session_blocked']:
        raise HarnessSafetyError('STAGE_B_CHILD_BLOCKED')
    require_stage_b_commits(report, Path(run['journal']).parent / 'transactions')


def require_stage_b_commits(report: dict, directory: Path) -> None:
    """@description: Require explicit management acknowledgements and durable application transaction audits.
    @param report: Restored child manifest for this parent session.
    @param directory: Exact child-owned application audit directory.
    @return: None; missing records, partial commits or a failed child preserve the block.
    """
    from tests.batch_b_harness import require_worker_audits
    commits = report['transaction_progress']  # [commit evidence]: ENABLE and CLEANUP bracket every successful run.
    if not commits or commits[0]['phase'] != 'ENABLE' or commits[-1]['phase'] != 'CLEANUP' or report['failures']:
        raise HarnessSafetyError('STAGE_B_COMMIT_AUDIT_INCOMPLETE')
    if any(item['outcome'] != 'ACKNOWLEDGED' for item in commits):
        raise HarnessSafetyError('STAGE_B_COMMIT_UNCERTAIN')
    if not directory.is_dir() or not any(directory.glob('transaction-*.json')):
        raise HarnessSafetyError('STAGE_B_WORKER_AUDIT_MISSING')
    require_worker_audits(str(directory))


def execute_stage_b(context: dict, state: dict) -> None:
    """@description: Keep the original-to-window-to-original sequence single-use and fail stopped.
    @param context: Owned parent resources and new-session directory.
    @param state: Durable audit outcome, blocked until every final check is acknowledged.
    @return: None; failure exits immediately without compensation DDL, cleanup replay or unblocking.
    """
    stage_b_save(state, 'B_ONLY_PREFLIGHT_STARTED')
    inspect_stage_b(context, state)
    stage_b_transition(context, True, state)
    stage_b_run(context, state)
    stage_b_transition(context, False, state)
    verify_restoration(context['management'], context['baseline'], state)
    stage_b_boundary(context, False)
    state['baseline_restored'] = True
    stage_b_save(state, 'B_ONLY_RESTORED_VERIFIED')


def close_stage_b(context: dict, state: dict) -> None:
    """@description: Release only parent-owned sessions and preserve secondary close failures.
    @param context: Partially acquired connection map.
    @param state: Audit outcome that cannot remain successful after resource-release uncertainty.
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
            state.update(baseline_restored=False, connection_close_failed=True)


def stage_b_main(stream: TextIO) -> int:
    """@description: Expose one explicit new-session mode without provisioning or altering old block records.
    @param stream: Masked-credential bridge from the existing PowerShell launcher.
    @return: Zero only after 23 genuine passes, precise cleanup, original FK and final journal verification.
    """
    state = dict(stage='B_ONLY_FAILED_BLOCKED', session_blocked=True, previous_session_blocked=True, baseline_restored=False)
    context, credentials = {}, []  # [private resources]: Credentials never enter the serialized state.
    try:
        close_gates()
        header, credentials = receive_stage_b(stream)
        context.update(header=header, directory=reserve_stage_b_session(header, state))
        os.environ['TEST_DB_SERVER_UUID'] = header['server_uuid']
        connect_stage_b(context, credentials, state)
        execute_stage_b(context, state)
    except BaseException as error:
        record_error(state, error)
        state['baseline_restored'] = False
    finally:
        credentials.clear()
        close_gates()
        close_stage_b(context, state)
        clear_stage_b_profiles()
    return emit_stage_b(state)


def clear_stage_b_profiles() -> None:
    """@description: Remove process-scoped credential adapters even after interrupted pytest or failed preflight.
    @param: none
    @return: None.
    """
    for key in tuple(os.environ):
        if key.startswith(('TEST_DB_', 'TEST_HARNESS_DB_', 'TEST_DBA_DB_')) or key == 'JUKEBOX_BATCH_B_EXCLUSIVE':
            os.environ.pop(key, None)


def emit_stage_b(state: dict) -> int:
    """@description: Never promote a final journal failure or partial restore to a usable session.
    @param state: Safe parent outcome and new-session manifest references.
    @return: Nonzero for any uncertainty; the previous session block is never cleared.
    """
    complete = state.get('baseline_restored') is True and 'error_type' not in state
    state['session_blocked'] = not complete
    state['stage'] = 'B_ONLY_COMPLETE_MANUAL_ACCEPTANCE_PENDING' if complete else 'B_ONLY_FAILED_BLOCKED'
    try:
        if 'session_journal' in state:
            stage_b_save(state, state['stage'])
    except BaseException as error:
        record_error(state, error)
        state.update(session_blocked=True, baseline_restored=False, stage='B_ONLY_FAILED_BLOCKED')
    print(json.dumps(state, ensure_ascii=True))
    return int(state['session_blocked'])


if __name__ == '__main__':
    raise SystemExit(entry())
