"""Persist Owner-confirmed canonical merges, exclusion evidence and daily reminder batches.

Accounting history is immutable; reminders use database UTC and never expire submission priority.
"""

from typing import Any

from bot.database.persisted_snapshot import fetch_all
from bot.database.review_persistence import decision_digest, replay_decision, save_decision
from bot.database.reward_settlement import current_decisions, persist_decisions
from bot.database.reward_history import audit_reward_history
from bot.database.submission_codec import decode_contract, encode_contract
from bot.version_identity import VersionExclusion


def merge_versions(cursor: Any, control: dict, decision_id: str, actor_id: int,
                   reason: str, source: str, target: str) -> tuple[str, str]:
    """@description: Merge evidenced recording identities without rewriting or clawing back previous awards.
    @param cursor: Globally serialized transaction cursor.
    @param control: Locked current read revision.
    @param decision_id: Unique Owner action key.
    @param actor_id: Authenticated configured Owner.
    @param reason: Trusted evidence explanation for the merge.
    @param source: Existing recording identity to merge.
    @param target: Existing canonical identity to retain.
    @return: Audited original source and target roots.
    """
    # [merge idempotency]: Reusing a decision key with different targets is rejected.
    digest = decision_digest((actor_id, reason, source, target))
    previous = replay_decision(cursor, decision_id, digest)
    if previous is not None:
        return decode_contract(previous, tuple)
    # [canonical roots]: Flattened roots avoid ambiguous chains and cycles.
    roots = {row["version_id"]: row["canonical_id"] for row in fetch_all(cursor, "SELECT version_id,canonical_id FROM jb2_versions")}
    if source not in roots or target not in roots:
        raise ValueError("Merge requires two existing evidenced versions")
    result = (roots[source], roots[target])
    if result[0] != result[1]:
        _merge_roots(cursor, control, actor_id, reason, *result)
    save_decision(cursor, decision_id, digest, actor_id, reason, result)
    return result


def _merge_roots(cursor: Any, control: dict, actor_id: int, reason: str, source: str, target: str) -> None:
    """@description: Preserve original claim rows and permanently close a merged rewarded identity.
    @param cursor: Locked transaction cursor.
    @param control: Current read revision.
    @param actor_id: Audited Owner identity.
    @param reason: Evidence-backed merge explanation.
    @param source: Source canonical root.
    @param target: Retained canonical root.
    @return: None.
    """
    # [original settlements]: Retain all ledger and claim links before changing comparison roots.
    claims = fetch_all(cursor, "SELECT c.version_id,c.operation_id,c.ledger_id,c.discord_user_id FROM jb2_claims c "
                       "JOIN jb2_versions v ON v.version_id=c.version_id WHERE v.canonical_id IN (%s,%s)", (source, target))
    cursor.execute("UPDATE jb2_versions SET canonical_id=%s WHERE canonical_id=%s", (target, source))
    cursor.execute("SELECT version_id FROM jb2_barriers WHERE version_id IN (%s,%s)", (source, target))
    if claims or cursor.fetchall():
        cursor.execute("INSERT INTO jb2_barriers (version_id,reason) VALUES (%s,%s) ON CONFLICT (version_id) DO NOTHING", (target, reason))
    audit_reward_history(cursor, actor_id, reason)
    _refresh(cursor, control)


def store_exclusion(cursor: Any, control: dict, decision_id: str, actor_id: int,
                    reason: str, exclusion: VersionExclusion) -> VersionExclusion:
    """@description: Persist trusted negative evidence without treating an exclusion as a new recording identity.
    @param cursor: Globally serialized cursor.
    @param control: Current read revision.
    @param decision_id: Owner action idempotency key.
    @param actor_id: Authenticated Owner identity.
    @param reason: Evidence explanation.
    @param exclusion: Scoped exclusion proof, never inferred from titles.
    @return: Original or newly stored exclusion.
    """
    # [evidence intent]: The proof reference cannot be silently repurposed for another version.
    digest = decision_digest((actor_id, reason, exclusion))
    previous = replay_decision(cursor, decision_id, digest)
    if previous is not None:
        return decode_contract(previous, VersionExclusion)
    cursor.execute("INSERT INTO jb2_exclusions (evidence_reference,video_id,version_id,evidence_json,actor_id,decision_reason) "
                   "VALUES (%s,%s,%s,%s,%s,%s)", (exclusion.evidence_reference, exclusion.video.video_id,
                   exclusion.version.value, encode_contract(exclusion), actor_id, reason))
    _refresh(cursor, control)
    save_decision(cursor, decision_id, digest, actor_id, reason, exclusion)
    return exclusion


def _refresh(cursor: Any, control: dict) -> None:
    """@description: Advance scope revision and recompute freezes within the same transaction.
    @param cursor: Globally serialized transaction cursor.
    @param control: Locked database state before the mutation.
    @return: None.
    """
    cursor.execute("UPDATE jb2_control SET revision=revision+1 WHERE singleton_id=1")
    persist_decisions(cursor, current_decisions(cursor, dict(control, revision=control["revision"] + 1)))


def pending_summary(cursor: Any, owner_id: int) -> tuple[str, ...]:
    """@description: Reserve at most one durable UTC-day Owner summary without sending Discord messages.
    @param cursor: Globally serialized transaction cursor using database time.
    @param owner_id: Configured authenticated Owner target retained in the summary audit.
    @return: Stable operation IDs for the day's original summary, or an empty tuple when none are due.
    """
    cursor.execute("SELECT statement_timestamp() AS now_utc, (statement_timestamp() AT TIME ZONE 'UTC')::date AS summary_date")
    clock = cursor.fetchone()  # [database clock]: Never use a caller timestamp to advance or expire priority.
    cursor.execute("SELECT operation_ids,owner_discord_user_id FROM jb2_reminder_batches WHERE summary_date=%s", (clock["summary_date"],))
    existing = cursor.fetchone()  # [daily idempotency]: A repeated poll reuses the original summary.
    if existing is not None:
        if existing["owner_discord_user_id"] != owner_id:
            raise PermissionError("Reminder target changed; original batch requires review")
        return decode_contract(existing["operation_ids"], tuple)
    # [due pending operations]: First due time is intake +24h; no query changes accepted_sequence.
    rows = fetch_all(cursor, "SELECT e.operation_id FROM jb2_events e JOIN jb2_videos v ON v.video_id=e.video_id "
                     "JOIN jb2_reminders r ON r.video_id=v.video_id WHERE r.next_due_at<=%s "
                     "AND (v.review_status IN (%s,%s,%s) OR e.reward_status IN (%s,%s)) ORDER BY e.accepted_sequence",
                     (clock["now_utc"], "pending_validation", "pending_review", "validation_failed", "waiting_identity", "conflict"))
    operations = tuple(row["operation_id"] for row in rows)  # [summary receipt]: Persist even if the later sender fails.
    if operations:
        _reserve_summary(cursor, clock, operations, owner_id)
    return operations


def _reserve_summary(cursor: Any, clock: dict, operations: tuple[str, ...], owner_id: int) -> None:
    """@description: Persist reminder scheduling separately from any eventual Discord delivery.
    @param cursor: Transaction-owned cursor.
    @param clock: UTC database time and date.
    @param operations: Original ordered pending-operation summary.
    @param owner_id: Configured Owner target recorded with the immutable batch.
    @return: None.
    """
    cursor.execute("INSERT INTO jb2_reminder_batches (summary_date,operation_ids,owner_discord_user_id) VALUES (%s,%s,%s)",
                   (clock["summary_date"], encode_contract(operations), owner_id))
    for operation_id in operations:
        cursor.execute("UPDATE jb2_reminders r SET last_summary_date=%s, next_due_at=%s::date + INTERVAL '1 day' "
                       "FROM jb2_events e WHERE e.video_id=r.video_id AND e.operation_id=%s",
                       (clock["summary_date"], clock["summary_date"], operation_id))
