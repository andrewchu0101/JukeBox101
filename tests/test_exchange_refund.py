"""Verify refunds of proven-unsent exchanges are once-only, sourced from the original debit, atomic, and release the song for a later exchange.

Public repository path over the offline fake; no MySQL, Discord, environment file or network is involved.
"""

import random
from datetime import datetime, timedelta, timezone

import pytest

from bot.database.exchange_delivery_store import RefundContradiction
from tests.test_identity_service_path import world  # noqa: F401  (world is a pytest fixture imported by name)
from tests.test_exchange_reservation import (  # noqa: F401  (two_submitters is a pytest fixture imported by name)
    balance,
    two_submitters,
    video_of,
)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=1)


def failed_reservation(repository, user_id: int = 101, key: str = "refund-case"):
    """@description: Create one reservation and mark it proven unsent through the public claim and record transitions.
    @param repository: Real repository over the fake.
    @param user_id: Requesting synthetic user.
    @param key: Request idempotency key.
    @return: Reservation identifier.
    """
    reservation = repository.exchange(user_id, key, random.Random(1))
    claim = repository.claim_delivery(reservation.recommendation_id, "token-" + key, NOW)[2]
    repository.record_delivery(claim, "failed", None, None)
    return reservation.recommendation_id


def refunds_for(database, recommendation_id: int) -> list:
    """@description: List refund ledger rows for one reservation.
    @param database: Offline fake database.
    @param recommendation_id: Reservation identifier.
    @return: Refund rows.
    """
    return [row for row in database.state["credit_ledger"].values()
            if row["reason"] == "delivery_refund" and row["recommendation_id"] == recommendation_id]


def test_a_proven_unsent_reservation_is_refunded_once_from_its_original_debit(two_submitters) -> None:
    """@description: The refund returns exactly the original debit to its recipient, releases the reservation and keeps the history.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    recommendation_id = failed_reservation(repository)
    assert balance(database, 101) == 0
    outcome = repository.refund_undelivered(recommendation_id, LATER)
    assert outcome.status == "refunded" and outcome.amount == 1
    assert balance(database, 101) == 1
    assert [(row["discord_user_id"], row["credit_delta"]) for row in refunds_for(database, recommendation_id)] == [(101, 1)]
    row = database.state["recommendations"][recommendation_id]
    assert row["released_at"] == LATER and row["status"] == "failed"
    assert any(entry["reason"] == "exchange_debit" and entry["recommendation_id"] == recommendation_id
               for entry in database.state["credit_ledger"].values())


def test_a_repeated_refund_changes_nothing(two_submitters) -> None:
    """@description: Calling the refund again, with or without a race, returns already_released and does not return another point.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    recommendation_id = failed_reservation(repository)
    repository.refund_undelivered(recommendation_id, LATER)
    again = repository.refund_undelivered(recommendation_id, LATER + timedelta(minutes=1))
    assert again.status == "already_released" and again.amount == 0
    assert balance(database, 101) == 1 and len(refunds_for(database, recommendation_id)) == 1


@pytest.mark.parametrize("state", ["sending", "uncertain", "delivered"])
def test_a_reservation_not_proven_unsent_is_never_refunded(two_submitters, state) -> None:
    """@description: Sending, uncertain and delivered reservations refuse the refund and write nothing.
    @param two_submitters: Two settled submitters with verified songs.
    @param state: Non-refundable delivery state.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "refund-" + state, random.Random(1))
    database.state["recommendations"][reservation.recommendation_id]["status"] = state
    before = (balance(database, 101), len(database.state["credit_ledger"]))
    assert repository.refund_undelivered(reservation.recommendation_id, LATER).status == "not_refundable"
    assert (balance(database, 101), len(database.state["credit_ledger"])) == before
    assert database.state["recommendations"][reservation.recommendation_id]["released_at"] is None


def test_a_missing_original_debit_stops_the_refund_without_repair(two_submitters) -> None:
    """@description: If the debit that a failed reservation points to is gone, the refund stops and nothing is credited.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    recommendation_id = failed_reservation(repository)
    debit = next(key for key, row in database.state["credit_ledger"].items() if row["reason"] == "exchange_debit")
    del database.state["credit_ledger"][debit]
    with pytest.raises(RefundContradiction):
        repository.refund_undelivered(recommendation_id, LATER)
    assert balance(database, 101) == 0 and refunds_for(database, recommendation_id) == []
    assert database.state["recommendations"][recommendation_id]["released_at"] is None


def test_a_debit_for_another_recipient_stops_the_refund(two_submitters) -> None:
    """@description: A debit whose recipient differs from the reservation is a contradiction; the refund never follows it.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    recommendation_id = failed_reservation(repository)
    debit = next(row for row in database.state["credit_ledger"].values() if row["reason"] == "exchange_debit")
    debit["discord_user_id"] = 102
    with pytest.raises(RefundContradiction):
        repository.refund_undelivered(recommendation_id, LATER)
    assert refunds_for(database, recommendation_id) == []


def test_a_failure_during_the_refund_rolls_back_the_point_the_ledger_and_the_release(two_submitters) -> None:
    """@description: A statement failure inside the refund leaves no credited point, no refund row and no release.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    recommendation_id = failed_reservation(repository)
    database.sql_fault = "UPDATE recommendations SET released_at"
    with pytest.raises(ValueError):
        repository.refund_undelivered(recommendation_id, LATER)
    assert balance(database, 101) == 0 and refunds_for(database, recommendation_id) == []
    assert database.state["recommendations"][recommendation_id]["released_at"] is None


def test_the_released_song_can_be_reserved_again_by_the_same_requester(two_submitters) -> None:
    """@description: After a refund the requester holds no reservation for that song, so the same song may be drawn again.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    first = failed_reservation(repository, key="refund-first")
    repository.refund_undelivered(first, LATER)
    again = repository.exchange(101, "refund-second", random.Random(1))
    assert again.status.value == "reserved" and again.youtube_url == "https://www.youtube.com/watch?v=" + video_of(2).video_id
    assert balance(database, 101) == 0


def test_a_delivered_reservation_is_not_refundable_and_keeps_the_point_spent(two_submitters) -> None:
    """@description: A delivered reservation cannot be refunded, so its point stays spent and the requester cannot exchange again without a point.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "delivered-first", random.Random(1))
    claim = repository.claim_delivery(reservation.recommendation_id, "token-delivered", NOW)[2]
    repository.record_delivery(claim, "delivered", 7101, NOW)
    assert repository.refund_undelivered(reservation.recommendation_id, LATER).status == "not_refundable"
    assert balance(database, 101) == 0
    assert repository.exchange(101, "delivered-second", random.Random(1)).status.value == "insufficient_credits"
