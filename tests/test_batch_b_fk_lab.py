"""Check the isolated FK experiment executor without network or database access.

Synthetic cursors test orchestration and refusal paths; they are not evidence of InnoDB behavior.
"""

import ast
import io
import json
from pathlib import Path
from unittest.mock import MagicMock
import pymysql
import pytest
from tests import batch_b_fk_lab as lab
from tests.batch_b_harness_contract import HarnessSafetyError


def header() -> dict:
    """@description: Supply a synthetic separately authorized lab protocol.
    @param: none
    @return: Nonsecret fixed test header.
    """
    return dict(authorization='FK_LAB_CREATE_AND_TEST', dba_user='root', server_uuid='11111111-2222-4333-8444-555555555555')


@pytest.mark.parametrize('expanded', [False, True])
def test_existing_global_dba_equivalent_formats(expanded: bool) -> None:
    """@description: Accept MySQL 8 expanded static authority as equivalent to literal ALL PRIVILEGES.
    @param expanded: Server representation of the same global grant.
    @return: None.
    """
    privileges = ', '.join(sorted(lab.MYSQL80_STATIC_ALL)) if expanded else 'ALL PRIVILEGES'
    grants = ({'grant': 'GRANT ' + privileges + ' ON *.* TO `root`@`localhost` WITH GRANT OPTION'},)
    assert lab.existing_dba_authority(grants)


@pytest.mark.parametrize('missing', sorted(lab.MYSQL80_STATIC_ALL))
def test_incomplete_expanded_grants_refused(missing: str) -> None:
    """@description: Do not turn privilege-format compatibility into a partial DBA bypass.
    @param missing: One absent static global privilege.
    @return: None.
    """
    privileges = ', '.join(sorted(lab.MYSQL80_STATIC_ALL - {missing}))
    assert not lab.existing_dba_authority(({'grant': 'GRANT ' + privileges + ' ON *.* TO root@localhost WITH GRANT OPTION'},))


@pytest.mark.parametrize('statement', ['GRANT ALL PRIVILEGES ON jukebox101_fk_lab.* TO root@localhost WITH GRANT OPTION',
    'GRANT ALL PRIVILEGES ON *.* TO root@localhost', 'GRANT UNKNOWN_PRIVILEGE ON *.* TO root@localhost WITH GRANT OPTION',
    'GRANT SELECT, CREATE, DROP ON *.* TO root@localhost WITH GRANT OPTION', 'unrecognized'])
def test_unknown_or_scoped_authority_refused(statement: str) -> None:
    """@description: Refuse unsupported evidence rather than guessing effective metadata visibility.
    @param statement: Inadequate or unrecognized server grant form.
    @return: None.
    """
    assert not lab.existing_dba_authority(({'grant': statement},))


def test_partial_revoke_blocks_global_all() -> None:
    """@description: Prevent a global grant from hiding schema-specific metadata restrictions.
    @param: none
    @return: None.
    """
    grants = ({'grant': 'GRANT ALL PRIVILEGES ON *.* TO root@localhost WITH GRANT OPTION'},
              {'grant': 'REVOKE SELECT ON hidden.* FROM root@localhost'})
    assert not lab.existing_dba_authority(grants)


@pytest.mark.parametrize('change', [{'authorization': 'BATCH_B_A_PLUS_B'}, {'dba_user': 'jukebox101_test_harness'},
                                  {'database': 'jukebox101_test'}, {'server_uuid': 'invalid'}])
def test_bad_protocol_never_connects(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, change: dict) -> None:
    """@description: Refuse mismatched authorization before requesting any database connection.
    @param monkeypatch: Connection spy.
    @param capsys: Sanitized output collector.
    @param change: Invalid protocol mutation.
    @return: None.
    """
    protocol = header()  # [invalid request]: No fallback to application or recovery credentials.
    protocol.update(change)
    connect = MagicMock()
    monkeypatch.setattr(lab.pymysql, 'connect', connect)
    assert lab.main(io.StringIO(json.dumps(protocol) + '\n"private-secret"\n')) == 1
    connect.assert_not_called()
    assert 'private-secret' not in capsys.readouterr().out


@pytest.mark.parametrize('case,number,remaining', [('no_action', 1451, 1), ('cascade_root', None, 0), ('cascade_alias', None, 0)])
def test_experiment_rolls_back_and_measures(monkeypatch: pytest.MonkeyPatch, case: str, number: int | None, remaining: int) -> None:
    """@description: Exercise the real orchestration without claiming a synthetic server result is proof.
    @param monkeypatch: Synthetic metadata and error boundary.
    @param case: Reviewed experiment.
    @param number: Injected DELETE outcome.
    @param remaining: Injected count after DELETE.
    @return: None.
    """
    connection, cursor = MagicMock(), MagicMock()
    monkeypatch.setattr(lab, 'delete_root', lambda *args: number)
    monkeypatch.setattr(lab, 'query', lambda *args: ({'count': remaining},))
    result = lab.experiment(connection, cursor, case)
    assert result['passed'] is True
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    inserts = [call for call in cursor.execute.call_args_list if call.args[0].startswith('INSERT')]
    assert len(inserts) == (2 if case == 'cascade_alias' else 1)


def test_unexpected_failure_still_rolls_back() -> None:
    """@description: Do not retain experimental DML when the server rejects an INSERT.
    @param: none
    @return: None.
    """
    connection, cursor = MagicMock(), MagicMock()
    cursor.execute.side_effect = pymysql.OperationalError(2013, 'private-secret')
    with pytest.raises(pymysql.OperationalError):
        lab.experiment(connection, cursor, 'cascade_root')
    connection.rollback.assert_called_once()


def test_ddl_is_lab_only_with_distinct_constraint_names() -> None:
    """@description: Catch schema-wide constraint-name collisions without executing DDL.
    @param: none
    @return: None.
    """
    cursor = MagicMock()
    lab.create_lab(cursor)
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert statements[0] == 'CREATE DATABASE `jukebox101_fk_lab`'
    assert len(statements) == 4
    for case, statement in zip(lab.CASES, statements[1:]):
        assert lab.table_name(case) in statement
        assert 'CONSTRAINT `probe_' + case + '_root`' in statement
        assert 'canonical_id VARCHAR(128) NOT NULL' in statement
        assert 'ON DELETE ' + ('NO ACTION' if case == 'no_action' else 'CASCADE') in statement


def test_failure_closes_connection_without_cleanup_or_retry(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """@description: Preserve partial lab evidence and sanitize failures rather than replaying DDL.
    @param monkeypatch: Synthetic connection and interrupted creation.
    @param capsys: Safe public report collector.
    @return: None.
    """
    connection, cleanup = MagicMock(), MagicMock()
    monkeypatch.setattr(lab.pymysql, 'connect', MagicMock(return_value=connection))
    monkeypatch.setattr(lab, 'verify_server', lambda *args: None)
    monkeypatch.setattr(lab, 'create_lab', MagicMock(side_effect=pymysql.OperationalError(2013, 'private-secret')))
    monkeypatch.setattr(lab, 'cleanup_lab', cleanup)
    assert lab.main(io.StringIO(json.dumps(header()) + '\n"private-secret"\n')) == 1
    report = capsys.readouterr().out
    assert 'private-secret' not in report
    assert json.loads(report)['mysql_error_number'] == 2013
    connection.close.assert_called_once()
    connection.commit.assert_not_called()
    cleanup.assert_not_called()


@pytest.mark.parametrize('number', [1451, 1062])
def test_delete_only_tolerates_expected_fk_error(number: int) -> None:
    """@description: Avoid misclassifying unrelated errors as proof of a self-FK restriction.
    @param number: Synthetic MySQL error number.
    @return: None.
    """
    cursor = MagicMock()
    cursor.execute.side_effect = pymysql.IntegrityError(number, 'private-driver')
    if number == 1451:
        assert lab.delete_root(cursor, 'no_action') == 1451
    else:
        with pytest.raises(pymysql.IntegrityError):
            lab.delete_root(cursor, 'no_action')


@pytest.mark.parametrize('drift', ['object', 'residue'])
def test_cleanup_refuses_unknown_objects_or_rows(monkeypatch: pytest.MonkeyPatch, drift: str) -> None:
    """@description: Preserve the lab instead of deleting unknown objects or unrolled-back evidence.
    @param monkeypatch: Synthetic lab inventory.
    @param drift: Unexpected object or residual row.
    @return: None.
    """
    cursor = MagicMock()
    tables = tuple({'TABLE_NAME': 'probe_' + case} for case in lab.CASES)
    monkeypatch.setattr(lab, 'query', lambda _, sql, *args: (({'TABLE_NAME': 'unknown'},) if drift == 'object' else tables)
                        if 'information_schema' in sql else ({'count': 1},))
    with pytest.raises(HarnessSafetyError):
        lab.cleanup_lab(cursor)
    cursor.execute.assert_not_called()


@pytest.mark.parametrize('drift', ['uuid', 'schema', 'fk', 'exists'])
def test_preflight_drift_refuses_before_ddl(monkeypatch: pytest.MonkeyPatch, drift: str) -> None:
    """@description: Bind lab creation to the approved instance and an absent dedicated schema.
    @param monkeypatch: Synthetic SELECT/SHOW metadata.
    @param drift: Violated isolation precondition.
    @return: None.
    """
    row = dict(db=None, account='root@localhost', port=3306, uuid=header()['server_uuid'], version='8.0.46', fk=1)
    row.update({'uuid': 'different'} if drift == 'uuid' else {'db': 'jukebox101_test'} if drift == 'schema' else {'fk': 0} if drift == 'fk' else {})
    results = [(row,), ({'grant': 'GRANT ALL PRIVILEGES ON *.* TO root WITH GRANT OPTION'},), ({'SCHEMA_NAME': lab.SCHEMA},) if drift == 'exists' else ()]
    monkeypatch.setattr(lab, 'query', MagicMock(side_effect=results))
    with pytest.raises(HarnessSafetyError):
        lab.verify_server(MagicMock(), header())


def test_lab_dispatch_never_calls_acceptance_or_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Keep lab execution disjoint from existing business and recovery entry points.
    @param monkeypatch: Explicit command mode and executor spies.
    @return: None.
    """
    from tests import batch_b_acceptance as acceptance
    normal, worker = MagicMock(), MagicMock(return_value=0)
    monkeypatch.setattr(acceptance.sys, 'argv', ['entry', '--fk-lab'])
    monkeypatch.setattr(acceptance, 'main', normal)
    monkeypatch.setattr(lab, 'main', worker)
    assert acceptance.entry() == 0
    normal.assert_not_called()
    worker.assert_called_once()


def test_sql_scope_and_runtime_quality() -> None:
    """@description: Enforce static lab names and project documentation and complexity rules.
    @param: none
    @return: None.
    """
    from tests.test_phase2_quality import _implementation_lines, _control_depth, _assert_function_documentation
    source = Path(lab.__file__).read_text(encoding='utf8')
    assert 'jukebox101_test' not in source
    assert 'FOREIGN_KEY_CHECKS=0' not in source
    with pytest.raises(HarnessSafetyError):
        lab.table_name('jukebox101_test.jb2_versions')
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef):
            assert _implementation_lines(node, source.splitlines()) <= 25, node.name
            assert _control_depth(node) <= 3, node.name
            _assert_function_documentation(node, Path(lab.__file__))
