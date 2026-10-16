"""Run real MySQL scenarios inside a parent-enforced 30-second process deadline.

All repository calls use application credentials; synthetic faults affect the client boundary after actual server operations.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from time import monotonic
from datetime import timedelta
from typing import Any
import pymysql
from bot.database.review_persistence import ReviewCommand
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.review import StaleReviewVersion
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.version_identity import VersionExclusion
from tests.batch_b_mysql_cases import request, repository, approve, observe, assert_accounting
from tests.batch_b_harness_database import verified_connection
from tests.batch_b_mysql_faults import InjectedClientFault


def persistence(settings: Any, scope: Any) -> None:
    """@description: Verify committed intake and replay of the immutable original operation receipt.
    @param settings: Application settings only.
    @param scope: Predeclared run roots.
    @return: None.
    """
    repo = repository(settings, scope)  # [production boundary]: No fake database is used.
    initial = repo.accept(request(scope, 1))
    approve(repo, scope, 1)
    retry = repo.accept(replace(request(scope, 1), source_url=request(scope, 1).source_url + "?t=30&list=test"))
    assert not retry.create_share_event and retry.candidate == initial.candidate
    rows = observe(settings)
    assert len(rows["jb2_events"]) == len(rows["jb2_receipts"]) == 1
    assert_accounting(rows, 0)


def priority(settings: Any, scope: Any) -> None:
    """@description: Keep pending A ahead of approved B using database-allocated acceptance order.
    @param settings: Application settings.
    @param scope: Same-version registered requests.
    @return: None.
    """
    repo = repository(settings, scope)
    first = repo.accept(request(scope, 1))  # [earlier acceptance]: Review completion must not change this order.
    second = repo.accept(request(scope, 2))
    assert first.candidate.accepted_sequence < second.candidate.accepted_sequence
    approve(repo, scope, 2)
    assert repo.settle(request(scope, 2).operation_id).status is Status.WAITING_PREDECESSOR
    approve(repo, scope, 1)
    assert repo.settle(request(scope, 1).operation_id).status is Status.AWARDED
    assert repo.settle(request(scope, 2).operation_id).status is Status.INELIGIBLE
    assert_accounting(observe(settings), 1)


def rejected(settings: Any, scope: Any) -> None:
    """@description: Release the next valid candidate only after explicit rejection of its predecessor.
    @param settings: Application settings.
    @param scope: Registered ordered pair.
    @return: None.
    """
    repo = repository(settings, scope)
    for number in (1, 2):
        repo.accept(request(scope, number))
    approve(repo, scope, 2)
    operation = request(scope, 1).operation_id
    repo.review(ReviewCommand(operation + ":reject", operation, scope.owner_id, "synthetic ineligible song", 1, song_eligible=False))
    assert repo.settle(request(scope, 2).operation_id).status is Status.AWARDED
    assert_accounting(observe(settings), 1)


def concurrent_accept(settings: Any, scope: Any) -> None:
    """@description: Race independent application connections through the real InnoDB acceptance gate.
    @param settings: Application settings.
    @param scope: Two independently registered intake operations.
    @return: None.
    """
    repo = repository(settings, scope)
    barrier = Barrier(2)  # [synchronized race]: Neither worker is ordered by Python submission time.
    def accept(number: int) -> Any:
        """@description: Start the independent transaction only after both workers are ready.
        @param number: Registered request index.
        @return: Actual committed intake result.
        """
        barrier.wait(timeout=5)
        return repo.accept(request(scope, number))
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(accept, number) for number in (1, 2)]
        results = [future.result(timeout=12) for future in futures]
    sequences = sorted(result.candidate.accepted_sequence for result in results)
    assert sequences[1] == sequences[0] + 1
    assert len(observe(settings)["jb2_events"]) == 2


def concurrent_reward(settings: Any, scope: Any) -> None:
    """@description: Compete for one global version claim using separate real settlement transactions.
    @param settings: Application settings.
    @param scope: Two same-recording videos.
    @return: None.
    """
    repo = repository(settings, scope)
    for number in (1, 2):
        repo.accept(request(scope, number))
        approve(repo, scope, number)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(repo.settle, request(scope, number).operation_id) for number in (2, 1)]
        results = [future.result(timeout=12) for future in futures]
    assert results[1].status is Status.AWARDED
    assert results[0].status in {Status.WAITING_PREDECESSOR, Status.INELIGIBLE}
    assert repo.settle(request(scope, 1).operation_id).status is Status.AWARDED
    assert_accounting(observe(settings), 1)


def lock_timeout(settings: Any, scope: Any) -> None:
    """@description: Verify the configured two-second InnoDB wait actually returns server error 1205.
    @param settings: Application settings for both independent sessions.
    @param scope: Registered intake operation that must not partially persist.
    @return: None.
    """
    connection = verified_connection(settings, "jukebox101_test_app")  # [blocking session]: Owns only the control-row lock.
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM jb2_control WHERE singleton_id=1 FOR UPDATE")
            cursor.fetchone()
        with ThreadPoolExecutor(max_workers=1) as workers:
            started = monotonic()  # [observed wait]: Confirm error 1205 came after a genuine bounded lock wait.
            future = workers.submit(repository(settings, scope).accept, request(scope, 1))
            try:
                future.result(timeout=8)
            except pymysql.OperationalError as error:
                assert error.args[0] == 1205 and 1.5 <= monotonic() - started <= 8
            else:
                raise AssertionError("LOCK_WAIT_DID_NOT_FAIL")
    finally:
        connection.rollback()
        connection.close()
    assert not observe(settings)["jb2_events"]


def owner_review(settings: Any, scope: Any) -> None:
    """@description: Persist Owner revisions and evidence while rejecting unauthorized or stale decisions.
    @param settings: Application settings.
    @param scope: Registered synthetic Owner.
    @return: None.
    """
    repo = repository(settings, scope)
    repo.accept(request(scope, 1))
    first = approve(repo, scope, 1)
    assert approve(repo, scope, 1) == first
    operation = request(scope, 1).operation_id
    try:
        repo.review(ReviewCommand(operation + ":stale", operation, scope.owner_id, "stale revision", 0))
    except StaleReviewVersion:
        pass
    else:
        raise AssertionError("STALE_REVIEW_ACCEPTED")
    try:
        repo.pending_reminders(scope.owner_id + 100)
    except PermissionError:
        pass
    else:
        raise AssertionError("UNAUTHORIZED_OWNER_ACCEPTED")
    rows = observe(settings)
    assert len(rows["jb2_decisions"]) == 1 and len(rows["jb2_review_history"]) >= 2
    assert len(rows["jb2_evidence"]) >= 2 and len(rows["jb2_reminders"]) == 1


def conflict(settings: Any, scope: Any) -> None:
    """@description: Freeze related unsettled candidates and preserve the already committed award.
    @param settings: Application settings.
    @param scope: Registered same-version pair and exclusion decision.
    @return: None.
    """
    repo = repository(settings, scope)
    repo.accept(request(scope, 1))
    approve(repo, scope, 1)
    repo.settle(request(scope, 1).operation_id)
    repo.accept(request(scope, 2))
    approve(repo, scope, 2)
    identity = request(scope, 1).identity
    proof = VersionExclusion(identity.video, identity.version, scope.run_id + ":exclusion")
    repo.exclude(scope.run_id + ":exclude", scope.owner_id, "synthetic conflicting evidence", proof)
    assert repo.settle(request(scope, 2).operation_id).status is Status.CONFLICT
    assert repo.settle(request(scope, 1).operation_id).status is Status.AWARDED
    assert any(row["conflict_frozen"] for row in observe(settings)["jb2_events"])
    assert_accounting(observe(settings), 1)


def remix_merge(settings: Any, scope: Any) -> None:
    """@description: Award trusted distinct versions and audit a later merge without rewriting original ledgers.
    @param settings: Application settings.
    @param scope: Predeclared independent recording identities.
    @return: None.
    """
    repo = repository(settings, scope)
    for number in (1, 3):
        repo.accept(request(scope, number))
        approve(repo, scope, number)
        assert repo.settle(request(scope, number).operation_id).status is Status.AWARDED
    before = observe(settings)["credit_ledger"]
    repo.merge(scope.run_id + ":merge", scope.owner_id, "synthetic confirmed same recording",
               request(scope, 1).identity.version.value, request(scope, 3).identity.version.value)
    rows = observe(settings)
    assert rows["credit_ledger"] == before and len(rows["jb2_exceptions"]) == 1
    assert_accounting(rows, 2)


def reminder_seed(settings: Any, scope: Any) -> None:
    """@description: Establish a real intake-based reminder deadline before harness-only clock setup.
    @param settings: Application settings.
    @param scope: Registered pending request.
    @return: None.
    """
    repo = repository(settings, scope)
    repo.accept(request(scope, 1))
    rows = observe(settings)
    assert rows["jb2_reminders"][0]["next_due_at"] - rows["jb2_events"][0]["accepted_at"] == timedelta(hours=24)
    assert not repo.pending_reminders(scope.owner_id)


def reminder_read(settings: Any, scope: Any) -> None:
    """@description: Persist and replay a due daily summary without sending Discord messages or changing priority.
    @param settings: Application settings.
    @param scope: Registered pending request with a harness-adjusted due time.
    @return: None.
    """
    repo = repository(settings, scope)
    sequence = observe(settings)["jb2_events"][0]["accepted_sequence"]
    assert repo.pending_reminders(scope.owner_id) == (request(scope, 1).operation_id,)
    assert repo.pending_reminders(scope.owner_id) == (request(scope, 1).operation_id,)
    rows = observe(settings)
    assert len(rows["jb2_reminder_batches"]) == 1 and rows["jb2_events"][0]["accepted_sequence"] == sequence


def rollback_case(settings: Any, scope: Any, prefix: str) -> None:
    """@description: Assert real SQL mutations roll back when a later client exception interrupts the transaction.
    @param settings: Application settings.
    @param scope: Registered operation.
    @param prefix: Statement prefix after which the client intentionally fails.
    @return: None.
    """
    intake_fault = prefix in {"INSERT INTO jb2_events", "INSERT INTO jb2_receipts", "INSERT INTO jb2_evidence"}
    repo = repository(settings, scope)
    if not intake_fault:
        repo.accept(request(scope, 1))
        approve(repo, scope, 1)
    before = observe(settings)  # [committed baseline]: Includes control revision and every dependent table.
    faulty = repository(settings, scope, {"sql": prefix})
    try:
        faulty.accept(request(scope, 1)) if intake_fault else faulty.settle(request(scope, 1).operation_id)
    except InjectedClientFault:
        pass
    else:
        raise AssertionError("FAULT_DID_NOT_FIRE")
    assert observe(settings) == before
    repo.accept(request(scope, 1)) if intake_fault else repo.settle(request(scope, 1).operation_id)
    assert_accounting(observe(settings), 0 if intake_fault else 1)


def uncertain_case(settings: Any, scope: Any, mode: str) -> None:
    """@description: Replay the same idempotency key after before-commit and after-real-commit acknowledgement loss.
    @param settings: Application settings.
    @param scope: Registered operation.
    @param mode: Intake or settlement plus the injected acknowledgement-loss position.
    @return: None.
    """
    operation, position = mode.split(":")
    repo = repository(settings, scope)
    if operation == "settle":
        repo.accept(request(scope, 1))
        approve(repo, scope, 1)
    faulty = repository(settings, scope, {"commit": position})
    try:
        faulty.accept(request(scope, 1)) if operation == "accept" else faulty.settle(request(scope, 1).operation_id)
    except CommitOutcomeUnknown:
        pass
    else:
        raise AssertionError("COMMIT_FAULT_DID_NOT_FIRE")
    for _ in range(2):
        repo.accept(request(scope, 1)) if operation == "accept" else repo.settle(request(scope, 1).operation_id)
    rows = observe(settings)
    assert len(rows["jb2_events"]) == 1
    assert_accounting(rows, 1 if operation == "settle" else 0)


def scenario_process(pipe: Any, settings: Any, scope: Any, scenario: str) -> None:
    """@description: Return only sanitized outcome codes from the bounded real-MySQL worker process.
    @param pipe: Parent-owned result channel; credentials are never returned.
    @param settings: App settings passed privately to the spawned worker.
    @param scope: Registered roots and synthetic Owner.
    @param scenario: Fixed test selector.
    @return: None.
    """
    try:
        dispatch(settings, scope, scenario)
        pipe.send({"status": "PASS", "scenario": scenario})
    except BaseException as error:
        number = error.args[0] if isinstance(error, pymysql.MySQLError) and error.args and type(error.args[0]) is int else None
        pipe.send({"status": "FAIL", "scenario": scenario, "error_type": type(error).__name__, "mysql_error_number": number})
    finally:
        pipe.close()


def dispatch(settings: Any, scope: Any, scenario: str) -> None:
    """@description: Restrict worker execution to reviewed real-MySQL scenarios, never arbitrary callables.
    @param settings: App settings.
    @param scope: Registered roots.
    @param scenario: Exact scenario selector.
    @return: None.
    """
    cases = {function.__name__: function for function in (persistence, priority, rejected, concurrent_accept,
             concurrent_reward, concurrent_retry, lock_timeout, owner_review, conflict, remix_merge, reminder_seed, reminder_read)}
    if scenario.startswith("rollback:"):
        rollback_case(settings, scope, scenario.removeprefix("rollback:"))
    elif scenario.startswith("uncertain:"):
        uncertain_case(settings, scope, scenario.removeprefix("uncertain:"))
    else:
        cases[scenario](settings, scope)


def concurrent_retry(settings: Any, scope: Any) -> None:
    """@description: Race the exact same operation on independent real sessions without creating a second event.
    @param settings: Application credentials.
    @param scope: Registered original operation.
    @return: None.
    """
    repo = repository(settings, scope)
    barrier = Barrier(2)  # [technical retry race]: Both requests carry the same original identity.
    def submit() -> Any:
        """@description: Align the two technical retries before entering independent database transactions.
        @param: none
        @return: Actual persisted or replayed operation result.
        """
        barrier.wait(timeout=5)
        return repo.accept(request(scope, 1))
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(submit) for _ in range(2)]
        results = [future.result(timeout=12) for future in futures]
    assert sorted(result.create_share_event for result in results) == [False, True]
    rows = observe(settings)
    assert len(rows["jb2_events"]) == len(rows["jb2_receipts"]) == 1
    assert_accounting(rows, 0)
