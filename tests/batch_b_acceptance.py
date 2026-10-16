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
from bot.config import DatabaseSettings
from tests.batch_b_harness_contract import HARNESS_USER, HarnessSafetyError, RUN_SWITCH, HARNESS_SWITCH
from tests.batch_b_harness_database import verified_connection, read_inventory, schema_fingerprints
from tests.batch_b_acceptance_setup import stage_a, record_error


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
    credentials = [json.loads(stream.readline(16384)) for _ in range(3)]  # [short-lived plaintext]: Never written to reports.
    if any(not isinstance(secret, str) or not secret or len(secret) > 4096 or '\x00' in secret for secret in credentials):
        raise HarnessSafetyError('CREDENTIAL_PROTOCOL_REJECTED')
    return header, credentials


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

    def __init__(self) -> None:
        """@description: Initialize separate genuine test outcomes.
        @param self: Private pytest report observer.
        @return: None.
        """
        self.counts = dict(passed=0, failed=0, skipped=0)  # [real results]: Offline tests never enter this runner.
        self.reports = []  # [pytest phase evidence]: Distinguish a passed call from a failed teardown without raw traceback.

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


def run_tests(state: dict) -> int:
    """@description: Run only the existing real suite while suppressing potentially sensitive traceback output.
    @param state: Sanitized counts and journal references.
    @return: Original pytest exit code.
    """
    import pytest
    observer = TestCounts()  # [actual result accounting]: Never substitute mock success for MySQL behavior.
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


if __name__ == '__main__':
    raise SystemExit(entry())
