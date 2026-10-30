"""Verify the Owner's final disposition rules on the offline SQLite stand-in: refund and redelivery are decided separately, a replay repeats neither, an uncertain redelivery keeps its song and blocks the account, and an overdue grant is only flagged.

Each case runs production store functions inside the writer-gate transaction on a disposable SQLite file. Nothing here connects to MySQL or Discord.
"""

from datetime import timedelta

import pytest

from bot.database import compensation_store as store
from bot.database.exchange_delivery_store import DeliveryStateMismatch
from tests.test_compensation_store import (
    NOW, OLD, OWNER, USER, approve, balance, block, count, held, seed_song, seed_stranded, settle, transaction,
)
from tests.compensation_sqlite import create_database


@pytest.fixture
def stranded(tmp_path):
    """@description: One stranded, never-claimed reservation for the synthetic requester, on a fresh database.
    @param tmp_path: Pytest temporary directory.
    @return: Tuple of database path and reservation identifier.
    """
    path = tmp_path / "disposition.sqlite"
    create_database(path)
    with transaction(path) as cursor:
        award, song = seed_song(cursor, 1), seed_song(cursor, 2)
        reservation = seed_stranded(cursor, USER, song, award, OLD)
    return path, reservation


def incident_row(path, incident: int) -> dict:
    """@description: Read one incident's disposition columns on a connection that is closed afterwards.
    @param path: Database file.
    @param incident: Incident identifier.
    @return: The incident row as a dictionary.
    """
    with transaction(path) as cursor:
        cursor.execute("SELECT state, refund_status, redelivery_decision, closure, closed_at FROM exchange_incidents WHERE incident_id = ?", (incident,))
        return dict(cursor.fetchone())


def test_a_refund_only_settlement_refunds_once_closes_and_issues_no_grant(stranded) -> None:
    """@description: Declining redelivery after a confirmed fault still refunds the original debit once, records the decision, and closes the incident without a grant.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    incident = approve(path, reservation)["incident_id"]
    outcome = settle(path, incident, redelivery_approved=False)
    assert outcome["outcome"] == "settled" and outcome["grant_id"] is None
    assert balance(path, USER) == 1
    assert incident_row(path, incident) == {"state": "settled", "refund_status": "refunded_once", "redelivery_decision": "not_approved",
                                            "closure": "refund_only", "closed_at": NOW.isoformat(" ")}
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 0
    assert block(path) is False


def test_a_replay_after_a_refund_only_settlement_repeats_neither_refund_nor_grant(stranded) -> None:
    """@description: Settling the same incident again with the other redelivery decision changes nothing: no second refund and no grant.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    incident = approve(path, reservation)["incident_id"]
    settle(path, incident, redelivery_approved=False)
    again = settle(path, incident, redelivery_approved=True)
    assert again["outcome"] == "already_settled" and again["grant_id"] is None
    assert balance(path, USER) == 1
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 1
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 0


def test_a_settled_incident_cannot_become_a_non_fault_or_be_approved_again(stranded) -> None:
    """@description: After settlement the decision is final: a not-system-fault record is refused, and a repeated approval returns the settled incident unchanged.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    incident = approve(path, reservation)["incident_id"]
    settle(path, incident, redelivery_approved=True)
    with transaction(path) as cursor:
        with pytest.raises(store.ApprovalRefused, match="ALREADY_DECIDED"):
            store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)
    with transaction(path) as cursor:
        again = store.approve_incident(cursor, reservation, OWNER, "system_fault_confirmed", NOW)
    assert again == {"incident_id": incident, "state": "settled", "created": False}


def test_a_not_system_fault_reservation_is_never_settled_as_a_fault(stranded) -> None:
    """@description: A reservation judged not a system fault cannot be approved or settled afterwards; the approval call reports the existing decision and the settlement is refused.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    with transaction(path) as cursor:
        recorded = store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)
    with transaction(path) as cursor:
        again = store.approve_incident(cursor, reservation, OWNER, "system_fault_confirmed", NOW)
    assert again["state"] == "not_system_fault" and again["created"] is False
    with pytest.raises(store.ApprovalRefused, match="NOT_SETTLEABLE"):
        settle(path, recorded["incident_id"])
    assert balance(path, USER) == 0


def dispatch_as(path, reservation: int, result: str) -> int:
    """@description: Settle a fault with redelivery, then dispatch its grant once with the given classified result.
    @param path: Database file.
    @param reservation: Stranded reservation.
    @param result: sent, not_sent or unknown.
    @return: Grant identifier.
    """
    grant = settle(path, approve(path, reservation)["incident_id"])["grant_id"]
    with transaction(path) as cursor:
        store.claim_grant(cursor, grant, "tok-dispatch", NOW)
    with transaction(path) as cursor:
        store.record_grant_delivery(cursor, grant, "tok-dispatch", result, 990001 if result == "sent" else None, NOW)
    return grant


def test_an_uncertain_redelivery_keeps_the_song_and_blocks_the_account_until_the_owner_decides(stranded) -> None:
    """@description: An unknown delivery keeps the song held and the requester blocked; the Owner's confirmation that it was delivered lifts the block and keeps the song held.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    grant = dispatch_as(path, reservation, "unknown")
    assert block(path) is True and held(path)
    with transaction(path) as cursor:
        store.record_owner_disposition(cursor, grant, OWNER, "confirmed_delivered", NOW)
    assert block(path) is False and held(path)


def test_a_not_sent_redelivery_releases_the_song_only_when_the_owner_closes_it(stranded) -> None:
    """@description: A proven non-delivery keeps the requester blocked and the song held until the Owner closes it; the closure releases the song and lifts the block.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    grant = dispatch_as(path, reservation, "not_sent")
    assert block(path) is True and held(path)
    with transaction(path) as cursor:
        assert store.record_owner_disposition(cursor, grant, OWNER, "no_further_action", NOW) == {"grant_id": grant, "holds_song": False}
    assert block(path) is False and not held(path)


def test_a_claim_in_flight_refuses_an_owner_disposition(stranded) -> None:
    """@description: While a redelivery is claimed and its outcome is not yet known, the Owner cannot close it; the claim and the disposition are serialised by the gate.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    grant = settle(path, approve(path, reservation)["incident_id"])["grant_id"]
    with transaction(path) as cursor:
        store.claim_grant(cursor, grant, "tok-inflight", NOW)
    with transaction(path) as cursor:
        with pytest.raises(store.DispositionRefused, match="NOT_CLOSABLE_FOR_STATE"):
            store.record_owner_disposition(cursor, grant, OWNER, "no_further_action", NOW)


def test_an_open_grant_is_flagged_overdue_only_after_twenty_four_hours_and_nothing_else_changes(stranded) -> None:
    """@description: The overdue flag appears in the Owner's list after twenty-four hours; the grant stays open and the requester stays blocked, with no automatic action.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    settle(path, approve(path, reservation)["incident_id"])
    with transaction(path) as cursor:
        within = store.open_grants(cursor, NOW + timedelta(hours=23))
        beyond = store.open_grants(cursor, NOW + timedelta(hours=25))
    assert within[0]["overdue"] is False and beyond[0]["overdue"] is True
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants WHERE state = 'open' AND closed_at IS NULL") == 1
    assert block(path) is True


def test_an_uncertain_redelivery_closed_as_not_delivered_still_keeps_the_song_and_lifts_the_block(stranded) -> None:
    """@description: Closing an unknown redelivery as not delivered lifts the account block but keeps the song held and the unknown delivery fact unchanged, as the designated uncertain-closure rule requires.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    grant = dispatch_as(path, reservation, "unknown")
    with transaction(path) as cursor:
        assert store.record_owner_disposition(cursor, grant, OWNER, "confirmed_not_delivered", NOW) == {"grant_id": grant, "holds_song": True}
    assert block(path) is False and held(path)
    with transaction(path) as cursor:
        cursor.execute("SELECT delivery_fact FROM exchange_redelivery_grants WHERE grant_id = ?", (grant,))
        assert cursor.fetchone()["delivery_fact"] == "unknown"


def test_pending_incidents_are_flagged_overdue_after_twenty_four_hours_and_nothing_else_changes(stranded) -> None:
    """@description: An approved incident still awaiting settlement is flagged overdue in the Owner's pending list after twenty-four hours, while it keeps blocking the requester and no money moves.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    approve(path, reservation)
    with transaction(path) as cursor:
        within = store.pending_incident_rows(cursor, NOW + timedelta(hours=23))
        beyond = store.pending_incident_rows(cursor, NOW + timedelta(hours=25))
    assert [row["overdue"] for row in within] == [False] and [row["overdue"] for row in beyond] == [True]
    assert beyond[0]["state"] == "approved"
    assert block(path) is True and balance(path, USER) == 0


def test_a_not_system_fault_order_is_flagged_overdue_after_twenty_four_hours_and_stays_unblocked(stranded) -> None:
    """@description: A reservation judged not a system fault appears in the pending list with an overdue flag after twenty-four hours; the requester stays unblocked and nothing is refunded or redelivered.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    with transaction(path) as cursor:
        store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)
    with transaction(path) as cursor:
        beyond = store.pending_incident_rows(cursor, NOW + timedelta(hours=25))
    assert len(beyond) == 1 and beyond[0]["state"] == "not_system_fault" and beyond[0]["overdue"] is True
    assert block(path) is False and balance(path, USER) == 0
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 0


def close_non_fault(path, incident: int, confirmed_undelivered: bool = True, now=NOW) -> dict:
    """@description: Close one not-system-fault incident as a confirmed-undelivered refund on its own transaction.
    @param path: Database file.
    @param incident: Not-system-fault incident.
    @param confirmed_undelivered: The Owner's explicit confirmation that the original order was never delivered.
    @param now: Closure time, naive UTC.
    @return: Outcome of the closure.
    """
    with transaction(path) as cursor:
        return store.close_non_fault_refund(cursor, incident, OWNER, confirmed_undelivered, now)


def test_a_confirmed_undelivered_non_fault_refunds_the_original_debit_once_and_closes_without_a_grant(stranded) -> None:
    """@description: The Owner's non-fault decision with confirmed non-delivery refunds the bound original debit once, closes the incident, issues no redelivery grant, releases the reservation, and leaves an audit trail.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    with transaction(path) as cursor:
        incident = store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)["incident_id"]
    outcome = close_non_fault(path, incident)
    assert outcome["outcome"] == "closed_refunded" and outcome["grant_id"] is None and outcome["refund_ledger_id"] is not None
    assert balance(path, USER) == 1
    assert incident_row(path, incident) == {"state": "non_fault_refunded", "refund_status": "refunded_once", "redelivery_decision": "not_approved",
                                            "closure": "non_fault_refund", "closed_at": NOW.isoformat(" ")}
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 0
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 1
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE credit_delta = 0") == 0
    assert block(path) is False
    with transaction(path) as cursor:
        cursor.execute("SELECT status, released_at FROM recommendations WHERE recommendation_id = ?", (reservation,))
        assert dict(cursor.fetchone()) == {"status": "failed", "released_at": NOW.isoformat(" ")}
        cursor.execute("SELECT action, detail FROM exchange_incident_events WHERE incident_id = ? ORDER BY event_id", (incident,))
        assert [tuple(row) for row in cursor.fetchall()][-1] == ("non_fault_refund", "confirmed_undelivered")
        assert store.pending_incident_rows(cursor, NOW) == []


def test_a_replay_after_a_non_fault_closure_repeats_no_refund_and_issues_no_grant(stranded) -> None:
    """@description: Closing the same non-fault incident again changes nothing: no second refund, no grant, and the first outcome is reported.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    with transaction(path) as cursor:
        incident = store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)["incident_id"]
    first = close_non_fault(path, incident)
    again = close_non_fault(path, incident, now=NOW + timedelta(minutes=5))
    assert again == {"outcome": "already_closed", "refund_ledger_id": first["refund_ledger_id"], "grant_id": None}
    assert balance(path, USER) == 1
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 1
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 0


def test_a_non_fault_closure_without_the_owner_confirming_non_delivery_is_refused_and_changes_nothing(stranded) -> None:
    """@description: Without the explicit confirmation of non-delivery the closure is refused before any write; the incident stays a pending non-fault and nothing is refunded.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    with transaction(path) as cursor:
        incident = store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)["incident_id"]
    with pytest.raises(store.ApprovalRefused, match="NOT_CONFIRMED_UNDELIVERED"):
        close_non_fault(path, incident, confirmed_undelivered=False)
    assert incident_row(path, incident)["state"] == "not_system_fault"
    assert balance(path, USER) == 0 and block(path) is False
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 0


def test_a_reservation_that_may_have_been_delivered_is_never_closed_as_a_non_fault_refund(stranded) -> None:
    """@description: A reservation whose delivery may have happened (claimed, uncertain) is not eligible for the non-fault refund, so an unknown delivery can never be refunded through this path.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    with transaction(path) as cursor:
        incident = store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)["incident_id"]
    with transaction(path) as cursor:
        cursor.execute("UPDATE recommendations SET status = 'uncertain', claim_token = 'tok-uncertain', claimed_at = ? WHERE recommendation_id = ?",
                       (OLD, reservation))
    with pytest.raises(store.ApprovalRefused, match="NOT_STRANDED"):
        close_non_fault(path, incident)
    assert incident_row(path, incident)["state"] == "not_system_fault"
    assert balance(path, USER) == 0


def test_a_failed_non_fault_refund_rolls_back_and_a_retry_completes_exactly_once(stranded) -> None:
    """@description: When the refund cannot be recorded the whole closure rolls back, leaving the incident open and the reservation untouched; a later retry refunds exactly once.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    with transaction(path) as cursor:
        incident = store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)["incident_id"]
    with pytest.raises(DeliveryStateMismatch):
        with transaction(path) as cursor:
            cursor.execute("DELETE FROM users WHERE discord_user_id = ?", (USER,))
            store.close_non_fault_refund(cursor, incident, OWNER, True, NOW)
    assert incident_row(path, incident)["state"] == "not_system_fault"
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 0
    with transaction(path) as cursor:
        cursor.execute("SELECT status, released_at FROM recommendations WHERE recommendation_id = ?", (reservation,))
        assert dict(cursor.fetchone()) == {"status": "reserved", "released_at": None}
    assert close_non_fault(path, incident)["outcome"] == "closed_refunded"
    assert close_non_fault(path, incident)["outcome"] == "already_closed"
    assert balance(path, USER) == 1
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 1
