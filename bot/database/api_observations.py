"""Keep official API values in one short-lived table, refuse expired ones, and clear them for real.

Every other table stores only the pass or reject conclusion. A value is trusted only while its row is unpurged and unexpired by the database clock;
the retention job (this module's purge functions, run by the Bot and by python -m bot.api_retention) is what actually sets the values to NULL.
"""

from dataclasses import dataclass
from typing import Any

from bot.database.persisted_snapshot import fetch_all
from bot.eligibility import DurationEligibility
from bot.review import DurationEvidenceExpired, ReviewSnapshot, ReviewStatus

RETENTION_DAYS = 14  # [normal retention]: expires_at = observed_at + this; the table's CHECK only bounds it at 30 days and never deletes anything.
PURGE_BATCH = 200  # [short transaction]: Rows cleared per statement; the job repeats until nothing is left.
VALID_SQL = "purged_at IS NULL AND expires_at > statement_timestamp()"  # [trust rule]: Every read of an observation carries this predicate.
OVERDUE_SQL = "purged_at IS NULL AND expires_at <= statement_timestamp()"  # [retention debt]: Expired but still holding values.


@dataclass(frozen=True)
class RetentionStatus:
    """Summarize retention health from the database clock, so a stopped purge job is visible without running it."""

    unpurged_rows: int  # [values held]: Rows still holding API values.
    overdue_rows: int  # [debt]: Of those, rows past expires_at.
    oldest_overdue_days: float | None  # [severity]: Days since the oldest overdue row expired; None when nothing is overdue.
    last_success_age_days: float | None  # [liveness]: Days since the last run that finished without error; None when never.


REQUIRED_TABLE_PRIVILEGES = (("jb2_api_observations", "SELECT"), ("jb2_api_observations", "INSERT"), ("jb2_api_observations", "UPDATE"),
                             ("jb2_retention_runs", "SELECT"), ("jb2_retention_runs", "INSERT"))  # [what the job needs]: Each checked on its own, since a combined check passes on any one.
REQUIRED_RUN_COLUMNS = ("finished_at", "purged_rows", "error_class")  # [run closing]: Columns the application role must be able to update.


def missing_retention_requirements(cursor: Any) -> list[str]:
    """@description: List what the current role lacks to keep and purge API observations, without changing anything.
    @param cursor: Cursor on the application connection.
    @return: Plain descriptions of each missing table or privilege; empty when everything needed exists.
    """
    missing = []
    for table in ("jb2_api_observations", "jb2_retention_runs"):
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL AS present", (table,))
        if not cursor.fetchone()["present"]:
            missing.append(f"table {table} is missing")
    if missing:
        return missing
    for table, privilege in REQUIRED_TABLE_PRIVILEGES:
        cursor.execute("SELECT has_table_privilege(current_user, %s, %s) AS granted", (table, privilege))
        if not cursor.fetchone()["granted"]:
            missing.append(f"{privilege} on {table} is not granted")
    for column in REQUIRED_RUN_COLUMNS:
        cursor.execute("SELECT has_column_privilege(current_user, 'jb2_retention_runs', %s, 'UPDATE') AS granted", (column,))
        if not cursor.fetchone()["granted"]:
            missing.append(f"UPDATE on jb2_retention_runs.{column} is not granted")
    return missing


def record_observation(cursor: Any, video_id: str, duration_seconds: int, channel_id: str | None) -> None:
    """@description: Store or refresh the only copy of the API values for a video, with a new expiry.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param video_id: Video the values describe.
    @param duration_seconds: Official full-video duration.
    @param channel_id: Official stable channel identifier, or None.
    @return: None.
    """
    cursor.execute("INSERT INTO jb2_api_observations (video_id, observed_at, expires_at, duration_seconds, channel_id) "
                   "VALUES (%s, statement_timestamp(), statement_timestamp() + make_interval(days => %s), %s, %s) "
                   "ON CONFLICT (video_id) DO UPDATE SET observed_at = EXCLUDED.observed_at, expires_at = EXCLUDED.expires_at, "
                   "duration_seconds = EXCLUDED.duration_seconds, channel_id = EXCLUDED.channel_id, purged_at = NULL, purge_reason = NULL",
                   (video_id, RETENTION_DAYS, duration_seconds, channel_id))


def has_valid_observation(cursor: Any, video_id: str) -> bool:
    """@description: Ask whether the API conclusion about a video is still backed by an unexpired, unpurged observation.
    @param cursor: Transaction-owned cursor.
    @param video_id: Video to check.
    @return: True only while the observation is trusted by the database clock.
    """
    cursor.execute(f"SELECT 1 FROM jb2_api_observations WHERE video_id = %s AND {VALID_SQL}", (video_id,))
    return cursor.fetchone() is not None


def relies_on_api_conclusion(review: ReviewSnapshot) -> bool:
    """@description: Say whether a passing API conclusion is the review's only duration basis; Owner evidence never expires with the API retention window.
    @param review: Current or projected review.
    @return: True when there is no Owner or legacy duration evidence and a passing verdict stands alone.
    """
    verdict = review.duration_verdict
    return review.duration is None and verdict is not None and verdict.outcome is DurationEligibility.PASS


def basis_expired(cursor: Any, review: ReviewSnapshot) -> bool:
    """@description: Decide whether a review's only duration basis has outlived its retention window.
    @param cursor: Transaction-owned cursor.
    @param review: Current or projected review.
    @return: True when the review rests only on an API conclusion whose observation is expired, purged or missing.
    """
    return relies_on_api_conclusion(review) and not has_valid_observation(cursor, review.video.video_id)


def require_unexpired_basis(cursor: Any, review: ReviewSnapshot) -> None:
    """@description: Stop an approval whose only duration basis has expired; already approved reviews never pass through here.
    @param cursor: Transaction-owned cursor.
    @param review: Review about to be approved.
    @return: None; raises DurationEvidenceExpired so the case stays pending for re-validation or Owner-entered duration.
    """
    if basis_expired(cursor, review):
        raise DurationEvidenceExpired("The official duration data has expired; re-validate or enter the duration as the Owner")


def purge_video(cursor: Any, video_id: str, reason: str) -> int:
    """@description: Clear the values of one video's observation, keeping only the fact that it was purged and why.
    @param cursor: Transaction-owned cursor.
    @param video_id: Video whose values are no longer needed.
    @param reason: expired, completed or unavailable.
    @return: Number of rows cleared.
    """
    cursor.execute("UPDATE jb2_api_observations SET duration_seconds = NULL, channel_id = NULL, purged_at = statement_timestamp(), purge_reason = %s "
                   "WHERE video_id = %s AND purged_at IS NULL", (reason, video_id))
    return cursor.rowcount


def purge_batch(cursor: Any, condition: str, reason: str, limit: int = PURGE_BATCH) -> int:
    """@description: Clear one bounded batch of observations selected by a fixed predicate.
    @param cursor: Transaction-owned cursor.
    @param condition: Constant SQL predicate over alias o (never user input).
    @param reason: expired or completed.
    @param limit: Maximum rows in this batch.
    @return: Number of rows cleared.
    """
    cursor.execute("UPDATE jb2_api_observations SET duration_seconds = NULL, channel_id = NULL, purged_at = statement_timestamp(), purge_reason = %s "
                   "WHERE video_id IN (SELECT o.video_id FROM jb2_api_observations o LEFT JOIN jb2_videos v ON v.video_id = o.video_id "
                   f"WHERE o.purged_at IS NULL AND ({condition}) ORDER BY o.expires_at LIMIT %s FOR UPDATE OF o SKIP LOCKED)", (reason, limit))
    return cursor.rowcount


EXPIRED_CONDITION = "o.expires_at <= statement_timestamp()"  # [first rule]: Past the retention window.
COMPLETED_CONDITION = f"v.review_status IN ('{ReviewStatus.APPROVED.value}', '{ReviewStatus.REJECTED.value}')"  # [second rule]: The decision stands on the stored conclusion, so the values are no longer needed.


def purge_pass(cursor: Any) -> int:
    """@description: Clear one batch of expired observations and one batch of observations whose case is decided.
    @param cursor: Transaction-owned cursor.
    @return: Rows cleared by this pass; zero means nothing is left to clear.
    """
    return purge_batch(cursor, EXPIRED_CONDITION, "expired") + purge_batch(cursor, COMPLETED_CONDITION, "completed")


def purge_preview(cursor: Any) -> tuple[int, int]:
    """@description: Count what a purge would clear right now, without changing anything.
    @param cursor: Transaction-owned cursor.
    @return: Rows past expiry, and rows whose case is decided.
    """
    cursor.execute(f"SELECT count(*) FILTER (WHERE {EXPIRED_CONDITION}) AS expired, count(*) FILTER (WHERE {COMPLETED_CONDITION}) AS completed "
                   "FROM jb2_api_observations o LEFT JOIN jb2_videos v ON v.video_id = o.video_id WHERE o.purged_at IS NULL")
    row = cursor.fetchone()
    return row["expired"], row["completed"]


def start_run(cursor: Any) -> int:
    """@description: Record that a purge run began, so a run that never finishes is visible.
    @param cursor: Transaction-owned cursor.
    @return: Run identifier.
    """
    cursor.execute("INSERT INTO jb2_retention_runs DEFAULT VALUES RETURNING run_id")
    return cursor.fetchone()["run_id"]


def finish_run(cursor: Any, run_id: int, purged_rows: int, error_class: str | None) -> None:
    """@description: Close a purge run with its count and, on failure, only the error class.
    @param cursor: Transaction-owned cursor.
    @param run_id: Run identifier from start_run.
    @param purged_rows: Rows cleared by the run.
    @param error_class: Exception class name, or None for success.
    @return: None.
    """
    cursor.execute("UPDATE jb2_retention_runs SET finished_at = statement_timestamp(), purged_rows = %s, error_class = %s WHERE run_id = %s",
                   (purged_rows, error_class, run_id))


def retention_status(cursor: Any) -> RetentionStatus:
    """@description: Read retention health from the database clock, without changing anything.
    @param cursor: Transaction-owned cursor.
    @return: Held, overdue and liveness figures.
    """
    cursor.execute("SELECT count(*) FILTER (WHERE purged_at IS NULL) AS held, count(*) FILTER (WHERE purged_at IS NULL AND expires_at <= statement_timestamp()) AS overdue, "
                   "EXTRACT(EPOCH FROM statement_timestamp() - min(expires_at) FILTER (WHERE purged_at IS NULL AND expires_at <= statement_timestamp())) / 86400 AS oldest "
                   "FROM jb2_api_observations")
    row = cursor.fetchone()
    cursor.execute("SELECT EXTRACT(EPOCH FROM statement_timestamp() - max(finished_at)) / 86400 AS age FROM jb2_retention_runs WHERE error_class IS NULL AND finished_at IS NOT NULL")
    last = cursor.fetchone()["age"]
    return RetentionStatus(row["held"], row["overdue"], None if row["oldest"] is None else float(row["oldest"]), None if last is None else float(last))


def revalidation_rows(cursor: Any, limit: int, excluded_video_ids: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """@description: List pending reviews that rest only on an expired API conclusion, oldest first, so the sweep can ask the provider again.
    @param cursor: Transaction-owned cursor.
    @param limit: Maximum rows read.
    @param excluded_video_ids: Videos the sweep is pausing.
    @return: Pairs of operation and video identifiers, one per video, in acceptance order.
    """
    rows = fetch_all(cursor, "SELECT e.operation_id, e.video_id FROM jb2_events e JOIN jb2_videos v ON v.video_id = e.video_id "
                             f"WHERE v.review_status = %s AND (v.review_json::jsonb #> '{{value,fields,duration_verdict}}') IS NOT NULL "
                             "AND (v.review_json::jsonb #>> '{value,fields,duration}') IS NULL AND NOT (e.video_id = ANY(%s::text[])) "
                             f"AND NOT EXISTS (SELECT 1 FROM jb2_api_observations o WHERE o.video_id = e.video_id AND o.{VALID_SQL}) "
                             "ORDER BY e.accepted_sequence LIMIT %s", (ReviewStatus.PENDING_REVIEW.value, list(excluded_video_ids), limit))
    first: dict[str, str] = {}
    for row in rows:
        first.setdefault(row["video_id"], row["operation_id"])
    return [(operation_id, video_id) for video_id, operation_id in first.items()]
