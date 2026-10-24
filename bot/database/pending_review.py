"""Identify stranded reserved exchanges and block the same requester's new exchanges until the Owner resolves them.

A reservation is stranded only when it is still reserved, was never claimed for delivery, is not released, and was created before
now - STRANDED_AFTER. Both queries run inside the writer-gate transaction, so they cannot interleave with a claim or a new reservation.
Nothing here changes money or delivery state; resolution is a later, separately approved step.
"""

from datetime import datetime
from typing import Any

from bot.database.exchange_delivery_store import DELIVERY_LEASE

STRANDED_AFTER = DELIVERY_LEASE  # [time basis]: The same bound as the sending lease; no in-request step between reserve and claim reaches it.
OWNER_LIST_LIMIT = 50  # [bounded review]: The Owner list is one page, never an unbounded export.
NOT_DECLINED = " AND NOT EXISTS (SELECT 1 FROM exchange_incidents i WHERE i.recommendation_id = recommendations.recommendation_id AND i.state = 'not_system_fault')"  # [Owner decision]: A reservation judged not a system fault no longer blocks or lists as stranded.


def stranded_before(now: datetime) -> datetime:
    """@description: Name the creation cutoff below which an unclaimed reservation counts as stranded.
    @param now: Current time supplied by the caller, naive UTC.
    @return: Strict upper bound for the creation time of a stranded reservation.
    """
    return now - STRANDED_AFTER


def has_stranded_reservation(cursor: Any, discord_user_id: int, now: datetime) -> bool:
    """@description: Decide whether this requester must be refused a new exchange because an old, never-claimed reservation exists.
    @param cursor: Cursor inside the writer-gate transaction.
    @param discord_user_id: Requesting user.
    @param now: Current time, naive UTC.
    @return: True when at least one stranded reservation belongs to the requester.
    """
    cursor.execute("SELECT COUNT(*) AS total FROM recommendations WHERE discord_user_id=%s AND status='reserved' "
                   "AND claim_token IS NULL AND released_at IS NULL AND created_at<%s" + NOT_DECLINED, (discord_user_id, stranded_before(now)))
    return cursor.fetchone()["total"] > 0


def owner_stranded_rows(cursor: Any, now: datetime) -> list[dict]:
    """@description: List stranded reservations for the Owner only, with the minimum fields needed to act and no link, token or balance.
    @param cursor: Cursor inside a transaction on the writer gate.
    @param now: Current time, naive UTC.
    @return: One dictionary per stranded reservation, oldest first, bounded by OWNER_LIST_LIMIT.
    """
    cursor.execute("SELECT recommendation_id,discord_user_id,song_id,created_at FROM recommendations WHERE status='reserved' "
                   "AND claim_token IS NULL AND released_at IS NULL AND created_at<%s" + NOT_DECLINED + " ORDER BY recommendation_id LIMIT %s",
                   (stranded_before(now), OWNER_LIST_LIMIT))
    return [{"operation_code": row["recommendation_id"], "requester": row["discord_user_id"],
             "song_id": row["song_id"], "reserved_at": row["created_at"]} for row in cursor.fetchall()]
