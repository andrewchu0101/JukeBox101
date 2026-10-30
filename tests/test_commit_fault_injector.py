"""Verify offline that the commit fault injector fires exactly at its intended stage, once, and leaves every other statement untouched.

These tests use a recording fake connection, so the injector's stage can be checked before it is trusted with the MySQL scenarios.
Each mode names the stage it must hit: lost acknowledgement after a real commit, failure before the commit is sent, or one failing statement.
"""

import psycopg
from psycopg import errors as pg_errors
import pytest

from tests.commit_fault_injector import FaultInjectingConnection

GATE_STATEMENT = "SELECT * FROM jb2_control WHERE singleton_id = 1 FOR UPDATE"
LEDGER_STATEMENT = "INSERT INTO credit_ledger (discord_user_id) VALUES (1)"


class RecordingConnection:
    """Record every call in order; a commit makes the transaction durable in this fake."""

    def __init__(self) -> None:
        """@description: Start an empty event log and an uncommitted transaction.
        @return: None.
        """
        self.events: list = []
        self.committed = False

    def cursor(self, *args, **kwargs):
        """@description: Return a recording cursor.
        @return: Recording cursor.
        """
        return RecordingCursor(self)

    def begin(self) -> None:
        """@description: Record the start of a transaction.
        @return: None.
        """
        self.events.append("begin")

    def commit(self) -> None:
        """@description: Make the transaction durable and record it.
        @return: None.
        """
        self.events.append("commit_real")
        self.committed = True

    def rollback(self) -> None:
        """@description: Record a rollback.
        @return: None.
        """
        self.events.append("rollback")

    def close(self) -> None:
        """@description: Record the close.
        @return: None.
        """
        self.events.append("close")


class RecordingCursor:
    """Record each statement as it is executed by the real connection."""

    def __init__(self, connection: RecordingConnection) -> None:
        """@description: Bind the cursor to its connection's event log.
        @param connection: Owning recording connection.
        @return: None.
        """
        self.connection = connection

    def __enter__(self):
        """@description: Support the context-manager form.
        @return: This cursor.
        """
        return self

    def __exit__(self, *details) -> None:
        """@description: Nothing to release in the fake.
        @param details: Exception details.
        @return: None.
        """
        return None

    def execute(self, sql: str, args=None) -> None:
        """@description: Record one executed statement.
        @param sql: Statement text.
        @param args: Bound values.
        @return: None.
        """
        self.connection.events.append(f"statement:{sql[:24]}")


def test_lost_ack_fires_only_after_the_real_commit_and_only_once() -> None:
    """@description: The lost-acknowledgement mode raises after the real commit, so the transaction is durable when the caller sees the failure.
    @return: None.
    """
    inner = RecordingConnection()
    connection = FaultInjectingConnection(inner, "lost_ack_after_commit")
    connection.begin()
    with connection.cursor() as cursor:
        cursor.execute(LEDGER_STATEMENT)
    with pytest.raises(psycopg.OperationalError):
        connection.commit()
    assert inner.committed is True
    assert inner.events == ["begin", f"statement:{LEDGER_STATEMENT[:24]}", "commit_real"]
    assert connection.fired == 1
    connection.commit()  # [once only]: A second commit reaches the real connection without another fault.
    assert connection.fired == 1


def test_fail_before_commit_never_reaches_the_real_commit() -> None:
    """@description: The before-commit mode fails without sending COMMIT, so nothing is durable.
    @return: None.
    """
    inner = RecordingConnection()
    connection = FaultInjectingConnection(inner, "fail_before_commit")
    connection.begin()
    with pytest.raises(psycopg.OperationalError):
        connection.commit()
    assert inner.committed is False
    assert "commit_real" not in inner.events
    assert connection.fired == 1


def test_fail_statement_fires_before_the_first_matching_statement_only() -> None:
    """@description: The statement mode fails before the first matching statement is executed, and never on other statements.
    @return: None.
    """
    inner = RecordingConnection()
    connection = FaultInjectingConnection(inner, "fail_statement", statement_marker="credit_ledger")
    connection.begin()
    with connection.cursor() as cursor:
        cursor.execute(GATE_STATEMENT)
        with pytest.raises(psycopg.OperationalError) as error:
            cursor.execute(LEDGER_STATEMENT)
    assert isinstance(error.value, pg_errors.DeadlockDetected)  # [definite rollback]: SQLSTATE 40P01, the victim's transaction is rolled back.
    assert f"statement:{LEDGER_STATEMENT[:24]}" not in inner.events
    assert connection.fired == 1


def test_untouched_modes_and_unrelated_statements_pass_through() -> None:
    """@description: With no matching fault, every call reaches the real connection unchanged.
    @return: None.
    """
    inner = RecordingConnection()
    connection = FaultInjectingConnection(inner, "fail_statement", statement_marker="no-such-table")
    connection.begin()
    with connection.cursor() as cursor:
        cursor.execute(LEDGER_STATEMENT)
    connection.commit()
    assert inner.events == ["begin", f"statement:{LEDGER_STATEMENT[:24]}", "commit_real"]
    assert connection.fired == 0


def test_unknown_mode_is_refused_before_any_wrapping() -> None:
    """@description: A misspelled mode cannot silently disable the intended fault.
    @return: None.
    """
    with pytest.raises(ValueError):
        FaultInjectingConnection(RecordingConnection(), "lost_ack_after_comit")


class RecordingRepository:
    """Record which named operations reach this repository, without any database."""

    def __init__(self, name: str, calls: list) -> None:
        """@description: Name the repository and share the call log.
        @param name: Label recorded with each call.
        @param calls: Shared list of (label, operation) records.
        @return: None.
        """
        self._name = name
        self._calls = calls

    def __getattr__(self, operation: str):
        """@description: Return a function that records the operation under this repository's label.
        @param operation: Operation name requested by the caller.
        @return: Recording function.
        """
        def record(*args, **kwargs):
            self._calls.append((self._name, operation))
        return record


def test_operation_router_sends_only_the_named_operation_to_the_faulted_repository() -> None:
    """@description: Only the exchange operation reaches the faulted repository, so a fault can target the reservation commit and nothing earlier.
    @return: None.
    """
    from tests.commit_fault_injector import OperationRouter
    calls: list = []
    router = OperationRouter(RecordingRepository("faulted", calls), RecordingRepository("clean", calls), "exchange")
    router.recover_interrupted_deliveries("t")
    router.retry_pending_refunds("t")
    router.exchange(1, "key", None)
    router.claim_delivery(7, "token", "t")
    assert calls == [("clean", "recover_interrupted_deliveries"), ("clean", "retry_pending_refunds"),
                     ("faulted", "exchange"), ("clean", "claim_delivery")]
