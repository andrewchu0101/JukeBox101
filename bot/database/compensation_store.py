"""Store the Owner-confirmed compensation for stranded, never-claimed reservations: refund of the bound original debit and one free redelivery grant.

Each incident binds one reservation and its one original debit, so retries and different request keys cannot open a second incident for the
same debit. Settlement refunds that debit once and issues one free redelivery grant once; the refund and the grant are separate records and
no zero-amount ledger row is written. There is no per-user quota or time window. Every function runs inside a submission transaction that
already holds the writer gate, so approvals, settlements and claims are serialised. Nothing here sends a direct message.
"""

import hashlib
from datetime import datetime, timedelta
from typing import Any

from bot.database.exchange_delivery_store import DELIVERY_LEASE, DeliveryStateMismatch, RefundContradiction
from bot.database.pending_review import has_stranded_reservation, stranded_before

LIST_LIMIT = 50  # [bounded owner view]: One page of rows per request.
SUMMARY_LIMIT = 10  # [bounded owner view]: The most recent incidents of one requester.
FACT_BY_RESULT = {"sent": ("sent", "sent"), "not_sent": ("not_sent", "not_sent"), "unknown": ("uncertain", "unknown")}  # [result -> (state, fact)].
CLOSABLE_STATES = {"not_sent": ("no_further_action",), "uncertain": ("no_further_action", "confirmed_delivered", "confirmed_not_delivered")}
OVERDUE_AFTER = timedelta(hours=24)  # [Owner list only]: An open grant older than this is flagged for the Owner; nothing else changes.


class ApprovalRefused(RuntimeError):
    """Refuse an approval, decline or settlement whose preconditions do not hold; the message names the reason code only."""


class DispositionRefused(RuntimeError):
    """Refuse an owner disposition that does not match the grant's delivery state or was already recorded."""


def _event(cursor: Any, incident_id: int, action: str, actor: int | None, now: datetime, detail: str) -> None:
    """@description: Append one audit event for an incident; events are never updated or deleted.
    @param cursor: Cursor inside the gated transaction.
    @param incident_id: Incident the event belongs to.
    @param action: Event name.
    @param actor: Owner identity, or None for an automatic transition.
    @param now: Event time, naive UTC.
    @param detail: Short reason or result code; never a link, token or balance.
    @return: None.
    """
    cursor.execute("INSERT INTO exchange_incident_events (incident_id, action, actor, at, detail) VALUES (%s, %s, %s, %s, %s)",
                   (incident_id, action, actor, now, detail))


def _is_stranded(cursor: Any, recommendation_id: int, now: datetime) -> bool:
    """@description: Confirm one reservation is still reserved, never claimed, not released, and older than the threshold.
    @param cursor: Cursor inside the gated transaction.
    @param recommendation_id: Reservation under decision.
    @param now: Current time, naive UTC.
    @return: True only for a reservation that has never been dispatched.
    """
    cursor.execute("SELECT recommendation_id FROM recommendations WHERE recommendation_id = %s AND status = 'reserved' "
                   "AND claim_token IS NULL AND released_at IS NULL AND created_at < %s", (recommendation_id, stranded_before(now)))
    return cursor.fetchone() is not None


def _legitimate_debit(cursor: Any, recommendation_id: int, user: int) -> int:
    """@description: Return the ledger id of the single original debit of one reservation, refusing any other shape explicitly.
    @param cursor: Cursor inside the gated transaction.
    @param recommendation_id: Reservation whose debit is bound.
    @param user: Recipient recorded on the reservation.
    @return: Ledger identifier of the original debit.
    """
    cursor.execute("SELECT ledger_id, discord_user_id, credit_delta, reason FROM credit_ledger WHERE recommendation_id = %s", (recommendation_id,))
    rows = cursor.fetchall()
    debits = [row for row in rows if row["reason"] == "exchange_debit"]
    refunds = [row for row in rows if row["reason"] == "delivery_refund"]
    if len(debits) != 1 or refunds or debits[0]["discord_user_id"] != user or debits[0]["credit_delta"] != -1:
        raise RefundContradiction("Reservation and original debit disagree; incident refused")  # [explicit]: Never repaired by guessing.
    return debits[0]["ledger_id"]


def approve_incident(cursor: Any, recommendation_id: int, owner_id: int, reason_code: str, now: datetime) -> dict:
    """@description: Record the Owner's confirmation of a system fault for one stranded reservation, bound to its one original debit; repeats return the first incident.
    @param cursor: Cursor inside the gated transaction.
    @param recommendation_id: Reservation to compensate.
    @param owner_id: Authorised Owner identity, already checked by the caller.
    @param reason_code: Reviewed reason code.
    @param now: Current time, naive UTC.
    @return: Incident identifier, its state, and whether this call created it.
    """
    cursor.execute("SELECT incident_id, state FROM exchange_incidents WHERE recommendation_id = %s", (recommendation_id,))
    existing = cursor.fetchone()  # [one incident per reservation]: The unique key makes a repeat a read, not a write.
    if existing is not None:
        return {"incident_id": existing["incident_id"], "state": existing["state"], "created": False}
    if not _is_stranded(cursor, recommendation_id, now):
        raise ApprovalRefused("NOT_STRANDED")
    return _insert_incident(cursor, recommendation_id, owner_id, reason_code, now, "approved")


def _insert_incident(cursor: Any, recommendation_id: int, owner_id: int, reason_code: str, now: datetime, state: str) -> dict:
    """@description: Insert one incident bound to the reservation's single lawful debit, refusing before any write when the debit is not lawful.
    @param cursor: Cursor inside the gated transaction.
    @param recommendation_id: Stranded reservation.
    @param owner_id: Authorised Owner identity.
    @param reason_code: Reviewed reason code.
    @param now: Current time, naive UTC.
    @param state: Initial state, approved or not_system_fault.
    @return: Incident identifier, state and creation flag.
    """
    cursor.execute("SELECT discord_user_id, song_id FROM recommendations WHERE recommendation_id = %s", (recommendation_id,))
    row = cursor.fetchone()
    debit = _legitimate_debit(cursor, recommendation_id, row["discord_user_id"])  # [binding]: Refused before any write when the debit is not lawful.
    cursor.execute("INSERT INTO exchange_incidents (recommendation_id, debit_ledger_id, discord_user_id, song_id, reason_code, approved_by, approved_at, state) "
                   "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                   (recommendation_id, debit, row["discord_user_id"], row["song_id"], reason_code, owner_id, now, state))  # [open at creation]: closed_at stays empty until settlement or delivery closes it.
    incident = cursor.lastrowid
    _event(cursor, incident, state, owner_id, now, reason_code)
    return {"incident_id": incident, "state": state, "created": True}


def record_not_system_fault(cursor: Any, recommendation_id: int, owner_id: int, reason_code: str, now: datetime) -> dict:
    """@description: Record that the Owner judged a stranded reservation not to be a system fault. No refund and no redelivery follow; the debit stays, the requester is unblocked, and the order stays in the Owner's list until the Owner decides it.
    @param cursor: Cursor inside the gated transaction.
    @param recommendation_id: Reservation judged not to be a system fault.
    @param owner_id: Authorised Owner identity, already checked by the caller.
    @param reason_code: Reviewed reason code.
    @param now: Current time, naive UTC.
    @return: Incident identifier and state.
    """
    cursor.execute("SELECT incident_id, state FROM exchange_incidents WHERE recommendation_id = %s", (recommendation_id,))
    existing = cursor.fetchone()
    if existing is not None:
        if existing["state"] == "not_system_fault":
            return {"incident_id": existing["incident_id"], "state": "not_system_fault", "created": False}
        raise ApprovalRefused("ALREADY_DECIDED")  # [explicit]: An approved incident is never turned into a non-fault after the fact.
    if not _is_stranded(cursor, recommendation_id, now):
        raise ApprovalRefused("NOT_STRANDED")
    return _insert_incident(cursor, recommendation_id, owner_id, reason_code, now, "not_system_fault")


def settle_incident(cursor: Any, incident_id: int, redelivery_approved: bool, now: datetime) -> dict:
    """@description: Atomically refund the bound original debit once, then either issue the one free redelivery grant or close as refund only; a repeated settlement changes nothing.
    @param cursor: Cursor inside the gated transaction.
    @param incident_id: Approved incident.
    @param redelivery_approved: The Owner's separate decision on free redelivery; declining it never cancels the refund.
    @param now: Current time, naive UTC.
    @return: Outcome, the refund ledger identifier and the grant identifier, if any.
    """
    incident = _load_incident(cursor, incident_id)
    if incident["state"] == "settled":
        return {"outcome": "already_settled", "refund_ledger_id": incident["refund_ledger_id"], "grant_id": _grant_of(cursor, incident_id)}
    if incident["state"] != "approved":
        raise ApprovalRefused("NOT_SETTLEABLE")
    if not _is_stranded(cursor, incident["recommendation_id"], now):
        return _void(cursor, incident_id, now)
    refund = _refund_original_debit(cursor, incident, now)
    grant = _issue_grant(cursor, incident, now) if redelivery_approved else None
    closure = "refund_and_redelivery" if redelivery_approved else "refund_only"
    _close_settlement(cursor, incident_id, refund, redelivery_approved, closure, now)
    return {"outcome": "settled", "refund_ledger_id": refund, "grant_id": grant}


def _load_incident(cursor: Any, incident_id: int) -> dict:
    """@description: Read one incident for settlement, refusing an unknown identifier explicitly.
    @param cursor: Cursor inside the gated transaction.
    @param incident_id: Incident identifier.
    @return: The incident row.
    """
    cursor.execute("SELECT * FROM exchange_incidents WHERE incident_id = %s", (incident_id,))
    incident = cursor.fetchone()
    if incident is None:
        raise ApprovalRefused("UNKNOWN_INCIDENT")
    return incident


def _grant_of(cursor: Any, incident_id: int) -> int | None:
    """@description: Return the grant issued for an incident, if any; the grant table is the single place the link lives.
    @param cursor: Cursor inside the gated transaction.
    @param incident_id: Incident identifier.
    @return: Grant identifier, or None.
    """
    cursor.execute("SELECT grant_id FROM exchange_redelivery_grants WHERE incident_id = %s", (incident_id,))
    row = cursor.fetchone()
    return row["grant_id"] if row is not None else None


def _void(cursor: Any, incident_id: int, now: datetime) -> dict:
    """@description: Void an approval whose reservation was claimed or released before settlement; nothing is refunded and no grant is issued.
    @param cursor: Cursor inside the gated transaction.
    @param incident_id: Approved incident.
    @param now: Current time, naive UTC.
    @return: Void outcome.
    """
    cursor.execute("UPDATE exchange_incidents SET state = 'void', closed_at = %s WHERE incident_id = %s AND state = 'approved'", (now, incident_id))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Incident changed during void")
    _event(cursor, incident_id, "void", None, now, "claimed_or_released_before_settlement")
    return {"outcome": "void", "refund_ledger_id": None, "grant_id": None}


def _refund_original_debit(cursor: Any, incident: dict, now: datetime) -> int:
    """@description: Refund exactly the bound original debit through the shared idempotency key and release the reservation.
    @param cursor: Cursor inside the gated transaction.
    @param incident: Approved incident row.
    @param now: Current time, naive UTC.
    @return: Ledger identifier of the refund.
    """
    recommendation_id, user = incident["recommendation_id"], incident["discord_user_id"]
    if _legitimate_debit(cursor, recommendation_id, user) != incident["debit_ledger_id"]:
        raise RefundContradiction("Bound debit changed since approval; settlement refused")  # [binding check]: The stored debit is the only one refunded.
    key = "refund:" + hashlib.sha256(str(recommendation_id).encode("ascii")).hexdigest()  # [shared refund key]: The existing path can never refund this reservation again.
    cursor.execute("INSERT INTO credit_ledger (discord_user_id, credit_delta, reason, idempotency_key, recommendation_id, created_at) "
                   "VALUES (%s, 1, 'delivery_refund', %s, %s, %s)", (user, key, recommendation_id, now))
    refund = cursor.lastrowid
    cursor.execute("UPDATE users SET credit_balance = credit_balance + 1 WHERE discord_user_id = %s", (user,))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Refund recipient disappeared")
    cursor.execute("UPDATE recommendations SET status = 'failed', released_at = %s WHERE recommendation_id = %s AND status = 'reserved' "
                   "AND claim_token IS NULL AND released_at IS NULL", (now, recommendation_id))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Reservation changed during settlement")
    return refund


def _issue_grant(cursor: Any, incident: dict, now: datetime) -> int:
    """@description: Issue the one free redelivery grant of an incident; the unique incident key makes a second grant impossible.
    @param cursor: Cursor inside the gated transaction.
    @param incident: Approved incident row.
    @param now: Current time, naive UTC.
    @return: Grant identifier.
    """
    cursor.execute("INSERT INTO exchange_redelivery_grants (incident_id, discord_user_id, song_id, granted_at, state, holds_song) "
                   "VALUES (%s, %s, %s, %s, 'open', 1)", (incident["incident_id"], incident["discord_user_id"], incident["song_id"], now))
    return cursor.lastrowid


def _close_settlement(cursor: Any, incident_id: int, refund: int, redelivery_approved: bool, closure: str, now: datetime) -> None:
    """@description: Mark the incident settled with its refund and redelivery decision, in the same transaction as the money movement; a refund-only settlement is closed at once.
    @param cursor: Cursor inside the gated transaction.
    @param incident_id: Approved incident.
    @param refund: Refund ledger identifier.
    @param redelivery_approved: Whether a redelivery grant was issued.
    @param closure: refund_only or refund_and_redelivery.
    @param now: Current time, naive UTC.
    @return: None; a changed incident aborts the transaction.
    """
    decision = "approved" if redelivery_approved else "not_approved"
    closed_at = None if redelivery_approved else now  # [refund-only closure]: Nothing else can happen to this incident.
    cursor.execute("UPDATE exchange_incidents SET state = 'settled', refund_status = 'refunded_once', refund_ledger_id = %s, "
                   "redelivery_decision = %s, settled_at = %s, closure = %s, closed_at = %s WHERE incident_id = %s AND state = 'approved'",
                   (refund, decision, now, closure, closed_at, incident_id))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Incident changed during settlement")
    _event(cursor, incident_id, "settled", None, now, closure)


def close_non_fault_refund(cursor: Any, incident_id: int, owner_id: int, confirmed_undelivered: bool, now: datetime) -> dict:
    """@description: Close a not-system-fault incident by refunding its bound original debit once, only when the Owner confirmed the order was never delivered and the reservation was never claimed; no grant is issued and a repeat changes nothing.
    @param cursor: Cursor inside the gated transaction.
    @param incident_id: Not-system-fault incident.
    @param owner_id: Authorised Owner identity, already checked by the caller.
    @param confirmed_undelivered: The Owner's explicit confirmation that the original order was not delivered; there is no default.
    @param now: Current time, naive UTC.
    @return: Outcome, the refund ledger identifier and no grant.
    """
    incident = _load_incident(cursor, incident_id)
    if incident["state"] == "non_fault_refunded":
        return {"outcome": "already_closed", "refund_ledger_id": incident["refund_ledger_id"], "grant_id": None}
    if incident["state"] != "not_system_fault":
        raise ApprovalRefused("NOT_NON_FAULT")
    if not confirmed_undelivered:
        raise ApprovalRefused("NOT_CONFIRMED_UNDELIVERED")  # [explicit]: Without proof of non-delivery the debit is never returned by this path.
    if not _is_stranded(cursor, incident["recommendation_id"], now):
        raise ApprovalRefused("NOT_STRANDED")  # [explicit]: A claimed, uncertain or released reservation may have been delivered; it is never refunded here.
    refund = _refund_original_debit(cursor, incident, now)
    _close_non_fault(cursor, incident_id, owner_id, refund, now)
    return {"outcome": "closed_refunded", "refund_ledger_id": refund, "grant_id": None}


def _close_non_fault(cursor: Any, incident_id: int, owner_id: int, refund: int, now: datetime) -> None:
    """@description: Mark a not-system-fault incident closed with its refund, in the same transaction as the money movement; the closure is audited and no grant exists.
    @param cursor: Cursor inside the gated transaction.
    @param incident_id: Not-system-fault incident.
    @param owner_id: Authorised Owner identity.
    @param refund: Refund ledger identifier.
    @param now: Current time, naive UTC.
    @return: None; a changed incident aborts the transaction.
    """
    cursor.execute("UPDATE exchange_incidents SET state = 'non_fault_refunded', refund_status = 'refunded_once', refund_ledger_id = %s, "
                   "redelivery_decision = 'not_approved', settled_at = %s, closure = 'non_fault_refund', closed_at = %s "
                   "WHERE incident_id = %s AND state = 'not_system_fault'", (refund, now, now, incident_id))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Incident changed during non-fault closure")
    _event(cursor, incident_id, "non_fault_refund", owner_id, now, "confirmed_undelivered")


def claim_grant(cursor: Any, grant_id: int, claim_token: str, now: datetime) -> tuple:
    """@description: Move one open grant to claimed under this attempt's token, before any direct message is attempted.
    @param cursor: Cursor inside the gated transaction.
    @param grant_id: Grant to dispatch.
    @param claim_token: Unique token of this attempt.
    @param now: Attempt start time, naive UTC.
    @return: State, and the link and recipient only when this call made the claim.
    """
    cursor.execute("SELECT g.state, g.discord_user_id, g.incident_id, s.youtube_url FROM exchange_redelivery_grants g "
                   "JOIN songs s ON s.song_id = g.song_id WHERE g.grant_id = %s", (grant_id,))
    row = cursor.fetchone()
    if row is None:
        raise DeliveryStateMismatch("Unknown grant")
    if row["state"] != "open":
        return row["state"], None  # [no second send]: Any state other than open reports itself and sends nothing.
    cursor.execute("UPDATE exchange_redelivery_grants SET state = 'claimed', claim_token = %s, claimed_at = %s "
                   "WHERE grant_id = %s AND state = 'open'", (claim_token, now, grant_id))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Grant changed during claim")
    _event(cursor, row["incident_id"], "claimed", None, now, "dispatch")
    return "claimed", {"grant_id": grant_id, "discord_user_id": row["discord_user_id"], "youtube_url": row["youtube_url"]}


def record_grant_delivery(cursor: Any, grant_id: int, claim_token: str, result: str, message_id: int | None, now: datetime) -> str:
    """@description: Record the classified result of one grant dispatch, only against the claim that made the attempt.
    @param cursor: Cursor inside the gated transaction.
    @param grant_id: Dispatched grant.
    @param claim_token: Token of the claim that performed the attempt.
    @param result: One of sent, not_sent or unknown.
    @param message_id: Accepted message identifier, or None.
    @param now: Result time, naive UTC.
    @return: The stored grant state.
    """
    state, fact = FACT_BY_RESULT[result]
    cursor.execute("SELECT incident_id FROM exchange_redelivery_grants WHERE grant_id = %s", (grant_id,))
    incident_id = cursor.fetchone()["incident_id"]
    if result == "sent":
        cursor.execute("UPDATE exchange_redelivery_grants SET state = 'sent', delivery_fact = 'sent', message_id = %s, delivered_at = %s, closed_at = %s "
                       "WHERE grant_id = %s AND state = 'claimed' AND claim_token = %s", (message_id, now, now, grant_id, claim_token))
        if cursor.rowcount == 1:
            cursor.execute("UPDATE exchange_incidents SET closed_at = %s WHERE incident_id = %s", (now, incident_id))
    else:
        cursor.execute("UPDATE exchange_redelivery_grants SET state = %s, delivery_fact = %s WHERE grant_id = %s AND state = 'claimed' AND claim_token = %s",
                       (state, fact, grant_id, claim_token))
    if cursor.rowcount != 1:
        raise DeliveryStateMismatch("Result does not belong to the current claim")
    _event(cursor, incident_id, "delivery_recorded", None, now, result)
    return state


def expire_grant_claims(cursor: Any, now: datetime) -> int:
    """@description: Turn every claim whose lease expired into an unknown delivery; nothing is sent again.
    @param cursor: Cursor inside the gated transaction.
    @param now: Current time, naive UTC.
    @return: Number of claims moved to uncertain.
    """
    cursor.execute("SELECT grant_id, incident_id FROM exchange_redelivery_grants WHERE state = 'claimed' AND claimed_at < %s", (now - DELIVERY_LEASE,))
    expired = cursor.fetchall()
    for row in expired:
        cursor.execute("UPDATE exchange_redelivery_grants SET state = 'uncertain', delivery_fact = 'unknown' WHERE grant_id = %s AND state = 'claimed'",
                       (row["grant_id"],))
        _event(cursor, row["incident_id"], "claim_expired", None, now, "unknown")
    return len(expired)


def record_owner_disposition(cursor: Any, grant_id: int, owner_id: int, disposition: str, now: datetime) -> dict:
    """@description: Record the Owner's closing decision for a not-sent or unknown grant; the delivery fact itself is never changed.
    @param cursor: Cursor inside the gated transaction.
    @param grant_id: Grant being closed.
    @param owner_id: Authorised Owner identity, already checked by the caller.
    @param disposition: One of the reviewed closing decisions.
    @param now: Current time, naive UTC.
    @return: Grant identifier and whether the song remains held.
    """
    cursor.execute("SELECT state, owner_disposition, incident_id FROM exchange_redelivery_grants WHERE grant_id = %s", (grant_id,))
    row = cursor.fetchone()
    if row is None:
        raise DispositionRefused("UNKNOWN_GRANT")
    if row["owner_disposition"] != "none":
        raise DispositionRefused("ALREADY_RECORDED")
    if disposition not in CLOSABLE_STATES.get(row["state"], ()):
        raise DispositionRefused("NOT_CLOSABLE_FOR_STATE")
    holds = 0 if (row["state"] == "not_sent" and disposition == "no_further_action") else 1  # [designated rule]: Only a not_sent closure releases the song; uncertain closures keep it.
    cursor.execute("UPDATE exchange_redelivery_grants SET owner_disposition = %s, disposition_by = %s, disposition_at = %s, closed_at = %s, "
                   "holds_song = %s WHERE grant_id = %s AND owner_disposition = 'none' AND state = %s",
                   (disposition, owner_id, now, now, holds, grant_id, row["state"]))
    if cursor.rowcount != 1:
        raise DispositionRefused("GRANT_CHANGED")
    cursor.execute("UPDATE exchange_incidents SET closed_at = %s WHERE incident_id = %s", (now, row["incident_id"]))
    _event(cursor, row["incident_id"], "disposition", owner_id, now, disposition)
    return {"grant_id": grant_id, "holds_song": bool(holds)}


def pending_block(cursor: Any, user: int, now: datetime) -> bool:
    """@description: Decide whether the requester is blocked from new exchanges, because of a stranded reservation or an open grant.
    @param cursor: Cursor inside the gated transaction.
    @param user: Requester.
    @param now: Current time, naive UTC.
    @return: True while any compensation for the requester is still open.
    """
    if has_stranded_reservation(cursor, user, now):
        return True
    cursor.execute("SELECT COUNT(*) AS total FROM exchange_redelivery_grants WHERE discord_user_id = %s AND closed_at IS NULL", (user,))
    return cursor.fetchone()["total"] > 0


def held_grant_song_ids(cursor: Any, user: int) -> frozenset:
    """@description: List the songs the requester holds through a free redelivery, so a paid redraw cannot repeat them.
    @param cursor: Cursor inside the gated transaction.
    @param user: Requester.
    @return: Song identifiers held through grants.
    """
    cursor.execute("SELECT song_id FROM exchange_redelivery_grants WHERE discord_user_id = %s AND holds_song = 1", (user,))
    return frozenset(row["song_id"] for row in cursor.fetchall())


def open_grants(cursor: Any, now: datetime) -> list[dict]:
    """@description: List open compensation grants for the Owner, with identifiers, state, times and an overdue flag only; no other action follows from the flag.
    @param cursor: Cursor inside a transaction.
    @param now: Current time, naive UTC, used only to compute the overdue flag.
    @return: Bounded list of open grants, oldest first.
    """
    cursor.execute("SELECT grant_id, discord_user_id, song_id, state, granted_at FROM exchange_redelivery_grants WHERE closed_at IS NULL "
                   "ORDER BY grant_id LIMIT %s", (LIST_LIMIT,))
    return [{"grant_id": row["grant_id"], "requester": row["discord_user_id"], "song_id": row["song_id"],
             "state": row["state"], "granted_at": row["granted_at"], "overdue": now - row["granted_at"] > OVERDUE_AFTER}
            for row in cursor.fetchall()]


def pending_incident_rows(cursor: Any, now: datetime) -> list[dict]:
    """@description: List incidents still awaiting the Owner, approved ones awaiting settlement and not-system-fault ones awaiting a decision, with an overdue flag only.
    @param cursor: Cursor inside a transaction.
    @param now: Current time, naive UTC, used only to compute the overdue flag.
    @return: Bounded list, oldest first; identifiers, requester, song, state, decision time and overdue flag only.
    """
    cursor.execute("SELECT recommendation_id, discord_user_id, song_id, state, approved_at FROM exchange_incidents "
                   "WHERE state IN ('approved', 'not_system_fault') AND closed_at IS NULL ORDER BY incident_id LIMIT %s", (LIST_LIMIT,))
    return [{"operation_code": row["recommendation_id"], "requester": row["discord_user_id"], "song_id": row["song_id"],
             "state": row["state"], "decided_at": row["approved_at"], "overdue": now - row["approved_at"] > OVERDUE_AFTER}
            for row in cursor.fetchall()]


def user_incident_summary(cursor: Any, user: int) -> list[dict]:
    """@description: Summarise one requester's most recent incidents for the Owner's manual judgement, with refund, redelivery and closure states and no score.
    @param cursor: Cursor inside a transaction.
    @param user: Requester whose incidents are shown.
    @return: Up to SUMMARY_LIMIT incidents, newest first.
    """
    cursor.execute("SELECT i.incident_id, i.recommendation_id, i.state, i.refund_status, i.redelivery_decision, i.closure, i.approved_at, "
                   "g.state AS grant_state, g.delivery_fact FROM exchange_incidents i LEFT JOIN exchange_redelivery_grants g "
                   "ON g.incident_id = i.incident_id WHERE i.discord_user_id = %s ORDER BY i.incident_id DESC LIMIT %s", (user, SUMMARY_LIMIT))
    return [{"incident_id": row["incident_id"], "recommendation_id": row["recommendation_id"], "state": row["state"],
             "refund_status": row["refund_status"], "redelivery_decision": row["redelivery_decision"], "closure": row["closure"],
             "approved_at": row["approved_at"], "grant_state": row["grant_state"], "delivery_fact": row["delivery_fact"]}
            for row in cursor.fetchall()]


