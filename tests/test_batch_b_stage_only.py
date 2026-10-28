"""Validate the new Stage-B-only boundary with synthetic metadata and durable offline journals.

No test opens MySQL, provisions accounts, runs real DDL or connects to Discord.
"""

import ast
import io
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pymysql
import pytest

from tests import batch_b_acceptance as runner
from tests import batch_b_recovery_fk as repair
from tests import batch_b_recovery_write as recovery
from tests import batch_b_harness as runtime
from tests import batch_b_mysql_cases as cases
from tests import batch_b_mysql_scenarios as scenarios
from tests.batch_b_fk_window import build_window, digest, switch_sql
from tests.batch_b_harness_contract import HarnessSafetyError, RunScope
from tests.batch_b_mysql_faults import FaultConnection, InjectedClientFault
from tests.test_batch_b_fk_window import definitions
from tests.test_batch_b_harness_safety import empty_inventory, owned_inventory, mock_harness
from tests.test_phase2_quality import _implementation_lines, _control_depth, _assert_function_documentation


@pytest.fixture
def header() -> dict:
    """@description: Supply one explicit new-session authorization with the existing pinned target.
    @param: none
    @return: Nonsecret protocol facts; these do not authorize a real test from this offline suite.
    """
    session_id = str(uuid4())  # [fresh unit scope]: Never an old Recovery run identifier.
    return dict(authorization='BATCH_B_STAGE_B_ONLY', dba_user='root', server_uuid=repair.SERVER_UUID,
                schema_fingerprint=repair.ORIGINAL_SCHEMA, session_id=session_id, exclusive_no_ddl=True,
                confirmation='STAGE_B_ONLY ' + session_id)


def private_stream(header: dict) -> io.StringIO:
    """@description: Exercise the anonymous protocol with synthetic credentials only.
    @param header: Explicit nonsecret scope.
    @return: In-memory header and three synthetic secret lines.
    """
    return io.StringIO('\n'.join(json.dumps(item) for item in (header, 'fake-dba', 'fake-app', 'fake-harness')))


@pytest.mark.parametrize('field,value', [('authorization', 'BATCH_B_A_PLUS_B'), ('authorization', 'BATCH_B_RECOVERY_FK_REPAIR'),
    ('server_uuid', '11111111-2222-4333-8444-555555555555'), ('schema_fingerprint', 'a' * 64),
    ('session_id', '../old-session'), ('confirmation', 'STAGE_B_ONLY old'), ('exclusive_no_ddl', False),
    ('dba_user', 'jukebox101_test_harness'), ('dba_user', 'root;unsafe')])
def test_stage_only_protocol_rejects_other_authority(header, field, value):
    """@description: Reject inherited, malformed or expanded scope before any connection.
    @param header: Synthetic valid header.
    @param field: Authority field to corrupt.
    @param value: Unapproved value.
    @return: None.
    """
    header[field] = value
    with pytest.raises((HarnessSafetyError, ValueError)):
        runner.receive_stage_b(private_stream(header))


def test_stage_only_protocol_preserves_private_framing(header):
    """@description: Keep all three existing roles separate without accepting an incident journal path.
    @param header: Synthetic new-session header.
    @return: None.
    """
    received, credentials = runner.receive_stage_b(private_stream(header))
    assert received == header and credentials == ['fake-dba', 'fake-app', 'fake-harness']
    header['journal'] = 'old.json'
    with pytest.raises(HarnessSafetyError):
        runner.receive_stage_b(private_stream(header))


def test_session_reservation_is_single_use_even_without_database(header, tmp_path, monkeypatch):
    """@description: Retain an attempted session so a new process cannot resume the same identity.
    @param header: Fresh synthetic authorization.
    @param tmp_path: Disposable local journal root.
    @param monkeypatch: Temporary directory selection.
    @return: None.
    """
    monkeypatch.setattr(runner.tempfile, 'gettempdir', lambda: str(tmp_path))
    state = {}
    directory = runner.reserve_stage_b_session(header, state)
    original = (directory / 'session.json').read_bytes()
    with pytest.raises(FileExistsError):
        runner.reserve_stage_b_session(header, {})
    assert (directory / 'session.json').read_bytes() == original
    assert state['session_blocked'] and state['previous_session_blocked']


@pytest.fixture
def window_context(header, tmp_path, monkeypatch):
    """@description: Feed actual FK transition guards with synthetic metadata that changes only after fake ALTER.
    @param header: Explicit synthetic session boundary.
    @param tmp_path: Fresh local audit root.
    @param monkeypatch: DBA visibility and full inventory substitutes.
    @return: Parent context, durable state and mutable simulated metadata authority.
    """
    original = definitions()
    window = build_window(original, digest(original))
    context = dict(header=header, dba=MagicMock(), harness=MagicMock(), dba_id=11, harness_id=12,
                   window=window, baseline=(empty_inventory(), original), directory=tmp_path)
    state = dict(session_journal=str(tmp_path / 'session.json'), session_id=header['session_id'])
    metadata = dict(definitions=original, rows=empty_inventory(), attempts=[])
    monkeypatch.setattr(repair, 'require_exclusive', MagicMock())
    monkeypatch.setattr(runner, 'require_stage_b_events_disabled', MagicMock())
    monkeypatch.setattr(recovery, 'verify_fk_metadata', MagicMock())
    monkeypatch.setattr(runner, 'schema_fingerprints', lambda cursor: metadata['definitions'])
    monkeypatch.setattr(runner, 'read_inventory', lambda cursor: metadata['rows'])
    return context, state, metadata


def fake_ddl(context: dict, metadata: dict, fail_at: int | None = None) -> None:
    """@description: Simulate only the four reviewed schema states, including acknowledgement loss.
    @param context: Original and expected bridge/window definitions.
    @param metadata: Private synthetic current metadata.
    @param fail_at: Optional one-based ALTER to lose its acknowledgement after applying it.
    @return: None.
    """
    window = context['window']
    statements = (*switch_sql(window, True), *switch_sql(window, False))
    phases = (window.bridge_original_first, window.cascade, window.bridge_temporary_first, window.original)
    def execute(statement: str) -> None:
        """@description: Model server-side schema progress without invoking a driver.
        @param statement: Exact fixed SQL from the existing helper.
        @return: None.
        """
        index = len(metadata['attempts'])
        assert statement == statements[index]
        metadata['attempts'].append(statement)
        metadata['definitions'] = dict(phases[index])
        if fail_at == index + 1:
            raise pymysql.OperationalError(2013, 'fake-private-driver-text')
    context['dba'].cursor.return_value.__enter__.return_value.execute.side_effect = execute


def test_stage_only_reuses_all_four_reviewed_ddl_and_exact_restore(window_context):
    """@description: Prove opening and restoration retain FK protection and return every original schema byte.
    @param window_context: Synthetic exact metadata with real transition logic.
    @return: None.
    """
    context, state, metadata = window_context
    fake_ddl(context, metadata)
    runner.stage_b_transition(context, True, state)
    assert metadata['definitions'] == dict(context['window'].cascade)
    state["tests_verified"] = True
    runner.stage_b_transition(context, False, state)
    assert metadata['definitions'] == context['baseline'][1]
    assert len(metadata['attempts']) == 4 and not state['fk_restoration_pending']
    assert [step['outcome'] for step in state['fk_ddl_progress']] == ['ACKNOWLEDGED'] * 4
    assert json.loads(Path(state['session_journal']).read_text())['restore_ddl_outcome'] == 'ACKNOWLEDGED'


@pytest.mark.parametrize('fail_at', [1, 2, 3, 4])
def test_unknown_ddl_never_runs_the_next_statement_or_compensation(window_context, fail_at):
    """@description: Keep every partial ALTER state blocked instead of guessing rollback or retrying.
    @param window_context: Fake metadata and actual transition helper.
    @param fail_at: ALTER whose acknowledgement is lost after server-side change.
    @return: None.
    """
    context, state, metadata = window_context
    fake_ddl(context, metadata, fail_at)
    with pytest.raises(pymysql.OperationalError):
        runner.stage_b_transition(context, True, state)
        state["tests_verified"] = True
        runner.stage_b_transition(context, False, state)
    assert len(metadata['attempts']) == fail_at
    assert state['fk_ddl_progress'][-1]['outcome'] == 'UNKNOWN'
    assert state['fk_restoration_pending']


@pytest.mark.parametrize('drift', ['rows', 'schema', 'exclusive', 'fk'])
def test_boundary_failure_forbids_first_ddl(window_context, monkeypatch, drift):
    """@description: Reject unknown inventory, schema or foreign-key/session changes before any ALTER.
    @param window_context: Synthetic preflight baseline.
    @param monkeypatch: Targeted boundary failure injection.
    @param drift: Safety check to fail.
    @return: None.
    """
    context, state, metadata = window_context
    if drift == 'rows':
        metadata['rows']['users'] = ({'discord_user_id': 999},)
    elif drift == 'schema':
        metadata['definitions'] = dict(metadata['definitions'], users='unexpected')
    else:
        module, name = (repair, 'require_exclusive') if drift == 'exclusive' else (recovery, 'verify_fk_metadata')
        monkeypatch.setattr(module, name, MagicMock(side_effect=HarnessSafetyError('INJECTED_BOUNDARY_FAILURE')))
    with pytest.raises(HarnessSafetyError):
        runner.stage_b_transition(context, True, state)
    context['dba'].cursor.return_value.__enter__.return_value.execute.assert_not_called()


def completed_session(tmp_path: Path) -> dict:
    """@description: Construct synthetic complete child audits for final-result rejection tests.
    @param tmp_path: Disposable new-session root.
    @return: Fake successful observer state, never evidence of real MySQL acceptance.
    """
    state = dict(session_id=str(uuid4()), tests=dict(passed=23, failed=0, skipped=0), pytest_exit_code=0, runs=[], journals=[])
    for index in range(23):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / 'transactions').mkdir()
        run = dict(run_id=uuid4().hex, journal=str(directory / 'run.json'))
        report = dict(run_id=run['run_id'], parent_session_id=state['session_id'], status='RESTORED_VERIFIED', failures=[],
                      transaction_progress=[dict(phase=phase, outcome='ACKNOWLEDGED') for phase in ('ENABLE', 'CLEANUP')])
        Path(run['journal']).write_text(json.dumps(report))
        (directory / 'transactions' / 'transaction-one.json').write_text(json.dumps(dict(blocked=False, closed=True, progress=[])))
        state['runs'].append(run)
        state['journals'].append(dict(run, restoration_pending=False, session_blocked=False, counts={}))
    return state


def test_complete_results_require_all_23_registered_child_manifests(tmp_path):
    """@description: Validate success only from the independently registered new-session manifest set.
    @param tmp_path: Disposable synthetic audit root.
    @return: None.
    """
    state = completed_session(tmp_path)
    runner.require_stage_b_results(state)
    state['runs'].pop()
    with pytest.raises(HarnessSafetyError, match='MANIFESTS_INCOMPLETE'):
        runner.require_stage_b_results(state)


@pytest.mark.parametrize('fault', ['skipped', 'pytest', 'old_session', 'child_block', 'child_pending', 'unknown_commit', 'missing_audit', 'foreign_summary'])
def test_incomplete_or_uncertain_child_evidence_blocks_restore(tmp_path, fault):
    """@description: Prevent a data-only or partial test success from authorizing original-FK restoration.
    @param tmp_path: Disposable synthetic journals.
    @param fault: Audit failure affecting one otherwise complete run.
    @return: None.
    """
    state = completed_session(tmp_path)
    path = Path(state['runs'][0]['journal'])
    report = json.loads(path.read_text())
    changes = dict(skipped=(state, 'tests', dict(passed=22, failed=0, skipped=1)), pytest=(state, 'pytest_exit_code', 1),
                   old_session=(report, 'parent_session_id', 'old'), child_block=(state['journals'][0], 'session_blocked', True),
                   child_pending=(state['journals'][0], 'restoration_pending', True),
                   foreign_summary=(state['journals'][0], 'run_id', 'foreign'),
                   unknown_commit=(report['transaction_progress'][-1], 'outcome', 'UNKNOWN'))
    if fault == 'missing_audit':
        (path.parent / 'transactions' / 'transaction-one.json').unlink()
    else:
        target, key, value = changes[fault]
        target[key] = value
    path.write_text(json.dumps(report))
    with pytest.raises(HarnessSafetyError):
        runner.require_stage_b_results(state)


@pytest.mark.parametrize('fault', ['before', 'after', 'real'])
def test_application_commit_audit_distinguishes_injected_and_real_unknown(tmp_path, fault):
    """@description: Permit reviewed client fault cases while poisoning real server acknowledgement loss.
    @param tmp_path: Disposable application audit directory.
    @param fault: Known client injection or genuine synthetic driver failure.
    @return: None.
    """
    connection = MagicMock()
    if fault == 'real':
        connection.commit.side_effect = pymysql.OperationalError(2013, 'fake-private')
    adapter = FaultConnection(connection, {'commit': fault} if fault != 'real' else {}, str(tmp_path))
    with pytest.raises((InjectedClientFault, pymysql.OperationalError)):
        adapter.commit()
    adapter.rollback()
    adapter.close()
    report = json.loads(adapter.audit_path.read_text())
    assert report['blocked'] is (fault == 'real')
    if fault == 'real':
        with pytest.raises(HarnessSafetyError):
            runtime.require_worker_audits(str(tmp_path), active_allowed=True)
    else:
        runtime.require_worker_audits(str(tmp_path))
    assert connection.commit.call_count == int(fault != 'before')


def test_known_injection_cannot_swallow_real_commit_failure(monkeypatch):
    """@description: Keep the existing unknown-commit scenario from retrying a genuine database uncertainty.
    @param monkeypatch: Synthetic repository and observer replacements.
    @return: None.
    """
    from bot.database.submission_transaction import CommitOutcomeUnknown
    scope = RunScope()
    repository = MagicMock()
    repository.accept.side_effect = CommitOutcomeUnknown('fake-private')
    monkeypatch.setattr(scenarios, 'repository', MagicMock(return_value=repository))
    with pytest.raises(CommitOutcomeUnknown):
        scenarios.uncertain_case(None, scope, 'accept:before')
    assert repository.accept.call_count == 1


@pytest.mark.parametrize('failure', ['unknown_rows', 'commit', 'scenario'])
def test_supervised_failure_never_emergency_writes_or_retries_cleanup(monkeypatch, failure):
    """@description: Preserve committed rows and stop management writes on every supervised failure.
    @param monkeypatch: Existing harness unit seams, never a real connection.
    @param failure: Unsafe inventory, unknown cleanup commit or already failed scenario.
    @return: None.
    """
    scope = RunScope()
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    harness.supervised_session_id = str(uuid4())
    rows = owned_inventory(scope)
    if failure == 'unknown_rows':
        rows['users'] += ({'discord_user_id': 999, 'credit_balance': 0},)
    if failure == 'commit':
        harness.connection.commit.side_effect = pymysql.OperationalError(2013, 'fake-private')
    harness.failed = failure == 'scenario'
    monkeypatch.setattr(runtime, 'lock_inventory', lambda connection: rows)
    emergency = MagicMock()
    monkeypatch.setattr(runtime.MySQLHarness, 'disable_only', emergency)
    with pytest.raises((HarnessSafetyError, pymysql.OperationalError)):
        harness.finish()
    emergency.assert_not_called()
    assert harness.connection.commit.call_count == int(failure == 'commit')
    if failure != 'commit':
        assert not any(call.args[0].startswith('DELETE') for call in cursor.execute.call_args_list)
    assert 'STOP_MANUAL_RECOVERY_REQUIRED' in statuses and harness.failed


def test_stage_only_executor_never_provisions_and_stops_after_window_failure(window_context, monkeypatch):
    """@description: Keep Stage A and pytest unreachable after a partial FK transition.
    @param window_context: Synthetic parent boundary.
    @param monkeypatch: Executor sequencing spies.
    @return: None.
    """
    context, state, metadata = window_context
    monkeypatch.setattr(runner, 'inspect_stage_b', lambda context, state: None)
    transition = MagicMock(side_effect=HarnessSafetyError('PARTIAL_DDL_UNKNOWN'))
    tests, stage_a = MagicMock(), MagicMock(side_effect=AssertionError('Provisioning forbidden'))
    monkeypatch.setattr(runner, 'stage_b_transition', transition)
    monkeypatch.setattr(runner, 'stage_b_run', tests)
    monkeypatch.setattr(runner, 'stage_a', stage_a)
    with pytest.raises(HarnessSafetyError):
        runner.execute_stage_b(context, state)
    assert transition.call_count == 1
    tests.assert_not_called()
    stage_a.assert_not_called()


def test_final_journal_failure_cannot_report_unblocked(tmp_path, monkeypatch, capsys):
    """@description: Leave a failed durable finalization blocked even after observed data restoration.
    @param tmp_path: Synthetic journal destination.
    @param monkeypatch: Last audit-write failure injection.
    @param capsys: Sanitized output capture.
    @return: None.
    """
    state = dict(baseline_restored=True, previous_session_blocked=True, session_journal=str(tmp_path / 'session.json'))
    monkeypatch.setattr(runner, 'stage_b_save', MagicMock(side_effect=OSError('fake-private')))
    assert runner.emit_stage_b(state) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['session_blocked'] and report['previous_session_blocked']
    assert not report['baseline_restored'] and 'fake-private' not in json.dumps(report)


def test_new_runtime_helpers_follow_rule_one():
    """@description: Enforce source length, nesting and documentation on all changed harness runtime helpers.
    @param: none
    @return: None.
    """
    modules = (runner, runtime, cases, scenarios)
    from tests import batch_b_mysql_faults
    for module in (*modules, batch_b_mysql_faults):
        path = Path(module.__file__)
        source = path.read_text(encoding='utf-8')
        for function in ast.walk(ast.parse(source)):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert _implementation_lines(function, source.splitlines()) <= 25, (path.name, function.name)
                assert _control_depth(function) <= 3, (path.name, function.name)
                _assert_function_documentation(function, path)


@pytest.mark.parametrize('outcome', ['PENDING', 'UNKNOWN', 'ACKNOWLEDGED'])
def test_same_session_cannot_retry_any_ddl_direction(window_context, outcome):
    """@description: Refuse a second DDL attempt even when the first failure left original metadata unchanged.
    @param window_context: Parent boundary and connection spies.
    @param outcome: Previous attempt state that cannot be cleared automatically.
    @return: None.
    """
    context, state, metadata = window_context
    state['cascade_ddl_outcome'] = outcome
    with pytest.raises(HarnessSafetyError, match='RETRY_REFUSED'):
        runner.stage_b_transition(context, True, state)
    context['dba'].cursor.assert_not_called()


def test_restore_requires_verified_tests_and_pytest_cannot_repeat(window_context):
    """@description: Prevent internal orchestration from bypassing complete results or rerunning pytest.
    @param window_context: New parent session.
    @return: None.
    """
    context, state, metadata = window_context
    with pytest.raises(HarnessSafetyError, match='COMPLETE_TESTS'):
        runner.stage_b_transition(context, False, state)
    state['b_started'] = True
    with pytest.raises(HarnessSafetyError, match='PYTEST_RETRY'):
        runner.stage_b_run(context, state)
    context['dba'].cursor.assert_not_called()


@pytest.mark.parametrize('events', [(), ({'EVENT_SCHEMA': 'another_schema', 'EVENT_NAME': 'unsafe'},)])
def test_cross_schema_scheduled_event_blocks_window(monkeypatch, events):
    """@description: Do not infer background quiescence from only the current schema's event list.
    @param monkeypatch: DBA metadata substitute.
    @param events: Globally enabled event metadata.
    @return: None.
    """
    from tests import batch_b_acceptance_setup as setup
    monkeypatch.setattr(setup, 'query', MagicMock(return_value=events))
    if events:
        with pytest.raises(HarnessSafetyError, match='ENABLED_EVENT'):
            runner.require_stage_b_events_disabled(MagicMock())
    else:
        runner.require_stage_b_events_disabled(MagicMock())


@pytest.mark.parametrize('failure', ['dba', 'harness', 'execute', 'none'])
def test_stage_only_main_erases_credentials_and_never_provisions(header, tmp_path, monkeypatch, capsys, failure):
    """@description: Keep private input, partial ownership and final session status bounded on every exit.
    @param header: Explicit new-session authorization.
    @param tmp_path: Disposable new-session journal root.
    @param monkeypatch: Complete offline connection and executor replacement.
    @param capsys: Sanitized final report capture.
    @param failure: Rejected boundary or successful synthetic completion.
    @return: None.
    """
    monkeypatch.setattr(runner.tempfile, 'gettempdir', lambda: str(tmp_path))
    monkeypatch.setattr(runner, 'connect_stage_b', lambda context, credentials, state: fake_connect(context, credentials, state, failure))
    monkeypatch.setattr(runner, 'execute_stage_b', lambda context, state: fake_execute(state, failure))
    monkeypatch.setattr(runner, 'stage_a', MagicMock(side_effect=AssertionError('Stage A forbidden')))
    code = runner.stage_b_main(private_stream(header))
    report = json.loads(capsys.readouterr().out)
    assert code == int(failure != 'none') and report['session_blocked'] is (failure != 'none')
    assert report['previous_session_blocked'] is True and report['a_completed'] is False
    assert 'fake-dba' not in json.dumps(report) and 'fake-private' not in json.dumps(report)
    assert not any(name.startswith(('TEST_DB_', 'TEST_HARNESS_DB_', 'TEST_DBA_DB_')) for name in runner.os.environ)


def fake_connect(context: dict, credentials: list, state: dict, failure: str) -> None:
    """@description: Model partial connection ownership without using a network driver.
    @param context: Private parent resource map.
    @param credentials: Synthetic receive buffer to erase on success or failure.
    @param state: New-session safe report.
    @param failure: Boundary to reject.
    @return: None.
    """
    if failure == 'dba':
        raise pymysql.OperationalError(1045, 'fake-private-password')
    context['dba'] = MagicMock()
    if failure == 'harness':
        raise pymysql.OperationalError(1045, 'fake-private-password')
    context['harness'] = MagicMock()
    credentials.clear()


def fake_execute(state: dict, failure: str) -> None:
    """@description: Model only the final guarded outcome, never actual DML or DDL.
    @param state: New-session report.
    @param failure: Optional failure at the execution boundary.
    @return: None.
    """
    if failure == 'execute':
        raise HarnessSafetyError('INJECTED_EXECUTION_FAILURE')
    state['baseline_restored'] = True


@pytest.mark.parametrize('drift', ['business_rows', 'counters', 'registry', 'schema', 'application', 'harness'])
def test_original_preflight_refuses_drift_before_transition(window_context, monkeypatch, drift):
    """@description: Check original inventory, zero counters, reviewed Migration and both existing account logins.
    @param window_context: Synthetic original supervisor state.
    @param monkeypatch: Specific existing preflight seam injection.
    @param drift: Original prerequisite to invalidate.
    @return: None.
    """
    from tests import batch_b_acceptance_setup as setup
    from tests import mysql_safety
    context, state, metadata = window_context
    rows, original = context['baseline']
    monkeypatch.setattr(repair, 'ORIGINAL_SCHEMA', digest(original))
    monkeypatch.setattr(setup, 'verify_metadata', MagicMock(return_value=(rows, original)))
    monkeypatch.setattr(setup, 'verify_application_login', MagicMock(side_effect=HarnessSafetyError('APP_REJECTED') if drift == 'application' else None))
    monkeypatch.setattr(setup, 'verify_harness_login', MagicMock(side_effect=HarnessSafetyError('HARNESS_REJECTED') if drift == 'harness' else None))
    monkeypatch.setattr(mysql_safety, 'validate_test_constraints', MagicMock())
    context.update(application=None, management=None)
    changes = dict(business_rows=(rows, 'users', ({'discord_user_id': 999},)),
                   counters=(rows['jb2_control'][0], 'revision', 1),
                   registry=(rows['jb2_migrations'][0], 'checksum', 'unapproved'), schema=(original, 'users', 'changed'))
    if drift in changes:
        target, key, value = changes[drift]
        target[key] = value
    with pytest.raises(HarnessSafetyError):
        runner.inspect_stage_b(context, state)
    context['dba'].cursor.return_value.__enter__.return_value.execute.assert_not_called()


def test_supervised_success_uses_manifest_pk_deletes_and_acknowledged_cleanup(monkeypatch):
    """@description: Retain existing ownership guards and one atomic cleanup commit in the new session mode.
    @param monkeypatch: Existing harness fake inventory seams.
    @return: None.
    """
    scope = RunScope()
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    harness.supervised_session_id = str(uuid4())
    monkeypatch.setattr(runtime, 'lock_inventory', MagicMock(side_effect=[owned_inventory(scope), empty_inventory()]))
    harness.finish()
    deletes = [call for call in cursor.execute.call_args_list if call.args[0].startswith('DELETE')]
    assert deletes and all(' WHERE ' in call.args[0] and '%s' in call.args[0] and call.args[1] for call in deletes)
    assert harness.transaction_progress == [dict(phase='CLEANUP', outcome='ACKNOWLEDGED')]
    assert not harness.enabled and 'RESTORED_VERIFIED' in statuses


def test_existing_entry_dispatches_stage_only_without_default_main(monkeypatch):
    """@description: Keep the new CLI flag from falling through to A+B provisioning or Recovery.
    @param monkeypatch: Arguments and explicit mode handlers.
    @return: None.
    """
    selected = MagicMock(return_value=17)
    default = MagicMock(side_effect=AssertionError('A+B forbidden'))
    monkeypatch.setattr(runner, 'stage_b_main', selected)
    monkeypatch.setattr(runner, 'main', default)
    monkeypatch.setattr(runner.sys, 'argv', ['runner', '--stage-b-only'])
    assert runner.entry() == 17
    selected.assert_called_once_with(runner.sys.stdin)
    default.assert_not_called()


def test_create_harness_binds_manifests_to_one_new_parent(window_context):
    """@description: Register independent existing RunScopes before any test database operation.
    @param window_context: Fresh parent journal and synthetic pinned definitions.
    @return: None.
    """
    context, state, metadata = window_context
    state['runs'] = []
    before = MagicMock()
    supervision = dict(directory=context['directory'], state=state, session_id=state['session_id'],
                       definitions=dict(context['window'].cascade), baseline=context['baseline'][0], before_run=before)
    first = runtime.create_harness(None, None, supervision)
    second = runtime.create_harness(None, None, supervision)
    assert first.scope.run_id != second.scope.run_id and before.call_count == 2
    assert first.supervised_session_id == state['session_id'] and first.expected_baseline == context['baseline'][0]
    assert first.expected_definitions == dict(context['window'].cascade)
    assert Path(first.scope.audit_directory).is_dir()
    assert [item['run_id'] for item in state['runs']] == [first.scope.run_id, second.scope.run_id]
    assert first.connection is None and second.connection is None


def test_reused_parent_session_fails_before_connector(header, tmp_path, monkeypatch, capsys):
    """@description: Refuse restart of a single-use parent even if no database work was previously done.
    @param header: Same explicit session UUID used twice.
    @param tmp_path: Disposable reservation directory.
    @param monkeypatch: Connection trap and temp root.
    @param capsys: Safe final report capture.
    @return: None.
    """
    monkeypatch.setattr(runner.tempfile, 'gettempdir', lambda: str(tmp_path))
    runner.reserve_stage_b_session(header, {})
    connector = MagicMock(side_effect=AssertionError('No connect after duplicate reservation'))
    monkeypatch.setattr(runner, 'connect_stage_b', connector)
    assert runner.stage_b_main(private_stream(header)) == 1
    report = json.loads(capsys.readouterr().out)
    connector.assert_not_called()
    assert report['session_blocked'] and report['error_type'] == 'FileExistsError'


def test_executor_runs_existing_suite_between_four_ddl_and_final_restore(window_context, monkeypatch):
    """@description: Prove the end-to-end parent sequence restores exact original schema after existing tests succeed.
    @param window_context: Real transition helper with fake metadata and connection objects.
    @param monkeypatch: Original preflight and test executor substitutes only.
    @return: None.
    """
    context, state, metadata = window_context
    fake_ddl(context, metadata)
    monkeypatch.setattr(runner, 'inspect_stage_b', lambda context, state: None)
    def run(context: dict, state: dict) -> None:
        """@description: Assert tests cannot start outside the exact temporary-only window.
        @param context: Private parent lifecycle state.
        @param state: Safe test completion fact.
        @return: None.
        """
        assert metadata['definitions'] == dict(context['window'].cascade)
        assert len(metadata['attempts']) == 2
        state['tests_verified'] = True
    monkeypatch.setattr(runner, 'stage_b_run', run)
    final_read = MagicMock()
    monkeypatch.setattr(runner, 'verify_restoration', final_read)
    context['management'] = None
    runner.execute_stage_b(context, state)
    assert len(metadata['attempts']) == 4 and state['baseline_restored'] is True
    assert metadata['definitions'] == context['baseline'][1]
    final_read.assert_called_once_with(None, context['baseline'], state)


@pytest.mark.parametrize('failure', ['dba_connect', 'harness_connect', 'dba_session', 'harness_session'])
def test_stage_only_connection_failure_keeps_precise_role(monkeypatch, failure):
    """@description: Keep authentication and post-login lock failures attributable without raw credential text.
    @param monkeypatch: Existing connector and session-setup replacements.
    @param failure: One exact new-session boundary to fail.
    @return: None.
    """
    from bot.database import connection as database_boundary
    dba, management = MagicMock(), MagicMock()
    error = pymysql.OperationalError(1045, 'fake-private-password')
    connector, verifier = MagicMock(return_value=dba), MagicMock(return_value=management)
    setup = MagicMock(side_effect=[11, 12])
    operations = dict(dba_connect=connector, harness_connect=verifier, dba_session=setup, harness_session=setup)
    operations[failure].side_effect = [11, error] if failure == 'harness_session' else error
    monkeypatch.setattr(database_boundary, 'open_connection', connector)
    monkeypatch.setattr(runner, 'verified_connection', verifier)
    monkeypatch.setattr(repair, 'prepare_session', setup)
    context, state = dict(header={'dba_user': 'root'}), {}
    with pytest.raises(pymysql.OperationalError):
        runner.connect_stage_b(context, ['fake-dba', 'fake-app', 'fake-harness'], state)
    labels = dict(dba_connect='B_ONLY_DBA_CONNECT', harness_connect='B_ONLY_HARNESS_CONNECT',
                  dba_session='B_ONLY_DBA_SESSION', harness_session='B_ONLY_HARNESS_SESSION')
    assert state['failure_step'] == labels[failure] and state['mysql_error_number'] == 1045
    assert 'fake-private' not in json.dumps(state)


def test_stage_only_connection_success_keeps_roles_private_and_clears_phase(monkeypatch):
    """@description: Verify real orchestration wiring after all mocked connection and session boundaries succeed.
    @param monkeypatch: Existing low-level connection and session setup replacements.
    @return: None.
    """
    from bot.database import connection as database_boundary
    connector, verifier = MagicMock(), MagicMock()
    monkeypatch.setattr(database_boundary, 'open_connection', connector)
    monkeypatch.setattr(runner, 'verified_connection', verifier)
    monkeypatch.setattr(repair, 'prepare_session', MagicMock(side_effect=[11, 12]))
    credentials = ['fake-dba', 'fake-app', 'fake-harness']
    context, state = dict(header={'dba_user': 'root'}), {}
    runner.connect_stage_b(context, credentials, state)
    assert credentials == [] and 'current_step' not in state
    assert context['dba_id'] == 11 and context['harness_id'] == 12
    assert connector.call_args.args[0].password == 'fake-dba'
    assert verifier.call_args.args[0].password == 'fake-harness'
    assert context['application'].user == 'jukebox101_test_app'
    assert context['application'].password == 'fake-app'
    assert 'fake-' not in json.dumps(state)
