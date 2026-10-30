"""Exercise durable controller transitions and real isolated fake-Gateway processes.

Registry, Discord, SSH and database behavior are simulated; process exit, locks,
journal fsync, controller SIGTERM and recovery are real local OS operations.
"""

import json
import subprocess
import sys
import time

import pytest

from deploy.controller import Controller, parse_request
from deploy.journal import exclusive, load
from deploy.lab_backend import ProcessLabBackend, initialize

OLD = 'sha256:' + '1' * 64
NEW = 'sha256:' + '2' * 64


def request(run=10, digest=NEW):
    """@description: Build a synthetic request with no credential or real image.
    @param run: Synthetic monotonically increasing workflow id.
    @param digest: Fake release digest.
    @return: Strictly parsed deployment request.
    """
    return parse_request(f'deploy-v1 {"a" * 40} {digest} {run} 1\n')


@pytest.fixture
def lab(tmp_path):
    """@description: Keep each process rehearsal in its own random directory and clean only it.
    @param tmp_path: Random pytest-owned scope.
    @return: Marked directory and process backend, always stopped after test.
    """
    initialize(tmp_path, OLD, {})
    backend = ProcessLabBackend(tmp_path)
    backend.start(OLD)
    try:
        yield tmp_path, backend
    finally:
        backend.stop()


@pytest.mark.parametrize('mode,old_mode,expected', [
    ('healthy', 'healthy', 'succeeded'),
    ('failed', 'healthy', 'rolled_back'),
    ('disconnect', 'healthy', 'rolled_back'),
    ('failed', 'failed', 'rollback_failed'),
])
def test_real_process_deployment_outcomes(lab, mode, old_mode, expected):
    """@description: Verify stop-before-start and stable observation for candidate and rollback.
    @param lab: Real fake process fixture.
    @param mode: Candidate's simulated Discord behavior.
    @param old_mode: Old release's simulated rollback health.
    @param expected: Truthful terminal transaction result.
    @return: None.
    """
    directory, backend = lab
    (directory / 'modes.json').write_text(json.dumps({NEW: mode, OLD: old_mode}))
    controller = Controller(directory, backend, authorize=lambda _: True)
    assert controller.execute(request()) == expected
    journal = load(directory / 'journal.json')
    assert journal['last_known_good'] == (NEW if expected == 'succeeded' else OLD)
    assert journal['requests']['10-1']['status'] == expected
    assert not backend.is_stopped()
    if expected == 'rollback_failed':
        assert controller.execute(request(11)) == 'rejected'


def test_duplicate_stale_lock_and_preflight(lab):
    """@description: Serialize controllers and prevent old or conflicting requests changing a newer release.
    @param lab: Isolated process fixture.
    @return: None.
    """
    directory, backend = lab
    controller = Controller(directory, backend, authorize=lambda _: True)
    with exclusive(directory):
        with pytest.raises(BlockingIOError):
            controller.execute(request())
    assert controller.execute(request()) == 'succeeded'
    assert controller.execute(request()) == 'succeeded'
    assert controller.execute(request(digest=OLD)) == 'rejected'
    assert controller.execute(request(9)) == 'rejected'
    (directory / 'reject-preflight').touch()
    assert controller.execute(request(11)) == 'rejected'
    assert json.loads((directory / 'active.json').read_text())['release'] == NEW


def test_unverified_requests_cannot_stop_gateway(lab):
    """@description: The default host policy refuses deployment even when syntax is valid.
    @param lab: Isolated process fixture.
    @return: None.
    """
    directory, backend = lab
    assert Controller(directory, backend).execute(request()) == 'rejected'
    assert json.loads((directory / 'active.json').read_text())['release'] == OLD


def test_main_process_death_after_ready_is_unhealthy(lab):
    """@description: Real fake-process death invalidates a retained healthy snapshot.
    @param lab: Isolated process fixture.
    @return: None.
    """
    directory, backend = lab
    assert backend.healthy(OLD)
    backend.children[0].kill()
    backend.children[0].wait(timeout=5)
    assert (directory / 'health.json').is_file()
    assert not backend.healthy(OLD)


def test_controller_outlives_submitting_client(lab):
    """@description: A real detached lab worker completes after the submitting client exits.
    @param lab: Isolated process fixture.
    @return: None.
    """
    directory, backend = lab
    program = '''
import subprocess,sys
subprocess.Popen([sys.executable,'-m','deploy.lab_worker',sys.argv[1],sys.argv[2]],
                 stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
'''
    result = subprocess.run([sys.executable, '-c', program, str(directory),
                             f'deploy-v1 {"a" * 40} {NEW} 10 1'], timeout=5)
    assert result.returncode == 0
    deadline = time.monotonic() + 10
    while True:
        transaction = load(directory / 'journal.json')['requests'].get('10-1')
        if transaction and transaction['status'] == 'succeeded':
            break
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert backend.healthy(NEW)


@pytest.mark.parametrize('phase,expected', [
    ('prepared', 'rejected'), ('stopping_old', 'rolled_back'),
    ('starting_candidate', 'rolled_back'), ('observing_candidate', 'rolled_back'),
    ('rollback_stopping', 'rollback_failed'), ('rollback_starting', 'rollback_failed'),
    ('rollback_observing', 'rollback_failed'),
])
def test_recovery_at_durable_phase_boundaries(lab, phase, expected):
    """@description: Simulate cancellation at each journal boundary with real reconciliation processes.
    @param lab: Isolated process fixture.
    @param phase: Simulated last durable intent at controller loss.
    @param expected: Safe recovery outcome, never automatic repeated rollback.
    @return: None.
    """
    from deploy.journal import persist
    directory, backend = lab
    journal = load(directory / 'journal.json')
    journal['requests']['10-1'] = {'request': request(), 'previous': OLD, 'phase': phase, 'status': 'running'}
    journal['highwater'] = 10
    persist(directory / 'journal.json', journal)
    result = Controller(directory, backend, authorize=lambda _: True).execute(request())
    assert result == expected
    assert load(directory / 'journal.json')['requests']['10-1']['status'] == expected
    assert load(directory / 'journal.json')['last_known_good'] == OLD


def test_unconfirmed_stop_never_starts_another_gateway(lab):
    """@description: An ambiguous old-process stop latches failure without any candidate start.
    @param lab: Isolated process fixture.
    @return: None.
    """
    from unittest.mock import Mock
    directory, backend = lab
    backend.stop = Mock()
    backend.start = Mock()
    assert Controller(directory, backend, authorize=lambda _: True).execute(request()) == 'rollback_failed'
    backend.start.assert_not_called()
    # Restore fixture cleanup rather than leaving the process behind.
    del backend.stop


def test_interrupted_rollback_is_not_retried(lab):
    """@description: Repeated controller recovery leaves a rollback failure latched for maintenance.
    @param lab: Isolated process fixture.
    @return: None.
    """
    from deploy.journal import persist
    directory, backend = lab
    journal = load(directory / 'journal.json')
    journal['requests']['10-1'] = {'request': request(), 'previous': OLD, 'phase': 'rollback_starting', 'status': 'running'}
    persist(directory / 'journal.json', journal)
    controller = Controller(directory, backend, authorize=lambda _: True)
    assert controller.execute(request(11)) == 'rejected'
    assert controller.execute(request(12)) == 'rejected'
    assert backend.healthy(OLD)


def test_controller_cancellation_and_recovery(lab):
    """@description: A real killed controller leaves durable intent and a separate controller recovers it.
    @param lab: Isolated fake process fixture.
    @return: None.
    """
    directory, backend = lab
    program = '''
import sys,time
from pathlib import Path
from deploy.controller import Controller,parse_request
from deploy.lab_backend import ProcessLabBackend
directory=Path(sys.argv[1])
class InterruptedBackend(ProcessLabBackend):
    def start(self, release):
        (self.directory/'controller-at-start').touch()
        time.sleep(30)
Controller(directory,InterruptedBackend(directory),authorize=lambda _:True).execute(parse_request(sys.argv[2]))
'''
    child = subprocess.Popen([sys.executable, '-c', program, str(directory),
                              f'deploy-v1 {"a" * 40} {NEW} 10 1'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        while not (directory / 'controller-at-start').exists():
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(0.02)
        child.terminate()
        child.wait(timeout=5)
        assert backend.is_stopped()
        assert Controller(directory, backend, authorize=lambda _: True).execute(request()) == 'rolled_back'
        assert backend.healthy(OLD)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize('line', ['deploy-v1 x y 1 1', 'status-v1 1 1\nextra',
                                  f'deploy-v1 {"a"*40} {NEW} 1 1\n\n', 'x' * 257])
def test_request_protocol_rejects_extra_input(line):
    """@description: Reject command injection, extra lines and oversized input.
    @param line: Untrusted SSH-style input.
    @return: None.
    """
    with pytest.raises(ValueError):
        parse_request(line)
