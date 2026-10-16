"""Supervise rollback-only contention workers with bounded waits and isolated process-owned connections.

Workers never commit; the supervisor terminates stuck processes and reports cleanup uncertainty as failure.
"""

import multiprocessing
import time
from contextlib import ExitStack
from dataclasses import dataclass

import pymysql

from tests.repository_mysql_support import authorized_repository_settings, rollback_repository_session
from bot.urls import parse_youtube_url

WORKER_HOLD_SECONDS = 20
SCENARIO_TIMEOUT_SECONDS = 30
PROCESS_JOIN_SECONDS = 3


@dataclass(frozen=True)
class SubmissionPlan:
    """Carry synthetic user/video identifiers without credentials between worker processes."""

    discord_user_id: int
    source_url: str


def contention_worker(plan: SubmissionPlan, role: str, release_event, sender) -> None:
    """@description: Exercise real repository SQL in one independent rollback-only transaction.
    @param plan: Synthetic submission identity, without secrets.
    @param role: Holder waits for explicit release; contender expects a bounded SQL lock failure.
    @param release_event: Supervisor signal releasing the holder's uncommitted transaction.
    @param sender: One-way pipe emitting only safe protocol outcomes.
    @return: None; completion is reported only after rollback and connection close.
    """
    try:
        with rollback_repository_session(authorized_repository_settings()) as session:
            outcome = _perform_submission(session, plan, role, release_event, sender)
        sender.send(outcome)
    except pymysql.MySQLError:
        sender.send("unexpected_database_error")
    except (ValueError, RuntimeError, AssertionError, OSError) as worker_error:
        sender.send("worker_failure:" + type(worker_error).__name__)
    finally:
        sender.close()


def _perform_submission(session, plan: SubmissionPlan, role: str, release_event, sender) -> str:
    """@description: Hold successful uncommitted writes while another connection attempts the same identity.
    @param session: Worker-owned rollback-only repository session.
    @param plan: Synthetic recipient and video shared by the scenario.
    @param role: Protocol role for lock ownership or competing insertion.
    @param release_event: Bounded holder-release synchronization primitive.
    @param sender: Safe protocol response pipe.
    @return: Safe protocol result, emitted only after outer cleanup succeeds.
    """
    try:
        result = session.repository.submit_pending(plan.discord_user_id, parse_youtube_url(plan.source_url))
    except pymysql.OperationalError as database_error:
        if role == "contender" and database_error.args[0] == 1205:
            return "lock_timeout"
        raise
    if result.resolution_status != "pending" or result.duplicate_submission:
        raise AssertionError("Unexpected rollback-only submission outcome")
    if role == "holder":
        sender.send("holding")
        if not release_event.wait(WORKER_HOLD_SECONDS):
            raise TimeoutError("Holder release deadline exceeded")
    return "rolled_back"


def wait_protocol(receiver, deadline: float) -> str:
    """@description: Avoid indefinite IPC waits when a child crashes before reporting.
    @param receiver: Supervisor-owned pipe receiving safe worker messages.
    @param deadline: Absolute monotonic deadline for the entire scenario.
    @return: Worker protocol outcome; raises on elapsed deadline or missing report.
    """
    remaining_seconds = max(0, deadline - time.monotonic())
    if not receiver.poll(remaining_seconds):
        raise TimeoutError("Repository worker protocol deadline exceeded")
    try:
        return receiver.recv()
    except EOFError:
        raise RuntimeError("Repository worker exited without cleanup report") from None


def stop_process(process) -> None:
    """@description: Bound process cleanup without waiting forever for a database socket.
    @param process: Started contention worker owned by the supervisor.
    @return: None; forced termination or inability to reap is an acceptance failure.
    """
    process.join(PROCESS_JOIN_SECONDS)
    forced_stop = process.is_alive()
    if forced_stop:
        process.terminate()
        process.join(PROCESS_JOIN_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(PROCESS_JOIN_SECONDS)
    if process.is_alive():
        raise RuntimeError("Worker could not be reaped; server cleanup is uncertain")
    exit_code = process.exitcode
    process.close()
    if forced_stop or exit_code != 0:
        raise RuntimeError("Worker did not exit normally; server cleanup requires investigation")


def start_worker(stack: ExitStack, context, plan: SubmissionPlan, role: str, release_event):
    """@description: Register pipe and process cleanup before allowing scenario assertions to fail.
    @param stack: Supervisor cleanup stack, unwound even after failed assertions.
    @param context: Explicit spawn multiprocessing context, independent of inherited DB state.
    @param plan: Credential-free synthetic submission inputs.
    @param role: Holder, contender or retry role.
    @param release_event: Bounded holder-release signal.
    @return: Supervisor receive end for the newly started worker.
    """
    receiver, sender = context.Pipe(duplex=False)
    stack.callback(receiver.close)
    stack.callback(sender.close)
    process = context.Process(target=contention_worker, args=(plan, role, release_event, sender))
    process.start()
    stack.callback(stop_process, process)
    sender.close()
    return receiver


def run_contention(holder_plan: SubmissionPlan, contender_plan: SubmissionPlan) -> None:
    """@description: Verify blocking, bounded lock rejection and successful retry after rollback release.
    @param holder_plan: First uncommitted pending submission.
    @param contender_plan: Same video, with either the same or another synthetic recipient.
    @return: None; safe protocol and cleanup failures become failed acceptance assertions.
    """
    context = multiprocessing.get_context("spawn")
    release_event = context.Event()  # [lock lifetime]: Always released before worker cleanup.
    deadline = time.monotonic() + SCENARIO_TIMEOUT_SECONDS
    with ExitStack() as stack:
        holder = start_worker(stack, context, holder_plan, "holder", release_event)
        stack.callback(release_event.set)
        assert wait_protocol(holder, deadline) == "holding"
        contender = start_worker(stack, context, contender_plan, "contender", release_event)
        assert wait_protocol(contender, deadline) == "lock_timeout"
        release_event.set()
        assert wait_protocol(holder, deadline) == "rolled_back"
        retry = start_worker(stack, context, contender_plan, "retry", release_event)
        assert wait_protocol(retry, deadline) == "rolled_back"
