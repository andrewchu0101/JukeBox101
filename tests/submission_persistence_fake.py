"""Simulate the new repository's SQL protocol with bounded Python transaction locks.

This fake checks application write coordination and failure recovery, not MySQL isolation, collation or foreign-key semantics.
"""

from copy import deepcopy
from datetime import datetime, timedelta
import re
from threading import RLock

TABLES = ("users", "songs", "credit_ledger", "jb2_versions", "jb2_videos", "jb2_legacy", "jb2_events",
          "jb2_receipts", "jb2_claims", "jb2_settlements", "jb2_review_history", "jb2_evidence", "jb2_decisions",
          "jb2_exclusions", "jb2_barriers", "jb2_exceptions", "jb2_reminders", "jb2_reminder_batches")
PRIMARY_KEYS = {"users": "discord_user_id", "songs": "platform_track_id", "credit_ledger": "ledger_id",
                "jb2_versions": "version_id", "jb2_videos": "video_id", "jb2_legacy": "song_id",
                "jb2_events": "operation_id", "jb2_receipts": "operation_id", "jb2_claims": "version_id",
                "jb2_settlements": "operation_id", "jb2_evidence": "evidence_id", "jb2_decisions": "decision_id",
                "jb2_exclusions": "evidence_reference", "jb2_barriers": "version_id", "jb2_exceptions": "exception_id",
                "jb2_reminders": "video_id", "jb2_reminder_batches": "summary_date"}


class FakeDatabase:
    """Keep shared committed dictionaries and inject faults without opening any socket."""

    def __init__(self) -> None:
        """@description: Initialize only synthetic migration-ready state.
        @param self: Offline database simulator.
        @return: None.
        """
        self.state = {name: {} for name in TABLES}  # [committed rows]: Never leave Python memory.
        self.state["control"] = {"schema_version": "2b2-b1", "accepted_sequence": 0, "revision": 0,
                                 "snapshot_ready": True, "legacy_writers_quiesced": True, "settlement_enabled": True}
        self.gate = RLock()  # [simulated row lock]: Bounded acquisition is not proof of InnoDB behavior.
        self.connections = []  # [resource audit]: Every connection must close on success and failure.
        self.commit_fault = None  # [failure injection]: 'before' or 'after' applying committed state.
        self.sql_fault = None  # [statement injection]: Prefix of the next statement to reject.
        self.statements = []  # [SQL audit]: Bound parameters remain inspectable synthetic values.
        self.now = datetime(2026, 10, 15, 0, 0)  # [fake UTC clock]: No wall clock dependence.

    def connect(self) -> "FakeConnection":
        """@description: Create one isolated simulated worker connection.
        @param self: Shared fake state.
        @return: Unconnected-in-reality transaction owner.
        """
        connection = FakeConnection(self)  # [owned fake connection]: No real driver call occurs.
        self.connections.append(connection)
        return connection


class FakeConnection:
    """Apply copy-on-transaction state only at the simulated commit point."""

    def __init__(self, database: FakeDatabase) -> None:
        """@description: Defer state acquisition until the production SQL locks its gate.
        @param self: Fake transaction owner.
        @param database: Shared committed simulator.
        @return: None.
        """
        self.database = database  # [shared committed source]: Isolated from uncommitted working state.
        self.working = None  # [transaction projection]: Populated only after the lock statement.
        self.locked = False  # [lock ownership]: Released exactly once even after ambiguous commit.
        self.closed = False  # [resource state]: Asserted by cleanup tests.

    def cursor(self) -> "FakeCursor":
        """@description: Reuse the production cursor context-manager boundary.
        @param self: Fake connection.
        @return: In-memory SQL protocol cursor.
        """
        return FakeCursor(self)

    def begin(self) -> None:
        """@description: Preserve explicit begin sequencing without acquiring the fake gate early.
        @param self: Fake connection.
        @return: None.
        """

    def acquire(self) -> None:
        """@description: Simulate a bounded cross-worker serialization gate before reading committed state.
        @param self: Fake connection.
        @return: None.
        """
        if not self.database.gate.acquire(timeout=3):
            raise TimeoutError("Fake serialization deadline exceeded")
        self.locked = True
        self.working = deepcopy(self.database.state)

    def commit(self) -> None:
        """@description: Inject both definitely-uncommitted and committed-but-unacknowledged failures.
        @param self: Fake transaction owner.
        @return: None.
        """
        fault = self.database.commit_fault  # [one-shot commit fault]: Retry uses a fresh healthy connection.
        self.database.commit_fault = None
        if fault == "before":
            raise OSError("synthetic lost commit")
        self.database.state = self.working
        self.release()
        if fault == "after":
            raise OSError("synthetic lost acknowledgement")

    def release(self) -> None:
        """@description: Prevent lock leaks on commit, rollback and connection close.
        @param self: Fake lock owner.
        @return: None.
        """
        if self.locked:
            self.locked = False
            self.database.gate.release()

    def rollback(self) -> None:
        """@description: Discard uncommitted state without undoing a completed ambiguous commit.
        @param self: Fake transaction owner.
        @return: None.
        """
        self.working = None
        self.release()

    def close(self) -> None:
        """@description: Make resource cleanup observable even after failed writes.
        @param self: Fake transaction owner.
        @return: None.
        """
        self.closed = True
        self.release()


class FakeCursor:
    """Interpret only the SQL protocol exercised by the new repository."""

    def __init__(self, connection: FakeConnection) -> None:
        """@description: Keep result rows and generated identifiers local to their cursor.
        @param self: Fake cursor.
        @param connection: Owning fake worker connection.
        @return: None.
        """
        self.connection = connection  # [transaction owner]: All mutation uses its isolated working copy.
        self.rows = []  # [query result]: Explicit dictionaries matching DictCursor output.
        self.rowcount = 0  # [affected rows]: Checked by the production balance update.
        self.lastrowid = 0  # [generated identifier]: Never used as acceptance order.

    def __enter__(self) -> "FakeCursor":
        """@description: Match the production connection's cursor lifetime protocol.
        @param self: Fake cursor.
        @return: Same cursor.
        """
        return self

    def __exit__(self, *arguments: object) -> None:
        """@description: Leave transaction cleanup to the connection boundary.
        @param self: Fake cursor.
        @param arguments: Context-manager exception details.
        @return: None.
        """

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        """@description: Route real repository statements into explicit in-memory protocol handlers.
        @param self: Fake cursor.
        @param statement: Production static parameterized SQL.
        @param parameters: Synthetic bound values.
        @return: None.
        """
        self.connection.database.statements.append((statement, parameters))
        if self.connection.database.sql_fault and statement.startswith(self.connection.database.sql_fault):
            self.connection.database.sql_fault = None
            raise ValueError("synthetic statement failure")
        self.rowcount = 1
        self.rows = []
        if statement.startswith("SET "):
            return
        if statement.startswith("SELECT * FROM jb2_control"):
            self.connection.acquire()
            self.rows = [dict(self.connection.working["control"])]
            return
        if statement.startswith("SELECT"):
            self.rows = read_rows(self.connection, statement, parameters)
            return
        if statement.startswith("INSERT"):
            self.lastrowid = insert_row(self.connection, statement, parameters)
            return
        if statement.startswith("UPDATE"):
            update_rows(self.connection, statement, parameters)
            return
        raise AssertionError("Unimplemented fake SQL: " + statement)

    def fetchone(self) -> dict | None:
        """@description: Return the current singleton query result.
        @param self: Fake cursor.
        @return: First row or None.
        """
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict]:
        """@description: Return all protocol rows without pagination.
        @param self: Fake cursor.
        @return: Current complete fake query result.
        """
        return self.rows


def read_rows(connection: FakeConnection, sql: str, parameters: tuple) -> list[dict]:
    """@description: Route joined projections separately from simple lookup statements.
    @param connection: Fake transaction owner.
    @param sql: Production SELECT statement.
    @param parameters: Bound synthetic values.
    @return: Explicit rows for the requested protocol.
    """
    if "FROM jb2_events e JOIN jb2_videos" in sql:
        return _event_rows(connection, sql, parameters)
    if "FROM jb2_barriers UNION" in sql:
        return list(connection.working["jb2_barriers"].values()) + [
            {"version_id": connection.working["jb2_versions"][row["version_id"]]["canonical_id"]}
            for row in connection.working["jb2_claims"].values()]
    if "FROM credit_ledger l JOIN songs" in sql:
        return _award_audit_rows(connection)
    if "FROM songs s LEFT JOIN jb2_legacy" in sql:
        return _legacy_rows(connection)
    if "FROM jb2_claims c JOIN jb2_versions" in sql:
        return [row for row in connection.working["jb2_claims"].values()
                if connection.working["jb2_versions"][row["version_id"]]["canonical_id"] in parameters]
    if "UTC_TIMESTAMP" in sql:
        return [{"now_utc": connection.database.now, "summary_date": connection.database.now.date()}]
    if "COALESCE(SUM" in sql:
        return [{"total": sum(row["credit_delta"] for row in connection.working["credit_ledger"].values()
                              if row["discord_user_id"] == parameters[0])}]
    return _simple_rows(connection, sql, parameters)


def _award_audit_rows(connection: FakeConnection) -> list[dict]:
    """@description: Expose original old/new ledger references for audited duplicate discovery.
    @param connection: Fake transaction owner.
    @return: Original ledger entries with explicit legacy and claim evidence.
    """
    rows = []  # [historical audit rows]: No balances or original ledger entries are rewritten.
    state = connection.working
    for ledger in state["credit_ledger"].values():
        if ledger["reason"] != "submission_award":
            continue
        song = next(row for row in state["songs"].values() if row["song_id"] == ledger["awarded_song_id"])
        legacy = state["jb2_legacy"].get(song["song_id"])
        video = state["jb2_videos"].get(song["platform_track_id"])
        claim = next((row for row in state["jb2_claims"].values() if row["ledger_id"] == ledger["ledger_id"]), None)
        rows.append({"ledger_id": ledger["ledger_id"], "discord_user_id": ledger["discord_user_id"],
                     "awarded_song_id": ledger["awarded_song_id"], "video_id": song["platform_track_id"],
                     "legacy_song_id": legacy["song_id"] if legacy else None,
                     "legacy_identity_json": legacy["identity_json"] if legacy else None,
                     "video_identity_json": video["identity_json"] if video else None,
                     "claimed_version": claim["version_id"] if claim else None})
    return rows


def _simple_rows(connection: FakeConnection, sql: str, parameters: tuple) -> list[dict]:
    """@description: Interpret single-table lookups against fake committed-order state.
    @param connection: Fake transaction owner.
    @param sql: Static single-table SELECT.
    @param parameters: Bound values.
    @return: Matching row dictionaries.
    """
    table = re.search(r"FROM (\w+)", sql).group(1)  # [protocol table]: Not a general SQL parser.
    rows = list(connection.working[table].values())  # [candidate rows]: Fake data only.
    if "WHERE" not in sql:
        return rows
    if table == "songs":
        return [row for row in rows if row["platform_track_id"] == parameters[-1]]
    if table == "jb2_barriers":
        return [row for row in rows if row["version_id"] in parameters]
    if table == "jb2_review_history":
        return [row for row in rows if (row["video_id"], row["revision"]) == parameters]
    key = PRIMARY_KEYS[table]  # [lookup key]: Production queries here use the declared primary key.
    return [row for row in rows if row[key] == parameters[0]]


def _event_rows(connection: FakeConnection, sql: str, parameters: tuple) -> list[dict]:
    """@description: Expose current video state and claim facts for all accepted events.
    @param connection: Fake transaction owner.
    @param sql: Joined event or pending-reminder projection.
    @param parameters: Optional operation or reminder filters.
    @return: Joined rows in acceptance order.
    """
    rows = []  # [global event projection]: Missing joins remain detectable by independent manifest IDs.
    for event in connection.working["jb2_events"].values():
        video = connection.working["jb2_videos"].get(event["video_id"])
        if video is not None:
            row = dict(event, identity_json=video["identity_json"], review_json=video["review_json"],
                       review_revision=video["review_revision"], review_status=video["review_status"])
            row["claim_operation"] = next((claim["operation_id"] for claim in connection.working["jb2_claims"].values()
                                           if claim["operation_id"] == event["operation_id"]), None)
            row["claimed_version"] = next((claim["version_id"] for claim in connection.working["jb2_claims"].values()
                                           if claim["operation_id"] == event["operation_id"]), None)
            rows.append(row)
    if "WHERE e.operation_id=" in sql:
        rows = [row for row in rows if row["operation_id"] == parameters[0]]
    if "JOIN jb2_reminders" in sql:
        rows = [row for row in rows if _pending_due(connection, row, parameters)]
    return sorted(rows, key=lambda row: row["accepted_sequence"])


def _pending_due(connection: FakeConnection, row: dict, parameters: tuple) -> bool:
    """@description: Match the repository's UTC pending-reminder predicate without delivering anything.
    @param connection: Fake transaction owner.
    @param row: Candidate event row.
    @param parameters: Database now and allowed pending statuses.
    @return: Whether the video is due and still pending.
    """
    video = connection.working["jb2_videos"][row["video_id"]]  # [current review]: Reminder never changes review or priority.
    reminder = connection.working["jb2_reminders"][row["video_id"]]
    return reminder["next_due_at"] <= parameters[0] and (video["review_status"] in parameters[1:4] or row["reward_status"] in parameters[4:])


def _legacy_rows(connection: FakeConnection) -> list[dict]:
    """@description: Keep unregistered old songs visible as unresolved catalog history.
    @param connection: Fake transaction owner.
    @return: Complete legacy catalog projection.
    """
    rows = []  # [legacy projection]: No intake sequence is generated for these songs.
    mapped = {row["song_id"] for row in connection.working["jb2_videos"].values()}
    for song in connection.working["songs"].values():
        legacy = connection.working["jb2_legacy"].get(song["song_id"])
        if legacy is not None or song["song_id"] not in mapped:
            rows.append({"video_id": song["platform_track_id"], "identity_json": legacy["identity_json"] if legacy else None})
    return rows


def insert_row(connection: FakeConnection, sql: str, parameters: tuple) -> int:
    """@description: Apply parameterized insert intent with explicit simulated uniqueness constraints.
    @param connection: Fake transaction owner.
    @param sql: Production INSERT statement.
    @param parameters: Bound values.
    @return: Simulated generated identifier, never an acceptance sequence.
    """
    table, columns = re.search(r"INSERT INTO (\w+) \(([^)]+)\)", sql).groups()
    row = dict(zip((column.strip() for column in columns.split(",")), parameters))  # [inserted intent]: No SQL evaluation.
    _insert_defaults(connection, table, row, parameters)
    if table == "jb2_exceptions" and any(item["audit_key"] == row["audit_key"] for item in connection.working[table].values()):
        return 0
    key = (row["video_id"], row["revision"]) if table == "jb2_review_history" else row[PRIMARY_KEYS[table]]
    if key in connection.working[table]:
        if "ON DUPLICATE KEY" in sql:
            return row.get("ledger_id", 0)
        raise ValueError("synthetic duplicate primary key")
    _check_uniqueness(connection, table, row)
    connection.working[table][key] = row
    return row.get("ledger_id", 0)


def _insert_defaults(connection: FakeConnection, table: str, row: dict, parameters: tuple) -> None:
    """@description: Supply only the explicit defaults used by the reviewed schema.
    @param connection: Fake transaction owner.
    @param table: Known fake table name.
    @param row: Mutable row under construction.
    @param parameters: Bound insert values.
    @return: None.
    """
    if table in ("credit_ledger", "jb2_evidence", "jb2_exceptions"):
        row[PRIMARY_KEYS[table]] = len(connection.working[table]) + 1
    if table == "songs":
        row["song_id"] = len(connection.working[table]) + 1
    if table == "users":
        row["credit_balance"] = 0
    if table == "jb2_legacy":
        row["identity_json"] = None
    if table == "jb2_events":
        row.update(accepted_at=connection.database.now, conflict_frozen=False)
    if table == "jb2_reminders":
        event = connection.working["jb2_events"][parameters[0]]
        row.update(video_id=event["video_id"], next_due_at=event["accepted_at"] + timedelta(hours=24), last_summary_date=None)


def _check_uniqueness(connection: FakeConnection, table: str, row: dict) -> None:
    """@description: Simulate the uniqueness barriers needed to test rollback of duplicate rewards.
    @param connection: Fake transaction owner.
    @param table: Fake target table.
    @param row: Proposed insert.
    @return: None.
    """
    keys = {"jb2_events": ("accepted_sequence",), "jb2_claims": ("operation_id", "ledger_id"),
            "credit_ledger": ("idempotency_key", "awarded_song_id")}.get(table, ())  # [unique guards]: InnoDB remains untested.
    if any(existing[key] == row[key] for existing in connection.working[table].values() for key in keys):
        raise ValueError("synthetic duplicate unique key")


def update_rows(connection: FakeConnection, sql: str, parameters: tuple) -> None:
    """@description: Route current-projection updates without a general database implementation.
    @param connection: Fake transaction owner.
    @param sql: Production UPDATE statement.
    @param parameters: Synthetic values.
    @return: None.
    """
    state = connection.working  # [isolated transaction state]: Committed only by FakeConnection.commit.
    if sql.startswith("UPDATE jb2_control"):
        state["control"]["revision"] += 1
        if "accepted_sequence" in sql:
            state["control"]["accepted_sequence"] = parameters[0]
        return
    if sql.startswith("UPDATE users SET credit_balance"):
        state["users"][parameters[0]]["credit_balance"] += 1
        return
    if sql.startswith("UPDATE jb2_videos"):
        state["jb2_videos"][parameters[-1]].update(dict(zip(("review_json", "review_revision", "review_status", "identity_json"), parameters)))
        return
    if sql.startswith("UPDATE jb2_events"):
        _update_events(state, sql, parameters)
        return
    _maintenance_update(state, sql, parameters)


def _update_events(state: dict, sql: str, parameters: tuple) -> None:
    """@description: Preserve freeze and award facts independently from review status.
    @param state: Isolated transaction dictionaries.
    @param sql: Event update statement.
    @param parameters: Bound values.
    @return: None.
    """
    if "SET reward_status" in sql:
        event = state["jb2_events"][parameters[-1]]  # [persisted scheduling]: Existing freeze remains sticky.
        event["reward_status"] = parameters[0]
        event["conflict_frozen"] = event["conflict_frozen"] or parameters[1]
    else:
        for event in state["jb2_events"].values():
            if event["video_id"] == parameters[0]:
                event["conflict_frozen"] = False


def _maintenance_update(state: dict, sql: str, parameters: tuple) -> None:
    """@description: Simulate audited merge, legacy resolution and reminder scheduling mutations.
    @param state: Isolated transaction dictionaries.
    @param sql: Maintenance UPDATE statement.
    @param parameters: Bound values.
    @return: None.
    """
    if sql.startswith("UPDATE jb2_versions"):
        for row in state["jb2_versions"].values():
            if row["canonical_id"] == parameters[1]:
                row["canonical_id"] = parameters[0]
    elif sql.startswith("UPDATE jb2_legacy"):
        song_id = state["jb2_videos"][parameters[1]]["song_id"]
        if song_id in state["jb2_legacy"]:
            state["jb2_legacy"][song_id]["identity_json"] = parameters[0]
    elif sql.startswith("UPDATE jb2_reminders"):
        video_id = state["jb2_events"][parameters[2]]["video_id"]
        state["jb2_reminders"][video_id].update(last_summary_date=parameters[0],
            next_due_at=datetime.combine(parameters[1], datetime.min.time()) + timedelta(days=1))
    else:
        raise AssertionError("Unimplemented fake update: " + sql)
