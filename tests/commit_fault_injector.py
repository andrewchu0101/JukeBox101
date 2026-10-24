"""Inject one reviewed commit-boundary or statement failure into a test-only connection wrapper, without changing production code.

Each mode names the exact stage it targets. The wrapper fires at most once, forwards every other call unchanged, and records the
fault in `fired`, so a scenario can prove that the intended stage was reached instead of inferring it.
Two outcomes are deliberately distinct: a lost acknowledgement after a real commit (durable), and a failure before COMMIT is sent
(the real connection is rolled back by the production transaction, so nothing is durable).
"""

import pymysql

MODES = ("lost_ack_after_commit", "fail_before_commit", "fail_statement")  # [reviewed stages]: Any other name is refused.
LOST_CONNECTION = 2013  # [unknown acknowledgement]: The client cannot tell whether the server committed.
DEADLOCK_VICTIM = 1213  # [definite rollback]: InnoDB rolls back the whole transaction for the victim.


class FaultInjectingConnection:
    """Forward one real connection, raising a single reviewed fault at the stage named by its mode."""

    def __init__(self, connection, mode: str, statement_marker: str = "") -> None:
        """@description: Bind one reviewed fault to one connection; unknown modes are refused before any call is wrapped.
        @param connection: Real or recording connection to forward to.
        @param mode: Stage to fault, one of MODES.
        @param statement_marker: Substring of the statement to fault, used only by the fail_statement mode.
        @return: None.
        """
        if mode not in MODES:
            raise ValueError(f"unknown fault mode: {mode}")
        self._connection = connection
        self._mode = mode
        self._marker = statement_marker
        self.fired = 0  # [fault count]: Proves the intended stage was reached exactly once.

    def cursor(self, *args, **kwargs):
        """@description: Return a cursor whose statements can carry the statement fault.
        @param args: Cursor options, forwarded.
        @param kwargs: Cursor options, forwarded.
        @return: Fault-aware cursor wrapper.
        """
        return _FaultCursor(self._connection.cursor(*args, **kwargs), self)

    def commit(self) -> None:
        """@description: Apply the commit-stage fault around the real commit.
        @return: None; the injected error is raised at the configured stage.
        """
        if self._mode == "fail_before_commit" and not self.fired:
            self.fired += 1
            raise pymysql.err.OperationalError(LOST_CONNECTION, "injected: connection lost before COMMIT was sent")
        self._connection.commit()
        if self._mode == "lost_ack_after_commit" and not self.fired:
            self.fired += 1
            raise pymysql.err.OperationalError(LOST_CONNECTION, "injected: COMMIT completed but the acknowledgement was lost")

    def __getattr__(self, name: str):
        """@description: Forward every other connection operation unchanged.
        @param name: Attribute name.
        @return: The real connection's attribute.
        """
        return getattr(self._connection, name)


class _FaultCursor:
    """Forward one cursor, raising the statement fault before the first matching statement is executed."""

    def __init__(self, cursor, owner: FaultInjectingConnection) -> None:
        """@description: Bind the real cursor to the fault owner.
        @param cursor: Real cursor.
        @param owner: Injecting connection that counts the fault.
        @return: None.
        """
        self._cursor = cursor
        self._owner = owner

    def __enter__(self):
        """@description: Support the context-manager form used by the production transaction.
        @return: This wrapper.
        """
        return self

    def __exit__(self, *details):
        """@description: Release the real cursor.
        @param details: Exception details, forwarded.
        @return: Whatever the real cursor returns.
        """
        return self._cursor.__exit__(*details)

    def execute(self, sql: str, args=None):
        """@description: Raise the statement fault before the matching statement reaches the server.
        @param sql: Statement text.
        @param args: Bound values, forwarded.
        @return: Whatever the real cursor returns when no fault applies.
        """
        owner = self._owner
        if owner._mode == "fail_statement" and not owner.fired and owner._marker in sql:
            owner.fired += 1
            raise pymysql.err.OperationalError(DEADLOCK_VICTIM, "injected: deadlock victim before the statement")
        return self._cursor.execute(sql, args)

    def __getattr__(self, name: str):
        """@description: Forward every other cursor operation unchanged.
        @param name: Attribute name.
        @return: The real cursor's attribute.
        """
        return getattr(self._cursor, name)


class OperationRouter:
    """Send one named repository operation to the faulted repository and every other operation to the clean one. Test-only."""

    def __init__(self, faulted, clean, operation: str) -> None:
        """@description: Bind the two repositories and the single operation that may carry the fault.
        @param faulted: Repository whose first connection carries the fault.
        @param clean: Repository used by every other operation, including the lease sweep and refund sweep.
        @param operation: Name of the one operation routed to the faulted repository.
        @return: None.
        """
        self._faulted = faulted
        self._clean = clean
        self._operation = operation

    def __getattr__(self, name: str):
        """@description: Resolve each operation name to the repository that owns the fault stage.
        @param name: Operation name requested by the caller.
        @return: The attribute of the selected repository.
        """
        target = self._faulted if name == self._operation else self._clean
        return getattr(target, name)
