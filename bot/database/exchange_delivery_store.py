"""Persist the delivery and release state of exchange reservations inside short locked transactions.

Every transition is conditional on its expected prior state and, for an attempt, on the claim token that made it, so a late or repeated result cannot overwrite a newer terminal state; network calls never run here.
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from bot.database.compensation_schema import require_schema
from bot.database.reward_settlement import lock_reconciled_account

DELIVERY_LEASE = timedelta(minutes=10)  # [single-process lease]: A sending row younger than this may still be owned by a live worker.


class DeliveryStateMismatch(RuntimeError):
    """Refuse a delivery or refund transition whose expected prior state no longer holds."""


class RefundContradiction(RuntimeError):
    """Stop when the original debit and the reservation disagree, instead of repairing the ledger by guesswork."""


@dataclass(frozen=True)
class DeliveryClaim:
    """Carry the facts a DM sender needs after the reservation has been claimed as sending."""

    recommendation_id: int  # [claimed reservation]: The row now owned by this delivery attempt.
    claim_token: str  # [attempt ownership]: Required by the only transition that may record this attempt's result.
    discord_user_id: int  # [recipient]: The reserved requester, never a Video ID.
    youtube_url: str  # [delivery link]: Verified at reservation time and never recomputed here.


def claim_reservation(cursor: Any, recommendation_id: int, claim_token: str, now: datetime, guard_incidents: bool = False) -> tuple[str, int | None, DeliveryClaim | None]:
    """@description: Move one reserved recommendation to sending under this attempt's token, or report its current state without any new send.
    @param cursor: Cursor inside the locked transaction.
    @param recommendation_id: Reservation identifier from the exchange result.
    @param claim_token: Unique token of this attempt.
    @param now: Time the attempt started, used by the restart lease.
    @param guard_incidents: Refuse the claim while the reservation belongs to a compensation incident; set when the switch is on or the compensation tables exist.
    @return: Current status, message identifier, and a claim only when this call moved it to sending.
    """
    cursor.execute("SELECT r.status,r.discord_message_id,r.discord_user_id,s.youtube_url FROM recommendations r "
                   "JOIN songs s ON s.song_id=r.song_id WHERE r.recommendation_id=%s FOR UPDATE", (recommendation_id,))
    row = cursor.fetchone()  # [locked reservation]: Status and link are read under the same gate as the transition.
    if row is None:
        raise ValueError("Unknown exchange reservation")
    if row["status"] != "reserved":
        return row["status"], row["discord_message_id"], None
    if guard_incidents:
        require_schema(cursor)  # [explicit]: A guarded claim never proceeds on a schema without the incident table.
    if guard_incidents and _has_incident(cursor, recommendation_id):
        _record_hold(cursor, recommendation_id, now)  # [audit]: Every held claim leaves an event; the incident row is the only link.
        return "held_for_review", None, None  # [Owner decision pending or not a system fault]: The original order is never claimed while an incident exists; nothing is sent.
    cursor.execute("UPDATE recommendations SET status='sending',claim_token=%s,claimed_at=%s "
                   "WHERE recommendation_id=%s AND status='reserved'", (claim_token, now, recommendation_id))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Reservation changed during claim")  # [atomic claim]: Never a silent second sender.
    return "sending", None, DeliveryClaim(recommendation_id, claim_token, row["discord_user_id"], row["youtube_url"])


def _record_hold(cursor: Any, recommendation_id: int, now: datetime) -> None:
    """@description: Append one audit event to the incident of a held reservation, inside the same gated transaction as the refusal.
    @param cursor: Cursor inside the locked transaction.
    @param recommendation_id: Reservation whose claim was held.
    @param now: Time of the refusal, naive UTC.
    @return: None.
    """
    cursor.execute("INSERT INTO exchange_incident_events (incident_id, action, actor, at, detail) "
                   "SELECT incident_id, 'claim_held', NULL, %s, 'original_order' FROM exchange_incidents WHERE recommendation_id = %s",
                   (now, recommendation_id))


def _has_incident(cursor: Any, recommendation_id: int) -> bool:
    """@description: Tell whether the reservation belongs to a compensation incident, checked inside the claim's gated transaction.
    @param cursor: Cursor inside the locked transaction.
    @param recommendation_id: Reservation under claim.
    @return: True when any incident, in any state, refers to this reservation.
    """
    cursor.execute("SELECT 1 FROM exchange_incidents WHERE recommendation_id = %s LIMIT 1", (recommendation_id,))
    return cursor.fetchone() is not None


def record_result(cursor: Any, claim: DeliveryClaim, status: str, message_id: int | None, delivered_at: Any) -> str:
    """@description: Record one attempt's outcome only while that exact attempt still owns the sending state.
    @param cursor: Cursor inside the locked transaction.
    @param claim: Claim returned to the caller that performed the send.
    @param status: Target state: delivered, failed or uncertain.
    @param message_id: Accepted DM identifier when delivered, otherwise None.
    @param delivered_at: Delivery time when delivered, otherwise None.
    @return: The status the database holds after the call; a late result never overwrites a newer state.
    """
    cursor.execute("UPDATE recommendations SET status=%s,discord_message_id=%s,delivered_at=%s "
                   "WHERE recommendation_id=%s AND status='sending' AND claim_token=%s",
                   (status, message_id, delivered_at, claim.recommendation_id, claim.claim_token))
    if cursor.rowcount == 1:
        return status
    cursor.execute("SELECT status FROM recommendations WHERE recommendation_id=%s", (claim.recommendation_id,))
    return cursor.fetchone()["status"]  # [late result]: The newer terminal state stands and nothing is rewritten.


def recover_interrupted(cursor: Any, now: datetime) -> int:
    """@description: Mark attempts whose sending lease has expired as uncertain, never resending them and never touching a fresh claim.
    @param cursor: Cursor inside the locked transaction.
    @param now: Current time supplied by the caller.
    @return: Number of reservations moved to uncertain.
    """
    cursor.execute("UPDATE recommendations SET status='uncertain' WHERE status='sending' AND claimed_at<%s", (now - DELIVERY_LEASE,))
    return cursor.rowcount


@dataclass(frozen=True)
class RefundOutcome:
    """Name the single refund result so repeated calls can be told apart from first ones."""

    status: str  # [refund state]: refunded, already_released, or not_refundable.
    amount: int  # [refund amount]: Taken from the original debit, never from the caller.


def refund_undelivered(cursor: Any, recommendation_id: int, now: datetime) -> RefundOutcome:
    """@description: Refund one point for a reservation proven unsent, releasing the song in the same commit; anything else writes nothing.
    @param cursor: Cursor inside the locked transaction.
    @param recommendation_id: Reservation whose original debit is the only authority for amount and recipient.
    @param now: Release time recorded on the reservation.
    @return: refunded for the first success, already_released for any repeat, not_refundable for every non-failed state.
    """
    cursor.execute("SELECT status,discord_user_id,released_at FROM recommendations WHERE recommendation_id=%s FOR UPDATE", (recommendation_id,))
    row = cursor.fetchone()  # [locked reservation]: The same gate covers the ledger reads and the release.
    if row is None:
        raise ValueError("Unknown exchange reservation")
    if row["released_at"] is not None:
        return RefundOutcome("already_released", 0)  # [once only]: The release marker is the durable refund receipt.
    if row["status"] != "failed":
        return RefundOutcome("not_refundable", 0)  # [proof required]: Sending, uncertain, delivered and unknown never refund.
    debit = _single_debit(cursor, recommendation_id, row["discord_user_id"])  # [authoritative debit]: Recipient and amount come from it.
    lock_reconciled_account(cursor, row["discord_user_id"])  # [balance guard]: Refuse to compound any preexisting mismatch.
    amount = -debit["credit_delta"]  # [refund amount]: Exactly the original debit's size.
    key = "refund:" + hashlib.sha256(str(recommendation_id).encode("ascii")).hexdigest()  # [ledger idempotency]: Bounded and deterministic.
    cursor.execute("INSERT INTO credit_ledger (discord_user_id,credit_delta,reason,idempotency_key,recommendation_id) "
                   "VALUES (%s,%s,%s,%s,%s)", (row["discord_user_id"], amount, "delivery_refund", key, recommendation_id))
    cursor.execute("UPDATE users SET credit_balance=credit_balance+%s WHERE discord_user_id=%s", (amount, row["discord_user_id"]))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Refund recipient disappeared")  # [atomic rollback]: The refund row is not kept alone.
    cursor.execute("UPDATE recommendations SET released_at=%s WHERE recommendation_id=%s AND status='failed' AND released_at IS NULL",
                   (now, recommendation_id))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Reservation release changed during refund")
    return RefundOutcome("refunded", amount)


def _single_debit(cursor: Any, recommendation_id: int, recipient: int) -> dict:
    """@description: Read the one original exchange debit for a reservation, refusing any contradiction instead of inferring one.
    @param cursor: Cursor inside the locked transaction.
    @param recommendation_id: Reservation identifier.
    @param recipient: Recipient recorded on the reservation.
    @return: The original debit ledger row.
    """
    cursor.execute("SELECT ledger_id,discord_user_id,credit_delta,reason FROM credit_ledger WHERE recommendation_id=%s", (recommendation_id,))
    rows = cursor.fetchall()  # [ledger facts]: Every entry that references this reservation.
    debits = [row for row in rows if row["reason"] == "exchange_debit"]
    refunds = [row for row in rows if row["reason"] == "delivery_refund"]
    if len(debits) != 1 or refunds or debits[0]["discord_user_id"] != recipient or debits[0]["credit_delta"] != -1:
        raise RefundContradiction("Reservation and original debit disagree")  # [stop]: No repair by guesswork.
    return debits[0]


PENDING_REFUND_BATCH = 20  # [sweep bound]: Refund retries per request stay small and resumable.


def pending_refund_ids(cursor: Any) -> list[int]:
    """@description: Find proven-unsent reservations whose refund has not yet committed, so a failed refund is retried instead of forgotten.
    @param cursor: Cursor inside the locked transaction.
    @return: Reservation identifiers awaiting refund, oldest first.
    """
    cursor.execute("SELECT recommendation_id FROM recommendations WHERE status='failed' AND released_at IS NULL "
                   "ORDER BY recommendation_id LIMIT %s", (PENDING_REFUND_BATCH,))  # [durable work queue]: The row is the record.
    return [row["recommendation_id"] for row in cursor.fetchall()]


def user_results(cursor: Any, discord_user_id: int) -> list[dict]:
    """@description: Project one requester's own exchange results without tokens, links or other users' facts.
    @param cursor: Cursor inside the transaction.
    @param discord_user_id: Requester whose results are shown.
    @return: Operation code, delivery state and financial state per reservation, oldest first.
    """
    cursor.execute("SELECT recommendation_id,song_id,status,released_at FROM recommendations WHERE discord_user_id=%s", (discord_user_id,))
    rows = sorted(cursor.fetchall(), key=lambda row: row["recommendation_id"])  # [caller scope]: Only this user's rows are selected.
    return [{"operation_code": row["recommendation_id"], "song_id": row["song_id"], "delivery_state": row["status"],
             "refund_state": _refund_state(row)} for row in rows]


def user_balance(cursor: Any, discord_user_id: int) -> int | None:
    """@description: Read one requester's own spendable credits, or None when no account exists.
    @param cursor: Cursor inside the transaction.
    @param discord_user_id: Requester whose balance is shown.
    @return: Credits held by that account, or None for an unknown account.
    """
    cursor.execute("SELECT credit_balance FROM users WHERE discord_user_id=%s", (discord_user_id,))
    row = cursor.fetchone()  # [caller scope]: Only the caller's own row is selected.
    return None if row is None else row["credit_balance"]


def owner_summary(cursor: Any) -> dict:
    """@description: Count exchange reservations by delivery and refund state for the Owner, without any identifier that grants access.
    @param cursor: Cursor inside the transaction.
    @return: Counts per delivery state plus the number of unrefunded failures.
    """
    cursor.execute("SELECT status,released_at FROM recommendations")
    summary = {"reserved": 0, "sending": 0, "delivered": 0, "failed": 0, "uncertain": 0, "refund_pending": 0}
    for row in cursor.fetchall():  # [aggregate only]: No row is exposed to the Owner view.
        summary[row["status"]] += 1
        if row["status"] == "failed" and row["released_at"] is None:
            summary["refund_pending"] += 1
    return summary


def _refund_state(row: dict) -> str:
    """@description: Derive the financial state from the release marker, which is written only by a committed refund.
    @param row: Projected reservation row.
    @return: refunded when released, pending when proven unsent but not released, otherwise none.
    """
    if row["released_at"] is not None:
        return "refunded"
    return "pending" if row["status"] == "failed" else "none"
