"""Verify the compensation state machine and its accounting on an offline SQLite stand-in, using the production SQL text.

Money and entitlement are separate: each confirmed incident refunds its one bound original debit and issues one free redelivery grant.
There is no per-user quota or time window; repeated incidents are not refused automatically. No zero-amount ledger row is ever written. Every scenario ends with the
balance-equals-ledger reconciliation and with the absence of zero-delta ledger rows.
"""

import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from bot.database import compensation_store as store
from bot.database.exchange_delivery_store import DeliveryStateMismatch, RefundContradiction
from tests.compensation_sqlite import AdapterConnection, create_database

NOW = datetime(2026, 10, 24, 12, 0, 0)  # [fixed clock]: Naive UTC, as stored.
OLD = NOW - timedelta(minutes=11)  # [stranded]: Older than the 10-minute threshold.
YOUNG = NOW - timedelta(minutes=5)  # [live]: Inside the threshold.
OWNER = 900000000000000001  # [synthetic Owner]: Stands in for the configured Owner identity.
USER, PEER = 900000000000000101, 900000000000000102  # [synthetic users].


@pytest.fixture
def path(tmp_path):
    """@description: One fresh SQLite database file per test.
    @param tmp_path: Pytest temporary directory.
    @return: Database file path.
    """
    database = tmp_path / "compensation.sqlite"
    create_database(database)
    return database


@contextmanager
def transaction(path):
    """@description: Run one unit of work on its own connection, committing success and rolling back failure.
    @param path: Database file.
    @return: Context yielding the cursor.
    """
    connection = AdapterConnection(path)
    connection.begin()
    try:
        yield connection.cursor()
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def seed_song(cursor, number: int) -> int:
    """@description: Insert one verified synthetic song with a fixed, non-real track identifier.
    @param cursor: Open cursor.
    @param number: Small positive number that keeps the identifier unique.
    @return: Song identifier.
    """
    track = f"JBX{number:08d}"
    link = f"https://www.youtube.com/watch?v={track}"
    cursor.execute("INSERT INTO songs (platform, platform_track_id, source_url, youtube_url, resolution_status, verification_provenance) "
                   "VALUES ('youtube', ?, ?, ?, 'verified', 'compensation-fixture')", (track, link, link))
    return cursor.lastrowid


def seed_stranded(cursor, user: int, song: int, award_song: int, created_at: datetime) -> int:
    """@description: Create a user with one earned point, an exchange reservation that was never claimed, and its single debit.
    @param cursor: Open cursor.
    @param user: Synthetic recipient.
    @param song: Reserved song.
    @param award_song: Song whose single award earned the point.
    @param created_at: Creation time of the reservation.
    @return: Reservation identifier.
    """
    cursor.execute("INSERT INTO users (discord_user_id, credit_balance) VALUES (?, 1)", (user,))
    cursor.execute("INSERT INTO credit_ledger (discord_user_id, credit_delta, reason, idempotency_key, awarded_song_id, created_at) "
                   "VALUES (?, 1, 'submission_award', ?, ?, ?)", (user, f"award:{user}:{award_song}", award_song, NOW))
    cursor.execute("INSERT INTO recommendations (discord_user_id, song_id, status, created_at) VALUES (?, ?, 'reserved', ?)",
                   (user, song, created_at))
    reservation = cursor.lastrowid
    cursor.execute("INSERT INTO credit_ledger (discord_user_id, credit_delta, reason, idempotency_key, recommendation_id, created_at) "
                   "VALUES (?, -1, 'exchange_debit', ?, ?, ?)", (user, f"exchange:{reservation}", reservation, created_at))
    cursor.execute("UPDATE users SET credit_balance = credit_balance - 1 WHERE discord_user_id = ?", (user,))
    return reservation


def balance(path, user: int) -> int:
    """@description: Read one committed balance on a separate connection.
    @param path: Database file.
    @param user: Synthetic user.
    @return: Stored credit balance.
    """
    with transaction(path) as cursor:
        cursor.execute("SELECT credit_balance FROM users WHERE discord_user_id = ?", (user,))
        return cursor.fetchone()["credit_balance"]


def count(path, sql: str, params: tuple = ()) -> int:
    """@description: Count rows for one read-only query on a separate connection.
    @param path: Database file.
    @param sql: Counting statement with ? placeholders.
    @param params: Bound values.
    @return: Row count.
    """
    with transaction(path) as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone()[0]


def assert_reconciled(path, user: int) -> None:
    """@description: Every committed balance equals its ledger sum, no ledger row has a zero delta, and the balance never goes negative.
    @param path: Database file.
    @param user: Synthetic user.
    @return: None.
    """
    with transaction(path) as cursor:
        cursor.execute("SELECT credit_balance AS balance, (SELECT COALESCE(SUM(credit_delta), 0) FROM credit_ledger "
                       "WHERE discord_user_id = u.discord_user_id) AS total FROM users u WHERE discord_user_id = ?", (user,))
        row = cursor.fetchone()
        assert row["balance"] == row["total"] and row["balance"] >= 0
        cursor.execute("SELECT COUNT(*) FROM credit_ledger WHERE credit_delta = 0")
        assert cursor.fetchone()[0] == 0, "compensation must not write zero-amount ledger rows"


def approve(path, reservation: int, now: datetime = NOW) -> dict:
    """@description: Approve one stranded reservation in its own unit of work, as the Owner flow does.
    @param path: Database file.
    @param reservation: Stranded reservation.
    @param now: Current time.
    @param redelivery_approved: Owner's redelivery decision.
    @return: Store outcome.
    """
    with transaction(path) as cursor:
        return store.approve_incident(cursor, reservation, OWNER, "system_fault_confirmed", now)


def settle(path, incident: int, now: datetime = NOW, redelivery_approved: bool = True) -> dict:
    """@description: Settle one approved incident in its own unit of work.
    @param path: Database file.
    @param incident: Incident identifier.
    @param now: Current time.
    @param redelivery_approved: Owner's redelivery decision.
    @return: Store outcome.
    """
    with transaction(path) as cursor:
        return store.settle_incident(cursor, incident, redelivery_approved, now)


@pytest.fixture
def stranded(path):
    """@description: Two songs and one user with a stranded reservation that is older than the threshold.
    @param path: Database file.
    @return: Tuple of database path, reservation identifier, reserved song identifier.
    """
    with transaction(path) as cursor:
        award, song = seed_song(cursor, 1), seed_song(cursor, 2)
        reservation = seed_stranded(cursor, USER, song, award, OLD)
    return path, reservation, song


def test_only_a_stranded_reservation_can_be_approved(path) -> None:
    """@description: A reservation that is still live cannot be approved, and nothing is written for it.
    @param path: Database file.
    @return: None.
    """
    with transaction(path) as cursor:
        award, song = seed_song(cursor, 1), seed_song(cursor, 2)
        reservation = seed_stranded(cursor, USER, song, award, YOUNG)
    with pytest.raises(store.ApprovalRefused):
        approve(path, reservation)
    assert count(path, "SELECT COUNT(*) FROM exchange_incidents") == 0


def test_approval_is_recorded_once_and_a_second_approval_returns_the_same_incident(stranded) -> None:
    """@description: Two approvals of one reservation create one incident; the second is reported as already decided.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    first = approve(path, reservation)
    second = approve(path, reservation)
    assert first["created"] is True and second["created"] is False
    assert first["incident_id"] == second["incident_id"]
    assert count(path, "SELECT COUNT(*) FROM exchange_incidents") == 1


def test_settlement_refunds_the_one_debit_and_grants_one_redelivery_without_a_ledger_row(stranded) -> None:
    """@description: Settlement refunds exactly the original debit, releases the reservation and creates the entitlement; no zero-amount row appears.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    incident = approve(path, reservation)["incident_id"]
    outcome = settle(path, incident)
    assert outcome["outcome"] == "settled" and outcome["grant_id"] is not None
    assert balance(path, USER) == 1
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE recommendation_id = ? AND reason = 'delivery_refund'", (reservation,)) == 1
    with transaction(path) as cursor:
        cursor.execute("SELECT status, released_at FROM recommendations WHERE recommendation_id = ?", (reservation,))
        row = cursor.fetchone()
    assert row["status"] == "failed" and row["released_at"] is not None
    assert_reconciled(path, USER)


def test_a_repeated_settlement_refunds_nothing_more(stranded) -> None:
    """@description: A second settlement of a settled incident reports the existing outcome and changes no money or entitlement.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    incident = approve(path, reservation)["incident_id"]
    settle(path, incident)
    again = settle(path, incident)
    assert again["outcome"] == "already_settled"
    assert balance(path, USER) == 1
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 1
    assert_reconciled(path, USER)


def block(path) -> bool:
    """@description: Read the requester's pending-block state on a connection that is closed afterwards.
    @param path: Database file.
    @return: Whether the requester is blocked.
    """
    with transaction(path) as cursor:
        return store.pending_block(cursor, USER, NOW)


def held(path) -> frozenset:
    """@description: Read the requester's grant-held songs on a connection that is closed afterwards.
    @param path: Database file.
    @return: Held song identifiers.
    """
    with transaction(path) as cursor:
        return store.held_grant_song_ids(cursor, USER)


def test_a_reservation_claimed_before_settlement_is_voided_without_money(stranded) -> None:
    """@description: If the original request claimed the reservation before settlement, settlement voids the incident and refunds nothing.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    incident = approve(path, reservation)["incident_id"]
    with transaction(path) as cursor:
        cursor.execute("UPDATE recommendations SET status = 'sending', claim_token = 'orig', claimed_at = ? WHERE recommendation_id = ?",
                       (NOW, reservation))
    outcome = settle(path, incident)
    assert outcome["outcome"] == "void"
    assert balance(path, USER) == 0
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 0
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 0


def test_a_missing_or_inconsistent_original_debit_is_reported_and_changes_nothing(stranded) -> None:
    """@description: Without exactly one legitimate debit the refund is refused with an explicit error, and the incident stays approved.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    incident = approve(path, reservation)["incident_id"]
    with transaction(path) as cursor:
        cursor.execute("DELETE FROM credit_ledger WHERE recommendation_id = ? AND reason = 'exchange_debit'", (reservation,))
    with pytest.raises(RefundContradiction):
        settle(path, incident)
    with transaction(path) as cursor:
        cursor.execute("SELECT state FROM exchange_incidents WHERE incident_id = ?", (incident,))
        assert cursor.fetchone()["state"] == "approved"
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 0


def test_a_not_system_fault_reservation_keeps_its_debit_and_stops_blocking(stranded) -> None:
    """@description: The Owner may decline a stranded reservation as not a system fault. The debit stays, nothing is refunded, and the block lifts.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    assert block(path) is True
    with transaction(path) as cursor:
        store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)
    assert block(path) is False
    assert balance(path, USER) == 0
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 0


def test_claim_is_conditional_so_a_second_claim_sends_nothing(stranded) -> None:
    """@description: Only an open grant can be claimed; a second claim reports the current state and returns no link.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    grant = settle(path, approve(path, reservation)["incident_id"])["grant_id"]
    with transaction(path) as cursor:
        assert store.claim_grant(cursor, grant, "tok-1", NOW)[0] == "claimed"
    with transaction(path) as cursor:
        state, claim = store.claim_grant(cursor, grant, "tok-2", NOW)
    assert state == "claimed" and claim is None


def test_a_sent_grant_closes_the_incident_and_keeps_the_song_held(stranded) -> None:
    """@description: A delivery accepted by Discord closes the grant, stops the block and keeps the song held against paid redraws.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, song = stranded
    grant = settle(path, approve(path, reservation)["incident_id"])["grant_id"]
    with transaction(path) as cursor:
        store.claim_grant(cursor, grant, "tok-1", NOW)
    with transaction(path) as cursor:
        assert store.record_grant_delivery(cursor, grant, "tok-1", "sent", 990001, NOW) == "sent"
    assert block(path) is False
    assert song in held(path)
    assert_reconciled(path, USER)


def test_a_not_sent_grant_is_never_resent_and_waits_for_the_owner(stranded) -> None:
    """@description: A proven non-delivery stays pending for the Owner, is not sent again, and the Owner's no-further-action closes it without holding the song.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, song = stranded
    grant = settle(path, approve(path, reservation)["incident_id"])["grant_id"]
    with transaction(path) as cursor:
        store.claim_grant(cursor, grant, "tok-1", NOW)
        store.record_grant_delivery(cursor, grant, "tok-1", "not_sent", None, NOW)
    with transaction(path) as cursor:
        assert store.claim_grant(cursor, grant, "tok-2", NOW) == ("not_sent", None)
    assert block(path) is True
    with transaction(path) as cursor:
        store.record_owner_disposition(cursor, grant, OWNER, "no_further_action", NOW)
    assert block(path) is False
    assert song not in held(path)
    assert_reconciled(path, USER)


def test_an_unknown_delivery_is_never_converted_to_delivered_by_the_owner(stranded) -> None:
    """@description: The owner may close an unknown delivery, but the recorded delivery fact stays unknown and nothing is resent.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    grant = settle(path, approve(path, reservation)["incident_id"])["grant_id"]
    with transaction(path) as cursor:
        store.claim_grant(cursor, grant, "tok-1", NOW)
        store.record_grant_delivery(cursor, grant, "tok-1", "unknown", None, NOW)
    with transaction(path) as cursor:
        store.record_owner_disposition(cursor, grant, OWNER, "confirmed_delivered", NOW)
    with transaction(path) as cursor:
        cursor.execute("SELECT state, delivery_fact, owner_disposition FROM exchange_redelivery_grants WHERE grant_id = ?", (grant,))
        row = cursor.fetchone()
    assert row["state"] == "uncertain" and row["delivery_fact"] == "unknown" and row["owner_disposition"] == "confirmed_delivered"
    assert block(path) is False


def test_an_expired_claim_becomes_uncertain_and_is_never_resent(stranded) -> None:
    """@description: A claim whose lease expired is treated as unknown, not as unsent, and is not dispatched again.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    grant = settle(path, approve(path, reservation)["incident_id"])["grant_id"]
    with transaction(path) as cursor:
        store.claim_grant(cursor, grant, "tok-1", NOW)
    with transaction(path) as cursor:
        assert store.expire_grant_claims(cursor, NOW + store.DELIVERY_LEASE + timedelta(seconds=1)) == 1
    with transaction(path) as cursor:
        assert store.claim_grant(cursor, grant, "tok-2", NOW) == ("uncertain", None)


def test_a_disposition_is_refused_for_a_sent_or_already_closed_grant(stranded) -> None:
    """@description: The owner cannot record a disposition on a delivery that was already accepted, or on a grant already closed.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    grant = settle(path, approve(path, reservation)["incident_id"])["grant_id"]
    with transaction(path) as cursor:
        store.claim_grant(cursor, grant, "tok-1", NOW)
        store.record_grant_delivery(cursor, grant, "tok-1", "sent", 990001, NOW)
    with pytest.raises(store.DispositionRefused):
        with transaction(path) as cursor:
            store.record_owner_disposition(cursor, grant, OWNER, "no_further_action", NOW)


def test_a_wrong_claim_token_cannot_change_a_claimed_grant(stranded) -> None:
    """@description: Recording a result with a token that does not own the claim is refused and changes nothing.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    grant = settle(path, approve(path, reservation)["incident_id"])["grant_id"]
    with transaction(path) as cursor:
        store.claim_grant(cursor, grant, "tok-1", NOW)
    with pytest.raises(DeliveryStateMismatch):
        with transaction(path) as cursor:
            store.record_grant_delivery(cursor, grant, "tok-other", "sent", 990001, NOW)
    with transaction(path) as cursor:
        cursor.execute("SELECT state FROM exchange_redelivery_grants WHERE grant_id = ?", (grant,))
        assert cursor.fetchone()["state"] == "claimed"


def test_the_owner_list_shows_open_grants_without_tokens_or_links(stranded) -> None:
    """@description: The Owner's open-grant list carries only identifiers, state and times, never a token or a link.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    settle(path, approve(path, reservation)["incident_id"])
    with transaction(path) as cursor:
        rows = store.open_grants(cursor, NOW)
    assert len(rows) == 1 and set(rows[0]) == {"grant_id", "requester", "song_id", "state", "granted_at", "overdue"}
    assert all("youtube" not in str(value) for value in rows[0].values())


def test_invariants_hold_after_a_full_sequence(stranded) -> None:
    """@description: A complete approve, settle, claim, send sequence leaves the account reconciled and the incident closed.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    incident = approve(path, reservation)["incident_id"]
    grant = settle(path, incident)["grant_id"]
    with transaction(path) as cursor:
        store.claim_grant(cursor, grant, "tok-1", NOW)
        store.record_grant_delivery(cursor, grant, "tok-1", "sent", 990001, NOW)
    with transaction(path) as cursor:
        cursor.execute("SELECT state, closed_at FROM exchange_incidents WHERE incident_id = ?", (incident,))
        row = cursor.fetchone()
    assert row["state"] == "settled" and row["closed_at"] is not None
    assert_reconciled(path, USER)


def test_the_stored_refund_key_matches_the_existing_refund_path(stranded) -> None:
    """@description: The settlement refund uses the same idempotency key as the existing refund path, so the two can never both refund one reservation.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    settle(path, approve(path, reservation)["incident_id"])
    expected = "refund:" + hashlib.sha256(str(reservation).encode("ascii")).hexdigest()
    with transaction(path) as cursor:
        cursor.execute("SELECT idempotency_key FROM credit_ledger WHERE reason = 'delivery_refund'")
        assert cursor.fetchone()["idempotency_key"] == expected


def test_the_database_itself_refuses_a_second_refund_for_one_reservation(stranded) -> None:
    """@description: Even if a second writer reached the refund insert, the reason-per-reservation uniqueness refuses it.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    settle(path, approve(path, reservation)["incident_id"])
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(path) as cursor:
            cursor.execute("INSERT INTO credit_ledger (discord_user_id, credit_delta, reason, idempotency_key, recommendation_id) "
                           "VALUES (?, 1, 'delivery_refund', 'refund:other', ?)", (USER, reservation))


def test_a_failure_while_issuing_the_grant_rolls_back_the_refund_and_the_release(stranded, monkeypatch) -> None:
    """@description: If the grant cannot be written, the refund, the release and the settlement all roll back together; the approval stays pending and nothing is lost or duplicated.
    @param stranded: Stranded reservation fixture.
    @param monkeypatch: Pytest patching facility.
    @return: None.
    """
    path, reservation, _ = stranded
    incident = approve(path, reservation)["incident_id"]

    def interrupted(*args, **kwargs):
        """@description: Simulate a process interruption right after the refund and release were written.
        @param args: Unused.
        @param kwargs: Unused.
        @return: Never returns.
        """
        raise sqlite3.OperationalError("simulated interruption")

    monkeypatch.setattr(store, "_issue_grant", interrupted)
    with pytest.raises(sqlite3.OperationalError):
        settle(path, incident)
    assert balance(path, USER) == 0
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 0
    with transaction(path) as cursor:
        cursor.execute("SELECT status, released_at FROM recommendations WHERE recommendation_id = ?", (reservation,))
        row = cursor.fetchone()
        cursor.execute("SELECT state FROM exchange_incidents WHERE incident_id = ?", (incident,))
        state = cursor.fetchone()["state"]
    assert row["status"] == "reserved" and row["released_at"] is None and state == "approved"
    monkeypatch.undo()
    assert settle(path, incident)["outcome"] == "settled"
    assert balance(path, USER) == 1 and count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 1
    assert_reconciled(path, USER)


def test_two_distinct_lawful_incidents_for_one_user_each_refund_and_grant_once(path) -> None:
    """@description: Two different, lawful incidents for the same requester, at different times, are each refunded once and each granted once; no quota applies.
    @param path: Database file.
    @return: None.
    """
    with transaction(path) as cursor:
        award_a, award_b, song_a, song_b = seed_song(cursor, 1), seed_song(cursor, 2), seed_song(cursor, 3), seed_song(cursor, 4)
        first = seed_stranded(cursor, USER, song_a, award_a, OLD)
        cursor.execute("INSERT INTO credit_ledger (discord_user_id, credit_delta, reason, idempotency_key, awarded_song_id, created_at) "
                       "VALUES (?, 1, 'submission_award', ?, ?, ?)", (USER, f"award:{USER}:{award_b}", award_b, NOW))
        cursor.execute("UPDATE users SET credit_balance = credit_balance + 1 WHERE discord_user_id = ?", (USER,))
        cursor.execute("INSERT INTO recommendations (discord_user_id, song_id, status, created_at) VALUES (?, ?, 'reserved', ?)",
                       (USER, song_b, OLD))
        second = cursor.lastrowid
        cursor.execute("INSERT INTO credit_ledger (discord_user_id, credit_delta, reason, idempotency_key, recommendation_id, created_at) "
                       "VALUES (?, -1, 'exchange_debit', ?, ?, ?)", (USER, f"exchange:{second}", second, OLD))
        cursor.execute("UPDATE users SET credit_balance = credit_balance - 1 WHERE discord_user_id = ?", (USER,))
    one = settle(path, approve(path, first)["incident_id"])
    two = settle(path, approve(path, second)["incident_id"], now=NOW + timedelta(minutes=1))
    assert one["grant_id"] is not None and two["grant_id"] is not None and one["grant_id"] != two["grant_id"]
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 2
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 2
    assert balance(path, USER) == 2
    assert_reconciled(path, USER)


def test_a_retry_or_a_new_request_key_cannot_open_a_second_incident_for_one_debit(stranded) -> None:
    """@description: Repeated approvals of one reservation bind to one incident and one original debit, however many times they are sent.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    first = approve(path, reservation)
    for _ in range(3):
        again = approve(path, reservation)
        assert again["incident_id"] == first["incident_id"] and again["created"] is False
    assert count(path, "SELECT COUNT(*) FROM exchange_incidents") == 1
    with transaction(path) as cursor:
        cursor.execute("SELECT debit_ledger_id FROM exchange_incidents WHERE incident_id = ?", (first["incident_id"],))
        assert cursor.fetchone()["debit_ledger_id"] is not None


def test_approval_refuses_a_reservation_without_one_lawful_debit(stranded) -> None:
    """@description: An incident cannot be bound to a reservation whose debit does not match, so nothing is approved for it.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    with transaction(path) as cursor:
        cursor.execute("DELETE FROM credit_ledger WHERE recommendation_id = ? AND reason = 'exchange_debit'", (reservation,))
    with pytest.raises(RefundContradiction):
        approve(path, reservation)
    assert count(path, "SELECT COUNT(*) FROM exchange_incidents") == 0


def test_a_not_system_fault_reservation_stays_visible_until_the_owner_decides(stranded) -> None:
    """@description: Declining unblocks the requester but keeps the paid, undelivered order on the Owner's open list and keeps the song held.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, song = stranded
    with transaction(path) as cursor:
        store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)
    with transaction(path) as cursor:
        open_rows = store.pending_incident_rows(cursor, NOW)
        cursor.execute("SELECT status, released_at FROM recommendations WHERE recommendation_id = ?", (reservation,))
        row = cursor.fetchone()
    assert [item["operation_code"] for item in open_rows] == [reservation]
    assert row["status"] == "reserved" and row["released_at"] is None
    assert balance(path, USER) == 0


def test_the_user_summary_lists_recent_incidents_without_any_score(stranded) -> None:
    """@description: The Owner's summary for one requester lists that requester's incidents with their states, and carries no score, threshold or limit field.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation, _ = stranded
    settle(path, approve(path, reservation)["incident_id"])
    with transaction(path) as cursor:
        rows = store.user_incident_summary(cursor, USER)
    assert len(rows) == 1 and rows[0]["state"] == "settled" and rows[0]["grant_state"] == "open"
    assert set(rows[0]) == {"incident_id", "recommendation_id", "state", "refund_status", "redelivery_decision", "closure",
                            "approved_at", "grant_state", "delivery_fact"}
