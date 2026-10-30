"""Verify incident investigation cannot perform cleanup or hide the original failure.

Synthetic journals and strict cursor spies exercise read-only boundaries and durable diagnostic intent without MySQL.
"""

import ast
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
import pymysql
from tests import batch_b_recovery as recovery
from tests import batch_b_acceptance as acceptance
from tests.batch_b_harness_database import observed_delete, delete_owned, version_delete_order
from tests.batch_b_harness_contract import RunScope, PRIMARY_KEYS, HarnessSafetyError
from tests.test_batch_b_harness_safety import empty_inventory, owned_inventory, mock_harness


def journal_fixture(scope: RunScope) -> dict:
    """@description: Supply a fully explicit synthetic registered scope and baseline without any file write.
    @param scope: Synthetic run roots.
    @return: Nonsecret incident record for pure tests.
    """
    rows = empty_inventory()  # [synthetic original baseline]: Never a copy of production or actual incident rows.
    return dict(run_id=scope.run_id, subjects=scope.subjects, owner_id=scope.owner_id, decisions=sorted(scope.decisions),
        original_control=rows['jb2_control'][0], migration=list(rows['jb2_migrations']),
        counts={name: len(records) for name, records in rows.items()}, observed_primary_keys={},
        server_uuid='11111111-2222-4333-8444-555555555555')


def diagnosis_fixture() -> tuple:
    """@description: Build actual-shaped synthetic FK evidence without any connection or recovery action.
    @param: none
    @return: Registered target, private inventory, named FK metadata and rules.
    """
    scope = RunScope()
    target = scope.run_id + ':v1'
    journal = journal_fixture(scope)
    journal['observed_primary_keys'] = {'jb2_versions': [{'version_id': target}]}
    rows = empty_inventory()
    rows['jb2_versions'] = ({'version_id': target, 'canonical_id': target},)
    edge = dict(TABLE_NAME='jb2_versions', CONSTRAINT_NAME='fk_jb2_version_root', COLUMN_NAME='canonical_id',
        ORDINAL_POSITION=1, REFERENCED_TABLE_SCHEMA='jukebox101_test', REFERENCED_TABLE_NAME='jb2_versions', REFERENCED_COLUMN_NAME='version_id')
    rules = (dict(TABLE_NAME='jb2_versions', CONSTRAINT_NAME='fk_jb2_version_root', UPDATE_RULE='RESTRICT', DELETE_RULE='NO ACTION'),)
    return scope, journal, rows, (edge,), rules


def test_version_diagnosis_reports_self_root_without_claiming_cause() -> None:
    """@description: Publish the missing actual-shaped root fields while leaving historical 1451 causation unresolved.
    @param: none
    @return: None.
    """
    scope, journal, rows, edges, rules = diagnosis_fixture()
    report = recovery.version_fk_diagnosis(rows, journal, {'jb2_versions': 'synthetic SHOW CREATE'}, edges, rules)
    root = report['target_details'][0]
    assert root['version_id'] == root['canonical_id'] == scope.run_id + ':v1'
    assert root['is_self_root'] is True and root['alias_count'] == 0
    assert root['referencing_rows'][0]['matching_rows'] == 1
    assert root['referencing_rows'][0]['non_self_rows'] == 0
    assert report['actual_create_table'] == 'synthetic SHOW CREATE'
    assert report['historical_blocking_constraint'] == 'NOT_RECORDED'


def test_version_diagnosis_separates_aliases_and_unknown_ids() -> None:
    """@description: Show registered aliases and count unregistered children without exposing unknown identities.
    @param: none
    @return: None.
    """
    scope, journal, rows, edges, rules = diagnosis_fixture()
    root = scope.run_id + ':v1'
    rows['jb2_versions'] += ({'version_id': scope.run_id + ':v2', 'canonical_id': root},
                            {'version_id': 'unknown-private-alias', 'canonical_id': root})
    report = recovery.version_fk_diagnosis(rows, journal, {'jb2_versions': 'definition'}, edges, rules)
    target = report['target_details'][0]
    assert target['alias_count'] == 2 and target['unregistered_alias_count'] == 1
    assert target['registered_aliases'] == [{'version_id': scope.run_id + ':v2', 'canonical_id': root}]
    assert target['referencing_rows'][0]['non_self_rows'] == 2
    assert 'unknown-private-alias' not in json.dumps(report)


def test_version_diagnosis_exposes_non_self_constraint_counts() -> None:
    """@description: Avoid attributing every version DELETE refusal to the self-FK when another child exists.
    @param: none
    @return: None.
    """
    scope, journal, rows, edges, rules = diagnosis_fixture()
    child = dict(edges[0], TABLE_NAME='jb2_claims', COLUMN_NAME='version_id', CONSTRAINT_NAME='fk_jb2_claim_version')
    rows['jb2_claims'] = ({'version_id': scope.run_id + ':v1'},)
    report = recovery.version_fk_diagnosis(rows, journal, {'jb2_versions': 'definition'}, (*edges, child), rules)
    reference = report['target_details'][0]['referencing_rows'][1]
    assert reference['constraint'] == 'fk_jb2_claim_version'
    assert reference['matching_rows'] == reference['non_self_rows'] == 1
    assert report['historical_blocking_constraint'] == 'NOT_RECORDED'


@pytest.mark.parametrize('present', [True, False])
def test_version_diagnosis_does_not_invent_unknown_canonical_value(present: bool) -> None:
    """@description: Distinguish absent root from an unexpected canonical identity without guessing a repair.
    @param present: Whether the synthetic target row exists.
    @return: None.
    """
    _, journal, rows, edges, rules = diagnosis_fixture()
    if present:
        rows['jb2_versions'][0]['canonical_id'] = 'unknown-private-version'
    else:
        rows['jb2_versions'] = ()
    report = recovery.version_fk_diagnosis(rows, journal, {'jb2_versions': 'definition'}, edges, rules)
    root = report['target_details'][0]
    assert root['row_count'] == int(present) and root['is_self_root'] is False
    assert root['canonical_id'] == ('UNREGISTERED' if present else None)
    assert 'unknown-private-version' not in json.dumps(report)


def test_version_diagnosis_marks_unreadable_reference_unresolved() -> None:
    """@description: Preserve metadata visibility limits instead of equating an unavailable child with zero rows.
    @param: none
    @return: None.
    """
    _, journal, rows, edges, rules = diagnosis_fixture()
    child = dict(edges[0], TABLE_NAME='unreviewed_table', CONSTRAINT_NAME='unknown_fk')
    report = recovery.version_fk_diagnosis(rows, journal, {'jb2_versions': 'definition'}, (*edges, child), rules)
    reference = report['target_details'][0]['referencing_rows'][1]
    assert reference['matching_rows'] is None
    assert reference['status'] == 'UNRESOLVED_REFERENCE'


def planning_fixture(monkeypatch: pytest.MonkeyPatch) -> tuple:
    """@description: Build explicit synthetic PK evidence while testing real ownership, counters and scope gates.
    @param monkeypatch: Replace only absent JSON payload codecs in this minimal row fixture.
    @return: Frozen owned rows, historical journal and registered scope.
    """
    scope = RunScope()
    rows = owned_inventory(scope)
    rows['jb2_control'][0].update(snapshot_ready=0, legacy_writers_quiesced=0, settlement_enabled=0)
    journal = journal_fixture(scope)
    journal['observed_control'] = (dict(rows['jb2_control'][0]),)
    journal['observed_primary_keys'] = {table: [{field: row[field] for field in fields} for row in rows[table]]
        for table, fields in PRIMARY_KEYS.items()}
    monkeypatch.setattr(recovery, 'validate_payloads', lambda _: None)
    return rows, journal, scope


def test_recovery_proposal_is_exact_and_never_write_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep a complete PK proposal distinct from executing or approving restoration.
    @param monkeypatch: Minimal JSON-only fixture boundary.
    @return: None.
    """
    rows, journal, scope = planning_fixture(monkeypatch)
    report = recovery.require_exact_recovery(rows, journal, scope)
    assert report['delete_manifest']['users'] == journal['observed_primary_keys']['users']
    assert report['control_restore'] == journal['original_control']
    assert report['recovery_writes_authorized'] is False
    assert report['self_roots'] == [scope.run_id + ':v1']
    assert 'SELF_FK_SQL_BEHAVIOR_REVIEW' in report['remaining_gates']


@pytest.mark.parametrize('mutation', ['unknown_pk', 'unknown_child', 'missing_pk', 'revision', 'switches', 'watermark', 'incomplete', 'legacy_child'])
def test_recovery_proposal_rejects_drift(monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    """@description: Reject both ownership violations and registered-but-unapproved additional PKs before any writes.
    @param monkeypatch: Minimal JSON-only fixture boundary.
    @param mutation: Fresh inventory or historical watermark inconsistency.
    @return: None.
    """
    rows, journal, scope = planning_fixture(monkeypatch)
    if mutation == 'unknown_pk':
        rows['users'] += ({'discord_user_id': scope.subjects[scope.run_id + ':2']['user'], 'credit_balance': 0},)
    elif mutation == 'unknown_child':
        rows['jb2_evidence'] = ({'evidence_id': 99, 'video_id': 'unknown', 'actor_id': scope.owner_id},)
    elif mutation == 'missing_pk':
        rows['jb2_versions'] = ()
    elif mutation == 'revision':
        rows['jb2_control'][0]['revision'] += 1
    elif mutation == 'switches':
        rows['jb2_control'][0].update(snapshot_ready=1, legacy_writers_quiesced=1, settlement_enabled=1)
    elif mutation == 'watermark':
        journal['observed_control'][0]['accepted_sequence'] = 99
    elif mutation == 'legacy_child':
        rows['submissions'] = ({'submission_id': 99},)
    else:
        rows.pop('submissions')
    with pytest.raises(HarnessSafetyError):
        recovery.require_exact_recovery(rows, journal, scope)


def test_incident_manifest_matches_reviewed_graph_and_fifteen_keys() -> None:
    """@description: Keep the auditable local proposal bound to the exact incident without authorizing its writes.
    @param: none
    @return: None.
    """
    from tests.batch_b_acceptance_setup import reviewed_foreign_keys
    manifest = json.loads(Path('docs/batch-b-recovery-manifest.json').read_text(encoding='utf-8'))
    assert manifest['run_id'] == '14d8aee30b034a9883e9f71cdec8146e'
    assert manifest['journal_sha256'] == '6a75a0bd379a08fa1f57992187b039f75f19bde1c51f7e457bc61dd0fcbfd71d'
    assert sum(map(len, manifest['delete_manifest'].values())) == 15
    assert manifest['recovery_writes_authorized'] is False
    assert not set(manifest['protected_tables']) & set(manifest['delete_manifest'])
    assert {tuple(edge) for edge in manifest['reviewed_fk_column_edges']} == reviewed_foreign_keys()
    positions = {table: index for index, table in enumerate(manifest['delete_order'])}
    for child, _, _, _, parent, _ in reviewed_foreign_keys():
        if child in positions and parent in positions and child != parent:
            assert positions[child] < positions[parent]


@pytest.mark.parametrize('versions', [({'version_id': 'a', 'canonical_id': 'b'}, {'version_id': 'b', 'canonical_id': 'a'}),
    ({'version_id': 'a', 'canonical_id': 'missing'},)])
def test_invalid_version_graph_blocks_before_any_delete(versions: tuple) -> None:
    """@description: Validate all self-table dependencies before touching even unrelated child rows.
    @param versions: Synthetic cycle or untracked parent.
    @return: None.
    """
    rows = empty_inventory()
    rows['jb2_versions'] = versions
    rows['jb2_receipts'] = ({'operation_id': 'registered'},)
    cursor = MagicMock()
    with pytest.raises(HarnessSafetyError):
        delete_owned(cursor, rows)
    cursor.execute.assert_not_called()


def test_aliases_precede_root_without_mutating_self_reference() -> None:
    """@description: Preserve approved identity values while ordering aliases before their single root.
    @param: none
    @return: None.
    """
    rows = ({'version_id': 'root', 'canonical_id': 'root'}, {'version_id': 'alias', 'canonical_id': 'root'})
    assert [row['version_id'] for row in version_delete_order(rows)] == ['alias', 'root']
    assert rows[0]['canonical_id'] == 'root'


def test_fk_error_rolls_back_and_never_reports_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Exercise the real harness failure boundary with synthetic 1451 and independent emergency shutdown.
    @param monkeypatch: Connection spies only; no actual server semantics are claimed.
    @return: None.
    """
    from tests import batch_b_harness as runtime
    scope = RunScope()
    harness, cursor, statuses = mock_harness(scope, monkeypatch)
    original = harness.connection
    monkeypatch.setattr(runtime, 'lock_inventory', lambda _: owned_inventory(scope))
    cursor.execute.side_effect = pymysql.IntegrityError(1451, 'synthetic-private-driver-text')
    emergency = MagicMock()
    emergency.cursor.return_value.__enter__.return_value.fetchone.side_effect = [{'owned': 1}, {'schema_version': '2b2-b1'}]
    monkeypatch.setattr(runtime, 'verified_connection', lambda *args: emergency)
    with pytest.raises(pymysql.IntegrityError):
        harness.finish()
    original.rollback.assert_called_once()
    original.commit.assert_not_called()
    assert harness.failed and harness.enabled
    assert 'RESTORED_VERIFIED' not in statuses
    assert harness.cleanup_progress[-1]['phase'] == 'ROLLBACK_ACKNOWLEDGED'


@pytest.mark.parametrize('sql', ['DELETE FROM users WHERE discord_user_id=%s', 'UPDATE jb2_control SET revision=0',
    'INSERT INTO users VALUES (1)', 'CREATE USER x', 'ALTER TABLE users ADD x INT', 'DROP TABLE users',
    'TRUNCATE users', 'GRANT SELECT ON *.* TO x', 'SELECT * FROM jb2_control FOR UPDATE',
    'SELECT 1; DELETE FROM users', 'SELECT * INTO OUTFILE "x" FROM users'])
def test_read_adapter_rejects_before_driver(sql: str) -> None:
    """@description: Prove read-only inspection cannot reuse a cleanup or account-provisioning statement.
    @param sql: Forbidden synthetic statement, never executed.
    @return: None.
    """
    raw = MagicMock()
    with pytest.raises(HarnessSafetyError):
        recovery.ReadOnlyCursor(raw).execute(sql)
    raw.execute.assert_not_called()


def test_read_adapter_preserves_bound_select() -> None:
    """@description: Reuse parameterized metadata reads without reporting their private parameters.
    @param: none
    @return: None.
    """
    raw = MagicMock()
    recovery.ReadOnlyCursor(raw).execute('SELECT User FROM mysql.user WHERE User=%s', ('synthetic',))
    raw.execute.assert_called_once_with('SELECT User FROM mysql.user WHERE User=%s', ('synthetic',))


def test_delete_failure_records_attempt_not_commit() -> None:
    """@description: Identify the failing exact PK without mistaking earlier executes for committed cleanup.
    @param: none
    @return: None.
    """
    cursor = MagicMock()
    cursor.fetchall.return_value = ()
    def fail_delete(sql: str, parameters: tuple) -> None:
        """@description: Model only a rejected DELETE after the new alias SELECT succeeds.
        @param sql: Actual statement produced by shared cleanup.
        @param parameters: Synthetic bound PK values.
        @return: None.
        """
        if sql.startswith('DELETE '):
            raise pymysql.IntegrityError(1451, 'synthetic-private-driver-message')
    cursor.execute.side_effect = fail_delete
    events = []  # [uncommitted attempt evidence]: No simulated server behavior is claimed as a MySQL test.
    with pytest.raises(pymysql.IntegrityError):
        observed_delete(cursor, 'jb2_versions', {'version_id': 'registered'}, lambda *event: events.append(event))
    assert events == [('DELETE_ATTEMPT', 'jb2_versions', {'version_id': 'registered'})]


def test_delete_execute_ack_is_explicitly_uncommitted() -> None:
    """@description: Keep exact cleanup progress distinct from transaction commit confirmation.
    @param: none
    @return: None.
    """
    cursor = MagicMock(rowcount=1)
    cursor.fetchall.return_value = ()  # No remaining version child in this synthetic inventory.
    events = []
    observed_delete(cursor, 'jb2_versions', {'version_id': 'registered'}, lambda *event: events.append(event))
    assert [event[0] for event in events] == ['DELETE_ATTEMPT', 'DELETE_EXECUTED_UNCOMMITTED']


def test_pytest_call_pass_and_teardown_error_remain_distinct() -> None:
    """@description: Prevent one passed call plus teardown failure from being described as a business assertion failure.
    @param: none
    @return: None.
    """
    observer = acceptance.TestCounts()
    for phase, outcome in [('call', 'passed'), ('teardown', 'failed')]:
        observer.pytest_runtest_logreport(SimpleNamespace(nodeid='tests/test_batch_b_mysql.py::test_real_batch_b_workflow[persistence]',
            when=phase, outcome=outcome, failed=outcome == 'failed', skipped=False, passed=outcome == 'passed'))
    assert observer.counts == dict(passed=1, failed=1, skipped=0)
    assert [report['when'] for report in observer.reports] == ['call', 'teardown']


def test_primary_diff_does_not_publish_unknown_keys() -> None:
    """@description: Report new unregistered rows as unresolved counts rather than approved deletion identities.
    @param: none
    @return: None.
    """
    scope = RunScope()
    journal = journal_fixture(scope)
    rows = empty_inventory()
    rows['users'] = ({'discord_user_id': 'unknown-private-id'},)
    differences = recovery.primary_key_diff(rows, journal)
    assert differences['users'] == dict(recorded_present=[], recorded_missing=[], unregistered_count=1)
    assert 'unknown-private-id' not in json.dumps(differences)


def test_control_freeze_simulation_never_changes_rows() -> None:
    """@description: Validate documented OFF emergency state without actually enabling or mutating input control.
    @param: none
    @return: None.
    """
    scope = RunScope()
    journal = journal_fixture(scope)
    rows = owned_inventory(scope)
    rows['jb2_control'][0].update(snapshot_ready=0, legacy_writers_quiesced=0, settlement_enabled=0)
    before = dict(rows['jb2_control'][0])
    assert recovery.control_check(rows, journal) == []
    assert rows['jb2_control'][0] == before


def test_baseline_diff_explains_residual_rows_and_counters() -> None:
    """@description: Expose meaningful restoration differences without dumping private business payloads.
    @param: none
    @return: None.
    """
    original = empty_inventory()
    rows = empty_inventory()
    rows['jb2_control'][0]['accepted_sequence'] = 1
    rows['users'] = ({'discord_user_id': 'unknown-private-id'},)
    report = acceptance.inventory_difference((rows, {}), (original, {}))
    assert report['tables']['users']['actual_count'] == 1
    assert report['control']['accepted_sequence'] == dict(expected=0, actual=1)
    assert 'unknown-private-id' not in json.dumps(report)


def test_readonly_mode_never_dispatches_stage_a(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep an explicit incident inspection request out of account setup and real tests.
    @param monkeypatch: Synthetic argv and read-only dispatch seam.
    @return: None.
    """
    monkeypatch.setattr(acceptance.sys, 'argv', ['entry', '--inspect-recovery'])
    inspect = MagicMock(return_value=0)
    monkeypatch.setattr(recovery, 'main', inspect)
    writes = MagicMock(side_effect=AssertionError('write mode reached'))
    monkeypatch.setattr(acceptance, 'main', writes)
    assert acceptance.entry() == 0
    inspect.assert_called_once()
    writes.assert_not_called()


def test_read_connection_target_is_fixed_without_init_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep read-only diagnostics away from DBA, production and business session initialization.
    @param monkeypatch: Offline driver factory spy.
    @return: None.
    """
    monkeypatch.setenv('TEST_DB_SERVER_UUID', '11111111-2222-4333-8444-555555555555')
    connect = MagicMock()
    monkeypatch.setattr(recovery.pymysql, 'connect', connect)
    recovery.open_read_only('synthetic-private')
    arguments = connect.call_args.kwargs
    assert (arguments['host'], arguments['port'], arguments['user'], arguments['database']) == (
        '127.0.0.1', 3306, 'jukebox101_test_harness', 'jukebox101_test')
    assert arguments['autocommit'] is None and 'init_command' not in arguments


def test_read_close_failure_forces_failing_report(capsys: pytest.CaptureFixture) -> None:
    """@description: Never describe an unconfirmed read-session close as a successful investigation.
    @param capsys: Captured safe report.
    @return: None.
    """
    connection = MagicMock()
    connection.close.side_effect = pymysql.OperationalError(2013, 'synthetic-private-secret')
    assert recovery.finish_read(connection, dict(stage='R_READ_ONLY_COMPLETE')) is True
    output = capsys.readouterr().out
    assert json.loads(output)['stage'] == 'R_READ_ONLY_FAILED'
    assert 'synthetic-private-secret' not in output


def test_read_main_uses_one_secret_and_preserves_evidence(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """@description: Exercise read-only dispatch with private stdin and no write-mode entry.
    @param monkeypatch: Entirely in-memory journal and connection.
    @param capsys: Sanitized JSON capture.
    @return: None.
    """
    import hashlib
    scope = RunScope()
    journal = journal_fixture(scope)
    content = b'synthetic immutable evidence'
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(recovery, 'read_journal', lambda _: (journal, digest, scope))
    monkeypatch.setattr(Path, 'read_bytes', lambda _: content)
    connection = MagicMock()
    monkeypatch.setattr(recovery, 'open_recovery_readonly', lambda *_: connection)
    monkeypatch.setattr(recovery, 'inspect_connection', lambda *args: dict(recovery_writes_authorized=False))
    header = dict(authorization='BATCH_B_RECOVERY_READONLY', journal='synthetic', account='jukebox101_test_readonly')
    stream = io.StringIO(json.dumps(header) + '\n"synthetic-private"\n')
    assert recovery.main(stream) == 0
    output = capsys.readouterr().out
    assert json.loads(output)['journal_sha256'] == digest
    assert 'synthetic-private' not in output
    connection.close.assert_called_once()


def test_cleanup_observer_persists_keys_without_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep exact failure target diagnostics without storing evidence or secret JSON.
    @param monkeypatch: In-memory harness journal seam.
    @return: None.
    """
    harness, _, statuses = mock_harness(RunScope(), monkeypatch)
    harness.observe_cleanup('DELETE_ATTEMPT', 'jb2_evidence', {'evidence_id': 42, 'evidence_json': 'synthetic-private'})
    assert harness.cleanup_progress == [dict(phase='DELETE_ATTEMPT', table='jb2_evidence', primary_key={'evidence_id': 42})]
    assert statuses == ['CLEANUP_PROGRESS']
    assert 'synthetic-private' not in json.dumps(harness.cleanup_progress)


@pytest.mark.parametrize('path', ['.env', 'credentials.json', 'unreviewed/run.json'])
def test_journal_reader_rejects_other_credential_sources(path: str) -> None:
    """@description: Do not treat arbitrary credential files as incident journals.
    @param path: Disallowed synthetic input path; it is never opened.
    @return: None.
    """
    with pytest.raises(HarnessSafetyError, match='JOURNAL_PATH_REJECTED'):
        recovery.read_journal(Path(path))


def test_journal_validation_has_no_write_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Validate reconstructed roots and baseline while preserving original byte evidence.
    @param monkeypatch: Pure in-memory file reads only.
    @return: None.
    """
    scope = RunScope()
    journal = journal_fixture(scope)
    content = json.dumps(journal).encode()
    monkeypatch.setattr(Path, 'is_symlink', lambda _: False)
    monkeypatch.setattr(Path, 'is_file', lambda _: True)
    monkeypatch.setattr(Path, 'stat', lambda _: SimpleNamespace(st_size=len(content)))
    monkeypatch.setattr(Path, 'read_bytes', lambda _: content)
    loaded, digest, restored_scope = recovery.read_journal(Path('jukebox101-batch-b-synthetic/run.json'))
    assert loaded == journal and restored_scope.subjects == scope.subjects
    assert len(digest) == 64


def test_recovery_module_quality_limits() -> None:
    """@description: Maintain documented function size, depth and docstrings without dependencies.
    @param: none
    @return: None.
    """
    from tests.test_phase2_quality import _implementation_lines, _control_depth, _assert_function_documentation
    source = Path(recovery.__file__).read_text(encoding='utf-8')
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            assert _implementation_lines(node, source.splitlines()) <= 25, node.name
            assert _control_depth(node) <= 3, node.name
            _assert_function_documentation(node, Path(recovery.__file__))
