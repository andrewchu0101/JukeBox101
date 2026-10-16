"""Inject client-side failures around real PyMySQL operations without replacing MySQL state.

Every executed statement delegates to a real cursor; optional faults exercise rollback and lost commit acknowledgement recovery.
"""

from typing import Any
from pathlib import Path
from uuid import uuid4


class InjectedClientFault(RuntimeError):
    """Represent an intentional client failure, not a MySQL server result."""


class FaultConnection:
    """Delegate all connection methods while exposing precise before/after commit fault points."""

    def __init__(self, connection: Any, fault: dict, audit_directory: str | None = None) -> None:
        """@description: Keep real transactional state in MySQL rather than an in-memory database substitute.
        @param self: Fault boundary around a real session.
        @param connection: Actual PyMySQL connection.
        @param fault: One-shot client fault state shared only by the owning test.
        @param audit_directory: Optional fresh supervised path; never an old incident journal.
        @return: None.
        """
        self.connection = connection  # [real owner]: Every noninjected call reaches MySQL.
        self.fault = fault  # [one-shot injection]: Retries use the same idempotency key without reinjection.
        self.audit_path = Path(audit_directory) / ("transaction-" + uuid4().hex + ".json") if audit_directory else None
        self.audit = dict(blocked=False, closed=False, progress=[])  # [actual acknowledgements]: Known injected faults remain distinct.
        self.persist_audit()

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
        position = self.fault.pop("commit", None)  # [acknowledgement fault]: An injected error is not a server error.
        if position == "before":
            self.record_audit("COMMIT", "INJECTED_BEFORE_SEND")
            raise InjectedClientFault("CLIENT_BEFORE_COMMIT")
        self.audit_call("COMMIT", self.connection.commit)
        if position == "after":
            self.record_audit("INJECTION", "AFTER_COMMIT_ACKNOWLEDGED")
            raise InjectedClientFault("CLIENT_AFTER_REAL_COMMIT")


    def rollback(self) -> None:
        """@description: Distinguish rollback acknowledgement from a previous uncertain commit.
        @param self: Adapter retaining immutable commit uncertainty.
        @return: None; rollback cannot clear a poisoned audit.
        """
        self.audit_call("ROLLBACK", self.connection.rollback)

    def close(self) -> None:
        """@description: Retain whether the application connection actually released its worker resources.
        @param self: Worker-owned audited connection.
        @return: None; close failures poison supervised cleanup.
        """
        self.audit_call("CLOSE", self.connection.close)
        self.audit["closed"] = True
        self.persist_audit()

    def audit_call(self, phase: str, operation: Any) -> None:
        """@description: Write intent before I/O and never hide a lost real acknowledgement.
        @param self: Existing connection adapter, also used without supervision.
        @param phase: Fixed COMMIT, ROLLBACK or CLOSE phase.
        @param operation: Actual owned connection operation.
        @return: None; real failures remain UNKNOWN even after later successful calls.
        """
        self.record_audit(phase, "PENDING")
        try:
            operation()
        except BaseException:
            self.audit["blocked"] = True
            self.record_audit(phase, "UNKNOWN")
            raise
        self.audit["progress"][-1]["outcome"] = "ACKNOWLEDGED"
        self.persist_audit()

    def record_audit(self, phase: str, outcome: str) -> None:
        """@description: Append safe transaction facts without SQL values or credentials.
        @param self: Adapter owning one uniquely named audit file.
        @param phase: Static transaction or injected-test phase.
        @param outcome: Explicit intent, acknowledgement or uncertainty.
        @return: None.
        """
        if outcome == "UNKNOWN" and self.audit["progress"]:
            self.audit["progress"][-1]["outcome"] = outcome
        else:
            self.audit["progress"].append(dict(phase=phase, outcome=outcome))
        self.persist_audit()

    def persist_audit(self) -> None:
        """@description: Reuse the existing atomic manifest writer for supervised worker evidence.
        @param self: Adapter with optional session-owned audit destination.
        @return: None; journal failure stops execution rather than inventing successful I/O.
        """
        if self.audit_path is not None:
            from tests.batch_b_harness import persist_manifest
            persist_manifest(self.audit_path, self.audit)


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
