"""Exercise the exact self-FK window and recovery failure boundaries entirely offline.

Synthetic metadata and transaction spies never prove MySQL DDL, locking or commit behavior.
"""

import ast
import io
import json
from pathlib import Path
from unittest.mock import MagicMock
import pymysql
import pytest
from tests import batch_b_fk_window as window
from tests import batch_b_recovery_fk as repair
from tests import batch_b_recovery_write as recovery
from tests.batch_b_harness_contract import TABLES, HarnessSafetyError


@pytest.mark.parametrize('role', ['dba', 'harness'])
def test_authentication_failure_reports_role_without_private_driver_text(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    """@description: Locate 1045 without leaking credential-bearing exception messages or running recovery.
    @param monkeypatch: Offline connection and executor replacement boundary.
    @param role: Authentication boundary that rejects a synthetic credential.
    @return: None.
    """
    state = {'stage': 'R_FK_PRECHECK', 'session_blocked': True}  # [safe diagnostics]: No private connection state.
    credentials = ['synthetic-dba', 'synthetic-harness']
    dba, harness, executor = MagicMock(), MagicMock(), MagicMock()
    connector = MagicMock(return_value=dba)
    verifier = MagicMock(return_value=harness)
    (connector if role == 'dba' else verifier).side_effect = pymysql.err.OperationalError(1045, 'synthetic-private-password')
    monkeypatch.setattr(repair, 'open_connection', connector)
    monkeypatch.setattr(repair, 'verified_connection', verifier)
    monkeypatch.setattr(repair, 'execute', executor)
    with pytest.raises(pymysql.err.OperationalError):
        repair.run_received(({'dba_user': 'root'}, {}, {}, {}, credentials), state)
    assert state['failure_step'] == 'R_FK_' + role.upper() + '_CONNECT'
    assert state['mysql_error_number'] == 1045 and state['session_blocked'] is True
    assert 'synthetic' not in json.dumps(state) and credentials == []
    executor.assert_not_called()
    assert verifier.call_count == int(role == 'harness')
    assert dba.close.call_count == int(role == 'harness')


def test_credentials_keep_role_order_and_connection_phase_does_not_mask_later_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Preserve private credentials exactly and reset completed connection diagnostics.
    @param monkeypatch: Entirely offline connectors and session preparation.
    @return: None.
    """
    context = {'header': {'dba_user': 'root'}}  # [private context]: Never serialized by the worker.
    state = {'stage': 'R_FK_PRECHECK'}
    connector, verifier = MagicMock(), MagicMock()
    monkeypatch.setattr(repair, 'open_connection', connector)
    monkeypatch.setattr(repair, 'verified_connection', verifier)
    monkeypatch.setattr(repair, 'prepare_session', MagicMock(side_effect=[11, 12]))
    repair.acquire_connections(context, ['測試 DBA\"\n', '測試 Harness\\'], state)
    for settings, user, password in ((connector.call_args.args[0], 'root', '測試 DBA\"\n'),
                                     (verifier.call_args.args[0], repair.HARNESS_USER, '測試 Harness\\')):
        assert (settings.host, settings.port, settings.database) == ('127.0.0.1', 3306, 'jukebox101_test')
        assert settings.user == user and settings.password == password
    assert context['dba_id'] == 11 and context['harness_id'] == 12
    assert 'current_step' not in state and 'failure_step' not in state


@pytest.mark.parametrize('role', ['dba', 'harness'])
def test_session_failure_is_not_reported_as_authentication(monkeypatch: pytest.MonkeyPatch, role: str) -> None:
    """@description: Attribute post-login lock or metadata failures to the actual session boundary.
    @param monkeypatch: Offline connectors and session failure injection.
    @param role: Session initialization that fails after both logins.
    @return: None.
    """
    state = {'stage': 'R_FK_PRECHECK'}  # [failure attribution]: Distinguish successful logins from subsequent SQL.
    monkeypatch.setattr(repair, 'open_connection', MagicMock())
    monkeypatch.setattr(repair, 'verified_connection', MagicMock())
    error = pymysql.err.OperationalError(1142, 'synthetic-private-driver-message')
    monkeypatch.setattr(repair, 'prepare_session', MagicMock(side_effect=[error] if role == 'dba' else [11, error]))
    with pytest.raises(pymysql.err.OperationalError):
        repair.acquire_connections({'header': {'dba_user': 'root'}}, ['synthetic', 'synthetic'], state)
    assert state['failure_step'] == 'R_FK_' + role.upper() + '_SESSION_SETUP'
    assert state['mysql_error_number'] == 1142 and 'synthetic' not in json.dumps(state)


def definitions(action: str = '') -> dict:
    """@description: Model SHOW CREATE formatting while making synthetic provenance explicit.
    @param action: Reviewed optional action clause.
    @return: Complete synthetic 23-table definitions for pure delta checks.
    """
    result = {name: 'synthetic ' + name for name in TABLES}  # [test-only schema]: No real database claim.
    result['jb2_versions'] = 'CREATE TABLE `jb2_versions` (\n  `canonical_id` varchar(128) NOT NULL,\n' + \
        '  CONSTRAINT `fk_jb2_version_root` FOREIGN KEY (`canonical_id`) REFERENCES `jb2_versions` (`version_id`)' + action + '\n) ENGINE=InnoDB'
    return result


@pytest.mark.parametrize('action', ['', ' ON DELETE NO ACTION', ' ON DELETE RESTRICT ON UPDATE NO ACTION'])
def test_only_root_action_changes_and_original_clause_is_preserved(action: str) -> None:
    """@description: Pin every byte except the single action and preserve the exact restore SQL.
    @param action: Original accepted metadata action formatting.
    @return: None.
    """
    original = definitions(action)
    proof = window.build_window(original, window.digest(original))
    window.require_schema(original, proof, False)
    window.require_schema(dict(proof.cascade), proof, True)
    assert {name for name in TABLES if dict(proof.cascade)[name] != original[name]} == {'jb2_versions'}
    assert 'ON DELETE CASCADE' in proof.cascade_clause
    assert proof.original_clause.endswith(action or '(`version_id`)')
    assert window.switch_sql(proof, False)[0].endswith(proof.original_clause)
    assert all('FOREIGN_KEY_CHECKS' not in sql for sql in window.switch_sql(proof, True))


@pytest.mark.parametrize('phase', [False, True, 'bridge'])
def test_original_cascade_and_bridge_have_exact_distinct_metadata(phase: bool | str) -> None:
    """@description: Distinguish confirmed states instead of inferring rollback from an error number.
    @param phase: Expected fully enumerated current FK state.
    @return: None.
    """
    original = definitions()
    proof = window.build_window(original, window.digest(original))
    variants = (proof.bridge_original_first, proof.bridge_temporary_first) if phase == 'bridge' else (proof.cascade if phase is True else proof.original,)
    for variant in variants:
        window.require_schema(dict(variant), proof, phase)
        with pytest.raises(HarnessSafetyError):
            window.require_schema(dict(variant), proof, not phase)


def test_four_statement_plan_preserves_a_foreign_key_without_name_collision() -> None:
    """@description: Avoid both duplicate ADD names and an unprotected gap without simulating a MySQL server.
    @param: none
    @return: None.
    """
    original = definitions()
    proof = window.build_window(original, window.digest(original))
    present = {'fk_jb2_version_root'}  # [symbolic plan proof]: Constraint names only, not SQL execution semantics.
    for sql in window.switch_sql(proof, True) + window.switch_sql(proof, False):
        assert not (' ADD ' in sql and ' DROP ' in sql)
        if ' ADD CONSTRAINT ' in sql:
            name = sql.split('ADD CONSTRAINT `')[1].split('`')[0]
            assert name not in present
            present.add(name)
        else:
            name = sql.split('DROP FOREIGN KEY `')[1].split('`')[0]
            assert name in present
            present.remove(name)
        assert present
    assert present == {'fk_jb2_version_root'}


@pytest.mark.parametrize('cascade', [True, False])
@pytest.mark.parametrize('failure_index', [0, 1])
@pytest.mark.parametrize('number', [1826, 2013])
def test_each_ddl_failure_is_unknown_and_never_retried(cascade: bool, failure_index: int, number: int) -> None:
    """@description: Preserve partial successes and refuse DELETE or another DDL after any failed response.
    @param cascade: Forward switch or original-FK restoration.
    @param failure_index: Which separately attempted DDL loses a successful acknowledgement.
    @param number: Synthetic duplicate-name or connection-loss error.
    @return: None.
    """
    original = definitions()
    proof = window.build_window(original, window.digest(original))
    connection, state, persist, checkpoint = MagicMock(), {}, MagicMock(), MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = [None] * failure_index + [pymysql.OperationalError(number, 'private-driver')]
    with pytest.raises(pymysql.OperationalError):
        window.apply_switch(connection, proof, cascade, state, persist, checkpoint)
    assert cursor.execute.call_count == failure_index + 1
    assert state['fk_ddl_progress'][-1]['outcome'] == 'UNKNOWN'
    assert [item['outcome'] for item in state['fk_ddl_progress'][:-1]] == ['ACKNOWLEDGED'] * failure_index
    assert state['restore_ddl_outcome' if not cascade else 'cascade_ddl_outcome'] == 'UNKNOWN'
    connection.rollback.assert_not_called()


def test_bridge_checkpoint_failure_keeps_previous_constraint_and_stops() -> None:
    """@description: Never drop the old FK if the additive bridge cannot be independently verified.
    @param: none
    @return: None.
    """
    original = definitions()
    proof = window.build_window(original, window.digest(original))
    connection, state = MagicMock(), {}
    checkpoint = MagicMock(side_effect=[None, HarnessSafetyError('BRIDGE_DRIFT')])
    with pytest.raises(HarnessSafetyError):
        window.apply_switch(connection, proof, True, state, MagicMock(), checkpoint)
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.assert_called_once()
    assert ' ADD CONSTRAINT ' in cursor.execute.call_args.args[0]
    assert state['fk_ddl_progress'][0]['outcome'] == 'ACKNOWLEDGED'
    assert state['cascade_ddl_outcome'] == 'UNKNOWN'


def test_already_cascade_is_not_an_automatic_resume_authorization() -> None:
    """@description: Keep the original start gate mandatory after a previous unknown DDL outcome.
    @param: none
    @return: None.
    """
    original = definitions()
    proof = window.build_window(original, window.digest(original))
    connection = MagicMock()
    checkpoint = lambda phase: window.require_schema(dict(proof.cascade), proof, phase)
    with pytest.raises(HarnessSafetyError):
        window.apply_switch(connection, proof, True, {}, MagicMock(), checkpoint)
    connection.cursor.return_value.__enter__.return_value.execute.assert_not_called()


def test_missing_fk_is_neither_an_approved_bridge_nor_a_resume_point() -> None:
    """@description: Refuse a partially modified table without guessing how to reconstruct missing enforcement.
    @param: none
    @return: None.
    """
    original = definitions()
    proof = window.build_window(original, window.digest(original))
    missing = dict(original)
    missing['jb2_versions'] = missing['jb2_versions'].replace('  ' + proof.original_clause, '')
    for phase in (False, True, 'bridge'):
        with pytest.raises(HarnessSafetyError):
            window.require_schema(missing, proof, phase)


@pytest.mark.parametrize('phase', [False, True, 'bridge'])
def test_metadata_constraint_name_sets_match_each_phase(monkeypatch: pytest.MonkeyPatch, phase: bool | str) -> None:
    """@description: Allow the known overlap only in DDL checkpoints, not during cleanup DML.
    @param monkeypatch: Synthetic complete metadata rows.
    @param phase: Required original, temporary or bridge inventory.
    @return: None.
    """
    fields = ('TABLE_NAME','COLUMN_NAME','ORDINAL_POSITION','REFERENCED_TABLE_SCHEMA','REFERENCED_TABLE_NAME','REFERENCED_COLUMN_NAME')
    edges = [dict(zip(fields, edge)) for edge in recovery.reviewed_foreign_keys()]
    names = window.constraint_names(recovery.reviewed_constraint_names(), phase)
    rules = [dict(TABLE_NAME=table, CONSTRAINT_NAME=name, UPDATE_RULE='NO ACTION',
                  DELETE_RULE='CASCADE' if name == window.TEMP_ROOT_NAME else 'NO ACTION') for table, name in names]
    monkeypatch.setattr(recovery, 'query', MagicMock(side_effect=[edges, (), rules]))
    recovery.verify_fk_metadata(MagicMock(), phase)
    assert len(names) == 26 + int(phase == 'bridge')


@pytest.mark.parametrize('mutation', ['hash', 'missing', 'cascade', 'extra_fk', 'wrong_column'])
def test_invalid_original_metadata_never_generates_a_window(mutation: str) -> None:
    """@description: Reject unreviewed or already modified schema before DDL intent exists.
    @param mutation: Drift or unexpected root constraint shape.
    @return: None.
    """
    original = definitions()
    if mutation == 'missing':
        original.pop('users')
    if mutation == 'cascade':
        original = definitions(' ON DELETE CASCADE')
    if mutation == 'extra_fk':
        original['jb2_versions'] += '\n' + original['jb2_versions'].splitlines()[2]
    if mutation == 'wrong_column':
        original['jb2_versions'] = original['jb2_versions'].replace('FOREIGN KEY (`canonical_id`)', 'FOREIGN KEY (`other`)')
    approved = '0' * 64 if mutation == 'hash' else window.digest(original)
    with pytest.raises(HarnessSafetyError):
        window.build_window(original, approved)


def test_cascade_does_not_allow_unrelated_schema_drift() -> None:
    """@description: Forbid accepting today's structure merely because the root action matches.
    @param: none
    @return: None.
    """
    original = definitions()
    proof = window.build_window(original, window.digest(original))
    changed = dict(proof.cascade)
    changed['users'] += ' changed'
    with pytest.raises(HarnessSafetyError, match='SCHEMA_DRIFT'):
        window.require_schema(changed, proof, True)


@pytest.mark.parametrize('aliases', [(), ({'version_id': 'registered-but-not-deleted'},), ({'version_id': 'unknown'},)])
def test_both_cleanup_paths_refuse_any_remaining_alias(aliases: tuple) -> None:
    """@description: Require alias deletion evidence rather than relying on implicit CASCADE row counts.
    @param aliases: Remaining children returned by the database, regardless of ownership label.
    @return: None.
    """
    from tests.batch_b_harness_database import delete_primary
    cursor = MagicMock(rowcount=1)
    cursor.fetchall.return_value = aliases
    if aliases:
        with pytest.raises(HarnessSafetyError, match='ALIAS_STILL_PRESENT'):
            delete_primary(cursor, 'jb2_versions', {'version_id': 'root'})
        assert not any(call.args[0].startswith('DELETE') for call in cursor.execute.call_args_list)
    else:
        delete_primary(cursor, 'jb2_versions', {'version_id': 'root'})
        assert cursor.execute.call_args.args[1] == ('root',)


@pytest.mark.parametrize('failure', [False, True])
def test_ddl_is_journaled_once_and_never_retried_or_rolled_back(failure: bool) -> None:
    """@description: Keep DDL uncertainty distinct from transactional cleanup rollback.
    @param failure: Synthetic lost DDL response.
    @return: None.
    """
    original = definitions()
    proof = window.build_window(original, window.digest(original))
    connection, state, persist = MagicMock(), {}, MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    if failure:
        cursor.execute.side_effect = pymysql.OperationalError(2013, 'synthetic-private')
        with pytest.raises(pymysql.OperationalError):
            window.apply_switch(connection, proof, True, state, persist, MagicMock())
    else:
        window.apply_switch(connection, proof, True, state, persist, MagicMock())
    assert cursor.execute.call_count == (1 if failure else 2)
    connection.rollback.assert_not_called()
    assert state['cascade_ddl_outcome'] == ('UNKNOWN' if failure else 'ACKNOWLEDGED')
    assert state['fk_restoration_pending'] is True


@pytest.mark.parametrize('action', ['', ' ON DELETE NO ACTION', ' ON DELETE CASCADE'])
def test_normal_harness_refuses_known_failure_before_enabling(action: str) -> None:
    """@description: Do not let an original NO ACTION suite produce the same residual-data incident again.
    @param action: Actual schema baseline during ordinary harness startup.
    @return: None.
    """
    from tests.batch_b_harness import MySQLHarness
    harness = MySQLHarness(None, None, journal=Path('not-written.json'))
    original = definitions()
    harness.definitions = dict(window.build_window(original, window.digest(original)).cascade) if 'CASCADE' in action else definitions(action)
    harness.verify_application = MagicMock(side_effect=RuntimeError('reached-next-readonly-probe'))
    with pytest.raises(RuntimeError, match='reached-next' if 'CASCADE' in action else 'REQUIRES_FK_WINDOW'):
        harness.verify_cleanup_readiness()
    assert not harness.enabled
    if 'CASCADE' not in action:
        harness.verify_application.assert_not_called()


@pytest.mark.parametrize('mutation', ['self_only', 'other_cascade', 'update_cascade', 'external', 'missing'])
def test_scoped_metadata_permission_is_only_one_exact_delete_action(monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    """@description: Preserve complete FK graph protection in the narrowly scoped temporary window.
    @param monkeypatch: Synthetic metadata responses.
    @param mutation: Expected root action or a forbidden metadata change.
    @return: None.
    """
    fields = ('TABLE_NAME','COLUMN_NAME','ORDINAL_POSITION','REFERENCED_TABLE_SCHEMA','REFERENCED_TABLE_NAME','REFERENCED_COLUMN_NAME')
    edges = [dict(zip(fields, edge)) for edge in recovery.reviewed_foreign_keys()]
    rules = [dict(TABLE_NAME=table, CONSTRAINT_NAME=name, UPDATE_RULE='NO ACTION', DELETE_RULE='NO ACTION')
             for table, name in sorted(recovery.reviewed_constraint_names())]
    root = next(row for row in rules if row['CONSTRAINT_NAME'] == 'fk_jb2_version_root')
    root['DELETE_RULE'] = 'CASCADE'
    root['CONSTRAINT_NAME'] = window.TEMP_ROOT_NAME
    if mutation == 'other_cascade':
        next(row for row in rules if row is not root)['DELETE_RULE'] = 'CASCADE'
    if mutation == 'update_cascade':
        root['UPDATE_RULE'] = 'CASCADE'
    if mutation == 'missing':
        rules.remove(root)
    monkeypatch.setattr(recovery, 'query', MagicMock(side_effect=[edges, ({'TABLE_SCHEMA': 'unknown'},) if mutation == 'external' else (), rules]))
    if mutation == 'self_only':
        recovery.verify_fk_metadata(MagicMock(), True)
    else:
        with pytest.raises(HarnessSafetyError):
            recovery.verify_fk_metadata(MagicMock(), True)


@pytest.mark.parametrize('outcome', ['rollback_ack', 'rollback_unknown', 'commit_unknown', 'commit_ack', 'ddl_unknown', 'safety_failure', 'restore_started'])
def test_failure_repair_only_when_outcomes_and_data_are_proven(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    """@description: Do not use an unknown commit or DDL as permission for another ALTER.
    @param monkeypatch: Exact transition spy; no SQL execution.
    @param outcome: Prior acknowledged or uncertain failure boundary.
    @return: None.
    """
    context = dict(before={'before': ()}, journal={})
    state = dict(cascade_verified=True, session_blocked=True)
    if outcome.startswith('rollback'):
        state['rollback_outcome'] = 'ACKNOWLEDGED' if outcome.endswith('ack') else 'UNKNOWN'
    elif outcome == 'ddl_unknown':
        state['restore_ddl_outcome'] = 'UNKNOWN'
    elif outcome in {'safety_failure', 'restore_started'}:
        state['rollback_outcome'] = 'ACKNOWLEDGED'
        state['error_type'] = 'HarnessSafetyError' if outcome == 'safety_failure' else 'RuntimeError'
        state['restore_phase_started'] = outcome == 'restore_started'
    else:
        state['commit_outcome'] = 'UNKNOWN' if outcome == 'commit_unknown' else 'ACKNOWLEDGED'
        state['read_only_baseline_matches'] = True
    transition = MagicMock()
    monkeypatch.setattr(repair, 'transition', transition)
    monkeypatch.setattr(recovery, 'expected_baseline', lambda _: {'baseline': ()})
    repair.restore_after_failure(context, state)
    assert transition.call_count == int(outcome in {'rollback_ack', 'commit_ack'})
    assert state['session_blocked'] is True


def test_fk_restore_failure_remains_pending_and_does_not_hide_primary_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Preserve the rollback result and pending FK obligation if restoring metadata fails.
    @param monkeypatch: Synthetic ALTER failure boundary.
    @return: None.
    """
    state = dict(cascade_verified=True, rollback_outcome='ACKNOWLEDGED', error_type='IntegrityError', baseline_restored=False)
    monkeypatch.setattr(repair, 'transition', MagicMock(side_effect=pymysql.OperationalError(2013, 'private-sql')))
    repair.restore_after_failure({'before': {}}, state)
    assert state['fk_restoration_pending'] is True
    assert state['fk_restore_mysql_error_number'] == 2013
    assert state['error_type'] == 'IntegrityError'
    assert state['baseline_restored'] is False


@pytest.mark.parametrize('phase', ['dml', 'restore', 'success'])
def test_coordinator_never_confuses_data_success_with_original_fk_success(monkeypatch: pytest.MonkeyPatch, phase: str) -> None:
    """@description: Exercise ordering around acknowledged DDL and DML without weakening the existing transaction checks.
    @param monkeypatch: Separate phase callbacks; core manifest and schema guards are tested independently.
    @param phase: Injected failure or fully verified orchestration.
    @return: None.
    """
    state = dict(session_blocked=True, baseline_restored=False)
    context = dict(before={}, journal={})
    monkeypatch.setattr(repair, 'inspect_original', lambda *args: None)
    monkeypatch.setattr(recovery, 'expected_baseline', lambda _: {})
    monkeypatch.setattr(recovery, 'save', lambda *args: None)
    monkeypatch.setattr(repair, 'inspect_unknown', lambda *args: None)
    monkeypatch.setattr(repair, 'restore_after_failure', MagicMock())
    monkeypatch.setattr(repair, 'transition', MagicMock(side_effect=[None, RuntimeError('restore')] if phase == 'restore' else None))
    monkeypatch.setattr(repair, 'settle', MagicMock(side_effect=RuntimeError('dml') if phase == 'dml' else None))
    monkeypatch.setattr(recovery, 'verify_after', lambda *args: state.update(baseline_restored=True, fk_restoration_pending=False))
    if phase == 'success':
        repair.execute(context, 'synthetic', state)
        assert state['baseline_restored'] is True
    else:
        with pytest.raises(RuntimeError):
            repair.execute(context, 'synthetic', state)
        assert state['baseline_restored'] is False
    assert state['session_blocked'] is True


def test_unknown_commit_only_uses_fresh_read_and_never_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Preserve uncertainty even when a fresh session can observe the restored data.
    @param monkeypatch: Read-only verification spy.
    @return: None.
    """
    connection, verify = MagicMock(), MagicMock()
    state = dict(commit_outcome='UNKNOWN', baseline_restored=False)
    context = dict(harness=connection, journal={}, window=object())
    monkeypatch.setattr(recovery, 'verify_after', verify)
    repair.inspect_unknown(context, 'synthetic', state)
    connection.close.assert_called_once()
    verify.assert_called_once()
    assert state['commit_outcome'] == 'UNKNOWN'


@pytest.mark.parametrize('drift', ['schema', 'unknown_data', 'unknown_reference'])
def test_ddl_preflight_drift_stops_before_alter(monkeypatch: pytest.MonkeyPatch, drift: str) -> None:
    """@description: Recheck structure, incoming dependencies and data after releasing transactional metadata locks.
    @param monkeypatch: Synthetic read-only metadata boundary.
    @param drift: Drift that must prevent any ALTER call.
    @return: None.
    """
    original = definitions()
    proof = window.build_window(original, window.digest(original))
    context = dict(dba=MagicMock(), window=proof)
    monkeypatch.setattr(repair, 'check_evidence', lambda: None)
    monkeypatch.setattr(repair, 'require_exclusive', lambda *args: None)
    monkeypatch.setattr(repair, 'schema_fingerprints', lambda _: {} if drift == 'schema' else original)
    monkeypatch.setattr(recovery, 'verify_fk_metadata', MagicMock(side_effect=HarnessSafetyError('UNKNOWN_REFERENCE') if drift == 'unknown_reference' else None))
    monkeypatch.setattr(repair, 'read_inventory', lambda _: {'unknown': ()} if drift == 'unknown_data' else {})
    monkeypatch.setattr(recovery, 'save', lambda *args: None)
    with pytest.raises(HarnessSafetyError):
        repair.transition(context, True, {}, {})
    context['dba'].cursor.return_value.__enter__.return_value.execute.assert_not_called()


@pytest.mark.parametrize('obstacle', ['other_session', 'event', 'restricted_dba'])
def test_full_dba_visibility_and_quiescence_are_required(monkeypatch: pytest.MonkeyPatch, obstacle: str) -> None:
    """@description: Do not substitute advisory locks for checking other possible writers and scheduled work.
    @param monkeypatch: Synthetic full-visibility metadata queries.
    @param obstacle: A condition that blocks even a previously approved UUID.
    @return: None.
    """
    grants = ({'grant': 'GRANT ' + ('SELECT' if obstacle == 'restricted_dba' else 'ALL PRIVILEGES') + ' ON *.* TO root WITH GRANT OPTION'},)
    replies = [grants, ({'ID': 99},) if obstacle == 'other_session' else (), ({'EVENT_NAME': 'scheduled'},) if obstacle == 'event' else ()]
    monkeypatch.setattr(repair, 'query', MagicMock(side_effect=replies))
    monkeypatch.setattr(repair, 'check_dba', lambda *args: None)
    with pytest.raises(HarnessSafetyError):
        repair.require_exclusive(MagicMock(), dict(header={'dba_user': 'root'}, dba_id=1, harness_id=2))


@pytest.mark.parametrize('changed', ['original', 'manifest', 'failed_attempt', 'ddl1826'])
def test_all_original_artifacts_are_rechecked_before_ddl(monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    """@description: Preserve source evidence rather than treating a parsed manifest as permanently valid.
    @param monkeypatch: In-memory evidence bytes only.
    @param changed: Historical source whose integrity was lost.
    @return: None.
    """
    import hashlib
    valid = hashlib.sha256(b'valid').hexdigest()
    monkeypatch.setattr(recovery, 'MANIFEST_HASH', valid)
    monkeypatch.setattr(repair, 'FAILED_HASH', valid)
    monkeypatch.setattr(repair, 'DDL_FAILURE_HASH', valid)
    monkeypatch.setattr(recovery, 'require_journal_hash', MagicMock(side_effect=HarnessSafetyError('CHANGED') if changed == 'original' else None))
    monkeypatch.setattr(Path, 'read_bytes', lambda path: b'changed' if (path == recovery.MANIFEST_FILE and changed == 'manifest') or
                        (path == repair.FAILED_JOURNAL and changed == 'failed_attempt') or
                        (path == repair.DDL_FAILURE_JOURNAL and changed == 'ddl1826') else b'valid')
    with pytest.raises(HarnessSafetyError):
        repair.check_evidence()


def test_final_journal_failure_cannot_report_success(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """@description: Keep restoration blocked if its final audit evidence cannot be persisted.
    @param monkeypatch: Synthetic journal I/O failure.
    @param capsys: Sanitized public report.
    @return: None.
    """
    monkeypatch.setattr(recovery, 'save', MagicMock(side_effect=OSError('private-path')))
    state = dict(stage='R_FK_RESTORED_VERIFIED_BLOCKED', baseline_restored=True, session_blocked=True, recovery_journal='synthetic')
    assert repair.emit(state) == 1
    report = json.loads(capsys.readouterr().out)
    assert report['baseline_restored'] is False
    assert report['session_blocked'] is True
    assert 'private-path' not in json.dumps(report)


def test_new_mode_is_disjoint_from_old_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Ensure choosing repair never provisions accounts or runs ordinary recovery or the FK Lab.
    @param monkeypatch: Explicit CLI argv and existing module executor spies.
    @return: None.
    """
    from tests import batch_b_acceptance as acceptance
    normal, worker = MagicMock(), MagicMock(return_value=0)
    monkeypatch.setattr(acceptance.sys, 'argv', ['entry', '--repair-recovery-fk'])
    monkeypatch.setattr(acceptance, 'main', normal)
    monkeypatch.setattr(repair, 'main', worker)
    assert acceptance.entry() == 0
    normal.assert_not_called()
    worker.assert_called_once()


def test_wrong_authorization_refused_before_database_access(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """@description: Keep this new mode independent from old restore, Lab and provisioning authorizations.
    @param monkeypatch: Connection creation spy.
    @param capsys: Safe failure report collector.
    @return: None.
    """
    connect = MagicMock()
    monkeypatch.setattr(repair, 'open_connection', connect)
    assert repair.main(io.StringIO('{"authorization":"BATCH_B_A_PLUS_B"}\n')) == 1
    connect.assert_not_called()
    assert json.loads(capsys.readouterr().out)['session_blocked'] is True


def test_new_runtime_functions_follow_project_rules() -> None:
    """@description: Enforce engineering limits on the shared repair code without new dependencies.
    @param: none
    @return: None.
    """
    from tests.test_phase2_quality import _implementation_lines, _control_depth, _assert_function_documentation
    for module in (window, repair):
        source = Path(module.__file__).read_text(encoding='utf8')
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.FunctionDef):
                assert _implementation_lines(node, source.splitlines()) <= 25, node.name
                assert _control_depth(node) <= 3, node.name
                _assert_function_documentation(node, Path(module.__file__))
