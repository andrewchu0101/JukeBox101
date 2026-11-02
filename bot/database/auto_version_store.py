"""Read the recent-song window and write the system's version confirmation inside the automatic review transaction.

Inputs are a locked cursor and a verdict computed before the transaction began; outputs are an in-memory comparison description and, only for a still-valid clean comparison, system-judged version evidence.
Nothing here reads or writes an official value of any existing song; only video IDs, sequence numbers and the new video's own system judgement are used.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from bot.auto_review import AutoReviewVerdict
from bot.auto_version_review import stale_verdict, system_version_evidence
from bot.database.persisted_snapshot import fetch_all
from bot.database.review_persistence import VersionConfirmationCommand, apply_version_confirmation
from bot.evidence_record import SYSTEM_JUDGE_ID
from bot.review import ApprovalSource, ReviewSnapshot, ReviewStatus


@dataclass(frozen=True)
class RecentComparison:
    """Describe the existing songs a new submission is compared with and the newest accepted submission at that moment."""

    video_ids: tuple[str, ...]  # [comparison set]: Newest first, each video once, never the candidate.
    last_accepted_sequence: int  # [staleness mark]: Global newest accepted submission; zero before the first.


def read_recent_comparison(cursor: Any, exclude_video_id: str, limit: int) -> RecentComparison:
    """@description: List the most recently submitted videos in every review state, so a new submission can be compared with what the catalog already holds.
    @param cursor: Locked cursor; the read changes nothing.
    @param exclude_video_id: The new video, never compared with itself.
    @param limit: Maximum number of videos.
    @return: Video IDs newest first and the staleness mark.
    """
    rows = fetch_all(cursor, "SELECT video_id FROM jb2_events WHERE video_id <> %s GROUP BY video_id "
                             "ORDER BY MAX(accepted_sequence) DESC LIMIT %s", (exclude_video_id, limit))
    cursor.execute("SELECT COALESCE(MAX(accepted_sequence), 0) AS latest FROM jb2_events")
    return RecentComparison(tuple(row["video_id"] for row in rows), cursor.fetchone()["latest"])


def checked_for_staleness(cursor: Any, verdict: AutoReviewVerdict) -> AutoReviewVerdict:
    """@description: Turn a clean comparison into an Owner review when any submission was accepted after it began, because it did not see that song.
    @param cursor: Locked cursor.
    @param verdict: Verdict computed outside the transaction.
    @return: The same verdict, or the stale-clearance downgrade; a missing staleness mark counts as stale.
    """
    basis = verdict.version_basis
    if basis is None:
        return verdict
    if basis.last_accepted_sequence is not None:
        cursor.execute("SELECT 1 FROM jb2_events WHERE accepted_sequence > %s LIMIT 1", (basis.last_accepted_sequence,))
        if cursor.fetchone() is None:
            return verdict
    return stale_verdict(verdict)


def confirm_version_by_system(cursor: Any, operation_id: str, verdict: AutoReviewVerdict, review: ReviewSnapshot) -> None:
    """@description: Stamp a new recording version for an automatically approved video in the same transaction, as an Owner-authorised system judgement.
    @param cursor: Locked cursor of the approving transaction.
    @param operation_id: Accepted operation.
    @param verdict: Applied verdict; only a clean comparison carries a basis.
    @param review: Review after the approval.
    @return: None; nothing is written unless the review is now approved by the automatic source.
    """
    if verdict.version_basis is None or review.status is not ReviewStatus.APPROVED or review.approval_source is not ApprovalSource.AUTO:
        return
    cursor.execute("SELECT * FROM jb2_control WHERE singleton_id = 1")
    control = cursor.fetchone()  # [fresh revision]: The approval just advanced it; the confirmation must build on the current one.
    evidence = system_version_evidence(review.video, verdict.version_basis.compared_count, datetime.now(timezone.utc))
    command = VersionConfirmationCommand(f"auto-version:{review.video.video_id}:{review.version}", operation_id, SYSTEM_JUDGE_ID,
                                         evidence.reason, review.version, evidence)
    apply_version_confirmation(cursor, control, command)
