"""Coordinate a bounded real-MySQL run through baseline, enable, cleanup and restoration.

The harness holds an advisory ownership lock, journals exact test roots locally, and never substitutes for the application repository.
"""

import json
import re
import os
import multiprocessing
import pymysql
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from bot.config import DatabaseSettings
from bot.database.test_privileges import collect_privileges, privilege_blockers
from tests.mysql_safety import validate_test_constraints
from tests.batch_b_mysql_cases import validate_payloads
from tests.batch_b_assertion_guard import require_assertions_enabled
from tests.batch_b_fk_window import require_cleanup_cascade
from tests.batch_b_harness_contract import RunScope, CONTROL_COLUMNS, HARNESS_USER, HarnessSafetyError, owned_rows, consistent_accounting, consistent_acceptance
from tests.batch_b_harness_database import (verified_connection, verify_harness_grants, read_inventory,
    schema_fingerprints, require_baseline, set_control, lock_inventory, delete_owned, lock_control, verify_visible_grants, verify_application_columns)


@dataclass
class MySQLHarness:
    """Own one isolated run and retain a nonsecret recovery manifest on every exit path."""

    application: DatabaseSettings = field(repr=False)  # [business identity]: Real repository workers use only this account.
    management: DatabaseSettings = field(repr=False)  # [cleanup identity]: Never passed to business repositories.
    scope: RunScope = field(default_factory=RunScope)
    connection: Any = field(default=None, repr=False)
    baseline: dict | None = None  # [restore authority]: Full accepted empty inventory, including original control and registry.
    definitions: dict = field(default_factory=dict, repr=False)
    last_counts: dict = field(default_factory=dict)
    observed_keys: dict = field(default_factory=dict)
    failure_details: list = field(default_factory=list)  # [sanitized diagnostics]: Keep both cleanup and recovery causes without driver text.
    observed_control: tuple = ()  # [failure evidence]: Preserve the last observed counters even after emergency shutdown.
    scenario_outcomes: list = field(default_factory=list)  # [original test evidence]: Safe worker outcomes survive pytest capture disposal.
    cleanup_progress: list = field(default_factory=list)  # [transaction attempts]: Never imply a DELETE committed from an execute acknowledgement.
    failed: bool = False  # [write poison]: Blocks every later scenario in the same run.
    enabled: bool = False  # [recovery obligation]: True before enable commit because its outcome may be uncertain.
    workers_stopped: bool = True  # [cleanup prerequisite]: No DELETE is allowed while a worker may still be alive.
    journal: Path = field(default_factory=lambda: Path(tempfile.mkdtemp(prefix="jukebox101-batch-b-")) / "run.json")

    supervised_session_id: str | None = None  # [new session boundary]: Never an old incident identifier.
    expected_definitions: dict | None = field(default=None, repr=False)
    expected_baseline: dict | None = field(default=None, repr=False)
    transaction_progress: list = field(default_factory=list)  # [management commits]: Attempt and ACK are distinct.

    def start(self) -> None:
        """@description: Certify an empty accepted baseline before enabling only the disposable test switches.
        @param self: Run owner with separately loaded app and harness profiles.
        @return: None.
        """
        require_assertions_enabled()  # [pre-connection gate]: Refuse before the journal or any management session.
        self.persist("REGISTERED")
        self.connection = verified_connection(self.management, HARNESS_USER)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s,0) AS owned", ("jukebox101:batch-b-test-harness",))
            if cursor.fetchone()["owned"] != 1:
                raise HarnessSafetyError("HARNESS_ALREADY_RUNNING")
            verify_harness_grants(cursor)
            self.definitions = schema_fingerprints(cursor)
        self.verify_cleanup_readiness()
        rows = lock_inventory(self.connection)  # [empty baseline]: Never infer disposal permission from a schema name.
        self.require_start_baseline(rows)
        with self.connection.cursor() as cursor:
            original = require_baseline(rows)  # [original singleton]: Restore exact counters and OFF switches only after verified cleanup.
            if schema_fingerprints(cursor) != self.definitions:
                raise HarnessSafetyError("SCHEMA_CHANGED_DURING_BASELINE")
            self.persist("BASELINE_VERIFIED", rows)
            self.enabled = True  # [uncertain enable commit]: Cleanup must run even if its acknowledgement is lost.
            set_control(cursor, dict({name: original[name] for name in CONTROL_COLUMNS}, snapshot_ready=1,
                                     legacy_writers_quiesced=1, settlement_enabled=1))
        self.commit_transaction("ENABLE")
        self.persist("ENABLED")

    def require_start_baseline(self, rows: dict) -> None:
        """@description: Prevent an individual test from accepting a drifted supervisor baseline.
        @param self: Run with optional immutable parent baseline.
        @param rows: Fresh locked 23-table inventory.
        @return: None; all expected empty rows, Registry and Control must match exactly.
        """
        if self.expected_baseline is not None and rows != self.expected_baseline:
            raise HarnessSafetyError("SUPERVISED_BASELINE_MISMATCH")
        self.baseline = rows

    def verify_cleanup_readiness(self) -> None:
        """@description: Refuse known self-root cleanup failure before enabling any application write.
        @param self: Harness whose exact schema baseline was just read under the ownership lock.
        @return: None; only a separately supervised temporary FK window permits testing.
        """
        require_cleanup_cascade(self.definitions)
        if self.expected_definitions is not None and self.definitions != self.expected_definitions:
            raise HarnessSafetyError("SUPERVISED_SCHEMA_MISMATCH")
        self.verify_application()
        validate_test_constraints(self.connection)

    def verify_application(self) -> None:
        """@description: Confirm every business operation will use the unchanged application-only privilege profile.
        @param self: Harness with explicit app credentials.
        @return: None.
        """
        connection = verified_connection(self.application, "jukebox101_test_app")  # [read-only ACL probe]: No business transaction yet.
        try:
            with connection.cursor() as cursor:
                grants = collect_privileges(cursor, False)
                blockers = privilege_blockers(grants, set(self.table_names()), False)
                if grants["global_privileges"] or grants["cross_schema_grants"]:
                    raise HarnessSafetyError("APPLICATION_BROAD_PRIVILEGES_REJECTED")
                if blockers:
                    raise HarnessSafetyError("APPLICATION_PRIVILEGES_REJECTED")
                verify_application_columns(cursor)
                verify_visible_grants(cursor, "jukebox101_test_app")
        finally:
            connection.rollback()
            connection.close()

    def table_names(self) -> frozenset[str]:
        """@description: Reuse the single reviewed schema allowlist for full-scope application checks.
        @param self: Current run owner.
        @return: Expected 23 tables.
        """
        from tests.batch_b_harness_contract import TABLES
        return TABLES

    def persist(self, status: str, rows: dict | None = None) -> None:
        """@description: Retain recovery roots and counts without credential values or application JSON payloads.
        @param self: Run scope and local diagnostic path.
        @param status: Fixed lifecycle stage, not raw driver error text.
        @param rows: Optional inventory for counts only.
        @return: None.
        """
        from tests.batch_b_harness_contract import PRIMARY_KEYS
        if rows is not None:
            self.last_counts = {name: len(records) for name, records in rows.items()}
            self.observed_control = rows["jb2_control"]
            self.observed_keys = {name: [{key: row[key] for key in PRIMARY_KEYS[name]} for row in records]
                                  for name, records in rows.items() if name in PRIMARY_KEYS}
        report = {"run_id": self.scope.run_id, "status": status, "subjects": self.scope.subjects,
                  "owner_id": self.scope.owner_id, "decisions": sorted(self.scope.decisions),
                  "server_uuid": os.environ.get("TEST_DB_SERVER_UUID"),
                  "counts": self.last_counts, "observed_primary_keys": self.observed_keys, "observed_control": self.observed_control,
                  "failures": self.failure_details, "scenario_outcomes": self.scenario_outcomes,
                  "cleanup_progress": self.cleanup_progress, "transaction_progress": self.transaction_progress,
                  "parent_session_id": self.supervised_session_id}
        if self.baseline is not None:
            report["original_control"] = self.baseline["jb2_control"][0]
            report["migration"] = self.baseline["jb2_migrations"]
        persist_manifest(self.journal, report)


    def execute(self, scenario: str) -> dict:
        """@description: Bound every real test scenario in a killable spawned process before teardown.
        @param self: Enabled run and predeclared subject roots.
        @param scenario: Fixed scenario name from the integration test list.
        @return: Sanitized process outcome; no raw exceptions or credentials cross into reports.
        """
        from tests.batch_b_mysql_scenarios import scenario_process
        if self.failed or not self.enabled:
            raise HarnessSafetyError("RUN_NOT_WRITABLE")
        context = multiprocessing.get_context("spawn")  # [hard deadline]: Thread timeouts cannot stop an executing SQL worker.
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=scenario_process, args=(send, self.application, self.scope, scenario))
        self.workers_stopped = False
        try:
            self.scenario_outcomes.append({'status': 'STARTED', 'scenario': scenario})
            self.persist('SCENARIO_STARTED')
            start_application_process(process)
            send.close()
            process.join(30)
            if process.is_alive():
                raise HarnessSafetyError("SCENARIO_TIMEOUT_30_SECONDS")
            if not receive.poll():
                raise HarnessSafetyError("WORKER_NO_RESULT")
            return self.receive_outcome(receive)
        except BaseException as error:
            self.record_failure(error, 'scenario')
            raise
        finally:
            self.release_worker(process, receive, send)

    def receive_outcome(self, receive: Any) -> dict:
        """@description: Preserve sanitized worker results before pytest assertion output is discarded.
        @param self: Run owning the original recovery journal.
        @param receive: Private worker result endpoint.
        @return: Original safe worker outcome.
        """
        outcome = receive.recv()  # [worker evidence]: Worker contract excludes traceback text and credentials.
        self.failed = outcome.get('status') != 'PASS'
        self.scenario_outcomes.append(outcome)
        self.persist('SCENARIO_FINISHED')
        return outcome

    def release_worker(self, process: Any, receive: Any, send: Any) -> None:
        """@description: Release IPC only after the owned scenario process has been stopped conclusively.
        @param self: Run tracking whether cleanup can safely begin.
        @param process: Owned child, possibly never started.
        @param receive: Parent result endpoint.
        @param send: Child send endpoint.
        @return: None.
        """
        try:
            stop_process(process)
            self.workers_stopped = not process.is_alive() if process.pid is not None else True
        finally:
            close_channels(receive, send)
        if self.workers_stopped:
            process.close()

    def finish(self) -> None:
        """@description: Attempt cleanup after failures without ever deleting an unproven row.
        @param self: Run whose workers have ended or whose startup failed.
        @return: None; unsafe teardown raises and poisons subsequent tests.
        """
        if self.connection is None:
            return
        try:
            if self.enabled:
                if self.supervised_session_id and self.failed:
                    raise HarnessSafetyError("SUPERVISED_SESSION_BLOCKED_NO_CLEANUP")
                self.cleanup()
        except BaseException as error:
            self.handle_cleanup_failure(error)
            raise
        finally:
            self.close_owned_session()


    def handle_cleanup_failure(self, error: BaseException) -> None:
        """@description: Preserve supervised uncertainty without an emergency write or automatic repair.
        @param self: Run retaining the original failed transaction and manifest.
        @param error: Original cleanup or poisoned-session failure.
        @return: None; rollback releases uncommitted work but never proves an unknown commit absent.
        """
        self.record_failure(error, "cleanup")
        try:
            if self.supervised_session_id:
                self.connection.rollback()
                self.observe_cleanup("ROLLBACK_ACKNOWLEDGED", "", {})
                return
            self.disable_only()
        except BaseException as recovery_error:
            self.record_failure(recovery_error, "shutdown")
            raise
        finally:
            self.persist("STOP_MANUAL_RECOVERY_REQUIRED")

    def commit_transaction(self, phase: str) -> None:
        """@description: Audit every management commit without retrying a missing acknowledgement.
        @param self: Connection-owning run with an existing durable journal.
        @param phase: Enable, cleanup, reminder adjustment or legacy emergency shutdown.
        @return: None; UNKNOWN poisons the run before further writes.
        """
        progress = dict(phase=phase, outcome="PENDING")  # [commit intent]: Durable before sending COMMIT.
        self.transaction_progress.append(progress)
        self.persist("DML_COMMIT_ATTEMPT")
        try:
            self.connection.commit()
        except BaseException as error:
            progress["outcome"] = "UNKNOWN"
            self.record_failure(error, "commit")
            self.persist("DML_COMMIT_UNKNOWN")
            raise
        progress["outcome"] = "ACKNOWLEDGED"
        self.persist("DML_COMMIT_ACKNOWLEDGED")

    def record_failure(self, error: BaseException, stage: str) -> None:
        """@description: Preserve actionable cleanup causes without logging credentials or raw driver messages.
        @param self: Run that must refuse further writes.
        @param error: Primary cleanup, shutdown or connection-close failure.
        @param stage: Fixed lifecycle stage for diagnosis.
        @return: None.
        """
        self.failed = True
        details = {"stage": stage, "error_type": type(error).__name__}  # [nonsecret cause]: Numeric MySQL codes replace driver text.
        if isinstance(error, pymysql.MySQLError) and error.args and type(error.args[0]) is int:
            details["mysql_error_number"] = error.args[0]
        if isinstance(error, HarnessSafetyError) and re.fullmatch(r"[A-Z0-9_]+(?::[a-z0-9_]+)?", str(error)):
            details["safety_code"] = str(error)
        self.failure_details.append(details)

    def close_owned_session(self) -> None:
        """@description: Do not silently report successful restoration if releasing the management session fails.
        @param self: Run retaining the owned management connection.
        @return: None.
        """
        if self.connection is None:
            return
        try:
            self.connection.close()
        except BaseException as error:
            self.record_failure(error, "connection_close")
            self.persist("STOP_CONNECTION_CLOSE_FAILED")
            raise

    def cleanup(self) -> None:
        """@description: Atomically delete verified run keys and restore the exact original control counters and switches.
        @param self: Run with committed baseline and exclusive ownership attestation.
        @return: None.
        """
        if not self.workers_stopped:
            raise HarnessSafetyError("WORKERS_STILL_RUNNING_NO_CLEANUP")
        require_worker_audits(self.scope.audit_directory)
        rows = lock_inventory(self.connection)  # [stable teardown inventory]: SERIALIZABLE protects rows and insertion gaps.
        self.persist("CLEANUP_CHECK", rows)
        with self.connection.cursor() as cursor:
            if schema_fingerprints(cursor) != self.definitions or rows["jb2_migrations"] != self.baseline["jb2_migrations"]:
                raise HarnessSafetyError("PROTECTED_BASELINE_CHANGED")
            if len(rows["jb2_control"]) != 1 or rows["jb2_control"][0]["schema_version"] != "2b2-b1":
                raise HarnessSafetyError("CONTROL_IDENTITY_CHANGED")
            owned_rows(self.scope, rows)
            consistent_accounting(rows)
            validate_payloads(rows)
            consistent_acceptance(rows, self.baseline["jb2_control"][0])
            delete_owned(cursor, rows, self.observe_cleanup)
            set_control(cursor, {name: self.baseline["jb2_control"][0][name] for name in CONTROL_COLUMNS})
            if read_inventory(cursor) != self.baseline:
                raise HarnessSafetyError("CLEANUP_BASELINE_MISMATCH")
        self.commit_transaction("CLEANUP")
        verified = lock_inventory(self.connection)
        if verified != self.baseline:
            raise HarnessSafetyError("POST_COMMIT_BASELINE_MISMATCH")
        self.connection.rollback()
        self.persist("RESTORED_VERIFIED", verified)
        self.enabled = False

    def observe_cleanup(self, phase: str, table: str, row: dict) -> None:
        """@description: Journal exact PK attempts before deletion without changing cleanup authorization.
        @param self: Owner of the existing recovery journal.
        @param phase: Fixed transaction phase, not a claim of durable deletion.
        @param table: Reviewed cleanup table or empty for transaction-only progress.
        @param row: Owned row whose primary key is safe recovery evidence.
        @return: None.
        """
        from tests.batch_b_harness_contract import PRIMARY_KEYS
        entry = {'phase': phase, 'table': table}  # [precise failure target]: The next driver error belongs to this attempted PK.
        if table:
            entry['primary_key'] = {key: row[key] for key in PRIMARY_KEYS[table]}
        self.cleanup_progress.append(entry)
        self.persist('CLEANUP_PROGRESS')

    def make_reminders_due(self) -> None:
        """@description: Advance only owned reminder due fields for time-boundary tests without altering priority or evidence.
        @param self: Enabled harness after the seed worker has fully terminated.
        @return: None.
        """
        try:
            self.adjust_reminder_deadlines()
        except BaseException:
            self.failed = True
            raise

    def adjust_reminder_deadlines(self) -> None:
        """@description: Change only an owned reminder deadline after full consistency checks.
        @param self: Verified run with no active application worker.
        @return: None.
        """
        if not self.workers_stopped or self.failed or not self.enabled:
            raise HarnessSafetyError("REMINDER_WORKER_STILL_RUNNING")
        rows = lock_inventory(self.connection)
        owned_rows(self.scope, rows)
        consistent_accounting(rows)
        consistent_acceptance(rows, self.baseline["jb2_control"][0])
        with self.connection.cursor() as cursor:
            for row in rows["jb2_reminders"]:
                cursor.execute("UPDATE jb2_reminders SET next_due_at=UTC_TIMESTAMP(6) WHERE video_id=%s", (row["video_id"],))
                if cursor.rowcount != 1:
                    raise HarnessSafetyError("REMINDER_SETUP_ROWCOUNT_MISMATCH")
        self.commit_transaction("REMINDER_SETUP")

    def disable_only(self) -> None:
        """@description: Freeze further settlement without disguising residual rows or resetting acceptance counters.
        @param self: Failed run whose diagnostic manifest must be retained.
        @return: None.
        """
        if not self.workers_stopped:
            raise HarnessSafetyError("WORKERS_ALIVE_MANUAL_SHUTDOWN_REQUIRED")
        self.abandon_session()
        self.connection = verified_connection(self.management, HARNESS_USER)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s,0) AS owned", ("jukebox101:batch-b-test-harness",))
            if cursor.fetchone()["owned"] != 1:
                raise HarnessSafetyError("EMERGENCY_OWNERSHIP_UNAVAILABLE")
            lock_control(cursor)
            cursor.execute("UPDATE jb2_control SET snapshot_ready=0,legacy_writers_quiesced=0,settlement_enabled=0 WHERE singleton_id=1")
        self.commit_transaction("EMERGENCY_SHUTDOWN")

    def abandon_session(self) -> None:
        """@description: Release a failed transaction before trying emergency shutdown on an independent verified session.
        @param self: Run owning the old potentially unusable connection.
        @return: None.
        """
        previous = self.connection  # [abandoned session]: Never reused after uncertain cleanup commit.
        self.connection = None
        try:
            previous.rollback()
            self.observe_cleanup('ROLLBACK_ACKNOWLEDGED', '', {})
        except pymysql.MySQLError as error:
            self.record_failure(error, "rollback")
            self.persist("CLEANUP_ROLLBACK_FAILED")
        finally:
            try:
                previous.close()
            except pymysql.MySQLError as error:
                self.record_failure(error, "abandoned_connection_close")
                self.persist("CLEANUP_CONNECTION_CLOSE_FAILED")


def stop_process(process: Any) -> None:
    """@description: Bound child termination and prevent teardown while any scenario can still write.
    @param process: Owned multiprocessing scenario process, possibly not started.
    @return: None.
    """
    if process.pid is None:
        return
    if process.is_alive():
        process.terminate()
        process.join(5)
    if process.is_alive():
        process.kill()
        process.join(5)
    if process.is_alive():
        raise HarnessSafetyError("WORKER_TERMINATION_FAILED")


def persist_manifest(path: Path, report: dict) -> None:
    """@description: Flush recovery metadata before writes and replace its journal atomically without storing secrets.
    @param path: Unique local temporary run manifest path.
    @param report: Synthetic roots, actual primary keys, baseline control and sanitized counts.
    @return: None.
    """
    pending = path.with_suffix(".pending")  # [durable recovery intent]: Never use a partial JSON file as cleanup authority.
    with pending.open("w", encoding="utf-8") as stream:
        json.dump(report, stream, sort_keys=True, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def close_channels(receive: Any, send: Any) -> None:
    """@description: Release both owned IPC ends without exposing credential-bearing process arguments.
    @param receive: Parent receive endpoint.
    @param send: Child send endpoint retained by parent until process creation.
    @return: None.
    """
    receive.close()
    send.close()


def start_application_process(process: Any) -> None:
    """@description: Keep harness and migration credential namespaces out of the spawned application worker environment.
    @param process: Owned application scenario process with app credentials in its private IPC arguments.
    @return: None.
    """
    hidden = {key: os.environ.pop(key) for key in tuple(os.environ)
              if key.startswith(("TEST_HARNESS_DB_", "TEST_MIGRATION_DB_", "TEST_DBA_DB_"))}  # [parent-only credentials]: Restored immediately after spawn.
    try:
        process.start()
    finally:
        os.environ.update(hidden)
        hidden.clear()


def require_worker_audits(directory: str | None, active_allowed: bool = False) -> None:
    """@description: Refuse cleanup or another worker after unresolved application commit evidence.
    @param directory: Fresh session-owned transaction audit directory, absent for legacy tests.
    @param active_allowed: Permit peer connections during deliberate concurrency, never a poisoned outcome.
    @return: None; incomplete, unknown or blocked worker records preserve all committed rows.
    """
    if directory is None:
        return
    for path in Path(directory).glob("transaction-*.json"):
        report = json.loads(path.read_text(encoding="utf-8"))
        if report["blocked"] or (not active_allowed and not report["closed"]):
            raise HarnessSafetyError("APPLICATION_TRANSACTION_UNCERTAIN")
        rejected = {"UNKNOWN"} if active_allowed else {"PENDING", "UNKNOWN"}
        if any(item["outcome"] in rejected for item in report["progress"]):
            raise HarnessSafetyError("APPLICATION_TRANSACTION_UNCERTAIN")


def create_harness(application: DatabaseSettings, management: DatabaseSettings, supervision: dict | None) -> MySQLHarness:
    """@description: Bind existing run manifests to a single parent-owned Stage-B-only session.
    @param application: Existing application profile, never DBA authority.
    @param management: Existing least-privileged cleanup profile.
    @param supervision: Parent-provided pytest configuration; absent for legacy execution.
    @return: Existing harness with a fresh run manifest and pinned temporary schema.
    """
    require_assertions_enabled()  # [pre-registration gate]: No supervisor hook or directory may run under -O.
    if supervision is None:
        return MySQLHarness(application, management)
    from tests.batch_b_acceptance import stage_b_save
    supervision['before_run']()
    scope = RunScope()  # [independent test manifest]: Never reuse an old incident or sibling test's roots.
    directory = Path(supervision['directory']) / scope.run_id
    directory.mkdir()
    transactions = directory / 'transactions'
    transactions.mkdir()
    scope.audit_directory = str(transactions)
    harness = MySQLHarness(application, management, scope=scope, journal=directory / 'run.json',
                           supervised_session_id=supervision['session_id'], expected_definitions=supervision['definitions'],
                           expected_baseline=supervision['baseline'])
    supervision['state']['runs'].append(dict(run_id=scope.run_id, journal=str(harness.journal)))
    stage_b_save(supervision['state'], 'B_ONLY_RUN_REGISTERED')
    return harness
