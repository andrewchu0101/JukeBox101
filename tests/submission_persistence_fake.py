"""Simulate the new repository's SQL protocol with bounded Python transaction locks.

This fake checks application write coordination and failure recovery, not MySQL isolation, collation or foreign-key semantics.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from threading import RLock

from bot.database.compensation_schema import SCHEMA_SPEC

TABLES = ("users", "songs", "credit_ledger", "jb2_versions", "jb2_videos", "jb2_legacy", "jb2_events",
          "jb2_receipts", "jb2_claims", "jb2_settlements", "jb2_review_history", "jb2_evidence", "jb2_decisions",
          "jb2_exclusions", "jb2_barriers", "jb2_exceptions", "jb2_reminders", "jb2_reminder_batches", "recommendations")
PRIMARY_KEYS = {"users": "discord_user_id", "songs": "platform_track_id", "credit_ledger": "ledger_id",
                "jb2_versions": "version_id", "jb2_videos": "video_id", "jb2_legacy": "song_id",
                "jb2_events": "operation_id", "jb2_receipts": "operation_id", "jb2_claims": "version_id",
                "jb2_settlements": "operation_id", "jb2_evidence": "evidence_id", "jb2_decisions": "decision_id",
                "jb2_exclusions": "evidence_reference", "jb2_barriers": "version_id", "jb2_exceptions": "exception_id",
                "jb2_reminders": "video_id", "jb2_reminder_batches": "summary_date", "recommendations": "recommendation_id"}


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
        self.compensation_tables = set(SCHEMA_SPEC)  # [catalogue presence, test knob]: The reviewed three tables exist unless a test removes some.
        self.compensation_hidden_columns = {}  # [schema knob, test only]: Table to columns the catalogue does not report.
        self.compensation_probe_fault = False  # [probe knob, test only]: Makes the presence probe fail as a broken connection would.
        self.grants = []  # [compensation grants, test knob]: Rows the grant queries read; empty by default.
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
        self.description = None  # [column names]: Set only by the reviewed-schema probe that the compensation guard runs.

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
        if _answer_schema_probe(self, statement, parameters):
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
            count = update_rows(self.connection, statement, parameters)
            if count is not None:
                self.rowcount = count
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


def _answer_schema_probe(cursor: "FakeCursor", statement: str, parameters: tuple) -> bool:
    """@description: Answer the compensation guard's catalogue probes from the reviewed specification, so a fake worker runs the real guard.
    @param cursor: Fake cursor receiving the probe.
    @param statement: Probe statement text.
    @param parameters: Bound values; the table name for catalogue probes.
    @return: True when the statement was a schema probe and has been answered.
    """
    if statement.startswith("SELECT * FROM exchange_") and statement.endswith("LIMIT 0"):
        table = statement.split()[3]
        hidden = cursor.connection.database.compensation_hidden_columns.get(table, set())
        cursor.description = [(name,) for name in SCHEMA_SPEC[table]["columns"] if name not in hidden]
        return True
    if "information_schema.TABLES" in statement:
        database = cursor.connection.database
        if database.compensation_probe_fault:
            raise RuntimeError("synthetic catalogue failure")
        cursor.rows = [{"table_name": name} for name in parameters if name in database.compensation_tables]
        return True
    if "information_schema.STATISTICS" in statement:
        cursor.rows = [{"index_name": "_".join(keys), "column_name": column} for keys in SCHEMA_SPEC[parameters[0]]["unique"] for column in keys]
        return True
    if "information_schema.TABLE_CONSTRAINTS" in statement:
        cursor.rows = [{"name": name} for name in SCHEMA_SPEC[parameters[0]]["checks"]]
        return True
    return False


def _claim_rows(connection: FakeConnection, sql: str, parameters: tuple) -> list[dict]:
    """@description: Answer claim lookups by operation or by canonical version, since jb2_claims is keyed by version.
    @param connection: Fake transaction owner.
    @param sql: Production SELECT statement over the claims table.
    @param parameters: Bound identifiers.
    @return: Matching claim rows.
    """
    claims = connection.working["jb2_claims"].values()
    if sql.startswith("SELECT operation_id FROM jb2_claims WHERE operation_id"):
        return [row for row in claims if row["operation_id"] == parameters[0]]
    return [row for row in claims if connection.working["jb2_versions"][row["version_id"]]["canonical_id"] in parameters]


def read_rows(connection: FakeConnection, sql: str, parameters: tuple) -> list[dict]:
    """@description: Route joined projections separately from simple lookup statements.
    @param connection: Fake transaction owner.
    @param sql: Production SELECT statement.
    @param parameters: Bound synthetic values.
    @return: Explicit rows for the requested protocol.
    """
    if "WHERE e.discord_user_id=%s ORDER BY e.accepted_sequence DESC LIMIT %s" in sql:
        return _public_history_rows(connection.database.state, parameters)
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
    if "FROM jb2_claims c JOIN jb2_events e" in sql:
        return _settled_claim_rows(connection)
    if sql.startswith("SELECT recommendation_id FROM recommendations WHERE status='failed' AND released_at IS NULL"):
        return [{"recommendation_id": row["recommendation_id"]} for row in sorted(connection.working["recommendations"].values(),
                key=lambda row: row["recommendation_id"]) if row["status"] == "failed" and row["released_at"] is None][:parameters[0]]
    if sql.startswith("SELECT ledger_id,discord_user_id,credit_delta,reason FROM credit_ledger WHERE recommendation_id=%s"):
        return [row for row in connection.working["credit_ledger"].values() if row.get("recommendation_id") == parameters[0]]
    if "FROM recommendations r JOIN songs s" in sql:
        return _claim_reservation_rows(connection, parameters[0])
    if sql.startswith("SELECT resolution_status,youtube_url FROM songs"):  # [publish state]: Read back by the approval link.
        track, platform = parameters
        return [{"resolution_status": row.get("resolution_status", "pending"), "youtube_url": row.get("youtube_url")} for row in connection.working["songs"].values()
                if row["platform_track_id"] == track and row.get("platform", "youtube") == platform]
    if sql.startswith("SELECT 1 FROM exchange_"):  # [claim guard]: The offline fake has no incident rows, so the guard sees none.
        return []
    if "exchange_redelivery_grants" in sql:  # [compensation grants]: Answered from the grants a test seeded for this requester.
        grants = [row for row in connection.database.grants if row["discord_user_id"] == parameters[0]]
        if "COUNT(*)" in sql:
            return [{"total": sum(1 for row in grants if row["closed_at"] is None)}]
        return [{"song_id": row["song_id"]} for row in grants if row["holds_song"] == 1]
    if "AND status='reserved' AND claim_token IS NULL" in sql and "discord_user_id=%s" in sql:
        threshold = parameters[1]  # [stranded check]: Same predicate as the production block, counted for one requester.
        return [{"total": sum(1 for row in connection.working["recommendations"].values()
                              if row["discord_user_id"] == parameters[0] and _unclaimed_before(row, threshold))}]
    if sql.startswith("SELECT recommendation_id,discord_user_id,song_id,created_at FROM recommendations WHERE status='reserved'"):
        threshold, limit = parameters  # [owner list]: Oldest stranded rows first, bounded like the production query.
        rows = sorted((row for row in connection.working["recommendations"].values() if _unclaimed_before(row, threshold)),
                      key=lambda row: row["recommendation_id"])
        return [{key: row[key] for key in ("recommendation_id", "discord_user_id", "song_id", "created_at")} for row in rows[:limit]]
    if "FROM recommendations WHERE discord_user_id=%s" in sql:
        return [row for row in connection.working["recommendations"].values() if row["discord_user_id"] == parameters[0]]
    if "FROM credit_ledger l JOIN recommendations" in sql:
        return _ledger_reservation_rows(connection, parameters[0])
    if "FROM jb2_claims" in sql:
        return _claim_rows(connection, sql, parameters)
    if "UTC_TIMESTAMP" in sql:
        return [{"now_utc": connection.database.now, "summary_date": connection.database.now.date()}]
    if "COALESCE(SUM" in sql:
        return [{"total": sum(row["credit_delta"] for row in connection.working["credit_ledger"].values()
                              if row["discord_user_id"] == parameters[0])}]
    if "FROM jb2_review_history WHERE video_id=%s ORDER BY revision" in sql:
        return _video_rows(connection.working["jb2_review_history"], parameters[0], "revision")
    if "FROM jb2_evidence WHERE video_id=%s ORDER BY evidence_id" in sql:
        return _video_rows(connection.working["jb2_evidence"], parameters[0], "evidence_id")
    return _simple_rows(connection, sql, parameters)


def _claim_reservation_rows(connection: FakeConnection, recommendation_id: int) -> list[dict]:
    """@description: Pair one reservation with its song's delivery link, as the claim join does.
    @param connection: Fake transaction owner.
    @param recommendation_id: Reservation identifier bound by the production query.
    @return: Matching reservation row with its link, or no row.
    """
    reservation = connection.working["recommendations"].get(recommendation_id)
    if reservation is None:
        return []
    song = next(row for row in connection.working["songs"].values() if row["song_id"] == reservation["song_id"])
    return [{"status": reservation["status"], "discord_message_id": reservation["discord_message_id"],
             "discord_user_id": reservation["discord_user_id"], "youtube_url": song["youtube_url"]}]


def _unclaimed_before(row: dict, threshold) -> bool:
    """@description: Apply the production stranded predicate to one fake reservation row.
    @param row: Fake reservation row.
    @param threshold: Creation cutoff, strictly earlier than which a row is stranded.
    @return: True when the row is reserved, never claimed, not released and created before the cutoff.
    """
    return row["status"] == "reserved" and row["claim_token"] is None and row["released_at"] is None and row["created_at"] < threshold


def _update_recommendations(state: dict, sql: str, parameters: tuple) -> int:
    """@description: Apply the reviewed delivery and release transitions, each guarded by its expected prior state and token.
    @param state: Working transaction state.
    @param sql: Production UPDATE statement over the recommendations table.
    @param parameters: Bound values of that statement.
    @return: Number of rows changed, as the driver would report.
    """
    rows = state["recommendations"]
    if sql.startswith("UPDATE recommendations SET status='sending',claim_token=%s"):
        row = rows.get(parameters[2])
        if row is None or row["status"] != "reserved":
            return 0
        row.update(status="sending", claim_token=parameters[0], claimed_at=parameters[1])
        return 1
    if sql.startswith("UPDATE recommendations SET status=%s,discord_message_id=%s"):
        row = rows.get(parameters[3])
        if row is None or row["status"] != "sending" or row["claim_token"] != parameters[4]:
            return 0
        row.update(status=parameters[0], discord_message_id=parameters[1], delivered_at=parameters[2])
        return 1
    if sql.startswith("UPDATE recommendations SET released_at=%s"):
        row = rows.get(parameters[1])
        if row is None or row["status"] != "failed" or row["released_at"] is not None:
            return 0
        row["released_at"] = parameters[0]
        return 1
    changed = [row for row in rows.values() if row["status"] == "sending" and row["claimed_at"] < parameters[0]]  # [lease]: Only expired claims.
    for row in changed:
        row["status"] = "uncertain"
    return len(changed)


def _settled_claim_rows(connection: FakeConnection) -> list[dict]:
    """@description: Pair each claimed version with the video of the event that earned it, as the exchange join does.
    @param connection: Fake transaction owner.
    @return: One row per claim with its version and video identifiers.
    """
    events = connection.working["jb2_events"]
    return [{"version_id": claim["version_id"], "video_id": events[claim["operation_id"]]["video_id"]}
            for claim in connection.working["jb2_claims"].values()]


def _ledger_reservation_rows(connection: FakeConnection, idempotency_key: str) -> list[dict]:
    """@description: Return the reservation and verified link for one exchange ledger key, as the replay join does.
    @param connection: Fake transaction owner.
    @param idempotency_key: Deterministic exchange key bound by the production query.
    @return: Matching reservation rows with their delivery link.
    """
    rows = []
    for ledger in connection.working["credit_ledger"].values():
        if ledger["idempotency_key"] != idempotency_key or ledger["recommendation_id"] is None:
            continue
        reservation = connection.working["recommendations"][ledger["recommendation_id"]]
        song = next(row for row in connection.working["songs"].values() if row["song_id"] == reservation["song_id"])
        rows.append({"recommendation_id": reservation["recommendation_id"], "youtube_url": song["youtube_url"]})
    return rows


def _video_rows(table: dict, video_id: str, order_key: str) -> list[dict]:
    """@description: Return one video's append-only audit rows in their stable database order.
    @param table: Committed or working fake audit table.
    @param video_id: Bound video identifier filter.
    @param order_key: Column that orders revisions or evidence rows.
    @return: Matching rows sorted by the requested key.
    """
    rows = [row for row in table.values() if row["video_id"] == video_id]
    return sorted(rows, key=lambda row: row[order_key])


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
    return row.get("ledger_id", row.get("recommendation_id", 0))


def _insert_defaults(connection: FakeConnection, table: str, row: dict, parameters: tuple) -> None:
    """@description: Supply only the explicit defaults used by the reviewed schema.
    @param connection: Fake transaction owner.
    @param table: Known fake table name.
    @param row: Mutable row under construction.
    @param parameters: Bound insert values.
    @return: None.
    """
    if table in ("credit_ledger", "jb2_evidence", "jb2_exceptions", "recommendations"):
        row[PRIMARY_KEYS[table]] = len(connection.working[table]) + 1
    if table == "recommendations":
        row.update(discord_message_id=None, delivered_at=None, claim_token=None, claimed_at=None, released_at=None,
                   created_at=datetime.now(timezone.utc).replace(tzinfo=None))  # [creation time]: Real naive UTC, as the reservation insert would stamp it.
    if table == "songs":
        row["song_id"] = len(connection.working[table]) + 1
        row.setdefault("resolution_status", "pending")  # [reviewed schema default]: Intake never sets a verified link.
        row.setdefault("youtube_url", None)
        row.setdefault("verification_provenance", None)
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
    if any(row.get(key) is not None and existing.get(key) == row[key] for existing in connection.working[table].values() for key in keys):
        raise ValueError("synthetic duplicate unique key")


def update_rows(connection: FakeConnection, sql: str, parameters: tuple) -> None:
    """@description: Route current-projection updates without a general database implementation.
    @param connection: Fake transaction owner.
    @param sql: Production UPDATE statement.
    @param parameters: Synthetic values.
    @return: None.
    """
    state = connection.working  # [isolated transaction state]: Committed only by FakeConnection.commit.
    if sql.startswith("UPDATE recommendations"):
        return _update_recommendations(state, sql, parameters)
    if sql.startswith("UPDATE songs"):
        return _update_song(state, sql, parameters)
    if sql.startswith("UPDATE jb2_control"):
        state["control"]["revision"] += 1
        if "accepted_sequence" in sql:
            state["control"]["accepted_sequence"] = parameters[0]
        return
    if sql.startswith("UPDATE users SET credit_balance"):
        if "credit_balance+%s" in sql:
            state["users"][parameters[1]]["credit_balance"] += parameters[0]  # [refund credit]: Amount is the bound original debit size.
        else:
            state["users"][parameters[0]]["credit_balance"] += -1 if "credit_balance-1" in sql else 1
        return
    if sql.startswith("UPDATE jb2_videos"):
        state["jb2_videos"][parameters[-1]].update(dict(zip(("review_json", "review_revision", "review_status", "identity_json"), parameters)))
        return
    if sql.startswith("UPDATE jb2_events"):
        _update_events(state, sql, parameters)
        return
    _maintenance_update(state, sql, parameters)


def _update_song(state: dict, sql: str, parameters: tuple) -> int:
    """@description: Apply the approval-link update to one song row, honouring its pending-and-unlinked condition as the production statement does.
    @param state: Isolated transaction dictionaries.
    @param sql: Production song update statement.
    @param parameters: Status, link, provenance, track identifier and platform, in statement order.
    @return: Number of rows matched, as the database reports it.
    """
    status, link, provenance, track, platform = parameters
    matches = [row for row in state["songs"].values() if row["platform_track_id"] == track and row.get("platform", "youtube") == platform]
    if "AND resolution_status='pending' AND youtube_url IS NULL" in sql:
        matches = [row for row in matches if row.get("resolution_status", "pending") == "pending" and row.get("youtube_url") is None]  # [schema defaults]: Seeded rows omit unset columns.
    for row in matches:
        row.update(resolution_status=status, youtube_url=link, verification_provenance=provenance)
    return len(matches)


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


def _public_history_rows(committed: dict, parameters: tuple) -> list[dict]:
    """@description: Simulate the new public SELECT without granting an unfiltered snapshot to callers.
    @param committed: Fake committed state used by a read-only connection.
    @param parameters: Authenticated user and maximum row count from bound SQL.
    @return: Recent caller-owned events with their current per-video review projection.
    """
    recipient_id, limit = parameters
    events = sorted(committed["jb2_events"].values(), key=lambda event: event["accepted_sequence"], reverse=True)
    rows = []  # [private projection]: Other submitters are excluded before pagination.
    for event in events:
        if event["discord_user_id"] != recipient_id:
            continue
        video = committed["jb2_videos"][event["video_id"]]
        rows.append(dict(operation_id=event["operation_id"], video_id=event["video_id"],
                         review_status=video["review_status"], reward_status=event["reward_status"]))
    return rows[:limit]
