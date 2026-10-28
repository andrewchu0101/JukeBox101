"""Offline proof that every Batch B command entry refuses under -O before any database connection.

Each command runs in a child interpreter started with -O, or in-process with a stripped interpreter state. Sentinel credentials are supplied, and pymysql.connect is replaced by a spy that records the attempt and exits with a distinct status. Pytest itself is also checked against a vacuous pass under -O. No database, network service or Discord client is contacted.
"""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pymysql
import pytest

from tests import batch_b_acceptance as acceptance
from tests import batch_b_assertion_guard as guard
from tests import batch_b_fk_lab
from tests import batch_b_migrate
from tests import batch_b_migration_preflight
from tests import batch_b_preflight
from tests import batch_b_recovery
from tests import batch_b_recovery_fk
from tests import batch_b_recovery_write

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REFUSAL_EXIT_CODE = 3  # [contract]: Approved refusal status for an assertion-stripped run.
CONNECTION_ATTEMPT_EXIT_CODE = 97  # [spy status]: Distinct exit proving a connection was attempted in a child.
ASSERTIONS_DISABLED_TEXT = 'ASSERTIONS_DISABLED'  # [refusal evidence]: Fixed safety code printed by refused entries.
SENTINEL_ENVIRONMENT = {  # [sentinel profiles]: Syntactically valid local values; a reached connection is caught by the spy.
    'TEST_DB_HOST': '127.0.0.1', 'TEST_DB_PORT': '3306', 'TEST_DB_USER': 'jukebox101_test_app',
    'TEST_DB_PASSWORD': 'sentinel-not-a-secret', 'TEST_DB_NAME': 'jukebox101_test',
    'TEST_MIGRATION_DB_HOST': '127.0.0.1', 'TEST_MIGRATION_DB_PORT': '3306', 'TEST_MIGRATION_DB_USER': 'jukebox101_test_migrator',
    'TEST_MIGRATION_DB_PASSWORD': 'sentinel-not-a-secret', 'TEST_MIGRATION_DB_NAME': 'jukebox101_test',
    'JUKEBOX_RUN_BATCH_B_PREFLIGHT': '1', 'JUKEBOX_RUN_BATCH_B_MIGRATION_PREFLIGHT': '1', 'JUKEBOX_RUN_BATCH_B_MIGRATION': '1',
}
SPY_PROGRAM = """
import os
import runpy
import sys

import pymysql


def refuse_connection(*arguments, **keywords):
    sys.stderr.write('CONNECT_ATTEMPTED\\n')
    sys.stderr.flush()
    os._exit(97)


pymysql.connect = refuse_connection
sys.argv = [{module!r}] + {arguments!r}
runpy.run_module({module!r}, run_name='__main__')
"""  # [child wrapper]: Runs the module's own __main__ block with pymysql.connect replaced by a hard-exiting spy.
THROWAWAY_TEST_SOURCE = 'from vacuous_helper import require_true\n\n\ndef test_helper_assert():\n    require_true()\n'
# [helper-level assert]: pytest rewrites test modules but not helper modules, so -O strips this assert in helpers like batch_b_*.
THROWAWAY_HELPER_FAILING = 'def require_true():\n    assert False\n'
THROWAWAY_HELPER_PASSING = 'def require_true():\n    assert True\n'


class ConnectionAttempted(Exception):
    """Mark an in-process database connection so the spy fails the test without reaching a server."""


class UnreadableStream:
    """Fail the test if any entry reads credentials before the guard runs."""

    def __getattr__(self, name: str) -> None:
        """@description: Turn any read attempt into a test failure that names the premature access.
        @param name: Attribute the entry tried to use on the input stream.
        @return: Never returns; always raises.
        """
        raise AssertionError('STREAM_READ_BEFORE_GUARD: ' + name)


@pytest.fixture
def stripped_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Model an interpreter started with -O for the guard during one in-process test.
    @param monkeypatch: Pytest patch registry restored after each test.
    @return: None.
    """
    monkeypatch.setattr(guard, 'sys', SimpleNamespace(flags=SimpleNamespace(optimize=1)))


@pytest.fixture
def connection_spy(monkeypatch: pytest.MonkeyPatch) -> list:
    """@description: Replace pymysql.connect so any in-process connection attempt is recorded and fails.
    @param monkeypatch: Pytest patch registry restored after each test.
    @return: Recorded attempts; must stay empty in every test.
    """
    attempts = []  # [connection evidence]: Any entry means a guarded path reached the database boundary.

    def refuse(*arguments: object, **keywords: object) -> None:
        """@description: Record the attempted connection and stop it immediately.
        @param arguments: Positional arguments a real connection would have used.
        @param keywords: Keyword arguments a real connection would have used.
        @return: Never returns; always raises ConnectionAttempted.
        """
        attempts.append((arguments, keywords))
        raise ConnectionAttempted('pymysql.connect must not run in this test')

    monkeypatch.setattr(pymysql, 'connect', refuse)
    return attempts


@pytest.fixture
def sentinel_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: Provide sentinel profiles so the entries reach their own connection boundary if not refused.
    @param monkeypatch: Pytest environment registry restored after each test.
    @return: None.
    """
    for name, value in SENTINEL_ENVIRONMENT.items():
        monkeypatch.setenv(name, value)


def child_environment() -> dict:
    """@description: Build the child environment from sentinel profiles only, dropping any inherited Batch B or Discord values.
    @param: none
    @return: Environment mapping for a child interpreter.
    """
    inherited = {name: value for name, value in os.environ.items()
                 if not name.startswith(('TEST_', 'DB_', 'JUKEBOX_RUN', 'DISCORD_'))}
    inherited.update(SENTINEL_ENVIRONMENT)
    return inherited


def run_optimized_module(module: str, arguments: list) -> subprocess.CompletedProcess:
    """@description: Run one Batch B module through its real __main__ path in a -O child with a connection spy.
    @param module: Dotted module name whose __main__ block is the command entry.
    @param arguments: Command-line arguments the operator would pass.
    @return: Completed process with exit status, stdout and stderr.
    """
    program = SPY_PROGRAM.format(module=module, arguments=arguments)
    return subprocess.run([sys.executable, '-B', '-O', '-c', program], input='', text=True, capture_output=True,
                          cwd=PROJECT_ROOT, env=child_environment(), timeout=120, check=False)


CLI_ENTRIES = [
    ('tests.batch_b_preflight', []),
    ('tests.batch_b_migration_preflight', []),
    ('tests.batch_b_migrate', []),
    ('tests.batch_b_acceptance', []),
    ('tests.batch_b_acceptance', ['--inspect-recovery']),
    ('tests.batch_b_acceptance', ['--restore-recovery']),
    ('tests.batch_b_acceptance', ['--fk-lab']),
    ('tests.batch_b_acceptance', ['--repair-recovery-fk']),
    ('tests.batch_b_acceptance', ['--stage-b-only']),
]


@pytest.mark.parametrize(('module', 'arguments'), CLI_ENTRIES,
                         ids=['preflight', 'migration_preflight', 'migrate', 'acceptance_default', 'inspect_recovery',
                              'restore_recovery', 'fk_lab', 'repair_recovery_fk', 'stage_b_only'])
def test_cli_entry_refuses_under_optimization_without_connecting(module, arguments):
    """@description: Refuse each real command entry under -O with the fixed code, no PASS text, and no connection attempt.
    @param module: Command module executed through its __main__ path.
    @param arguments: Operator arguments for that command.
    @return: None.
    """
    result = run_optimized_module(module, arguments)
    assert result.returncode == REFUSAL_EXIT_CODE
    assert 'CONNECT_ATTEMPTED' not in result.stderr
    assert ASSERTIONS_DISABLED_TEXT in result.stdout
    assert not any(word in result.stdout for word in ('PASS', 'COMPLETE', 'APPLIED'))


DIRECT_ENTRY_CALLS = {
    'preflight': lambda: batch_b_preflight.main(),
    'migration_preflight': lambda: batch_b_migration_preflight.main(),
    'migrate': lambda: batch_b_migrate.main(),
    'recovery': lambda: batch_b_recovery.main(UnreadableStream()),
    'recovery_write': lambda: batch_b_recovery_write.main(UnreadableStream()),
    'recovery_fk': lambda: batch_b_recovery_fk.main(UnreadableStream()),
    'fk_lab': lambda: batch_b_fk_lab.main(UnreadableStream()),
    'acceptance_main': lambda: acceptance.main(),
}


@pytest.mark.parametrize('entry_name', sorted(DIRECT_ENTRY_CALLS))
def test_direct_call_refuses_before_connection(entry_name, stripped_guard, sentinel_environment, connection_spy):
    """@description: Refuse a directly called Batch B entry under a stripped interpreter before any credential read or connection.
    @param entry_name: Key of the direct entry under test.
    @param stripped_guard: Fixture modelling an optimized interpreter.
    @param sentinel_environment: Fixture supplying sentinel profiles that would otherwise reach the connection.
    @param connection_spy: Fixture failing any in-process database connection.
    @return: None.
    """
    with pytest.raises(guard.AssertionsDisabledError):
        DIRECT_ENTRY_CALLS[entry_name]()
    assert connection_spy == []


def test_run_guarded_refuses_without_running_the_command(stripped_guard, capsys):
    """@description: Convert a stripped-interpreter refusal into the fixed exit status without running the command body.
    @param stripped_guard: Fixture modelling an optimized interpreter.
    @param capsys: Captured standard output for the refusal message.
    @return: None.
    """
    calls = []  # [command evidence]: Any entry means the command body ran despite the refusal.
    status = guard.run_guarded(lambda: calls.append('ran') or 0)
    assert status == REFUSAL_EXIT_CODE and calls == []
    assert ASSERTIONS_DISABLED_TEXT in capsys.readouterr().out


def test_run_guarded_returns_command_status_when_enabled():
    """@description: Keep ordinary runs unchanged by returning the command's own status when assertions are enabled.
    @param: none
    @return: None.
    """
    assert guard.run_guarded(lambda: 0) == 0


def run_pytest_child(test_file: Path, optimized: bool) -> subprocess.CompletedProcess:
    """@description: Run pytest on a throwaway test in a child, loading the project conftest, with or without -O.
    @param test_file: Throwaway test module outside the repository test tree.
    @param optimized: Whether the child interpreter runs with -O.
    @return: Completed process with exit status and output.
    """
    flags = ['-B', '-O'] if optimized else ['-B']
    command = [sys.executable, *flags, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'tests.conftest', str(test_file)]
    return subprocess.run(command, capture_output=True, text=True, cwd=PROJECT_ROOT, env=child_environment(),
                          timeout=180, check=False)


def write_throwaway(tmp_path: Path, helper_source: str) -> Path:
    """@description: Create a throwaway test whose outcome depends on an assert inside a non-rewritten helper module.
    @param tmp_path: Isolated directory that holds the helper and the test.
    @param helper_source: Helper module source, either failing or passing.
    @return: Path of the throwaway test module.
    """
    (tmp_path / 'vacuous_helper.py').write_text(helper_source, encoding='utf-8')
    test_file = tmp_path / 'test_helper_assert.py'
    test_file.write_text(THROWAWAY_TEST_SOURCE, encoding='utf-8')
    return test_file


def test_pytest_under_optimization_refuses_vacuous_pass(tmp_path):
    """@description: Refuse a pytest run under -O so a helper assert stripped by the interpreter cannot report success.
    @param tmp_path: Isolated directory for the throwaway helper and test.
    @return: None.
    """
    result = run_pytest_child(write_throwaway(tmp_path, THROWAWAY_HELPER_FAILING), optimized=True)
    output = result.stdout + result.stderr
    assert result.returncode == REFUSAL_EXIT_CODE
    assert ' passed' not in output and 'run without -O' in output


def test_pytest_without_optimization_still_executes_helper_asserts(tmp_path):
    """@description: Control run showing the same helper assert fails normally, so the -O result is a real refusal.
    @param tmp_path: Isolated directory for the throwaway helper and test.
    @return: None.
    """
    result = run_pytest_child(write_throwaway(tmp_path, THROWAWAY_HELPER_FAILING), optimized=False)
    assert result.returncode == 1 and '1 failed' in result.stdout + result.stderr


def test_pytest_without_optimization_keeps_normal_pass(tmp_path):
    """@description: Confirm an ordinary passing run is unaffected by the -O guard in conftest.
    @param tmp_path: Isolated directory for the throwaway helper and test.
    @return: None.
    """
    result = run_pytest_child(write_throwaway(tmp_path, THROWAWAY_HELPER_PASSING), optimized=False)
    assert result.returncode == 0 and '1 passed' in result.stdout + result.stderr
