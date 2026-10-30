"""Verify the new read-only precheck entry offline with stand-ins: only fixed SELECT and SHOW statements are sent, hidden input refuses without
a terminal, the Discord check is REST-only and always closes its client, and shutdown sends only SHUTDOWN.

Connections, clients and the port probe are stand-ins; no MySQL, Discord, network, environment file or process is involved.
"""

import asyncio
import re
from types import SimpleNamespace

import pytest

from bot.guild_acceptance import AcceptanceError
from tests import readonly_precheck as precheck

OWNER = 456708412697215018
GUILD = 1559749813011087392
APPLICATION = 1559751168723521576
APPROVED = {"balance": (), "daily": ("mode",), "exchange": (), "history": (), "share": ("url",)}
WRITE_WORDS = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|GRANT|REVOKE|REPLACE|TRUNCATE|SHUTDOWN)\b", re.IGNORECASE)


class RecordingCursor:
    """@description: Cursor stand-in that records statements and answers from fixed rows keyed by a statement prefix.
    @param answers: Mapping from statement prefix to rows.
    @return: Cursor stand-in usable as a context manager.
    """

    def __init__(self, answers: dict, statements: list) -> None:
        """@description: Store the answers and the shared statement log.
        @param self: Cursor stand-in.
        @param answers: Rows keyed by statement prefix.
        @param statements: Shared list that receives each executed statement and its parameters.
        @return: None.
        """
        self.answers, self.statements, self.rows = answers, statements, []

    def __enter__(self):
        """@description: Return this cursor for a with-block.
        @param self: Cursor stand-in.
        @return: This cursor.
        """
        return self

    def __exit__(self, *exc) -> None:
        """@description: Leave the with-block without suppressing errors.
        @param self: Cursor stand-in.
        @param exc: Exception details, ignored.
        @return: None.
        """
        return None

    def execute(self, sql: str, params=()) -> None:
        """@description: Record one statement and select the rows the matching prefix returns.
        @param self: Cursor stand-in.
        @param sql: Statement text.
        @param params: Bound parameters.
        @return: None.
        """
        self.statements.append((sql, params))
        self.rows = next((rows for prefix, rows in self.answers.items() if sql.startswith(prefix)), [])

    def fetchone(self):
        """@description: Return the first recorded row.
        @param self: Cursor stand-in.
        @return: First row or None.
        """
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list:
        """@description: Return all recorded rows.
        @param self: Cursor stand-in.
        @return: Recorded rows.
        """
        return list(self.rows)


class RecordingConnection:
    """@description: Connection stand-in that hands out cursors sharing one statement log.
    @param answers: Rows keyed by statement prefix.
    @return: Connection stand-in.
    """

    def __init__(self, answers: dict) -> None:
        """@description: Create the shared statement log.
        @param self: Connection stand-in.
        @param answers: Rows keyed by statement prefix.
        @return: None.
        """
        self.answers, self.statements = answers, []

    def cursor(self) -> RecordingCursor:
        """@description: Return a cursor over the shared statement log.
        @param self: Connection stand-in.
        @return: Recording cursor.
        """
        return RecordingCursor(self.answers, self.statements)


SNAPSHOT_ANSWERS = {
    "SHOW GRANTS": [{"Grants for jukebox101_test_exchange@127.0.0.1": "GRANT SELECT ON `jukebox101_test`.* TO ..."}],
    "SELECT singleton_id": [{"singleton_id": 1, "settlement_enabled": 0}],
    "SELECT e.discord_user_id": [{"submitter": 123, "settled_versions": 1, "deliverable": 1}],
    "SELECT credit_balance": [{"credit_balance": 2}],
    "SELECT status": [],
    "SELECT table_name": [],
    "SELECT discord_user_id, credit_balance": [],
    "SELECT operation_id": [],
    "SELECT decision_id": [],
}


def test_every_statement_the_snapshot_sends_is_one_of_the_fixed_read_only_constants() -> None:
    """@description: The snapshot sends only its module constants, each a SELECT, SHOW GRANTS or the read-only session guard, with no write keyword.
    @return: None.
    """
    connection = RecordingConnection(SNAPSHOT_ANSWERS)
    precheck.run_read_only_snapshot(connection, OWNER, 9000000000000000001, ("live-c-1",), ("dur-c-1",))
    fixed = {precheck.READ_ONLY_SESSION, precheck.GRANTS_QUERY, precheck.CONTROL_QUERY, precheck.POOL_QUERY, precheck.BALANCE_QUERY,
             precheck.OPEN_QUERY, precheck.COMPENSATION_QUERY, precheck.USER_ROW_QUERY, precheck.RECEIPT_QUERY,
             precheck.EVENT_QUERY, precheck.DECISION_QUERY, precheck.EVENTS_TOTAL_QUERY}
    assert [sql for sql, _ in connection.statements][0] == precheck.READ_ONLY_SESSION
    for sql, _ in connection.statements:
        assert sql in fixed and sql.startswith(precheck.ALLOWED_PREFIXES) and not WRITE_WORDS.search(sql)


def test_the_snapshot_returns_the_settled_pool_balance_and_control_without_any_secret() -> None:
    """@description: The snapshot reports the grants, control row, settled pool, Owner balance and table presence, bound to the Owner parameter.
    @return: None.
    """
    connection = RecordingConnection(SNAPSHOT_ANSWERS)
    snapshot = precheck.run_read_only_snapshot(connection, OWNER, 9000000000000000001, ("live-c-1",), ("dur-c-1",))
    assert snapshot["control"]["settlement_enabled"] == 0
    assert snapshot["settled_pool"] == [{"submitter": 123, "settled_versions": 1, "deliverable": 1}]
    assert snapshot["owner_balance"] == {"credit_balance": 2} and snapshot["owner_deliveries"] == []
    assert snapshot["compensation_tables"] == []
    assert (precheck.BALANCE_QUERY, (OWNER,)) in connection.statements
    assert snapshot["occupied_identifiers"] == {"operations": [], "decisions": []}


def test_hidden_input_refuses_when_no_terminal_is_attached() -> None:
    """@description: Without a terminal the entry refuses, so a fallback that would echo a secret is never used.
    @return: None.
    """
    with pytest.raises(SystemExit, match="HIDDEN_INPUT_REQUIRES_A_TERMINAL"):
        precheck.require_interactive(False)
    precheck.require_interactive(True)


class FakeDiscordClient:
    """@description: REST-only client stand-in that records every call and fails loudly on gateway or synchronization.
    @param remote: Remote commands the fetch returns.
    @param application_id: Application identifier the stand-in reports.
    @return: Client stand-in.
    """

    def __init__(self, remote: list, application_id: int = APPLICATION) -> None:
        """@description: Build the approved local tree and the call log.
        @param self: Client stand-in.
        @param remote: Remote commands the fetch returns.
        @param application_id: Application identifier the stand-in reports.
        @return: None.
        """
        self.calls = []
        self.application_id = application_id
        local = [SimpleNamespace(name=name, parameters=[SimpleNamespace(name=p) for p in options]) for name, options in APPROVED.items()]
        self.tree = SimpleNamespace(get_commands=lambda: local, fetch_commands=self._fetch(remote), sync=self._forbidden("sync"))
        self.connect = self._forbidden("connect")

    def _fetch(self, remote: list):
        """@description: Return an async read-only fetch that records one call.
        @param self: Client stand-in.
        @param remote: Remote commands to return.
        @return: Async fetch callable.
        """
        async def fetch_commands(*, guild):
            """@description: Record the read-only fetch and return the stored remote commands.
            @param guild: Guild the fetch names.
            @return: Stored remote commands.
            """
            self.calls.append("fetch_commands")
            return remote
        return fetch_commands

    def _forbidden(self, name: str):
        """@description: Return a callable that fails the test if a write or gateway operation is attempted.
        @param self: Client stand-in.
        @param name: Operation name for the failure message.
        @return: Failing callable.
        """
        def fail(*args, **kwargs):
            """@description: Fail because a write or gateway operation must never run here.
            @param args: Ignored.
            @param kwargs: Ignored.
            @return: Never returns.
            """
            raise AssertionError(f"{name} must never run in the read-only precheck")
        return fail

    async def login(self, token: str) -> None:
        """@description: Record one REST login with a token that must be present.
        @param self: Client stand-in.
        @param token: Token value, never stored in the log.
        @return: None.
        """
        assert token
        self.calls.append("login")

    async def application_info(self):
        """@description: Return the application identity the stand-in holds.
        @param self: Client stand-in.
        @return: Identity with an id.
        """
        self.calls.append("application_info")
        return SimpleNamespace(id=self.application_id)

    async def fetch_guild(self, guild_id: int):
        """@description: Return the guild identity for the requested id.
        @param self: Client stand-in.
        @param guild_id: Requested guild identifier.
        @return: Identity with the id.
        """
        self.calls.append("fetch_guild")
        return SimpleNamespace(id=guild_id)

    async def close(self) -> None:
        """@description: Record that the client was closed.
        @param self: Client stand-in.
        @return: None.
        """
        self.calls.append("close")


def settings(application: int = APPLICATION) -> SimpleNamespace:
    """@description: Build the expected application, guild and token stand-in.
    @param application: Expected application identifier.
    @return: Settings stand-in.
    """
    return SimpleNamespace(guild_id=GUILD, application_id=application, discord=SimpleNamespace(token="synthetic-token"))


def same_commands() -> list:
    """@description: Remote commands identical to the approved registry.
    @return: Command stand-ins.
    """
    return [SimpleNamespace(name=name, options=[SimpleNamespace(name=p) for p in options]) for name, options in APPROVED.items()]


def test_the_discord_check_is_rest_only_reports_the_difference_and_closes_the_client() -> None:
    """@description: The check logs in over REST, compares the approved names and options, never connects or synchronizes, and closes the client.
    @return: None.
    """
    client = FakeDiscordClient(same_commands())
    result = asyncio.run(precheck.discord_precheck(client, settings()))
    assert result["report"] == {"missing_remotely": [], "not_approved_remotely": [], "option_differences": {}}
    assert client.calls == ["login", "application_info", "fetch_guild", "fetch_commands", "close"]


def test_a_token_for_another_application_is_refused_and_the_client_still_closes() -> None:
    """@description: An application mismatch stops before any command fetch, and the client is closed on the way out.
    @return: None.
    """
    client = FakeDiscordClient(same_commands(), application_id=APPLICATION + 1)
    with pytest.raises(AcceptanceError, match="application"):
        asyncio.run(precheck.discord_precheck(client, settings()))
    assert "fetch_commands" not in client.calls and client.calls[-1] == "close"


def test_shutdown_sends_only_the_shutdown_statement_and_reports_the_port_closed() -> None:
    """@description: Shutdown sends one SHUTDOWN and reports whether the port stopped listening, without any other statement.
    @return: None.
    """
    root = RecordingConnection({})
    states = iter([True, False])
    result = precheck.shutdown_isolated_instance(root, lambda: next(states), wait_seconds=5)
    assert root.statements == [("SHUTDOWN", ())] and result == {"shutdown_sent": True, "port_closed": True}


def test_the_compensation_presence_query_aliases_information_schema_columns_to_lower_case() -> None:
    """@description: information_schema returns upper-case column names, so the query aliases its column to the name the reader uses.
    @return: None.
    """
    assert "TABLE_NAME AS table_name" in precheck.COMPENSATION_QUERY


def test_an_already_occupied_planned_identifier_is_reported_not_reused() -> None:
    """@description: A planned operation or decision identifier that already exists is listed as occupied, so the run stops instead of reusing it.
    @return: None.
    """
    answers = {**SNAPSHOT_ANSWERS, "SELECT operation_id": [{"operation_id": "live-c-1"}], "SELECT decision_id": [{"decision_id": "dur-c-1"}]}
    snapshot = precheck.run_read_only_snapshot(RecordingConnection(answers), OWNER, 9000000000000000001, ("live-c-1",), ("dur-c-1",))
    assert snapshot["occupied_identifiers"] == {"operations": ["live-c-1"], "decisions": ["dur-c-1"]}


def test_a_schema_with_any_earlier_accepted_operation_is_visible_to_the_priority_check() -> None:
    """@description: The snapshot reports the total accepted operations, so a non-empty schema is visible before any new intake.
    @return: None.
    """
    answers = {**SNAPSHOT_ANSWERS, "SELECT COUNT(*) AS total FROM jb2_events": [{"total": 3}]}
    snapshot = precheck.run_read_only_snapshot(RecordingConnection(answers), OWNER, 9000000000000000001, (), ())
    assert snapshot["events_total"] == 3
