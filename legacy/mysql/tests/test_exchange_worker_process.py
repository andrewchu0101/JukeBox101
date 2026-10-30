"""Verify offline that an exchange worker process fails closed without the isolation switch, before it signals readiness or opens any connection.

No database is contacted; the worker is started as a child process with the switch removed from its environment.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests.exchange_worker_process import GateSignalConnection
from tests.isolated_mysql_target import ISOLATION_SWITCH

REPO_ROOT = Path(__file__).resolve().parents[1]  # [working directory]: Workers import the repository package from here.
PROCESS_TIMEOUT_SECONDS = 60  # [offline bound]: A refusal should return almost immediately.


def test_worker_refuses_without_the_isolation_switch(tmp_path) -> None:
    """@description: A worker started without the explicit switch fails closed with a code-only error and never creates its readiness file.
    @param tmp_path: Directory for the readiness and release files, which must not be created.
    @return: None.
    """
    environment = {key: value for key, value in os.environ.items() if key != ISOLATION_SWITCH}
    ready_file = tmp_path / "ready"
    process = subprocess.run([sys.executable, "-X", "utf8", "-m", "tests.exchange_worker_process", "--request-key", "offline-refusal",
                              "--user-id", "900000000000000101", "--ready-file", str(ready_file), "--go-file", str(tmp_path / "go"),
                              "--gate-file", str(tmp_path / "gate")],
                             cwd=REPO_ROOT, capture_output=True, text=True, env=environment, timeout=PROCESS_TIMEOUT_SECONDS)
    assert process.returncode != 0
    assert "ISOLATION_NOT_AUTHORIZED" in process.stderr
    assert not ready_file.exists()
    assert process.stdout == ""


class FakeCursor:
    """Record statements and the state of the signal file at the moment each statement is sent."""

    def __init__(self, signal_file: Path, sent: list) -> None:
        """@description: Bind the recorder to the signal file under test.
        @param signal_file: Signal file the wrapper must create before the gate statement.
        @param sent: Shared list of (statement, signal-present) records.
        @return: None.
        """
        self.signal_file = signal_file
        self.sent = sent

    def __enter__(self):
        """@description: Support the context-manager form used by the production transaction.
        @return: This cursor.
        """
        return self

    def __exit__(self, *details) -> None:
        """@description: Nothing to release in the fake.
        @param details: Exception details, unused.
        @return: None.
        """
        return None

    def execute(self, sql: str, args=None) -> None:
        """@description: Record whether the signal existed at the moment this statement was sent.
        @param sql: Statement text.
        @param args: Bound values, unused.
        @return: None.
        """
        self.sent.append((sql, self.signal_file.exists()))


class FakeConnection:
    """Provide the connection methods the production transaction calls, without any database."""

    def __init__(self, signal_file: Path) -> None:
        """@description: Create the fake with a shared statement recorder.
        @param signal_file: Signal file the wrapper must create.
        @return: None.
        """
        self.sent: list = []
        self.signal_file = signal_file
        self.begun = False

    def cursor(self, *args, **kwargs) -> FakeCursor:
        """@description: Return a recording cursor.
        @param args: Unused cursor options.
        @param kwargs: Unused cursor options.
        @return: Recording cursor.
        """
        return FakeCursor(self.signal_file, self.sent)

    def begin(self) -> None:
        """@description: Record that the transaction was started through the wrapper.
        @return: None.
        """
        self.begun = True


GATE_SQL = "SELECT * FROM jb2_control WHERE singleton_id = 1 FOR UPDATE"


def test_gate_signal_exists_when_the_gate_statement_is_sent_and_not_before_ordinary_sql(tmp_path) -> None:
    """@description: The wrapper writes its signal only for the writer-gate statement, and the signal already exists while that statement is sent.
    @param tmp_path: Directory holding the signal file under test.
    @return: None.
    """
    signal_file = tmp_path / "gate-sent"
    connection = GateSignalConnection(FakeConnection(signal_file), signal_file)
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        assert not signal_file.exists(), "ordinary SQL must not produce the gate signal"
        cursor.execute(GATE_SQL)
    assert signal_file.exists()
    assert connection._connection.sent == [("SELECT 1", False), (GATE_SQL, True)]


def test_gate_signal_wrapper_delegates_transaction_methods(tmp_path) -> None:
    """@description: Transaction methods the production code calls on the connection still reach the real connection.
    @param tmp_path: Directory holding the signal file under test.
    @return: None.
    """
    inner = FakeConnection(tmp_path / "gate-sent")
    connection = GateSignalConnection(inner, tmp_path / "gate-sent")
    connection.begin()
    assert inner.begun is True
