"""Verify the writer gate across two independent processes on the explicitly isolated MySQL target.

Opt-in only: every test is skipped unless JUKEBOX_RUN_EXCHANGE_MYSQL_ISOLATED=1 and the isolated target is configured.
Each worker is a separate Python process running one production SubmissionRepository.exchange request. The parent holds the
jb2_control row lock itself and releases it only by committing, so the gate is controlled by the test rather than by timing.

What is and is not observed:
- The worker creates a signal immediately before the gate statement is invoked (test-only wrapper). That shows only the invocation, not that the statement reached the server or that the server is waiting.
- The parent does not query MySQL for lock waits: the exchange account has no monitoring privilege, and none is granted here.
- A worker still running while the parent holds the gate is therefore waiting at the gate by inference from ordering, not by a direct read
  of performance_schema. The 1205 case is different: the worker must return the server's lock-timeout code while the parent still holds the gate.
"""

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from bot.database.exchange_reservation import ExchangeStatus
from tests.financial_invariants import attribution_violations
from tests.isolated_mysql_target import ISOLATION_SWITCH, open_verified_connection
from tests.test_exchange_mysql_integration import USER_A, fixture_footprint, fixture_snapshot, isolated
from tests.test_exchange_repository_mysql import (
    assert_footprint_unchanged, assert_repository_state, funded_pair, repository_world,
)

pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(os.environ.get(ISOLATION_SWITCH) != "1", reason="Requires explicitly authorized isolated MySQL target"),
]

REPO_ROOT = Path(__file__).resolve().parents[1]  # [working directory]: Workers import the repository package from here.
STEP_TIMEOUT_SECONDS = 30  # [step bound]: Ready and gate signals must appear within this window or the test fails.
RESULT_TIMEOUT_SECONDS = 60  # [overall bound]: A released worker must return a result within this window, while the gate is still held.
BLOCK_OBSERVATION_SECONDS = 1.0  # [observation window]: Must stay below LOCK_WAIT_SECONDS so a healthy wait is not mistaken for a timeout.


class Worker:
    """Own one worker process and its synchronisation files for the duration of one test."""

    def __init__(self, label: str, request_key: str, directory: Path, go_file: Path) -> None:
        """@description: Prepare the files for one worker without starting it yet.
        @param label: Unique name of this worker within the test, so files never collide when request keys repeat.
        @param request_key: Durable technical request identity of this worker.
        @param directory: Per-test directory for this worker's markers.
        @param go_file: Shared release file created by the parent.
        @return: None.
        """
        self.ready_file = directory / f"{label}.ready"
        self.gate_file = directory / f"{label}.gate"
        self.go_file = go_file
        command = [sys.executable, "-X", "utf8", "-m", "tests.exchange_worker_process", "--request-key", request_key,
                   "--user-id", str(USER_A), "--ready-file", str(self.ready_file), "--go-file", str(go_file),
                   "--gate-file", str(self.gate_file)]
        self.process = subprocess.Popen(command, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_ready(self) -> None:
        """@description: Wait until this worker has passed its configuration check and is waiting for release.
        @return: None; a worker that never reports ready fails the test.
        """
        wait_for_file(self.ready_file, self.process)

    def wait_gate_sent(self) -> None:
        """@description: Wait until this worker has invoked the writer-gate statement. This is invocation, not proof that the server is waiting.
        @return: None; a worker that never reaches the gate fails the test.
        """
        wait_for_file(self.gate_file, self.process)

    def collect(self) -> dict:
        """@description: Return this worker's JSON result within the overall bound, killing the worker and failing if no result arrives.
        @return: Parsed business or database outcome.
        """
        try:
            stdout, stderr = self.process.communicate(timeout=RESULT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            self.kill()
            raise AssertionError("worker produced no result within the overall bound") from None
        assert self.process.returncode == 0, f"worker failed: {stderr.strip()[-500:]}"
        return json.loads(stdout.strip().splitlines()[-1])

    def kill(self) -> None:
        """@description: Terminate this worker if it is still running, so a failed test leaves no child process behind.
        @return: None.
        """
        if self.process.poll() is None:
            self.process.kill()
            self.process.communicate()


def wait_for_file(path: Path, process: subprocess.Popen) -> None:
    """@description: Poll for one marker file while failing fast when the worker exits early.
    @param path: Marker file the worker creates.
    @param process: Worker process being observed.
    @return: None; an early exit or a timeout fails the test with the worker's diagnostic output.
    """
    deadline = time.monotonic() + STEP_TIMEOUT_SECONDS
    while not path.exists():
        if process.poll() is not None:
            _, stderr = process.communicate()
            raise AssertionError(f"worker exited before {path.name}: {stderr.strip()[-500:]}")
        assert time.monotonic() < deadline, f"worker did not create {path.name}"
        time.sleep(0.01)


def release(workers: list, go_file: Path) -> None:
    """@description: Release every worker at once, after each has reported ready.
    @param workers: Workers to release together.
    @param go_file: Shared release file.
    @return: None.
    """
    for worker in workers:
        worker.wait_ready()
    go_file.write_text("go", encoding="utf-8")


@contextmanager
def held_writer_gate(target):
    """@description: Hold the production writer gate from the parent by locking the single jb2_control row, and release it on exit.
    @param target: Tuple of settings and identity pin for the isolated target.
    @return: Context whose exit commits (releasing the gate) on success and rolls back on failure.
    """
    settings, pin = target
    connection = open_verified_connection(settings, pin)
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM jb2_control WHERE singleton_id = 1 FOR UPDATE")
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def workers_for(specs: tuple, tmp_path: Path):
    """@description: Start the named workers and guarantee that every one of them is killed when the test ends, pass or fail.
    @param specs: One (label, request key) pair per worker.
    @param tmp_path: Per-test directory for markers.
    @return: List of started workers and the shared release file.
    """
    go_file = tmp_path / "go"
    workers = [Worker(label, key, tmp_path, go_file) for label, key in specs]
    try:
        yield workers, go_file
    finally:
        for worker in workers:
            worker.kill()


def test_second_process_waits_at_the_gate_and_completes_after_release(funded_pair, tmp_path) -> None:
    """@description: A worker that has invoked the gate statement is not finished while the parent holds the gate, and completes correctly after release.
    @param funded_pair: Two funded submitters and one candidate over real MySQL.
    @param tmp_path: Per-test directory for markers.
    @return: None.
    """
    target, _, _ = funded_pair
    before = fixture_snapshot(target)
    with workers_for((("blocked", "s2-gate-blocked"),), tmp_path) as (workers, go_file):
        worker = workers[0]
        with held_writer_gate(target):
            release(workers, go_file)
            worker.wait_gate_sent()
            time.sleep(BLOCK_OBSERVATION_SECONDS)
            assert worker.process.poll() is None, "the worker finished while the parent held the writer gate"
        result = worker.collect()
    assert result["status"] == ExchangeStatus.RESERVED.value and result["error_code"] is None
    assert attribution_violations("s2-gate-blocked", result["recommendation_id"], before, fixture_snapshot(target)) == ()
    assert_repository_state(funded_pair, 1, 0)


def test_gate_held_until_the_worker_reports_the_lock_timeout_and_nothing_is_written(funded_pair, tmp_path) -> None:
    """@description: The parent keeps the gate until the worker returns error 1205 by itself, then verifies the fixture range and releases.
    @param funded_pair: Two funded submitters and one candidate over real MySQL.
    @param tmp_path: Per-test directory for markers.
    @return: None.
    """
    target, _, _ = funded_pair
    before = fixture_footprint(target)
    with workers_for((("timeout", "s2-gate-timeout"),), tmp_path) as (workers, go_file):
        worker = workers[0]
        with held_writer_gate(target):
            release(workers, go_file)
            worker.wait_gate_sent()
            result = worker.collect()  # [gate still held]: The result is awaited inside the gate; the lock wait must expire on the server side.
            assert result == {"status": None, "recommendation_id": None, "error_code": 1205}
            assert_footprint_unchanged(target, before)
            assert_repository_state(funded_pair, 0, 1)
        assert worker.process.poll() is not None


def test_two_processes_with_different_keys_reserve_once_and_refuse_once(funded_pair, tmp_path) -> None:
    """@description: Two independent processes racing for the single credit produce one reservation and one refusal, and every new row is attributed to the winner.
    @param funded_pair: Two funded submitters and one candidate over real MySQL.
    @param tmp_path: Per-test directory for markers.
    @return: None.
    """
    target, _, _ = funded_pair
    keys = ("s2-diff-a", "s2-diff-b")
    before = fixture_snapshot(target)
    with workers_for((("first", keys[0]), ("second", keys[1])), tmp_path) as (workers, go_file):
        release(workers, go_file)
        results = [worker.collect() for worker in workers]
    assert sorted(result["status"] for result in results) == sorted([ExchangeStatus.RESERVED.value, ExchangeStatus.INSUFFICIENT_CREDITS.value])
    assert all(result["error_code"] is None for result in results)
    winner = next(index for index, result in enumerate(results) if result["status"] == ExchangeStatus.RESERVED.value)
    assert attribution_violations(keys[winner], results[winner]["recommendation_id"], before, fixture_snapshot(target)) == ()
    assert_repository_state(funded_pair, 1, 0)


def test_two_processes_with_the_same_key_reserve_once_and_replay(funded_pair, tmp_path) -> None:
    """@description: Two independent processes sharing one technical key produce one reservation and one replay of the same recommendation, and only the original writes.
    @param funded_pair: Two funded submitters and one candidate over real MySQL.
    @param tmp_path: Per-test directory for markers.
    @return: None.
    """
    target, _, _ = funded_pair
    before = fixture_snapshot(target)
    with workers_for((("first", "s2-same-key"), ("second", "s2-same-key")), tmp_path) as (workers, go_file):
        release(workers, go_file)
        results = [worker.collect() for worker in workers]
    assert sorted(result["status"] for result in results) == sorted([ExchangeStatus.RESERVED.value, ExchangeStatus.REPLAYED.value])
    assert all(result["error_code"] is None for result in results)
    assert results[0]["recommendation_id"] == results[1]["recommendation_id"]
    assert attribution_violations("s2-same-key", results[0]["recommendation_id"], before, fixture_snapshot(target)) == ()
    assert_repository_state(funded_pair, 1, 0)
