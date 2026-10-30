"""Run one production SubmissionRepository.exchange request in its own process for the isolated cross-process writer-gate test.

Opt-in only: refuses to start unless the isolation switch and the isolated target are configured. The worker reports readiness with a file,
waits for a go file so two workers start together, creates a signal immediately before the writer-gate statement is invoked, and prints one JSON line
with the business outcome or the MySQL error code. It never prints credentials or the target identity.
"""

import argparse
import json
import random
import time
from pathlib import Path

import pymysql

from bot.database.submission_repository import SubmissionRepository
from tests.isolated_mysql_target import isolated_settings_from_environment, open_verified_connection
from tests.test_identity_service_path import OWNER_ID

GATE_MARKERS = ("jb2_control", "FOR UPDATE")  # [gate statement]: Substrings that identify the single production statement that takes the writer gate.


class GateSignalCursor:
    """Forward one cursor unchanged, but create a signal file immediately before the writer-gate statement is invoked. Test-only; no production change."""

    def __init__(self, cursor, signal_file: Path) -> None:
        """@description: Wrap one real cursor with the signal location for this worker.
        @param cursor: Real cursor on the isolated connection.
        @param signal_file: File created immediately before the gate statement is invoked.
        @return: None.
        """
        self._cursor = cursor
        self._signal_file = signal_file

    def __enter__(self):
        """@description: Support the context-manager form used by the production transaction.
        @return: This wrapper.
        """
        return self

    def __exit__(self, *details):
        """@description: Release the real cursor when the production transaction leaves its block.
        @param details: Exception details, passed through.
        @return: Whatever the real cursor returns.
        """
        return self._cursor.__exit__(*details)

    def execute(self, sql: str, args=None):
        """@description: Create the signal immediately before the gate statement is invoked; it does not show that the statement reached the server or that the server is waiting.
        @param sql: Statement text.
        @param args: Bound values, passed through.
        @return: Whatever the real cursor returns.
        """
        if all(marker in sql for marker in GATE_MARKERS):
            self._signal_file.write_text("gate-sent", encoding="utf-8")  # [invocation signal]: Created immediately before the gate statement is called, not when it arrives or when the server waits.
        return self._cursor.execute(sql, args)

    def __getattr__(self, name: str):
        """@description: Delegate every other cursor operation to the real cursor.
        @param name: Attribute name.
        @return: The real cursor's attribute.
        """
        return getattr(self._cursor, name)


class GateSignalConnection:
    """Forward one connection unchanged, but hand out signalling cursors. Test-only; the production code path is unchanged."""

    def __init__(self, connection, signal_file: Path) -> None:
        """@description: Wrap one real isolated connection with the signal location for this worker.
        @param connection: Real verified connection.
        @param signal_file: File created immediately before the gate statement is invoked.
        @return: None.
        """
        self._connection = connection
        self._signal_file = signal_file

    def cursor(self, *args, **kwargs) -> GateSignalCursor:
        """@description: Return a cursor that signals before the gate statement.
        @param args: Cursor options, passed through.
        @param kwargs: Cursor options, passed through.
        @return: Signalling cursor wrapper.
        """
        return GateSignalCursor(self._connection.cursor(*args, **kwargs), self._signal_file)

    def __getattr__(self, name: str):
        """@description: Delegate every other connection operation (begin, commit, rollback, close) to the real connection.
        @param name: Attribute name.
        @return: The real connection's attribute.
        """
        return getattr(self._connection, name)

GO_TIMEOUT_SECONDS = 30  # [bounded start wait]: A worker that is never released must end instead of hanging the run.
POLL_INTERVAL_SECONDS = 0.01  # [start poll]: Short enough that both workers start within the same lock window.


def parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """@description: Keep the worker's inputs explicit so a parent test controls the request identity and synchronisation files.
    @param argv: Argument list supplied by the parent test, or None for the process arguments.
    @return: Parsed worker arguments.
    """
    parser = argparse.ArgumentParser(description="One isolated production exchange request")
    parser.add_argument("--request-key", required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--go-file", type=Path, required=True)
    parser.add_argument("--gate-file", type=Path, required=True)
    return parser.parse_args(argv)


def wait_for_go(go_file: Path) -> None:
    """@description: Block until the parent releases this worker, so competing requests start together.
    @param go_file: File created by the parent when every worker is ready.
    @return: None; a missing release fails the worker instead of hanging.
    """
    deadline = time.monotonic() + GO_TIMEOUT_SECONDS
    while not go_file.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("go file was not released")
        time.sleep(POLL_INTERVAL_SECONDS)


def run_request(user_id: int, request_key: str, gate_file: Path) -> dict:
    """@description: Execute one production exchange request on this worker's own connection and report the result as data.
    @param user_id: Requesting synthetic user.
    @param request_key: Durable technical request identity.
    @param gate_file: File created immediately before the writer-gate statement is invoked.
    @return: Business status and recommendation identifier, or the MySQL error code when the database refused the request.
    """
    settings, pin = isolated_settings_from_environment()
    repository = SubmissionRepository(lambda: GateSignalConnection(open_verified_connection(settings, pin), gate_file), OWNER_ID)
    try:
        outcome = repository.exchange(user_id, request_key, random.Random(1))
    except pymysql.MySQLError as error:
        return {"status": None, "recommendation_id": None, "error_code": error.args[0]}
    return {"status": outcome.status.value, "recommendation_id": outcome.recommendation_id, "error_code": None}


def main(argv: list[str] | None = None) -> int:
    """@description: Refuse without the isolation switch, signal readiness, wait for release, then perform exactly one exchange request.
    @param argv: Optional argument list for tests.
    @return: Process exit code; configuration refusals exit non-zero before any connection.
    """
    arguments = parse_arguments(argv)
    isolated_settings_from_environment()  # [fail closed]: Refuse before signalling readiness or opening a connection.
    arguments.ready_file.write_text("ready", encoding="utf-8")
    wait_for_go(arguments.go_file)
    print(json.dumps(run_request(arguments.user_id, arguments.request_key, arguments.gate_file), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
