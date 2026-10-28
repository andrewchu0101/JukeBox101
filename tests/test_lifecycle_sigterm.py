"""Verify the formal entry's bounded SIGTERM shutdown: new work stops, in-flight work completes or is reported, the Discord connection closes, and exit codes are truthful.

The real bot.__main__.main() runs in a child process. Discord login and the Gateway loop are replaced by a recorder, in-flight interaction work is scripted, and the MySQL connector is trapped.
Nothing connects to Discord or MySQL, and no production secret is used. The child shortens the shutdown deadline as a module attribute so the tests stay fast; the entry itself has no environment switch for it.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bot import __main__ as entry

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
TEST_DEADLINE_SECONDS = 6  # [test-only deadline]: Scaled-down total budget set as a module attribute in the child.
TEST_INTERACTION_WAIT_SECONDS = 2  # [test-only interaction wait]: Scaled-down share of the deadline for in-flight interaction work.
TEST_SETTLE_SECONDS = 1  # [test-only settle]: Scaled-down cancellation settle window.
TEST_CLOSE_RESERVED_SECONDS = 4  # [test-only close reserve]: Scaled-down budget kept for the Discord close.
CHILD_EXIT_TIMEOUT_SECONDS = 30  # [test bound]: Generous upper bound on any child; the shutdown itself must finish well before it.

CHILD_PROGRAM = """
import asyncio
import time
import discord
from bot import __main__ as entry

entry.SHUTDOWN_DEADLINE_SECONDS = %(deadline)r
entry.INTERACTION_WAIT_SECONDS = %(interaction)r
entry.CANCELLATION_SETTLE_SECONDS = %(settle)r
entry.CLOSE_RESERVED_SECONDS = %(close_reserved)r
SCENARIO = %(scenario)r

async def in_flight_finishes():
    await asyncio.sleep(0.5)
    print("IN_FLIGHT_COMPLETED", flush=True)

async def in_flight_forever():
    try:
        await asyncio.sleep(3600)
    finally:
        print("IN_FLIGHT_CANCELLED", flush=True)

async def database_thread_stuck():
    try:
        await asyncio.to_thread(time.sleep, 60)
    finally:
        print("IN_FLIGHT_CANCELLED", flush=True)

WORK = {
    "finishes": in_flight_finishes,
    "stuck": in_flight_forever,
    "stuck_db": database_thread_stuck,
}

async def fake_start(self, token, *, reconnect=True):
    print("LOGIN_STARTED", flush=True)
    if SCENARIO in WORK:
        asyncio.create_task(WORK[SCENARIO](), name="discord.py: on_interaction")
    await asyncio.Event().wait()

async def fake_close(self):
    print("CLIENT_CLOSED", flush=True)
    if SCENARIO == "close_fails":
        raise RuntimeError("synthetic close failure")
    if SCENARIO == "close_hangs":
        await asyncio.Event().wait()

def trapped_connect(*args, **kwargs):
    raise AssertionError("No database connection may be opened by the lifecycle test")

discord.Client.start = fake_start
discord.Client.close = fake_close
import pymysql
pymysql.connect = trapped_connect
entry.main()
"""

ENTRY_ENVIRONMENT = {
    "DISCORD_BOT_TOKEN": "SYNTHETIC-TOKEN-0123456789",
    "DISCORD_DEV_GUILD_ID": "456",
    "DISCORD_EXPECTED_APPLICATION_ID": "789001",
    "DISCORD_OWNER_USER_ID": "123",
    "DB_HOST": "db.synthetic.invalid",
    "DB_PORT": "3306",
    "DB_NAME": "jukebox_synthetic",
    "DB_USER": "jukebox_app",
    "DB_PASSWORD": "SYNTHETIC-DB-PASSWORD-9876",
}


class Shutdown:
    """Result of one SIGTERM scenario: exit status, captured output and elapsed seconds from signal to exit."""

    def __init__(self, returncode: int, stdout: str, stderr: str, elapsed: float) -> None:
        """@description: Keep what the scenario observed so each test asserts one contract.
        @param returncode: Exit status of the child.
        @param stdout: Captured standard output after shutdown.
        @param stderr: Captured standard error after shutdown.
        @param elapsed: Seconds from SIGTERM to exit.
        @return: None.
        """
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.elapsed = elapsed


def _stop_child_after_login(scenario: str) -> Shutdown:
    """@description: Start the real formal entry for one scenario, send SIGTERM once it is logged in, and record how it ends.
    @param scenario: Name of the scripted in-flight behaviour or failure.
    @return: Exit status, output and elapsed time.
    """
    environment = dict(os.environ, **ENTRY_ENVIRONMENT, PYTHONPATH=str(REPOSITORY_ROOT))
    for name in ("DISCORD_BOT_TOKEN_FILE", "DB_PASSWORD_FILE"):
        environment.pop(name, None)
    program = CHILD_PROGRAM % {"deadline": TEST_DEADLINE_SECONDS, "interaction": TEST_INTERACTION_WAIT_SECONDS,
                               "settle": TEST_SETTLE_SECONDS, "close_reserved": TEST_CLOSE_RESERVED_SECONDS, "scenario": scenario}
    child = subprocess.Popen([sys.executable, "-c", program], cwd=REPOSITORY_ROOT, env=environment,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "LOGIN_STARTED", child.stderr.read()
        started = time.monotonic()
        child.send_signal(signal.SIGTERM)
        output, errors = child.communicate(timeout=CHILD_EXIT_TIMEOUT_SECONDS)
        return Shutdown(child.returncode, output, errors, time.monotonic() - started)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()


@pytest.fixture(scope="module")
def idle_shutdown() -> Shutdown:
    """@description: One shutdown with no in-flight work, shared by the idle assertions.
    @return: Shutdown result of the idle scenario.
    """
    return _stop_child_after_login("idle")


def test_idle_sigterm_closes_the_discord_connection_once_and_exits_zero(idle_shutdown):
    """@description: With no work in flight, SIGTERM closes Discord once and the process reports a clean stop.
    @param idle_shutdown: Result of the idle scenario.
    @return: None.
    """
    assert idle_shutdown.stdout.count("CLIENT_CLOSED") == 1
    assert idle_shutdown.returncode == 0


def test_idle_sigterm_finishes_without_waiting_for_the_deadline(idle_shutdown):
    """@description: Cleanup that has nothing to wait for ends at once instead of sleeping out the deadline.
    @param idle_shutdown: Result of the idle scenario.
    @return: None.
    """
    assert idle_shutdown.elapsed < TEST_DEADLINE_SECONDS


def test_in_flight_work_that_finishes_within_the_deadline_completes_and_exits_zero():
    """@description: Work still running at SIGTERM is allowed to finish inside the bounded deadline and is not cancelled.
    @return: None.
    """
    shutdown = _stop_child_after_login("finishes")
    assert "IN_FLIGHT_COMPLETED" in shutdown.stdout
    assert "IN_FLIGHT_CANCELLED" not in shutdown.stdout
    assert shutdown.returncode == 0


def test_in_flight_work_past_the_deadline_is_cancelled_and_reported_as_incomplete():
    """@description: Work that outlives the deadline is cancelled, the Discord connection still closes, and the exit status says the stop was incomplete.
    @return: None.
    """
    shutdown = _stop_child_after_login("stuck")
    assert "IN_FLIGHT_CANCELLED" in shutdown.stdout
    assert shutdown.stdout.count("CLIENT_CLOSED") == 1
    assert shutdown.returncode == entry.SHUTDOWN_INCOMPLETE_EXIT_CODE
    assert f"still running after {TEST_INTERACTION_WAIT_SECONDS} seconds" in shutdown.stderr
    assert "close_discord" not in shutdown.stderr


def test_database_worker_thread_past_the_deadline_is_reported_and_the_process_does_not_hang():
    """@description: A database call still running in a worker thread cannot be stopped; the stop reports it as unfinished and exits before the orchestrator's grace period ends.
    @return: None.
    """
    shutdown = _stop_child_after_login("stuck_db")
    assert shutdown.returncode == entry.SHUTDOWN_INCOMPLETE_EXIT_CODE
    assert "database" in shutdown.stderr
    assert shutdown.elapsed < TEST_DEADLINE_SECONDS + 10


def test_a_failing_discord_close_is_reported_by_step_and_class_without_a_traceback_or_exit_zero():
    """@description: A cleanup error is never swallowed: the failing step and exception class are reported, the status is nonzero, and no traceback or secret text is printed.
    @return: None.
    """
    shutdown = _stop_child_after_login("close_fails")
    assert shutdown.returncode == entry.SHUTDOWN_FAILED_EXIT_CODE
    assert "close_discord" in shutdown.stderr and "RuntimeError" in shutdown.stderr
    assert "Traceback" not in shutdown.stderr
    assert "synthetic close failure" not in shutdown.stderr


def test_the_shutdown_deadline_is_twenty_seconds_and_inside_the_compose_grace_period():
    """@description: The application deadline is 20 seconds and stays inside the 30-second Compose stop_grace_period.
    @return: None.
    """
    compose_text = (REPOSITORY_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert entry.SHUTDOWN_DEADLINE_SECONDS == 20
    assert "stop_grace_period: 30s" in compose_text
    assert entry.SHUTDOWN_DEADLINE_SECONDS < 30


def test_admission_closes_before_cleanup_so_no_new_interaction_is_accepted():
    """@description: After shutdown starts, the admission gate refuses new interactions even when the underlying guard would admit them.
    @return: None.
    """
    async def scenario() -> tuple[bool, bool]:
        gate = entry.ShutdownGate(lambda interaction: asyncio.sleep(0, result=True))
        before = await gate(object())
        gate.stop_admitting()
        after = await gate(object())
        return before, after
    assert asyncio.run(scenario()) == (True, False)


def test_a_close_that_never_returns_is_bounded_by_the_same_deadline_and_reported_as_a_timeout():
    """@description: A Discord close that never returns counts against the same cleanup deadline; the process ends near the deadline with a nonzero status and an explicit timeout diagnostic, not a Docker kill.
    @return: None.
    """
    shutdown = _stop_child_after_login("close_hangs")
    assert shutdown.elapsed <= TEST_DEADLINE_SECONDS + 1
    assert shutdown.returncode == entry.SHUTDOWN_FAILED_EXIT_CODE
    assert "close_discord:timeout" in shutdown.stderr
    assert "Traceback" not in shutdown.stderr
    assert "SYNTHETIC" not in shutdown.stderr


def test_the_budget_split_gives_interactions_fifteen_seconds_settle_one_and_keeps_four_for_close():
    """@description: The production budget is 15 seconds for in-flight interactions, 1 second for cancel settling and at least 4 seconds reserved for the Discord close, all inside the 20-second total.
    @return: None.
    """
    assert entry.INTERACTION_WAIT_SECONDS == 15
    assert entry.CANCELLATION_SETTLE_SECONDS == 1
    assert entry.CLOSE_RESERVED_SECONDS == 4
    assert entry.INTERACTION_WAIT_SECONDS + entry.CANCELLATION_SETTLE_SECONDS + entry.CLOSE_RESERVED_SECONDS <= entry.SHUTDOWN_DEADLINE_SECONDS

