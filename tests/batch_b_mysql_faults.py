"""Inject client-side failures around real PyMySQL operations without replacing MySQL state.

Every executed statement delegates to a real cursor; optional faults exercise rollback and lost commit acknowledgement recovery.
"""

from typing import Any


class InjectedClientFault(RuntimeError):
    """Represent an intentional client failure, not a MySQL server result."""


class FaultConnection:
    """Delegate all connection methods while exposing precise before/after commit fault points."""

    def __init__(self, connection: Any, fault: dict) -> None:
        """@description: Keep real transactional state in MySQL rather than an in-memory database substitute.
        @param self: Fault boundary around a real session.
        @param connection: Actual PyMySQL connection.
        @param fault: One-shot client fault state shared only by the owning test.
        @return: None.
        """
        self.connection = connection  # [real owner]: Every noninjected call reaches MySQL.
        self.fault = fault  # [one-shot injection]: Retries use the same idempotency key without reinjection.

    def __getattr__(self, name: str) -> Any:
        """@description: Preserve the real connection protocol without reimplementing transactions.
        @param self: Real-session adapter.
        @param name: Delegated method or property.
        @return: Original connection member.
        """
        return getattr(self.connection, name)

    def cursor(self) -> Any:
        """@description: Place SQL faults after actual execution so rollback covers genuine server writes.
        @param self: Adapter owning the real connection.
        @return: Context-managed real-cursor adapter.
        """
        return FaultCursor(self.connection.cursor(), self.fault)

    def commit(self) -> None:
        """@description: Model missing acknowledgement on either side of a real commit boundary.
        @param self: Adapter with a one-shot commit fault.
        @return: None.
        """
        position = self.fault.pop("commit", None)  # [acknowledgement fault]: Deliberately not a simulated database engine.
        if position == "before":
            raise InjectedClientFault("CLIENT_BEFORE_COMMIT")
        self.connection.commit()
        if position == "after":
            raise InjectedClientFault("CLIENT_AFTER_REAL_COMMIT")


class FaultCursor:
    """Delegate SQL and fetches to a real cursor, injecting only after the selected server operation."""

    def __init__(self, cursor: Any, fault: dict) -> None:
        """@description: Retain real cursor ownership and shared one-shot fault selection.
        @param self: Cursor adapter.
        @param cursor: Actual PyMySQL cursor.
        @param fault: Pending client fault selector.
        @return: None.
        """
        self.actual = cursor  # [server cursor]: No rows are fabricated.
        self.fault = fault  # [injection scope]: Only one matching statement fails.

    def __getattr__(self, name: str) -> Any:
        """@description: Preserve fetch, rowcount and lastrowid from the actual server cursor.
        @param self: Adapter.
        @param name: Requested real cursor member.
        @return: Delegated member.
        """
        return getattr(self.actual, name)

    def __enter__(self) -> Any:
        """@description: Keep the existing repository cursor-context contract.
        @param self: Adapter whose real cursor is owned by this context.
        @return: This adapter.
        """
        self.actual.__enter__()
        return self

    def __exit__(self, *arguments: Any) -> Any:
        """@description: Always close the real cursor through its original context boundary.
        @param self: Cursor adapter.
        @param arguments: Active exception details.
        @return: Original cursor context result.
        """
        return self.actual.__exit__(*arguments)

    def execute(self, statement: str, parameters: tuple = ()) -> Any:
        """@description: Fail only after MySQL really executed the selected statement inside its transaction.
        @param self: Real cursor adapter.
        @param statement: Repository SQL, unchanged.
        @param parameters: Original bound values.
        @return: Actual execute result when no client fault fires.
        """
        result = self.actual.execute(statement, parameters)  # [real mutation]: A later client exception requires actual rollback.
        prefix = self.fault.get("sql")
        if prefix and statement.startswith(prefix):
            self.fault.pop("sql")
            raise InjectedClientFault("CLIENT_AFTER_SQL")
        return result
