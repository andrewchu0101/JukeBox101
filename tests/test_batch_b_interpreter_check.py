"""Offline proof that the interpreter pre-check accepts a normal interpreter and refuses every assertion-stripping mode.

Each case runs the check module in a child interpreter with empty stdin and no Batch B credential environment. No database, network service or Discord client is contacted.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHECK_MODULE = 'tests.batch_b_interpreter_check'
PASSED_OR_COMPLETED_WORDS = ('PASS', 'COMPLETE', 'APPLIED')  # [success vocabulary]: Must never appear for a pre-check run.


def run_interpreter_check(interpreter_flags: list, extra_environment: dict | None = None) -> subprocess.CompletedProcess:
    """@description: Run the pre-check module in a child interpreter with credentials and PYTHONOPTIMIZE removed unless requested.
    @param interpreter_flags: Interpreter flags placed before -m, such as -O or -OO.
    @param extra_environment: Environment variables to add for this single run.
    @return: Completed process with exit status, stdout and stderr.
    """
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith(('TEST_', 'DB_', 'JUKEBOX_RUN', 'DISCORD_', 'PYTHONOPTIMIZE'))}
    environment.update(extra_environment or {})
    command = [sys.executable, '-B', *interpreter_flags, '-m', CHECK_MODULE]
    return subprocess.run(command, input='', text=True, capture_output=True, cwd=PROJECT_ROOT,
                          env=environment, timeout=60, check=False)


def test_normal_interpreter_passes():
    """@description: Accept a normal interpreter so ordinary Batch B runs continue to the credential prompt.
    @param: none
    @return: None.
    """
    assert run_interpreter_check([]).returncode == 0


def test_python_O_is_refused():
    """@description: Refuse python -O with the approved refusal status.
    @param: none
    @return: None.
    """
    assert run_interpreter_check(['-O']).returncode == 3


def test_python_OO_is_refused():
    """@description: Refuse python -OO, which also strips docstrings, with the approved refusal status.
    @param: none
    @return: None.
    """
    assert run_interpreter_check(['-OO']).returncode == 3


def test_pythonoptimize_environment_is_refused():
    """@description: Refuse an interpreter started with PYTHONOPTIMIZE=1, judged by its own flags rather than the variable.
    @param: none
    @return: None.
    """
    assert run_interpreter_check([], {'PYTHONOPTIMIZE': '1'}).returncode == 3


@pytest.mark.parametrize('flags, environment', [
    ([], {}),
    (['-O'], {}),
    (['-OO'], {}),
    ([], {'PYTHONOPTIMIZE': '1'}),
])
def test_no_success_vocabulary_is_printed(flags, environment):
    """@description: Ensure no pre-check outcome, refused or accepted, prints a PASS, COMPLETE or APPLIED verdict.
    @param flags: Interpreter flags for this case.
    @param environment: Extra environment for this case.
    @return: None.
    """
    result = run_interpreter_check(flags, environment)
    output = result.stdout + result.stderr
    assert not any(word in output for word in PASSED_OR_COMPLETED_WORDS)
