"""Offline proof that Batch B refuses to run when Python strips assert statements.

Interpreter states are injected through the guard's own inputs, and pymysql.connect is replaced by a spy so any accidental connection fails the test. No database, network service or Discord client is contacted.
"""

import ast
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pymysql
import pytest

from bot.config import DatabaseSettings
from tests import batch_b_acceptance as acceptance
from tests import batch_b_assertion_guard as guard
from tests import batch_b_harness as harness_module
from tests import batch_b_mysql_scenarios as scenarios

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIRECTORY = PROJECT_ROOT / 'tests'
REFUSAL_EXIT_CODE = 3  # [contract]: Exit status reserved for an assertion-stripped refusal, distinct from ordinary failures.
GUARD_NAME = 'require_assertions_enabled'


class ConnectionAttempted(Exception):
    """Mark an unexpected database connection so the spy fails the test without reaching a server."""


class UnreadableStream:
    """Fail the test if any code reads credentials before the guard has run."""

    def __getattr__(self, name: str) -> None:
        """@description: Turn any read attempt into a test failure that names the premature access.
        @param name: Attribute the entry point tried to use on the input stream.
        @return: Never returns; always raises.
        """
        raise AssertionError('STREAM_READ_BEFORE_GUARD: ' + name)


class FakePipe:
    """Record worker messages so a refusal can be distinguished from a PASS."""

    def __init__(self) -> None:
        """@description: Start with no messages so the test can inspect exactly what the worker sent.
        @param: none
        @return: None.
        """
        self.sent = []  # [worker outcome log]: Every message the worker tried to return to the parent.

    def send(self, message: dict) -> None:
        """@description: Capture the worker outcome for assertion instead of crossing a process boundary.
        @param message: Sanitized status dictionary the worker returns.
        @return: None.
        """
        self.sent.append(message)

    def close(self) -> None:
        """@description: Accept the worker's close call without holding any resource.
        @param: none
        @return: None.
        """
        return None


def stripped_interpreter() -> SimpleNamespace:
    """@description: Model an interpreter started with -O so the guard sees the same state as python -O.
    @param: none
    @return: Object exposing the sys.flags.optimize attribute the guard reads.
    """
    return SimpleNamespace(flags=SimpleNamespace(optimize=1))


@pytest.fixture
def connection_spy(monkeypatch: pytest.MonkeyPatch) -> list:
    """@description: Replace pymysql.connect so any database connection attempt is recorded and fails.
    @param monkeypatch: Pytest patch registry restored after each test.
    @return: List of recorded connection attempts; must stay empty in every test.
    """
    attempts = []  # [connection evidence]: Any entry here means a guarded path reached the database boundary.

    def refuse(*arguments: object, **keywords: object) -> None:
        """@description: Record the attempted connection and stop it immediately.
        @param arguments: Positional arguments that a real connection would have used.
        @param keywords: Keyword arguments that a real connection would have used.
        @return: Never returns; always raises ConnectionAttempted.
        """
        attempts.append((arguments, keywords))
        raise ConnectionAttempted('pymysql.connect must not run in this test')

    monkeypatch.setattr(pymysql, 'connect', refuse)
    return attempts


@pytest.fixture
def stripped_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Make the guard read an optimized interpreter state for the duration of one test.
    @param monkeypatch: Pytest patch registry restored after each test.
    @return: None.
    """
    monkeypatch.setattr(guard, 'sys', stripped_interpreter())


def test_guard_refuses_optimized_level():
    """@description: Refuse any optimize level other than the assert-preserving default.
    @param: none
    @return: None.
    """
    with pytest.raises(guard.AssertionsDisabledError):
        guard.require_assertions_enabled(optimize_level=1, debug_flag=True)


def test_guard_refuses_when_debug_flag_is_false():
    """@description: Refuse an interpreter whose __debug__ state is False even at optimize level zero.
    @param: none
    @return: None.
    """
    with pytest.raises(guard.AssertionsDisabledError):
        guard.require_assertions_enabled(optimize_level=0, debug_flag=False)


def test_guard_accepts_default_interpreter_state():
    """@description: Allow the normal interpreter state so ordinary test runs and operator runs stay unchanged.
    @param: none
    @return: None.
    """
    assert guard.require_assertions_enabled(optimize_level=0, debug_flag=True) is None


def test_guard_defaults_read_the_running_interpreter():
    """@description: Confirm the no-argument call uses the running interpreter, which pytest runs without -O.
    @param: none
    @return: None.
    """
    assert guard.require_assertions_enabled() is None


def test_guard_defaults_follow_the_interpreter_flags(stripped_guard):
    """@description: Confirm the no-argument call refuses when the interpreter flags report optimization.
    @param stripped_guard: Fixture that models an optimized interpreter for the guard.
    @return: None.
    """
    with pytest.raises(guard.AssertionsDisabledError):
        guard.require_assertions_enabled()


def test_guard_module_contains_no_assert_statement():
    """@description: Keep the guard itself immune to -O by forbidding assert statements in its source.
    @param: none
    @return: None.
    """
    tree = ast.parse((TESTS_DIRECTORY / 'batch_b_assertion_guard.py').read_text(encoding='utf-8'))
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.Assert)]


def test_direct_entry_refuses_before_any_connection(stripped_guard, connection_spy, monkeypatch):
    """@description: Refuse the direct acceptance entry under an optimized interpreter without reading stdin or connecting.
    @param stripped_guard: Fixture that models an optimized interpreter.
    @param connection_spy: Fixture that fails any database connection attempt.
    @param monkeypatch: Pytest patch registry for argv and stdin.
    @return: None.
    """
    monkeypatch.setattr(sys, 'argv', ['tests.batch_b_acceptance'])
    monkeypatch.setattr(sys, 'stdin', UnreadableStream())
    assert acceptance.entry() == REFUSAL_EXIT_CODE
    assert connection_spy == []


def test_direct_entry_refusal_prints_no_pass(stripped_guard, connection_spy, monkeypatch, capsys):
    """@description: Ensure the refusal message names the safety code and never contains a PASS verdict.
    @param stripped_guard: Fixture that models an optimized interpreter.
    @param connection_spy: Fixture that fails any database connection attempt.
    @param monkeypatch: Pytest patch registry for argv and stdin.
    @param capsys: Captured standard output for the refusal message.
    @return: None.
    """
    monkeypatch.setattr(sys, 'argv', ['tests.batch_b_acceptance'])
    monkeypatch.setattr(sys, 'stdin', UnreadableStream())
    acceptance.entry()
    output = capsys.readouterr().out
    assert guard.ASSERTIONS_DISABLED_SAFETY_CODE in output
    assert 'PASS' not in output and connection_spy == []


def test_main_refuses_before_reading_credentials(stripped_guard, connection_spy, monkeypatch):
    """@description: Stop the legacy acceptance main before it reads any credential stream.
    @param stripped_guard: Fixture that models an optimized interpreter.
    @param connection_spy: Fixture that fails any database connection attempt.
    @param monkeypatch: Pytest patch registry for stdin.
    @return: None.
    """
    monkeypatch.setattr(sys, 'stdin', UnreadableStream())
    with pytest.raises(guard.AssertionsDisabledError):
        acceptance.main()
    assert connection_spy == []


def test_stage_b_main_refuses_before_reading_credentials(stripped_guard, connection_spy):
    """@description: Stop the new-session Stage B entry before it reserves a session or reads credentials.
    @param stripped_guard: Fixture that models an optimized interpreter.
    @param connection_spy: Fixture that fails any database connection attempt.
    @return: None.
    """
    with pytest.raises(guard.AssertionsDisabledError):
        acceptance.stage_b_main(UnreadableStream())
    assert connection_spy == []


def test_worker_refuses_before_dispatch(stripped_guard, connection_spy, monkeypatch):
    """@description: Prove the spawned worker reports FAIL and never dispatches a real MySQL scenario.
    @param stripped_guard: Fixture that models an optimized interpreter.
    @param connection_spy: Fixture that fails any database connection attempt.
    @param monkeypatch: Pytest patch registry for the dispatch spy.
    @return: None.
    """
    dispatched = []  # [dispatch evidence]: Any entry means a real scenario was started under -O.
    monkeypatch.setattr(scenarios, 'dispatch', lambda *arguments: dispatched.append(arguments))
    pipe = FakePipe()
    scenarios.scenario_process(pipe, None, None, 'persistence')
    assert dispatched == [] and connection_spy == []
    assert pipe.sent == [{'status': 'FAIL', 'scenario': 'persistence', 'error_type': 'AssertionsDisabledError',
                          'mysql_error_number': None}]


def test_harness_start_refuses_before_journal_or_connection(stripped_guard, connection_spy, tmp_path):
    """@description: Stop the real-MySQL harness before it writes its journal or opens the management connection.
    @param stripped_guard: Fixture that models an optimized interpreter.
    @param connection_spy: Fixture that fails any database connection attempt.
    @param tmp_path: Isolated directory holding the journal path that must stay absent.
    @return: None.
    """
    settings = DatabaseSettings(host='127.0.0.1', port=3306, user='not_used', password='not_used', database='not_used')
    journal = tmp_path / 'run.json'
    harness = harness_module.MySQLHarness(settings, settings, journal=journal)
    with pytest.raises(guard.AssertionsDisabledError):
        harness.start()
    assert connection_spy == [] and not journal.exists() and harness.connection is None


def test_create_harness_refuses_before_supervision_side_effects(stripped_guard, connection_spy, tmp_path):
    """@description: Stop run creation before the supervisor's before_run hook, directories or manifests are touched.
    @param stripped_guard: Fixture that models an optimized interpreter.
    @param connection_spy: Fixture that fails any database connection attempt.
    @param tmp_path: Supervisor directory that must remain empty.
    @return: None.
    """
    calls = []  # [supervisor evidence]: Any entry means the run was registered under -O.
    supervision = {'before_run': lambda: calls.append('before_run'), 'directory': str(tmp_path),
                   'session_id': 'session-not-used', 'definitions': {}, 'baseline': {}, 'state': {'runs': []}}
    with pytest.raises(guard.AssertionsDisabledError):
        harness_module.create_harness(None, None, supervision)
    assert calls == [] and list(tmp_path.iterdir()) == [] and connection_spy == []


def test_optimized_subprocess_refuses_without_pass():
    """@description: Run the real entry under python -O with empty stdin and confirm a fixed refusal, not PASS.
    @param: none
    @return: None.
    """
    result = subprocess.run([sys.executable, '-B', '-O', '-m', 'tests.batch_b_acceptance'], input='', text=True,
                            capture_output=True, cwd=PROJECT_ROOT, timeout=60, check=False)
    assert result.returncode == REFUSAL_EXIT_CODE
    assert guard.ASSERTIONS_DISABLED_SAFETY_CODE in result.stdout and 'PASS' not in result.stdout


def test_normal_subprocess_is_not_refused():
    """@description: Control run proving the same entry without -O is not refused by the guard.
    @param: none
    @return: None.
    """
    result = subprocess.run([sys.executable, '-B', '-m', 'tests.batch_b_acceptance'], input='', text=True,
                            capture_output=True, cwd=PROJECT_ROOT, timeout=60, check=False)
    assert result.returncode != REFUSAL_EXIT_CODE
    assert guard.ASSERTIONS_DISABLED_SAFETY_CODE not in result.stdout


def run_optimized_child(program: str) -> subprocess.CompletedProcess:
    """@description: Run a Python program in a separate interpreter started with -O, so assert statements are stripped.
    @param program: Source text executed by the child interpreter with the project root as working directory.
    @return: Completed process holding exit status, stdout and stderr.
    """
    return subprocess.run([sys.executable, '-B', '-O', '-c', program], input='', text=True, capture_output=True,
                          cwd=PROJECT_ROOT, timeout=120, check=False)


STAGE_B_CHILD_PROGRAM = """
import pymysql
import runpy
import sys


def refuse_connection(*arguments, **keywords):
    sys.stderr.write('CONNECT_ATTEMPTED\\n')
    sys.stderr.flush()
    raise SystemExit(97)


pymysql.connect = refuse_connection
sys.argv = ['tests/batch_b_acceptance.py', '--stage-b-only']
runpy.run_module('tests.batch_b_acceptance', run_name='__main__')
"""  # [real CLI entry]: runpy executes the module's own __main__ block, the same path the launcher's child uses.

SCENARIO_CHILD_PROGRAM = """
import json
import multiprocessing
from bot.config import DatabaseSettings
from tests.batch_b_mysql_scenarios import scenario_process


def main():
    context = multiprocessing.get_context('spawn')
    receiver, sender = context.Pipe(duplex=False)
    settings = DatabaseSettings(host='127.0.0.1', port=3306, user='not_used', password='not_used', database='not_used')
    worker = context.Process(target=scenario_process, args=(sender, settings, None, 'persistence'))
    worker.start()
    sender.close()
    try:
        outcome = receiver.recv()
    except EOFError:
        outcome = {'status': 'NO_OUTCOME'}
    worker.join(90)
    print(json.dumps(outcome))
    raise SystemExit(3 if outcome.get('error_type') == 'AssertionsDisabledError' else 1)


if __name__ == '__main__':
    main()
"""  # [spawned worker]: Spawn child inherits -O; the parent wrapper maps its outcome to an exit status.


def test_stage_b_only_child_refuses_under_optimization():
    """@description: Refuse the --stage-b-only supervisor mode in a -O child before any connection or pass verdict.
    @param: none
    @return: None.
    """
    result = run_optimized_child(STAGE_B_CHILD_PROGRAM)
    assert result.returncode == REFUSAL_EXIT_CODE
    assert 'CONNECT_ATTEMPTED' not in result.stderr
    assert guard.ASSERTIONS_DISABLED_SAFETY_CODE in result.stdout and 'PASS' not in result.stdout


def test_worker_child_refuses_before_dispatch_under_optimization():
    """@description: Refuse the spawned scenario worker under -O before dispatch, reporting FAIL and never PASS.
    @param: none
    @return: None.
    """
    result = run_optimized_child(SCENARIO_CHILD_PROGRAM)
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    assert result.returncode == REFUSAL_EXIT_CODE
    assert outcome == {'status': 'FAIL', 'scenario': 'persistence', 'error_type': 'AssertionsDisabledError',
                       'mysql_error_number': None}
    assert 'PASS' not in result.stdout


def function_node(module_path: Path, function_name: str) -> ast.FunctionDef:
    """@description: Locate one uniquely named function so static checks inspect the exact implementation.
    @param module_path: Source file to parse without importing it.
    @param function_name: Name of the single function expected in that file.
    @return: Parsed function definition node.
    """
    tree = ast.parse(module_path.read_text(encoding='utf-8'))
    matches = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == function_name]
    assert len(matches) == 1, function_name
    return matches[0]


def call_names(function: ast.FunctionDef) -> list[tuple[int, str]]:
    """@description: List every simple-name call with its line so ordering checks can compare guard placement.
    @param function: Parsed function definition to scan.
    @return: Pairs of source line number and called name, in source order.
    """
    return sorted((node.lineno, node.func.id) for node in ast.walk(function)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name))


GUARDED_FUNCTIONS = [
    ('batch_b_acceptance.py', 'entry'),
    ('batch_b_acceptance.py', 'main'),
    ('batch_b_acceptance.py', 'stage_b_main'),
    ('batch_b_mysql_scenarios.py', 'scenario_process'),
    ('batch_b_harness.py', 'start'),
    ('batch_b_harness.py', 'create_harness'),
]


@pytest.mark.parametrize(('module_name', 'function_name'), GUARDED_FUNCTIONS)
def test_guard_runs_before_any_other_call(module_name, function_name):
    """@description: Verify statically that each entry point calls the guard before any other call in its body.
    @param module_name: Batch B source file that owns the entry point.
    @param function_name: Entry point whose first action must be the guard.
    @return: None.
    """
    calls = call_names(function_node(TESTS_DIRECTORY / module_name, function_name))
    guard_lines = [line for line, name in calls if name == GUARD_NAME]
    other_lines = [line for line, name in calls if name != GUARD_NAME]
    assert len(guard_lines) == 1
    assert not other_lines or guard_lines[0] < min(other_lines)


@pytest.mark.parametrize('function_name', ['concurrent_accept', 'lock_timeout', 'owner_review',
                                           'reminder_seed', 'reminder_read'])
def test_scenario_uses_assertion_reconciliation(function_name):
    """@description: Require each real-MySQL scenario to reconcile committed rows through the shared validators.
    @param function_name: Scenario whose body must invoke assert_accounting or validate_payloads.
    @return: None.
    """
    names = {name for _, name in call_names(function_node(TESTS_DIRECTORY / 'batch_b_mysql_scenarios.py', function_name))}
    assert names & {'assert_accounting', 'validate_payloads'}
