"""Verify explicit recovery writes using offline transaction spies and synthetic inventories.

No test in this module connects to MySQL or claims server FK or commit behavior has been validated.
"""

import ast
import io
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import pymysql
from tests import batch_b_recovery_write as write
from tests import batch_b_acceptance as acceptance
from tests.batch_b_harness_contract import HarnessSafetyError, RunScope
from tests.test_batch_b_recovery import planning_fixture, journal_fixture


def header() -> dict:
    """@description: Supply explicit synthetic approval without consulting secrets or external services.
    @param: none
    @return: Nonsecret protocol matching the separate restore mode.
    """
    return dict(authorization='BATCH_B_RECOVERY_WRITE', journal=str(write.JOURNAL_FILE),
        confirmation='RESTORE ' + write.RUN_ID, schema_fingerprint='a' * 64, exclusive_no_ddl=True)


def state() -> dict:
    """@description: Keep all offline output in memory rather than creating an attempt journal.
    @param: none
    @return: Synthetic nonsecret blocked attempt state.
    """
    return dict(stage='R_RECOVERY_PRECHECK', recovery_journal='not-written.json', cleanup_progress=[], session_blocked=True)


def transaction_fixture(monkeypatch: pytest.MonkeyPatch) -> tuple:
    """@description: Isolate transaction orchestration while retaining real PK, ownership and control guards.
    @param monkeypatch: Synthetic connection and filesystem-free progress seams.
    @return: Owned rows, journal, scope, exact proposal and driver spies.
    """
    rows, journal, scope = planning_fixture(monkeypatch)
    proposal = write.require_exact_recovery(rows, journal, scope)
    manifest = dict(delete_manifest=proposal['delete_manifest'], control_precondition=dict(rows['jb2_control'][0]),
                    control_restore=journal['original_control'])
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.rowcount = 1
    cursor.fetchall.return_value = ()  # Registered aliases were individually deleted.
    cursor.fetchone.return_value = manifest['control_precondition']
    monkeypatch.setattr(write, 'lock_inventory', lambda _: rows)
    monkeypatch.setattr(write, 'guard_metadata', MagicMock())
    monkeypatch.setattr(write, 'save', lambda report, phase: report.update(stage=phase))
    monkeypatch.setattr(write, 'read_inventory', lambda _: write.expected_baseline(journal))
    return rows, journal, scope, manifest, connection, cursor


@pytest.mark.parametrize('field,value', [('authorization','BATCH_B_RECOVERY_READONLY'), ('confirmation','RESTORE'),
    ('exclusive_no_ddl',False), ('schema_fingerprint','bad'), ('journal','another/run.json')])
def test_contract_rejects_wrong_mode_and_scope_before_connect(field: str, value: object) -> None:
    """@description: Fail before credential consumption or driver creation on incomplete intent.
    @param field: Protocol field under test.
    @param value: Invalid synthetic value.
    @return: None.
    """
    request = header()
    request[field] = value
    with pytest.raises(HarnessSafetyError):
        write.load_contract(request)


@pytest.mark.parametrize('digest', ['0' * 64, write.JOURNAL_HASH])
def test_contract_pins_journal_and_manifest(monkeypatch: pytest.MonkeyPatch, digest: str) -> None:
    """@description: Bind the only accepted incident to immutable evidence and reviewed artifact bytes.
    @param monkeypatch: Pure journal reader replacement.
    @param digest: Correct or wrong synthetic journal digest.
    @return: None.
    """
    scope = RunScope(run_id=write.RUN_ID)
    journal = journal_fixture(scope)
    monkeypatch.setattr(write, 'read_journal', lambda _: (journal, digest, scope))
    if digest == write.JOURNAL_HASH:
        assert write.load_contract(header())[2]['run_id'] == write.RUN_ID
    else:
        with pytest.raises(HarnessSafetyError, match='HASH_MISMATCH'):
            write.load_contract(header())


def test_changed_manifest_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Do not accept edited cleanup keys merely because a manifest still parses.
    @param monkeypatch: Synthetic artifact bytes and registered journal.
    @return: None.
    """
    scope = RunScope(run_id=write.RUN_ID)
    monkeypatch.setattr(write, 'read_journal', lambda _: (journal_fixture(scope), write.JOURNAL_HASH, scope))
    monkeypatch.setattr(Path, 'read_bytes', lambda _: b'{"delete_manifest":{}}')
    with pytest.raises(HarnessSafetyError, match='HASH_MISMATCH'):
        write.load_contract(header())


@pytest.mark.parametrize('mutation', ['valid','cascade','self_cascade','missing_rule','extra_edge','incoming'])
def test_fk_actions_and_full_visible_graph_are_strict(monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    """@description: Reject automatic cascades and unreviewed incoming dependencies before any delete.
    @param monkeypatch: Synthetic metadata responses only.
    @param mutation: Live metadata shape being modeled, not a real server observation.
    @return: None.
    """
    fields = ('TABLE_NAME','COLUMN_NAME','ORDINAL_POSITION','REFERENCED_TABLE_SCHEMA','REFERENCED_TABLE_NAME','REFERENCED_COLUMN_NAME')
    edges = [dict(zip(fields, edge)) for edge in write.reviewed_foreign_keys()]
    rules = [dict(TABLE_NAME=table, CONSTRAINT_NAME=name, UPDATE_RULE='RESTRICT', DELETE_RULE='NO ACTION')
             for table, name in sorted(write.reviewed_constraint_names())]
    incoming = ({'TABLE_SCHEMA': 'unreviewed'},) if mutation == 'incoming' else ()
    if mutation in {'cascade', 'self_cascade'}:
        target = next(row for row in rules if row['TABLE_NAME'] == 'jb2_versions') if mutation == 'self_cascade' else rules[0]
        target['DELETE_RULE'] = 'CASCADE'
    elif mutation == 'missing_rule':
        rules.pop()
    elif mutation == 'extra_edge':
        edges.append(dict(edges[0], COLUMN_NAME='unknown'))
    monkeypatch.setattr(write, 'query', MagicMock(side_effect=[edges, incoming, rules]))
    if mutation == 'valid':
        write.verify_fk_metadata(MagicMock())
    else:
        with pytest.raises(HarnessSafetyError):
            write.verify_fk_metadata(MagicMock())


def test_schema_drift_stops_before_fk_and_data_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Require the previously approved structure rather than self-approving today's schema.
    @param monkeypatch: Existing read-only identity/grant probes replaced for this unit test.
    @return: None.
    """
    monkeypatch.setattr(write, 'check_identity', lambda *args: None)
    monkeypatch.setattr(write, 'verify_harness_grants', lambda *args: None)
    monkeypatch.setattr(write, 'schema_fingerprints', lambda _: {'table': 'changed'})
    fk = MagicMock()
    monkeypatch.setattr(write, 'verify_fk_metadata', fk)
    with pytest.raises(HarnessSafetyError, match='SCHEMA_FINGERPRINT_CHANGED'):
        write.guard_metadata(MagicMock(), 'a' * 64)
    fk.assert_not_called()


def test_server_uuid_is_checked_before_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Reuse the real fixed-account identity verifier on restore sessions.
    @param monkeypatch: Approved nonsecret instance pin.
    @return: None.
    """
    monkeypatch.setenv('TEST_DB_SERVER_UUID', '11111111-2222-4333-8444-555555555555')
    cursor = MagicMock()
    cursor.fetchone.return_value = dict(db='jukebox101_test', account='jukebox101_test_harness@127.0.0.1',
        port=3306, roles='NONE', fk=1, version='8.0.46', server_uuid='aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')
    with pytest.raises(HarnessSafetyError, match='SERVER_UUID_MISMATCH'):
        write.guard_metadata(cursor, 'a' * 64)


def test_postcommit_verification_failure_never_reports_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Preserve a confirmed commit with failed final verification as blocked, not falsely rolled back.
    @param monkeypatch: Synthetic fresh read session with a residual row.
    @return: None.
    """
    scope = RunScope()
    journal = journal_fixture(scope)
    rows = write.expected_baseline(journal)
    rows['users'] = ({'discord_user_id': 'synthetic-private'},)
    connection = MagicMock()
    monkeypatch.setattr(write, 'open_read_only', lambda _: connection)
    monkeypatch.setattr(write, 'guard_metadata', lambda *args: None)
    monkeypatch.setattr(write, 'read_inventory', lambda _: rows)
    report = dict(commit_outcome='ACKNOWLEDGED', baseline_restored=False)
    with pytest.raises(HarnessSafetyError, match='POSTCOMMIT_BASELINE_MISMATCH'):
        write.verify_after('synthetic', journal, 'a' * 64, report)
    assert report['baseline_restored'] is False
    assert 'synthetic-private' not in json.dumps(report)
    connection.rollback.assert_not_called()
    connection.close.assert_called_once()


def test_root_and_alias_proof_keeps_both_exact_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep alias-before-root ordering explicit without modifying canonical identity values.
    @param monkeypatch: Minimal payload fixture boundary.
    @return: None.
    """
    rows, journal, scope = planning_fixture(monkeypatch)
    rows['jb2_versions'] += ({'version_id': scope.run_id + ':v2', 'canonical_id': scope.run_id + ':v1'},)
    journal['observed_primary_keys']['jb2_versions'] = [{'version_id': row['version_id']} for row in rows['jb2_versions']]
    proposal = write.require_exact_recovery(rows, journal, scope)
    assert proposal['version_order'] == [scope.run_id + ':v2', scope.run_id + ':v1']
    manifest = dict(delete_manifest=proposal['delete_manifest'], control_precondition=rows['jb2_control'][0],
                    control_restore=journal['original_control'])
    write.check_manifest(rows, journal, scope, manifest)


def test_transaction_deletes_exact_keys_and_restores_control_without_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep cleanup and counter restoration within the same explicitly owned unit.
    @param monkeypatch: Offline transaction fixture.
    @return: None.
    """
    rows, journal, scope, manifest, connection, cursor = transaction_fixture(monkeypatch)
    report = state()
    write.restore_transaction(connection, journal, scope, manifest, 'a' * 64, report)
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert any(sql.startswith('DELETE FROM `jb2_versions` WHERE `version_id`=%s') for sql in statements)
    assert any(sql.startswith('UPDATE jb2_control SET accepted_sequence=') for sql in statements)
    assert all(' WHERE ' in sql for sql in statements if sql.startswith('DELETE'))
    connection.commit.assert_not_called()
    assert report['stage'] == 'R_RECOVERY_PRECOMMIT_VERIFIED'


@pytest.mark.parametrize('mutation', ['registry','unknown_pk','unknown_child','control','self_parent'])
def test_bad_inventory_never_reaches_delete(monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    """@description: Reject fresh inconsistencies before the first delete, including corrupted version closure.
    @param monkeypatch: Offline transaction fixture.
    @param mutation: Inconsistent inventory component.
    @return: None.
    """
    rows, journal, scope, manifest, connection, cursor = transaction_fixture(monkeypatch)
    if mutation == 'registry':
        rows['jb2_migrations'] = ({'version': 'wrong'},)
    elif mutation == 'unknown_pk':
        rows['users'] += ({'discord_user_id': 999, 'credit_balance': 0},)
    elif mutation == 'unknown_child':
        rows['submissions'] = ({'submission_id': 999},)
    elif mutation == 'control':
        rows['jb2_control'][0]['revision'] += 1
    else:
        rows['jb2_versions'][0]['canonical_id'] = 'outside'
    with pytest.raises(HarnessSafetyError):
        write.restore_transaction(connection, journal, scope, manifest, 'a' * 64, state())
    cursor.execute.assert_not_called()


def test_delete_rowcount_mismatch_stops_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Refuse partial or stale key deletion without ever reaching commit.
    @param monkeypatch: Offline transaction fixture.
    @return: None.
    """
    _, journal, scope, manifest, connection, cursor = transaction_fixture(monkeypatch)
    cursor.rowcount = 0
    with pytest.raises(HarnessSafetyError, match='ROWCOUNT_MISMATCH'):
        write.restore_transaction(connection, journal, scope, manifest, 'a' * 64, state())
    connection.commit.assert_not_called()


def test_self_root_1451_rolls_back_all_earlier_child_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep a late root refusal atomic instead of leaving previously deleted children committed.
    @param monkeypatch: Offline real-orchestration fixture with a root-only synthetic driver fault.
    @return: None.
    """
    rows, journal, scope, manifest, connection, cursor = transaction_fixture(monkeypatch)
    monkeypatch.setattr(write, 'verified_connection', lambda *args: connection)
    monkeypatch.setattr(write, 'query', lambda *args: ({'owned': 1},))
    monkeypatch.setattr(write, 'read_inventory', lambda _: rows)
    def execute(sql: str, parameters: tuple = ()) -> None:
        """@description: Model only a late driver rejection, without pretending to emulate MySQL FK semantics.
        @param sql: Actual cleanup statement generated by the shared implementation.
        @param parameters: Private synthetic bound values.
        @return: None.
        """
        if sql.startswith('DELETE FROM `jb2_versions`'):
            raise pymysql.IntegrityError(1451, 'synthetic-private-root-error')
    cursor.execute.side_effect = execute
    report = state()
    with pytest.raises(pymysql.IntegrityError):
        write.execute('synthetic', journal, scope, manifest, 'a' * 64, report)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    assert report['cleanup_progress'][-1]['table'] == 'jb2_versions'
    assert report['cleanup_progress'][-1]['phase'] == 'DELETE_ATTEMPT'
    assert not any(call.args[0].startswith('UPDATE') for call in cursor.execute.call_args_list)


@pytest.mark.parametrize('fault', [pymysql.IntegrityError(1451, 'synthetic-private'), RuntimeError('synthetic-midpoint')])
def test_transaction_failure_rolls_back_once(monkeypatch: pytest.MonkeyPatch, fault: BaseException) -> None:
    """@description: Preserve a primary FK or mid-transaction error while rolling back and staying blocked.
    @param monkeypatch: Offline orchestration seams.
    @param fault: Synthetic transaction exception.
    @return: None.
    """
    rows, journal, scope, manifest, connection, _ = transaction_fixture(monkeypatch)
    monkeypatch.setattr(write, 'verified_connection', lambda *args: connection)
    monkeypatch.setattr(write, 'query', lambda *args: ({'owned': 1},))
    monkeypatch.setattr(write, 'read_inventory', lambda _: rows)
    monkeypatch.setattr(write, 'restore_transaction', MagicMock(side_effect=fault))
    report = state()
    with pytest.raises(type(fault)):
        write.execute('synthetic', journal, scope, manifest, 'a' * 64, report)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    assert report['session_blocked'] is True
    assert 'synthetic-private' not in json.dumps(report)


def test_unknown_commit_never_rolls_back_or_replays_delete(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep lost acknowledgement ambiguous and investigate through the read-only path only.
    @param monkeypatch: Offline commit fault and read-only probe.
    @return: None.
    """
    connection = MagicMock()
    connection.commit.side_effect = pymysql.OperationalError(2013, 'synthetic-private')
    monkeypatch.setattr(write, 'save', lambda *args: None)
    report = state()
    with pytest.raises(pymysql.OperationalError):
        write.commit_once(connection, report)
    write.rollback_failure(connection, report)
    inspect = MagicMock()
    monkeypatch.setattr(write, 'verify_after', inspect)
    write.investigate_unknown('synthetic', {}, header(), report)
    assert report['commit_outcome'] == 'UNKNOWN'
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()
    inspect.assert_called_once()


def test_repeated_recovery_does_not_delete_or_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Treat an already restored baseline as a blocked no-replay condition, not a new cleanup.
    @param monkeypatch: Offline orchestration fixture.
    @return: None.
    """
    _, journal, scope, manifest, connection, _ = transaction_fixture(monkeypatch)
    monkeypatch.setattr(write, 'verified_connection', lambda *args: connection)
    monkeypatch.setattr(write, 'query', lambda *args: ({'owned': 1},))
    delete = MagicMock()
    monkeypatch.setattr(write, 'restore_transaction', delete)
    with pytest.raises(HarnessSafetyError, match='ALREADY_BASELINE'):
        write.execute('synthetic', journal, scope, manifest, 'a' * 64, state())
    delete.assert_not_called()
    connection.commit.assert_not_called()


@pytest.mark.parametrize('mode', ['success','postverify','unknown'])
def test_main_status_remains_blocked_and_sanitized(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, mode: str) -> None:
    """@description: Report restoration only after verification and never clear the historical session block.
    @param monkeypatch: Private protocol and filesystem-free executor.
    @param capsys: Safe public JSON.
    @param mode: Confirmed success or synthetic postcommit failure.
    @return: None.
    """
    monkeypatch.setattr(write, 'receive', lambda _: (header(), {'server_uuid': '11111111-2222-4333-8444-555555555555'}, None, {}, 'synthetic-private'))
    monkeypatch.setattr(write.tempfile, 'mkdtemp', lambda **kwargs: 'synthetic-owned-folder')
    monkeypatch.setattr(write, 'save', lambda report, phase: report.update(stage=phase))
    monkeypatch.setattr(write, 'investigate_unknown', lambda *args: None)
    def execute(*arguments: object) -> None:
        """@description: Inject only final outcome classification, without opening any database.
        @param arguments: Private synthetic executor arguments.
        @return: None.
        """
        report = arguments[-1]
        report['commit_outcome'] = 'UNKNOWN' if mode == 'unknown' else 'ACKNOWLEDGED'
        report['baseline_restored'] = mode == 'postverify'
        if mode != 'success':
            raise RuntimeError('synthetic-private')
        report['baseline_restored'] = True
    monkeypatch.setattr(write, 'execute', execute)
    result = write.main(io.StringIO())
    output = capsys.readouterr().out
    report = json.loads(output)
    assert result == int(mode != 'success')
    assert report['session_blocked'] is True
    assert report['baseline_restored'] is (mode == 'success')
    assert 'synthetic-private' not in output


@pytest.mark.parametrize('mode', ['--inspect-recovery','--restore-recovery'])
def test_recovery_modes_never_dispatch_stage_a_b(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """@description: Keep read, restore and account-provisioning dispatch mutually exclusive.
    @param monkeypatch: Explicit argv and execution spies.
    @param mode: Selected recovery mode.
    @return: None.
    """
    from tests import batch_b_recovery
    monkeypatch.setattr(acceptance.sys, 'argv', ['entry', mode])
    read, restore, normal = MagicMock(return_value=0), MagicMock(return_value=0), MagicMock()
    monkeypatch.setattr(batch_b_recovery, 'main', read)
    monkeypatch.setattr(write, 'main', restore)
    monkeypatch.setattr(acceptance, 'main', normal)
    assert acceptance.entry() == 0
    normal.assert_not_called()
    assert restore.call_count == int(mode == '--restore-recovery')
    assert read.call_count == int(mode == '--inspect-recovery')


def test_runtime_quality() -> None:
    """@description: Enforce existing engineering limits without introducing tooling dependencies.
    @param: none
    @return: None.
    """
    from tests.test_phase2_quality import _implementation_lines, _control_depth, _assert_function_documentation
    source = Path(write.__file__).read_text(encoding='utf8')
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            assert _implementation_lines(node, source.splitlines()) <= 25, node.name
            assert _control_depth(node) <= 3, node.name
            _assert_function_documentation(node, Path(write.__file__))
