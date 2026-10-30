"""Check the private bootstrap protocol and safety gates without database connections.

Synthetic credentials and spies exercise provisioning order, cleanup and actual-test dispatch without claiming MySQL acceptance.
"""

import ast
import io
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import pymysql
from tests import batch_b_acceptance as runner
from tests import batch_b_acceptance_setup as setup
from tests.batch_b_harness_contract import HarnessSafetyError

UUID = '11111111-2222-4333-8444-555555555555'


@pytest.mark.parametrize('code,step', [(2003, 'A_DBA_CONNECT'), (1045, 'A_DBA_CONNECT'),
    (1044, 'A_DBA_CONNECT'), (1142, 'A_EXISTING_ACCOUNTS'), (1227, 'A_DBA_IDENTITY_AND_VISIBILITY'),
    (1054, 'A_SCHEMA_AND_BASELINE_METADATA'), (1146, 'A_CONSTRAINT_METADATA'),
    (2006, 'A_APPLICATION_LOGIN_AND_PRIVILEGES'), (2013, 'A_EXCLUSIVE_LOCK')])
def test_driver_diagnostics_are_numeric_and_step_only(code: int, step: str) -> None:
    """@description: Distinguish synthetic driver failures without serializing their sensitive message text.
    @param code: Representative synthetic MySQL error number, not a diagnosis of the real failure.
    @param step: Fixed internal operation label.
    @return: None.
    """
    error = pymysql.OperationalError(code, 'fake-private-password sql-parameters connection-string')
    state = {}
    with pytest.raises(pymysql.OperationalError) as caught:
        setup.stage_call(state, step, MagicMock(side_effect=error))
    assert caught.value is error
    assert state == dict(current_step=step, failure_step=step, error_type='OperationalError', mysql_error_number=code)
    assert 'fake-private' not in json.dumps(state)


@pytest.mark.parametrize('error', [pymysql.OperationalError('fake-private'), pymysql.OperationalError(True, 'fake'),
    pymysql.OperationalError(-1, 'fake'), pymysql.OperationalError(65536, 'fake'), RuntimeError(1045, 'fake')])
def test_invalid_driver_number_is_not_coerced(error: BaseException) -> None:
    """@description: Never stringify error arguments or mistake a non-driver value for MySQL evidence.
    @param error: Synthetic malformed exception arguments.
    @return: None.
    """
    state = {}
    setup.record_error(state, error)
    assert 'mysql_error_number' not in state
    assert 'fake' not in json.dumps(state)


@pytest.mark.parametrize('failure_step', ['A_DBA_IDENTITY_AND_VISIBILITY', 'A_EXCLUSIVE_LOCK',
    'A_SCHEMA_AND_BASELINE_METADATA', 'A_EXISTING_ACCOUNTS', 'A_HARNESS_ABSENCE', 'A_CONSTRAINT_METADATA'])
def test_precheck_preserves_specific_failure_step(monkeypatch: pytest.MonkeyPatch, failure_step: str) -> None:
    """@description: Confirm actual precheck wiring labels each failing read-only operation before provisioning.
    @param monkeypatch: Offline metadata operation injection.
    @param failure_step: Specific wired precheck substep to fail.
    @return: None.
    """
    original = setup.stage_call
    error = pymysql.OperationalError(1142, 'fake-private-query')
    def injected(state: dict, step: str, operation: object, *arguments: object) -> object:
        """@description: Inject only the chosen metadata boundary, leaving diagnostic capture real.
        @param state: Shared report.
        @param step: Actual precheck label.
        @param operation: Replaced operation, never executed.
        @param arguments: Private synthetic arguments.
        @return: Minimal safe response or original diagnostic exception.
        """
        response = ({'owned': 1},) if step == 'A_EXCLUSIVE_LOCK' else ()
        action = MagicMock(side_effect=error) if step == failure_step else lambda: response
        return original(state, step, action)
    monkeypatch.setattr(setup, 'stage_call', injected)
    state = {}
    with pytest.raises(pymysql.OperationalError):
        setup.precheck(MagicMock(), 'root', UUID, state)
    assert state['failure_step'] == failure_step
    assert state['mysql_error_number'] == 1142


def test_connection_failure_reaches_sanitized_main_report(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """@description: Exercise the real runner boundary on a failed connect without making a socket call.
    @param monkeypatch: Synthetic stdin and failing database factory.
    @param capsys: Final public JSON capture.
    @return: None.
    """
    monkeypatch.setattr(runner.sys, 'stdin', protocol())
    monkeypatch.setattr(setup, 'open_connection', MagicMock(side_effect=pymysql.OperationalError(1045, 'fake-private-secret')))
    provision = MagicMock()
    monkeypatch.setattr(setup, 'provision', provision)
    assert runner.main() == 1
    output = capsys.readouterr().out
    report = json.loads(output)
    assert (report['stage'], report['failure_step'], report['mysql_error_number']) == ('A_PRECHECK_FAILED', 'A_DBA_CONNECT', 1045)
    assert report['a_completed'] is False and report['b_started'] is False
    assert 'fake-private-secret' not in output
    provision.assert_not_called()


def test_close_error_preserves_primary_diagnostics() -> None:
    """@description: Report secondary close failure without masking the original metadata error.
    @param: none
    @return: None.
    """
    state = dict(current_step='A_EXISTING_ACCOUNTS')
    setup.record_error(state, pymysql.OperationalError(1142, 'fake-primary-secret'))
    connection = MagicMock()
    connection.close.side_effect = pymysql.OperationalError(2013, 'fake-close-secret')
    setup.close_dba(connection, state)
    assert (state['failure_step'], state['mysql_error_number']) == ('A_EXISTING_ACCOUNTS', 1142)
    assert state['dba_close_mysql_error_number'] == 2013
    assert state['dba_close_failed'] is True
    assert 'secret' not in json.dumps(state)


def protocol(**changes: object) -> io.StringIO:
    """@description: Supply synthetic input identical to the private anonymous pipe framing.
    @param changes: Nonsecret header overrides.
    @return: In-memory input with fake distinct credentials.
    """
    header = dict(dba_user='root', server_uuid=UUID, authorization='BATCH_B_A_PLUS_B')
    header.update(changes)
    return io.StringIO('\n'.join(json.dumps(value) for value in (header, 'fake-dba', 'fake-app', 'fake-harness')))


@pytest.mark.parametrize('key,value', [('authorization', 'no'), ('dba_user', 'root;DROP'),
    ('dba_user', 'jukebox101_test_app'), ('dba_user', 'jukebox101_test_migrator'),
    ('dba_user', 'jukebox101_test_harness'), ('server_uuid', 'bad')])
def test_protocol_rejects_before_connection(key: str, value: str) -> None:
    """@description: Prevent untrusted headers from changing destination, authorization or roles.
    @param key: Header field under test.
    @param value: Invalid synthetic header value.
    @return: None.
    """
    with pytest.raises((HarnessSafetyError, ValueError)):
        runner.receive(protocol(**{key: value}))


def test_protocol_and_redacted_profiles() -> None:
    """@description: Retain special characters through JSON while redacting settings representation.
    @param: none
    @return: None.
    """
    header, credentials = runner.receive(protocol())
    assert header['server_uuid'] == UUID
    assert credentials == ['fake-dba', 'fake-app', 'fake-harness']
    profile = runner.settings('root', 'fake-secret')
    assert (profile.host, profile.port, profile.database) == ('127.0.0.1', 3306, 'jukebox101_test')
    assert 'fake-secret' not in repr(profile)


@pytest.mark.parametrize('credential', ['', None, 3, 'x' * 4097, '\x00'])
def test_invalid_private_credential_rejected(credential: object) -> None:
    """@description: Bound input and reject missing credentials without echoing their values.
    @param credential: Synthetic malformed private value.
    @return: None.
    """
    stream = protocol()
    lines = stream.getvalue().splitlines()
    lines[1] = json.dumps(credential)
    with pytest.raises(HarnessSafetyError):
        runner.receive(io.StringIO('\n'.join(lines)))


def test_grant_artifact_is_exact_reviewed_set() -> None:
    """@description: Refuse arbitrary SQL execution by loading only the fixed 25 least-privilege statements.
    @param: none
    @return: None.
    """
    statements = setup.grant_statements()
    assert len(statements) == 25
    assert all('GRANT OPTION' not in sql and '*.*' not in sql for sql in statements)
    assert len(setup.reviewed_foreign_keys()) > 20


def test_reviewed_foreign_keys_include_commented_submissions() -> None:
    """@description: Prevent a semicolon inside the actual submissions comment from hiding both FKs.
    @param: none
    @return: None.
    """
    expected = setup.reviewed_foreign_keys()  # [complete reviewed graph]: Counts include compound FK positions.
    assert len(expected) == 29
    assert {edge for edge in expected if edge[0] == 'submissions'} == {
        ('submissions', 'discord_user_id', 1, 'jukebox101_test', 'users', 'discord_user_id'),
        ('submissions', 'song_id', 1, 'jukebox101_test', 'songs', 'song_id')}
    assert {edge for edge in expected if edge[0] == 'daily_dispatches' and edge[4] == 'recommendations'} == {
        ('daily_dispatches', 'recommendation_id', 1, 'jukebox101_test', 'recommendations', 'recommendation_id'),
        ('daily_dispatches', 'discord_user_id', 2, 'jukebox101_test', 'recommendations', 'discord_user_id'),
        ('daily_dispatches', 'song_id', 3, 'jukebox101_test', 'recommendations', 'song_id')}


@pytest.mark.parametrize('comment', ['-- harmless comment', '-- comment; another; fragment',
    '-- CREATE TABLE IF NOT EXISTS wrong_table (; misleading header;'])
def test_foreign_key_boundaries_ignore_comment_semicolons(monkeypatch: pytest.MonkeyPatch, comment: str) -> None:
    """@description: Keep line-comment formatting out of the expected schema contract.
    @param monkeypatch: Synthetic read-only SQL source substitution.
    @param comment: Harmless or misleading line comment inside a reviewed CREATE statement.
    @return: None.
    """
    source = 'CREATE TABLE IF NOT EXISTS sample (\n' + comment + '\n'
    source += 'CONSTRAINT sample_fk FOREIGN KEY (owner_id) REFERENCES users (discord_user_id));'
    monkeypatch.setattr(Path, 'read_text', lambda *args, **kwargs: source)
    assert setup.reviewed_foreign_keys() == {('sample', 'owner_id', 1, 'jukebox101_test', 'users', 'discord_user_id')}


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'column_order', 'target_table', 'schema', 'case'])
def test_foreign_key_drift_still_blocks_real_differences(monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    """@description: Ensure the comment fix never weakens actual FK equality checks.
    @param monkeypatch: Offline metadata-only cursor responses.
    @param mutation: Structural mismatch that must still fail closed.
    @return: None.
    """
    actual = set(setup.reviewed_foreign_keys())
    edge = ('submissions', 'song_id', 1, 'jukebox101_test', 'songs', 'song_id')
    actual.remove(edge)
    replacements = dict(extra=(*edge[:-1], 'other_column'), column_order=(edge[0], edge[1], 2, *edge[3:]),
        target_table=(*edge[:4], 'users', edge[5]), schema=(*edge[:3], 'jukebox101', *edge[4:]),
        case=('SUBMISSIONS', *edge[1:]))
    if mutation != 'missing':
        actual.add(replacements[mutation])
    if mutation == 'extra':
        actual.add(edge)
    cursor = metadata_cursor(monkeypatch, actual)
    with pytest.raises(HarnessSafetyError, match='^FOREIGN_KEY_DRIFT$'):
        setup.verify_metadata(cursor)


def metadata_cursor(monkeypatch: pytest.MonkeyPatch, edges: set) -> MagicMock:
    """@description: Supply synthetic information-schema shape while keeping FK comparison real.
    @param monkeypatch: Baseline helpers replaced to avoid any external operation.
    @param edges: Explicit actual metadata edges for this offline test.
    @return: Cursor with key-column and incoming-schema responses.
    """
    names = ('TABLE_NAME', 'COLUMN_NAME', 'ORDINAL_POSITION', 'REFERENCED_TABLE_SCHEMA',
             'REFERENCED_TABLE_NAME', 'REFERENCED_COLUMN_NAME')
    monkeypatch.setattr(setup, 'schema_fingerprints', lambda _: {})
    monkeypatch.setattr(setup, 'read_inventory', lambda _: {})
    monkeypatch.setattr(setup, 'require_baseline', lambda _: None)
    cursor = MagicMock()
    cursor.fetchall.side_effect = [tuple(dict(zip(names, edge)) for edge in edges), ()]
    return cursor


def test_reviewed_metadata_matches_without_row_order_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Accept a complete synthetic metadata graph regardless of MySQL row return order.
    @param monkeypatch: Offline metadata-only fixture.
    @return: None.
    """
    cursor = metadata_cursor(monkeypatch, setup.reviewed_foreign_keys())
    assert setup.verify_metadata(cursor) == ({}, {})


@pytest.mark.parametrize('statement,allowed', [
    ('GRANT ALL PRIVILEGES ON *.* TO root@localhost WITH GRANT OPTION', True),
    ('GRANT SELECT, TRIGGER, CREATE USER ON *.* TO root@localhost WITH GRANT OPTION', True),
    ('GRANT SELECT ON *.* TO user@localhost WITH GRANT OPTION', False),
    ('GRANT ALL PRIVILEGES ON jukebox101_test.* TO root@localhost WITH GRANT OPTION', False),
    ('GRANT ALL PRIVILEGES ON *.* TO root@localhost', False)])
def test_full_metadata_visibility_required(statement: str, allowed: bool) -> None:
    """@description: Reject metadata invisibility rather than interpreting hidden objects as absent.
    @param statement: Synthetic non-authentication grant metadata.
    @param allowed: Expected visibility decision.
    @return: None.
    """
    assert setup.full_metadata_authority(statement) is allowed


def test_precheck_failure_closes_without_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Stop before provisioning when independent server identity fails.
    @param monkeypatch: Offline function replacement.
    @return: None.
    """
    connection = MagicMock()
    monkeypatch.setattr(setup, 'open_connection', lambda _: connection)
    monkeypatch.setattr(setup, 'check_dba', MagicMock(side_effect=HarnessSafetyError('UUID_MISMATCH')))
    provision = MagicMock()
    monkeypatch.setattr(setup, 'provision', provision)
    state = dict(stage='A_PRECHECK_FAILED')
    with pytest.raises(HarnessSafetyError):
        setup.stage_a(runner.settings('root', 'fake-dba'), None, None, UUID, state)
    provision.assert_not_called()
    connection.close.assert_called_once()
    connection.cursor.return_value.__enter__.return_value.execute.assert_not_called()


def test_partial_provision_failure_relocks_only_new_account(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Preserve nontransactional setup evidence without dropping or recreating accounts.
    @param monkeypatch: Offline cursor adapter.
    @return: None.
    """
    connection = MagicMock()
    state = dict(stage='A_PROVISION_FAILED', account_created=True)
    setup.relock_created_account(connection, state)
    connection.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
        'ALTER USER ' + setup.ACCOUNT + ' ACCOUNT LOCK')
    assert state['harness_relocked'] is True
    connection.reset_mock()
    setup.relock_created_account(connection, dict(stage='A_PROVISION_FAILED'))
    connection.cursor.assert_not_called()


def test_grant_adapter_uses_locked_target() -> None:
    """@description: Prevent the DBA's broad grants from being mistaken for harness privileges.
    @param: none
    @return: None.
    """
    cursor = MagicMock()
    setup.GrantCursor(cursor).execute('SHOW GRANTS FOR CURRENT_USER()')
    cursor.execute.assert_called_once_with('SHOW GRANTS FOR ' + setup.ACCOUNT, ())


@pytest.mark.parametrize('failure', ['grants', 'extra_authority', 'baseline', 'none'])
def test_provision_unlocks_only_after_all_gates(monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    """@description: Enforce lock and bound-password sequencing across partial setup failures.
    @param monkeypatch: Offline metadata replacement.
    @param failure: Gate that fails or successful synthetic sequence.
    @return: None.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    monkeypatch.setattr(setup, 'account_snapshot', lambda _: 'unchanged')
    monkeypatch.setattr(setup, 'verify_locked_account', lambda _: None)
    monkeypatch.setattr(setup, 'verify_metadata', lambda _: 'changed' if failure == 'baseline' else ('rows', 'schema'))
    monkeypatch.setattr(setup, 'verify_harness_grants_as_dba', MagicMock(side_effect=HarnessSafetyError('GRANTS') if failure == 'grants' else None))
    monkeypatch.setattr(setup, 'verify_extra_authority', MagicMock(side_effect=HarnessSafetyError('EXTRA') if failure == 'extra_authority' else None))
    state = {}
    if failure == 'none':
        setup.provision(connection, 'fake-private-secret', ('rows', 'schema'), state)
    else:
        with pytest.raises(HarnessSafetyError):
            setup.provision(connection, 'fake-private-secret', ('rows', 'schema'), state)
    calls = cursor.execute.call_args_list
    assert calls[0].args == ('CREATE USER ' + setup.ACCOUNT + ' IDENTIFIED BY %s ACCOUNT LOCK', ('fake-private-secret',))
    assert all('fake-private-secret' not in call.args[0] for call in calls)
    assert any('ACCOUNT UNLOCK' in call.args[0] for call in calls) is (failure == 'none')
    assert all('DROP ' not in call.args[0] and 'migrator' not in call.args[0] for call in calls)


def test_existing_harness_prevents_creation(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Never reset a preexisting harness password or grants during bootstrap retry.
    @param monkeypatch: Offline precheck query responses.
    @return: None.
    """
    connection = MagicMock()
    monkeypatch.setattr(setup, 'check_dba', lambda *args: None)
    monkeypatch.setattr(setup, 'verify_metadata', lambda _: ('rows', 'schema'))
    monkeypatch.setattr(setup, 'account_snapshot', lambda _: 'unchanged')
    monkeypatch.setattr(setup, 'query', lambda cursor, sql, parameters=(): ({'owned': 1},) if 'GET_LOCK' in sql else ({'User': 'existing'},))
    with pytest.raises(HarnessSafetyError, match='HARNESS_ALREADY_EXISTS'):
        setup.precheck(connection, 'root', UUID)
    connection.cursor.return_value.__enter__.return_value.execute.assert_not_called()


def test_private_profiles_are_child_process_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Enable only reviewed opt-ins and never install DBA credentials in environment.
    @param monkeypatch: Isolated process environment.
    @return: None.
    """
    monkeypatch.setattr(runner.os, 'environ', {})
    runner.install_profiles(runner.settings('jukebox101_test_app', 'fake-app'), runner.settings(setup.HARNESS_USER, 'fake-harness'))
    assert runner.os.environ['TEST_DB_PASSWORD'] == 'fake-app'
    assert runner.os.environ['TEST_HARNESS_DB_PASSWORD'] == 'fake-harness'
    assert not any(key.startswith('TEST_DBA_') for key in runner.os.environ)
    assert {key for key, value in runner.os.environ.items() if key.startswith('JUKEBOX_RUN_') and value == '1'} == {runner.RUN_SWITCH, runner.HARNESS_SWITCH}


def test_main_sanitizes_and_clears_on_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """@description: Never expose driver messages or retain environment credential adapters after failure.
    @param monkeypatch: Synthetic input and exception injection.
    @param capsys: Captured sanitized terminal stream.
    @return: None.
    """
    monkeypatch.setattr(runner.sys, 'stdin', protocol())
    monkeypatch.setenv('TEST_DB_PASSWORD', 'fake-private-value')
    monkeypatch.setenv('JUKEBOX_RUN_BATCH_B_MIGRATION', '1')
    monkeypatch.setattr(runner, 'execute', MagicMock(side_effect=RuntimeError('fake-private-value')))
    assert runner.main() == 1
    output = capsys.readouterr().out
    assert 'fake-private-value' not in output
    assert json.loads(output)['stage'] == 'A_PRECHECK_FAILED'
    assert 'TEST_DB_PASSWORD' not in runner.os.environ
    assert runner.os.environ['JUKEBOX_RUN_BATCH_B_MIGRATION'] == '0'


def test_captured_output_does_not_escape() -> None:
    """@description: Drop raw traceback text and unexpected fields from private pytest capture.
    @param: none
    @return: None.
    """
    assert runner.journal_reports('secret trace\n{"run_id":"x","password":"fake"}') == []
    report = dict(run_id='x', journal='safe-path', restoration_pending=False, counts={}, session_blocked=False)
    assert runner.journal_reports('.' + json.dumps(report)) == [report]


def test_cleanup_failure_is_never_reported_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Preserve a teardown failure even when a later read observes an empty baseline.
    @param monkeypatch: Offline verification seam.
    @return: None.
    """
    monkeypatch.setattr(runner, 'verify_restoration', MagicMock())
    state = dict(journals=[dict(restoration_pending=True)], tests=dict(passed=23, failed=0, skipped=0))
    assert runner.conclude(None, (), state, 0) == 1
    assert state['stage'] == 'B_CLEANUP_FAILED'


@pytest.mark.parametrize('result,counts,stage', [(0, dict(passed=23, failed=0, skipped=0), 'COMPLETE_MANUAL_ACCEPTANCE_PENDING'),
    (1, dict(passed=0, failed=1, skipped=0), 'B_TEST_FAILED'), (0, dict(passed=22, failed=0, skipped=1), 'B_TEST_FAILED')])
def test_dispatch_requires_full_real_count(monkeypatch: pytest.MonkeyPatch, result: int, counts: dict, stage: str) -> None:
    """@description: Verify genuine-test dispatch and post-restoration requirements without connecting MySQL.
    @param monkeypatch: Offline stage spies.
    @param result: Synthetic pytest exit code.
    @param counts: Synthetic report counts, never claimed as actual MySQL results.
    @param stage: Expected sanitized classification.
    @return: None.
    """
    monkeypatch.setattr(runner, 'stage_a', lambda *args: ('baseline', 'definitions'))
    verify = MagicMock()
    monkeypatch.setattr(runner, 'verify_restoration', verify)
    monkeypatch.setattr(runner, 'install_profiles', MagicMock())
    monkeypatch.setattr(runner, 'run_tests', lambda state: state.update(tests=counts) or result)
    header, credentials = runner.receive(protocol())
    state = {}
    runner.execute(header, credentials, state)
    assert credentials == []
    assert state['stage'] == stage
    assert verify.call_count == 2


def test_new_module_function_lengths() -> None:
    """@description: Enforce project function-size limits without an additional tooling dependency.
    @param: none
    @return: None.
    """
    for module in (runner, setup):
        source = Path(module.__file__).read_text(encoding='utf-8')
        lines = source.splitlines()
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.FunctionDef):
                continue
            first = node.body[1].lineno if ast.get_docstring(node) else node.body[0].lineno
            count = sum(bool(line.strip()) and not line.lstrip().startswith('#') for line in lines[first - 1:node.end_lineno])
            assert count <= 25, (node.name, count)
            from tests.test_phase2_quality import _control_depth, _assert_function_documentation
            assert _control_depth(node) <= 3, node.name
            _assert_function_documentation(node, Path(module.__file__))
