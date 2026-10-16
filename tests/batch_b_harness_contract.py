"""Define disposable MySQL run ownership and narrowly scoped harness permissions.

Every generated subject is registered before workers start; ownership is checked through actual foreign-key relationships.
"""

import hashlib
import json
import os
from dataclasses import dataclass, field
from uuid import UUID, uuid4
from bot.config import ConfigurationError, DatabaseSettings, required_environment_value
from bot.database.test_profiles import profile_settings
from bot.database.test_privileges import ORIGINAL_TABLES, NEW_TABLES

RUN_SWITCH = "JUKEBOX_RUN_BATCH_B_MYSQL"
HARNESS_SWITCH = "JUKEBOX_RUN_BATCH_B_HARNESS"
HARNESS_USER = "jukebox101_test_harness"
TABLES = ORIGINAL_TABLES | NEW_TABLES
CONTROL_COLUMNS = ("accepted_sequence", "revision", "snapshot_ready", "legacy_writers_quiesced", "settlement_enabled")
DELETE_ORDER = ("jb2_claims", "jb2_settlements", "jb2_receipts", "jb2_review_history", "jb2_evidence",
                "jb2_exclusions", "jb2_exceptions", "jb2_barriers", "jb2_reminders", "jb2_reminder_batches",
                "jb2_decisions", "credit_ledger", "jb2_events", "jb2_legacy", "jb2_videos", "songs", "users")
PRIMARY_KEYS = {name: (key,) for name, key in dict(users="discord_user_id", songs="song_id",
    credit_ledger="ledger_id", jb2_versions="version_id", jb2_videos="video_id", jb2_legacy="song_id",
    jb2_events="operation_id", jb2_receipts="operation_id", jb2_claims="version_id", jb2_settlements="operation_id",
    jb2_evidence="evidence_id", jb2_decisions="decision_id", jb2_exclusions="evidence_reference",
    jb2_barriers="version_id", jb2_exceptions="exception_id", jb2_reminders="video_id",
    jb2_reminder_batches="summary_date").items()}
PRIMARY_KEYS["jb2_review_history"] = ("video_id", "revision")


class HarnessSafetyError(RuntimeError):
    """Stop writes or cleanup when the disposable ownership proof is unavailable."""


@dataclass
class RunScope:
    """Track exact predeclared subjects instead of treating a run prefix as ownership proof."""

    run_id: str = field(default_factory=lambda: uuid4().hex)
    audit_directory: str | None = field(default=None, repr=False)  # [supervised worker audit]: Fresh session-owned path, never a credential.
    owner_id: int = field(init=False)
    subjects: dict = field(init=False)  # [predeclared ownership]: Exact user/video/version triples bound to each operation.
    decisions: set[str] = field(init=False)  # [approved audit keys]: Cleanup does not infer ownership from arbitrary prefixes.

    def __post_init__(self) -> None:
        """@description: Register collision-resistant roots before any database transaction can run.
        @param self: New run manifest.
        @return: None.
        """
        self.owner_id = 4_000_000_000_000_000 + int(self.run_id[:10], 16)  # [synthetic actor]: Never a real Discord identity.
        self.subjects = {self.run_id + ":" + str(index): {
            "user": self.owner_id + index, "video": hashlib.sha256((self.run_id + str(index)).encode()).hexdigest()[:11],
            "version": self.run_id + (":v2" if index == 3 else ":v1")} for index in range(1, 4)}
        self.decisions = {operation + suffix for operation in self.subjects for suffix in (":approve", ":reject", ":conflict", ":stale")}
        self.decisions.update({self.run_id + ":merge", self.run_id + ":exclude"})


def harness_requirements() -> dict[str, set[str]]:
    """@description: Allow baseline reads and exact-row teardown without schema or registry authority.
    @param: none
    @return: Table-level grants; restricted column updates are declared separately.
    """
    rights = {name: {"SELECT"} for name in TABLES}  # [harness authority]: Business INSERT remains application-only.
    for name in (*DELETE_ORDER, "jb2_versions"):
        rights[name].add("DELETE")
    return rights


def load_profiles() -> tuple[DatabaseSettings, DatabaseSettings]:
    """@description: Refuse implicit opt-in, production fallback or migration-enabled processes before I/O.
    @param: none
    @return: Independent application and harness credentials, never loaded from dotenv.
    """
    if os.environ.get(HARNESS_SWITCH) != "1" or os.environ.get("JUKEBOX_BATCH_B_EXCLUSIVE") != "jukebox101_test":
        raise ConfigurationError("HARNESS_EXCLUSIVE_OPT_IN_REQUIRED")
    if any(value == "1" for key, value in os.environ.items() if key.startswith("JUKEBOX_RUN_") and key not in {RUN_SWITCH, HARNESS_SWITCH}):
        raise ConfigurationError("OTHER_DATABASE_SWITCHES_MUST_BE_OFF")
    expected_server_uuid()
    application = profile_settings(RUN_SWITCH)  # [application-only workflow]: Never substitute the cleanup account.
    prefix = "TEST_HARNESS_DB_"
    harness = DatabaseSettings(required_environment_value(prefix + "HOST"), int(required_environment_value(prefix + "PORT")),
                               required_environment_value(prefix + "USER"), required_environment_value(prefix + "PASSWORD"),
                               required_environment_value(prefix + "NAME"))
    if (harness.host, harness.port, harness.user, harness.database) != ("127.0.0.1", 3306, HARNESS_USER, "jukebox101_test") or not harness.password:
        raise ConfigurationError("HARNESS_DESTINATION_MISMATCH")
    return application, harness


def owned_rows(scope: RunScope, rows: dict) -> None:
    """@description: Prove every committed row belongs to predeclared subjects before any deletion.
    @param scope: Manifest registered before test writes.
    @param rows: Complete locked inventory of all reviewed tables.
    @return: None; uncertainty blocks every DELETE.
    """
    roots = ownership_roots(scope, rows)  # [cross-table facts]: Discover generated IDs through registered natural identities.
    for table, records in rows.items():
        if table in {"jb2_control", "jb2_migrations"}:
            continue
        for row in records:
            if not row_is_owned(table, row, scope, roots):
                raise HarnessSafetyError("UNOWNED_ROW:" + table)


def ownership_roots(scope: RunScope, rows: dict) -> dict:
    """@description: Link server-generated song and ledger keys to exact registered requests.
    @param scope: Registered subjects.
    @param rows: Full inventory; no pagination or prefix-only queries.
    @return: Resolved root sets used by all dependent ownership checks.
    """
    users = {item["user"] for item in scope.subjects.values()}  # [allowed accounts]: No preexisting user may be touched.
    videos = {item["video"] for item in scope.subjects.values()}
    songs = {row["song_id"] for row in rows["songs"] if row["platform"] == "youtube" and row["platform_track_id"] in videos}
    ledger_keys = {"jb2:" + hashlib.sha256(operation.encode()).hexdigest() for operation in scope.subjects}
    ledgers = {row["ledger_id"] for row in rows["credit_ledger"] if row["discord_user_id"] in users
               and row["awarded_song_id"] in songs and row["idempotency_key"] in ledger_keys}
    return {"users": users, "videos": videos, "songs": songs, "ledgers": ledgers,
            "versions": {item["version"] for item in scope.subjects.values()}}


def row_is_owned(table: str, row: dict, scope: RunScope, roots: dict) -> bool:
    """@description: Validate dependent rows using explicit relationships rather than assuming Run ID propagation.
    @param table: Reviewed table identifier.
    @param row: Actual server row under the control lock.
    @param scope: Registered requests and decision IDs.
    @param roots: Verified natural and generated identity sets.
    @return: Whether deletion of this particular row is authorized by the run manifest.
    """
    if table in {"submissions", "recommendations", "daily_dispatches"}:
        return False
    checks = {
        "users": lambda: row["discord_user_id"] in roots["users"],
        "songs": lambda: row["song_id"] in roots["songs"],
        "credit_ledger": lambda: row["ledger_id"] in roots["ledgers"],
        "jb2_versions": lambda: row["version_id"] in roots["versions"] and row["canonical_id"] in roots["versions"],
        "jb2_videos": lambda: row["video_id"] in roots["videos"] and row["song_id"] in roots["songs"],
        "jb2_legacy": lambda: row["song_id"] in roots["songs"],
        "jb2_events": lambda: event_owned(row, scope),
        "jb2_decisions": lambda: row["decision_id"] in scope.decisions and row["actor_id"] == scope.owner_id,
        "jb2_reminder_batches": lambda: batch_owned(row, scope),
    }
    if table in checks:
        return checks[table]()
    return dependent_owned(table, row, scope, roots)


def event_owned(row: dict, scope: RunScope) -> bool:
    """@description: Bind each persisted operation to its predeclared user and video pair.
    @param row: Actual event.
    @param scope: Accepted operation manifest.
    @return: Whether all operation identity fields match.
    """
    subject = scope.subjects.get(row["operation_id"])  # [registered intake]: A prefix alone is insufficient.
    return subject is not None and (row["discord_user_id"], row["video_id"]) == (subject["user"], subject["video"])


def batch_owned(row: dict, scope: RunScope) -> bool:
    """@description: Attribute day-keyed reminder batches through their original operation list and Owner.
    @param row: Actual reminder batch without a Run ID column.
    @param scope: Predeclared operations and synthetic Owner.
    @return: Whether the entire stored summary belongs to this run.
    """
    from bot.database.submission_codec import decode_contract
    operations = decode_contract(row["operation_ids"], tuple)
    return bool(operations) and set(operations).issubset(scope.subjects) and row["owner_discord_user_id"] == scope.owner_id


def dependent_owned(table: str, row: dict, scope: RunScope, roots: dict) -> bool:
    """@description: Attribute append-only and generated-key children through their verified parents.
    @param table: Reviewed dependent table.
    @param row: Actual dependent row.
    @param scope: Exact registered request identities.
    @param roots: Verified parent key sets.
    @return: Whether all applicable identity relationships remain inside the run.
    """
    if table in {"jb2_receipts", "jb2_settlements"}:
        return row["operation_id"] in scope.subjects
    if table in {"jb2_evidence", "jb2_review_history"}:
        return row["video_id"] in roots["videos"] and row["actor_id"] in roots["users"] | {scope.owner_id}
    checks = {"jb2_claims": lambda: claim_owned(row, scope, roots),
              "jb2_reminders": lambda: row["video_id"] in roots["videos"],
              "jb2_barriers": lambda: row["version_id"] in roots["versions"],
              "jb2_exclusions": lambda: exclusion_owned(row, scope, roots),
              "jb2_exceptions": lambda: row["version_id"] in roots["versions"] and row["actor_id"] == scope.owner_id and exception_owned(row, roots)}
    return checks.get(table, lambda: False)()


def claim_owned(row: dict, scope: RunScope, roots: dict) -> bool:
    """@description: Attribute a claim only when its operation, version, ledger and recipient are run-owned.
    @param row: Actual claim.
    @param scope: Predeclared operation set.
    @param roots: Verified parent key sets.
    @return: Whether every claim identity stays inside this run.
    """
    return (row["operation_id"] in scope.subjects and row["version_id"] in roots["versions"]
            and row["ledger_id"] in roots["ledgers"] and row["discord_user_id"] in roots["users"])


def exclusion_owned(row: dict, scope: RunScope, roots: dict) -> bool:
    """@description: Bind exclusion evidence to the explicitly registered subject, version, actor and proof reference.
    @param row: Actual exclusion row.
    @param scope: Predeclared evidence action.
    @param roots: Verified video and version sets.
    @return: Whether the complete exclusion identity is owned.
    """
    return (row["video_id"] in roots["videos"] and row["version_id"] in roots["versions"]
            and row["actor_id"] == scope.owner_id and row["evidence_reference"] == scope.run_id + ":exclusion")


def exception_owned(row: dict, roots: dict) -> bool:
    """@description: Check generated audit exceptions against every referenced original ledger entry.
    @param row: Stored exception with original claim JSON.
    @param roots: Verified ledger identities.
    @return: Whether the exception exclusively describes this run's ledger entries.
    """
    entries = json.loads(row["original_claims"]) if isinstance(row["original_claims"], str) else row["original_claims"]
    return bool(entries) and all(entry["ledger_id"] in roots["ledgers"] for entry in entries)


def consistent_accounting(rows: dict) -> None:
    """@description: Preserve inconsistent accounting evidence instead of deleting a failed atomicity result.
    @param rows: Complete stable test inventory.
    @return: None; mismatch blocks all cleanup writes except emergency switch shutdown.
    """
    consistent_event_receipts(rows)
    consistent_settlements(rows)
    consistent_claim_links(rows)
    consistent_intake_links(rows)
    consistent_balances(rows)


def consistent_event_receipts(rows: dict) -> None:
    """@description: Require an immutable original receipt for every accepted operation.
    @param rows: Full stable inventory.
    @return: None.
    """
    if {row["operation_id"] for row in rows["jb2_events"]} != {row["operation_id"] for row in rows["jb2_receipts"]}:
        raise HarnessSafetyError("EVENT_RECEIPT_INCONSISTENCY")


def consistent_settlements(rows: dict) -> None:
    """@description: Require every claim to have exactly the corresponding ledger and durable settlement receipt.
    @param rows: Full stable inventory.
    @return: None.
    """
    ledger_ids = {row["ledger_id"] for row in rows["credit_ledger"]}  # [durable awards]: No orphan ledger can be silently deleted.
    if {row["ledger_id"] for row in rows["jb2_claims"]} != ledger_ids:
        raise HarnessSafetyError("CLAIM_LEDGER_INCONSISTENCY")
    if {row["operation_id"] for row in rows["jb2_claims"]} != {row["operation_id"] for row in rows["jb2_settlements"]}:
        raise HarnessSafetyError("CLAIM_SETTLEMENT_INCONSISTENCY")


def consistent_balances(rows: dict) -> None:
    """@description: Preserve balance drift as evidence instead of cleaning away a broken accounting result.
    @param rows: Full stable inventory.
    @return: None.
    """
    for user in rows["users"]:
        total = sum(row["credit_delta"] for row in rows["credit_ledger"] if row["discord_user_id"] == user["discord_user_id"])
        if total != user["credit_balance"]:
            raise HarnessSafetyError("USER_LEDGER_INCONSISTENCY")


def consistent_claim_links(rows: dict) -> None:
    """@description: Reconcile each award to its original event, video, recipient and deterministic ledger key.
    @param rows: Complete stable inventory.
    @return: None; mismatched but individually run-owned rows remain available for diagnosis.
    """
    events = {row["operation_id"]: row for row in rows["jb2_events"]}  # [original recipients]: Claims cannot swap two registered users.
    videos = {row["video_id"]: row for row in rows["jb2_videos"]}
    ledgers = {row["ledger_id"]: row for row in rows["credit_ledger"]}
    for claim in rows["jb2_claims"]:
        event = events.get(claim["operation_id"])
        ledger = ledgers[claim["ledger_id"]]
        if event is None or event["video_id"] not in videos:
            raise HarnessSafetyError("CLAIM_EVENT_LINK_INCONSISTENCY")
        expected_key = "jb2:" + hashlib.sha256(claim["operation_id"].encode()).hexdigest()
        if (claim["discord_user_id"], ledger["discord_user_id"], ledger["awarded_song_id"], ledger["idempotency_key"], ledger["credit_delta"], ledger["reason"]) != (
                event["discord_user_id"], event["discord_user_id"], videos[event["video_id"]]["song_id"], expected_key, 1, "submission_award"):
            raise HarnessSafetyError("CLAIM_RECIPIENT_SONG_KEY_INCONSISTENCY")


def consistent_intake_links(rows: dict) -> None:
    """@description: Preserve orphaned intake projections instead of erasing partial commits during cleanup.
    @param rows: Full stable inventory.
    @return: None; all video, user, history, evidence and reminder roots must be complete.
    """
    links = (("jb2_videos", "video_id", "jb2_events", "video_id"),
             ("users", "discord_user_id", "jb2_events", "discord_user_id"),
             ("songs", "song_id", "jb2_videos", "song_id"))  # [required intake parents]: No orphan business root is disposable.
    for left, left_key, right, right_key in links:
        if {row[left_key] for row in rows[left]} != {row[right_key] for row in rows[right]}:
            raise HarnessSafetyError("INTAKE_LINK_INCONSISTENCY:" + left)
    videos = {row["video_id"] for row in rows["jb2_videos"]}
    for table in ("jb2_reminders", "jb2_review_history", "jb2_evidence"):
        if {row["video_id"] for row in rows[table]} != videos:
            raise HarnessSafetyError("VIDEO_DEPENDENT_INCONSISTENCY:" + table)


def consistent_acceptance(rows: dict, original: dict) -> None:
    """@description: Preserve sequence-counter drift instead of resetting evidence of a partial intake commit.
    @param rows: Full current inventory under the harness control lock.
    @param original: Accepted pre-run control singleton.
    @return: None; only contiguous committed run events justify a changed acceptance watermark.
    """
    control = rows["jb2_control"][0]  # [committed watermark]: Technical retries cannot consume another sequence.
    sequences = sorted(row["accepted_sequence"] for row in rows["jb2_events"])
    expected = list(range(original["accepted_sequence"] + 1, original["accepted_sequence"] + len(sequences) + 1))
    if sequences != expected or control["accepted_sequence"] != original["accepted_sequence"] + len(sequences):
        raise HarnessSafetyError("ACCEPTANCE_SEQUENCE_INCONSISTENCY")
    revisions = len(sequences) + len(rows["jb2_claims"]) + sum(decision_revision_effect(row) for row in rows["jb2_decisions"])
    if control["revision"] != original["revision"] + revisions:
        raise HarnessSafetyError("CONTROL_REVISION_INCONSISTENCY")
    if any(control[name] != 1 for name in CONTROL_COLUMNS[2:]):
        raise HarnessSafetyError("CONTROL_SWITCH_DRIFT")


def decision_revision_effect(row: dict) -> int:
    """@description: Attribute control revision changes to durable Owner actions, including legitimate no-op merges.
    @param row: Owned persisted decision receipt.
    @return: Exact revision increment performed by the existing Repository path.
    """
    if row["decision_id"].endswith(":merge"):
        from bot.database.submission_codec import decode_contract
        source, target = decode_contract(row["result_json"], tuple)
        return int(source != target)
    return 1


def expected_server_uuid() -> str:
    """@description: Bind tests to the exact MySQL instance reviewed during one-time DBA setup.
    @param: none
    @return: Canonical nonsecret server UUID supplied by the approved setup session.
    """
    value = required_environment_value("TEST_DB_SERVER_UUID")  # [instance pin]: Loopback and port alone do not prove server identity.
    try:
        canonical = str(UUID(value))
    except ValueError:
        raise ConfigurationError("TEST_SERVER_UUID_INVALID") from None
    if canonical != value.lower():
        raise ConfigurationError("TEST_SERVER_UUID_INVALID")
    return canonical
