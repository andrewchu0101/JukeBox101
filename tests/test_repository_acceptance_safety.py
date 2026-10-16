"""Verify rollback-only repository acceptance guards and supervisor cleanup entirely offline.

Synthetic settings and mock connections exercise failure paths without opening database sockets.
"""

from dataclasses import replace
from unittest.mock import MagicMock

import pymysql
import pytest

from bot.config import ConfigurationError, DatabaseSettings
from tests import repository_mysql_support as support
from tests import repository_mysql_concurrency as concurrency
from tests import test_repository_mysql as acceptance
from tests import repository_mysql_counts as counts_entry
from tests.mysql_safety import EXPECTED_TABLES


@pytest.fixture
def acceptance_settings(monkeypatch):
    """@description: Supply only synthetic dedicated test credentials and explicit opt-ins.
    @param monkeypatch: Isolated environment facility.
    @return: Fixed isolated configuration suitable only for mocked connection tests.
    """
    settings = DatabaseSettings("127.0.0.1", 3306, "jukebox101_test_app", "synthetic-secret", "jukebox101_test")
    environment = {
        "TEST_DB_HOST": settings.host, "TEST_DB_PORT": str(settings.port),
        "TEST_DB_USER": settings.user, "TEST_DB_PASSWORD": settings.password, "TEST_DB_NAME": settings.database,
        "JUKEBOX_RUN_MYSQL_TEST": "1", "JUKEBOX_RUN_REPOSITORY_MYSQL": "1",
        "JUKEBOX_TEST_SCHEMA_READY": "1", "JUKEBOX_DISPOSABLE_DATABASE": settings.database,
    }
    for setting_name, setting_value in environment.items():
        monkeypatch.setenv(setting_name, setting_value)
    return settings


@pytest.fixture
def acceptance_connection(monkeypatch):
    """@description: Trap all connections while observing ownership and cleanup callbacks.
    @param monkeypatch: Connection and server-preflight injection facility.
    @return: Owned mock connection and preflight verifier.
    """
    connection = MagicMock()
    preflight = MagicMock()
    monkeypatch.setattr(support, "open_connection", MagicMock(return_value=connection))
    monkeypatch.setattr(support, "validate_connected_test_database", preflight)
    monkeypatch.setattr(support, "table_counts", MagicMock(return_value=_zero_counts()))
    monkeypatch.setattr(support, "require_savepoint_timeout_mode", MagicMock())
    return connection, preflight


def _zero_counts() -> dict[str, int]:
    """@description: Represent the required empty isolated database baseline.
    @param: none
    @return: Six-table synthetic zero-count snapshot.
    """
    return dict.fromkeys(EXPECTED_TABLES, 0)


@pytest.mark.parametrize("switch", ["JUKEBOX_RUN_MYSQL_TEST", "JUKEBOX_RUN_REPOSITORY_MYSQL"])
def test_missing_switch_blocks_connection(acceptance_settings, monkeypatch, switch):
    """@description: Require both explicit opt-ins before any acceptance connection is created.
    @param acceptance_settings: Synthetic authorized environment.
    @param monkeypatch: Environment and connection injection facility.
    @param switch: Required authorization to remove.
    @return: None.
    """
    monkeypatch.setenv(switch, "0")
    connect = MagicMock()
    monkeypatch.setattr(support, "open_connection", connect)
    with pytest.raises(ConfigurationError):
        with support.rollback_repository_session(acceptance_settings):
            pytest.fail("Unauthorized session opened")
    connect.assert_not_called()


def test_production_settings_blocked_before_connect(acceptance_settings, monkeypatch):
    """@description: Prevent repository construction from redirecting fixtures to the production database.
    @param acceptance_settings: Dedicated synthetic TEST_DB configuration.
    @param monkeypatch: Connection trap facility.
    @return: None.
    """
    connect = MagicMock()
    monkeypatch.setattr(support, "open_connection", connect)
    with pytest.raises(ConfigurationError):
        with support.rollback_repository_session(replace(acceptance_settings, database="jukebox101")):
            pytest.fail("Production session opened")
    connect.assert_not_called()


def test_missing_test_password_has_no_fallback(acceptance_settings, monkeypatch):
    """@description: Keep production credentials unavailable to the new acceptance entry.
    @param acceptance_settings: Dedicated synthetic environment.
    @param monkeypatch: Environment isolation facility.
    @return: None.
    """
    monkeypatch.delenv("TEST_DB_PASSWORD")
    monkeypatch.setenv("DB_PASSWORD", "synthetic-production-secret")
    with pytest.raises(ConfigurationError, match="TEST_DB_PASSWORD"):
        support.authorized_repository_settings()


@pytest.mark.parametrize("preflight_failure", ["identity", "nonempty", "begin"])
def test_prewrite_failure_closes_connection(acceptance_settings, acceptance_connection, monkeypatch, preflight_failure):
    """@description: Close resources and never run repository SQL after a failed prerequisite.
    @param acceptance_settings: Authorized synthetic environment.
    @param acceptance_connection: Owned mocked connection and server verifier.
    @param monkeypatch: Precondition injection facility.
    @param preflight_failure: Pre-write stage that fails.
    @return: None.
    """
    connection, preflight = acceptance_connection
    if preflight_failure == "identity":
        preflight.side_effect = ConfigurationError("Synthetic identity mismatch")
    elif preflight_failure == "nonempty":
        monkeypatch.setattr(support, "table_counts", MagicMock(return_value={**_zero_counts(), "songs": 1}))
    else:
        connection.begin.side_effect = pymysql.OperationalError(2006, "Synthetic failure")
    with pytest.raises((ConfigurationError, pymysql.OperationalError)):
        with support.rollback_repository_session(acceptance_settings):
            pytest.fail("Preflight failure did not stop writes")
    connection.close.assert_called_once()
    connection.commit.assert_not_called()


def test_savepoint_success_never_commits(acceptance_settings, acceptance_connection):
    """@description: Verify operation success releases a savepoint while final session cleanup rolls back.
    @param acceptance_settings: Authorized synthetic environment.
    @param acceptance_connection: Mocked connection and preflight verifier.
    @return: None.
    """
    connection, preflight = acceptance_connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None
    with support.rollback_repository_session(acceptance_settings) as session:
        assert session.repository.balance(123) == 0
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert "SAVEPOINT repository_operation" in statements
    assert "RELEASE SAVEPOINT repository_operation" in statements
    assert "ROLLBACK TO SAVEPOINT repository_operation" not in statements
    assert connection.rollback.call_count == 2
    connection.commit.assert_not_called()
    connection.close.assert_called_once()
    preflight.assert_called_once_with(connection)


def test_sql_failure_uses_savepoint_rollback(acceptance_settings, acceptance_connection):
    """@description: Require SQL failures to roll back the operation and outer fixture transaction.
    @param acceptance_settings: Authorized synthetic configuration.
    @param acceptance_connection: Mocked resources.
    @return: None.
    """
    connection, preflight = acceptance_connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.execute.side_effect = [None, None, pymysql.OperationalError(1205, "Synthetic lock timeout"), None, None]
    with pytest.raises(pymysql.OperationalError):
        with support.rollback_repository_session(acceptance_settings) as session:
            session.repository.balance(123)
    statements = [call.args[0] for call in cursor.execute.call_args_list]
    assert "ROLLBACK TO SAVEPOINT repository_operation" in statements
    connection.commit.assert_not_called()
    connection.close.assert_called_once()


def test_rollback_failure_still_closes(acceptance_settings, acceptance_connection):
    """@description: Refuse clean completion if the outer transaction cannot be rolled back.
    @param acceptance_settings: Authorized synthetic configuration.
    @param acceptance_connection: Mocked resources.
    @return: None.
    """
    connection, preflight = acceptance_connection
    connection.rollback.side_effect = [None, pymysql.OperationalError(2006, "Synthetic cleanup failure")]
    with pytest.raises(pymysql.OperationalError):
        with support.rollback_repository_session(acceptance_settings):
            pass
    connection.close.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("violation", ["settings", "thread", "authorization"])
def test_operation_rechecks_ownership_and_authorization(acceptance_settings, acceptance_connection, monkeypatch, violation):
    """@description: Fail before operation SQL when destination, thread or authorization changes.
    @param acceptance_settings: Authorized synthetic configuration.
    @param acceptance_connection: Mocked owned resources.
    @param monkeypatch: Guard violation injection facility.
    @param violation: Contract to break after session creation.
    @return: None.
    """
    connection, preflight = acceptance_connection
    with support.rollback_repository_session(acceptance_settings) as session:
        connection.cursor.reset_mock()
        if violation == "settings":
            session.repository.settings = replace(acceptance_settings, database="jukebox101")
        elif violation == "thread":
            monkeypatch.setattr(support, "get_ident", MagicMock(return_value=session.owner_thread_id + 1))
        else:
            monkeypatch.setenv("JUKEBOX_RUN_REPOSITORY_MYSQL", "0")
        with pytest.raises((ConfigurationError, RuntimeError)):
            session.repository.balance(123)
        connection.cursor.assert_not_called()


@pytest.mark.parametrize("counts", [{}, {**dict.fromkeys(EXPECTED_TABLES, 0), "users": 1}])
def test_nonempty_or_incomplete_counts_rejected(counts):
    """@description: Prevent automatic reuse or clearing of unknown test data.
    @param counts: Unsafe pre-test snapshot.
    @return: None.
    """
    with pytest.raises(ConfigurationError):
        support.require_empty_tables(counts)


def test_count_sql_is_fixed_and_complete():
    """@description: Verify comparison covers exactly the six tables with read-only fixed SQL.
    @param: none
    @return: None.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [{"table_name": name, "row_count": 0} for name in EXPECTED_TABLES]
    assert support.table_counts(connection) == _zero_counts()
    cursor.execute.assert_called_once_with(support.COUNTS_SQL)
    assert support.COUNTS_SQL.count("COUNT(*)") == 6
    assert all(keyword not in support.COUNTS_SQL.upper() for keyword in ("DELETE", "TRUNCATE", "UPDATE", "INSERT"))


def test_full_transaction_timeout_mode_rejected():
    """@description: Reject server settings that invalidate savepoint-scoped timeout cleanup.
    @param: none
    @return: None.
    """
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [{"rollback_on_timeout": 1}]
    with pytest.raises(ConfigurationError, match="innodb_rollback_on_timeout"):
        support.require_savepoint_timeout_mode(connection)


def test_fixture_failed_assertion_still_checks_counts(acceptance_settings, acceptance_connection):
    """@description: Run the actual acceptance fixture failure path without contacting MySQL.
    @param acceptance_settings: Synthetic TEST_DB environment.
    @param acceptance_connection: Mocked connection and preflight verifier.
    @return: None.
    """
    connection, preflight = acceptance_connection
    fixture = acceptance.repository_mysql_session.__wrapped__(acceptance_settings)
    next(fixture)
    with pytest.raises(AssertionError, match="Synthetic assertion"):
        fixture.throw(AssertionError("Synthetic assertion"))
    assert connection.close.call_count == 3
    connection.commit.assert_not_called()


def test_fixture_detects_post_test_residue(acceptance_settings, acceptance_connection, monkeypatch):
    """@description: Fail acceptance if post-cleanup committed counts differ from the empty baseline.
    @param acceptance_settings: Synthetic TEST_DB environment.
    @param acceptance_connection: Mocked resources.
    @param monkeypatch: Snapshot injection facility.
    @return: None.
    """
    snapshots = MagicMock(side_effect=[_zero_counts(), _zero_counts(), {**_zero_counts(), "songs": 1}])
    monkeypatch.setattr(support, "table_counts", snapshots)
    fixture = acceptance.repository_mysql_session.__wrapped__(acceptance_settings)
    next(fixture)
    with pytest.raises(AssertionError, match="Six-table"):
        next(fixture)


def test_protocol_wait_has_deadline():
    """@description: Avoid indefinite waits for a crashed or unresponsive worker.
    @param: none
    @return: None.
    """
    receiver = MagicMock()
    receiver.poll.return_value = False
    with pytest.raises(TimeoutError):
        concurrency.wait_protocol(receiver, 0)
    receiver.poll.assert_called_once_with(0)
    receiver.recv.assert_not_called()


def test_worker_eof_fails_safely():
    """@description: Reject worker exit without a rollback-completion message.
    @param: none
    @return: None.
    """
    receiver = MagicMock()
    receiver.poll.return_value = True
    receiver.recv.side_effect = EOFError
    with pytest.raises(RuntimeError, match="cleanup report"):
        concurrency.wait_protocol(receiver, 0)


@pytest.mark.parametrize("alive_states, exit_code, expected_failure", [
    ([False, False, False], 0, False), ([True, False, False], -1, True),
    ([True, True, False], -1, True), ([True, True, True], None, True),
])
def test_process_cleanup_is_bounded(alive_states, exit_code, expected_failure):
    """@description: Exercise normal, terminate, kill and unreapable cleanup paths without spawning.
    @param alive_states: Simulated worker state across cleanup stages.
    @param exit_code: Simulated process result.
    @param expected_failure: Whether cleanup must reject acceptance.
    @return: None.
    """
    process = MagicMock()
    process.is_alive.side_effect = alive_states
    process.exitcode = exit_code
    if expected_failure:
        with pytest.raises(RuntimeError):
            concurrency.stop_process(process)
    else:
        concurrency.stop_process(process)
    assert all(call.args == (concurrency.PROCESS_JOIN_SECONDS,) for call in process.join.call_args_list)
    assert process.join.call_count <= 3


@pytest.mark.parametrize("protocol_outcomes", [["holding", "lock_timeout", "rolled_back", "rolled_back"], ["holding", "unexpected_database_error"]])
def test_contention_supervisor_releases_holder(monkeypatch, protocol_outcomes):
    """@description: Ensure success and failed contention assertions always release owned workers.
    @param monkeypatch: Process and IPC injection facility.
    @param protocol_outcomes: Safe worker outcomes to feed the supervisor.
    @return: None.
    """
    context = MagicMock()
    context.Pipe.side_effect = [(MagicMock(), MagicMock()) for sequence in range(3)]
    processes = [MagicMock(exitcode=0) for sequence in range(3)]
    for process in processes:
        process.is_alive.return_value = False
    context.Process.side_effect = processes
    monkeypatch.setattr(concurrency.multiprocessing, "get_context", MagicMock(return_value=context))
    monkeypatch.setattr(concurrency, "wait_protocol", MagicMock(side_effect=protocol_outcomes))
    plan = concurrency.SubmissionPlan(123, "https://youtu.be/Abc_123-Xyz")
    if len(protocol_outcomes) == 4:
        concurrency.run_contention(plan, plan)
    else:
        with pytest.raises(AssertionError):
            concurrency.run_contention(plan, plan)
    assert context.Event.return_value.set.call_count >= 1
    for process in processes[:context.Process.call_count]:
        process.close.assert_called_once()


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_worker_reports_timeout_only_after_cleanup(monkeypatch, cleanup_failure):
    """@description: Ensure a failed outer rollback cannot masquerade as accepted lock contention.
    @param monkeypatch: Worker session injection facility.
    @param cleanup_failure: Whether cleanup raises after an expected operation timeout.
    @return: None.
    """
    context = MagicMock()
    session = context.__enter__.return_value
    session.repository.submit_pending.side_effect = pymysql.OperationalError(1205, "synthetic-secret")
    if cleanup_failure:
        context.__exit__.side_effect = pymysql.OperationalError(2006, "synthetic-cleanup-secret")
    monkeypatch.setattr(concurrency, "authorized_repository_settings", MagicMock())
    monkeypatch.setattr(concurrency, "rollback_repository_session", MagicMock(return_value=context))
    sender = MagicMock()
    concurrency.contention_worker(concurrency.SubmissionPlan(123, "https://youtu.be/Abc_123-Xyz"), "contender", MagicMock(), sender)
    sender.send.assert_called_once_with("unexpected_database_error" if cleanup_failure else "lock_timeout")
    sender.close.assert_called_once()
    assert "secret" not in repr(sender.send.call_args)


def test_new_acceptance_sources_follow_function_limits():
    """@description: Enforce documented function limits for every new acceptance helper and test.
    @param: none
    @return: None.
    """
    import ast
    from pathlib import Path
    from tests.test_phase2_quality import _assert_function_documentation, _control_depth, _implementation_lines
    module_paths = [support.__file__, concurrency.__file__, acceptance.__file__, counts_entry.__file__, __file__, str(Path(__file__).with_name("test_repository_mysql_concurrent.py"))]
    for module_name in module_paths:
        module_path = Path(module_name)
        source = module_path.read_text(encoding="utf-8")
        module = ast.parse(source)
        assert ast.get_docstring(module), module_path
        for function in ast.walk(module):
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert _implementation_lines(function, source.splitlines()) <= 25, (module_path, function.name)
                assert _control_depth(function) <= 3, (module_path, function.name)
                _assert_function_documentation(function, module_path)


@pytest.mark.parametrize("nonempty", [False, True])
def test_manual_count_entry_is_safe(monkeypatch, capsys, nonempty):
    """@description: Expose comparable table counts while rejecting residue without writing cleanup SQL.
    @param monkeypatch: Read-only boundary injection facility.
    @param capsys: Captured nonsecret count output.
    @param nonempty: Whether the synthetic baseline contains a row.
    @return: None.
    """
    counts = {**_zero_counts(), "songs": int(nonempty)}
    monkeypatch.setattr(counts_entry, "authorized_repository_settings", MagicMock())
    monkeypatch.setattr(counts_entry, "verified_counts", MagicMock(return_value=counts))
    if nonempty:
        with pytest.raises(SystemExit, match="ConfigurationError"):
            counts_entry.main()
    else:
        counts_entry.main()
    assert '"songs": ' + str(int(nonempty)) in capsys.readouterr().out


def test_manual_count_failure_suppresses_secrets(monkeypatch, capsys):
    """@description: Prevent raw connection error messages from leaking through manual count reports.
    @param monkeypatch: Failing synthetic credential-loader injection facility.
    @param capsys: Captured diagnostic output.
    @return: None.
    """
    monkeypatch.setattr(counts_entry, "authorized_repository_settings", MagicMock(side_effect=pymysql.OperationalError(2003, "synthetic-secret")))
    with pytest.raises(SystemExit) as captured_error:
        counts_entry.main()
    assert "secret" not in str(captured_error.value) + capsys.readouterr().out
