"""Verify an exchange interrupted by a process restart is recognised from the database, never debited twice, and never resent.

Each scenario builds a new repository over the same fake database to model a restarted process; no Discord, MySQL, environment file or network is involved.
"""

import asyncio
from datetime import timedelta

import pytest

from bot.database.exchange_reservation import ExchangeStatus
from bot.database.submission_repository import SubmissionRepository
from bot.exchange_delivery import DeliveryStatus, exchange_and_deliver
from tests.test_exchange_delivery import (  # noqa: F401  (fixtures and helpers are imported by name)
    NOW,
    FirstPicks,
    RecordingReply,
    ScriptedDm,
    delivery_row,
    refund_rows,
    start,
    two_submitters,
)
from tests.test_exchange_reservation import OWNER_ID, balance, world  # noqa: F401

__all__ = ["two_submitters", "world"]


def _restarted_repository(database) -> SubmissionRepository:
    """@description: Model a restarted process by building a new repository over the same database, so nothing from the earlier process memory is reused.
    @param database: Fake database that still holds the committed state.
    @return: Fresh repository instance.
    """
    return SubmissionRepository(database.connect, OWNER_ID)


def _credit_debits(database, discord_user_id: int) -> list:
    """@description: List every committed exchange debit for one user, so duplicate debits are visible.
    @param database: Offline fake database.
    @param discord_user_id: Requesting synthetic user.
    @return: Ledger rows with the exchange debit reason for that user.
    """
    return [row for row in database.state["credit_ledger"].values()
            if row["reason"] == "exchange_debit" and row["discord_user_id"] == discord_user_id]


def test_reserved_exchange_after_restart_is_delivered_once_without_a_second_debit(two_submitters):
    """@description: An exchange that committed its debit and reservation before the restart is replayed by its key and delivered exactly once.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    requester = 101
    balance_before_exchange = balance(database, requester)
    first_process_outcome = repository.exchange(requester, "flow-restart-reserved", FirstPicks(1), NOW)
    assert first_process_outcome.status is ExchangeStatus.RESERVED
    assert balance(database, requester) == balance_before_exchange - 1

    restarted_sender = ScriptedDm()
    restarted_repository = _restarted_repository(database)
    result = asyncio.run(exchange_and_deliver(restarted_repository, restarted_sender, RecordingReply(), requester,
                                              "flow-restart-reserved", FirstPicks(1), NOW + timedelta(minutes=1),
                                              new_token=lambda: "token-restart-reserved"))

    assert result.delivery_status is DeliveryStatus.DELIVERED
    assert len(restarted_sender.attempts) == 1
    assert len(_credit_debits(database, requester)) == 1
    assert balance(database, requester) == balance_before_exchange - 1


def test_sending_interrupted_by_restart_is_uncertain_on_a_new_process_and_never_resent(two_submitters):
    """@description: A send interrupted by a crash stays uncertain across a restart once its lease has expired, and the new process never resends or refunds it.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    requester = 101
    crashing_sender = ScriptedDm(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        start(repository, crashing_sender, requester, "flow-restart-crash")
    recommendation_id = next(iter(database.state["recommendations"]))
    assert delivery_row(database, recommendation_id)["status"] == "sending"

    restarted_sender = ScriptedDm()
    restarted_repository = _restarted_repository(database)
    result = asyncio.run(exchange_and_deliver(restarted_repository, restarted_sender, RecordingReply(), requester,
                                              "flow-restart-crash", FirstPicks(1), NOW + timedelta(minutes=11),
                                              new_token=lambda: "token-restart-crash"))

    assert result.delivery_status is DeliveryStatus.UNCERTAIN
    assert delivery_row(database, recommendation_id)["status"] == "uncertain"
    assert restarted_sender.attempts == []
    assert refund_rows(database) == []
    assert len(_credit_debits(database, requester)) == 1


def test_a_live_send_is_not_taken_over_by_a_restarted_process_before_its_lease_expires(two_submitters):
    """@description: Before the sending lease expires, a restarted process must not claim or sweep the row, so a still-live attempt is never duplicated.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    requester = 101
    crashing_sender = ScriptedDm(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        start(repository, crashing_sender, requester, "flow-restart-live")
    recommendation_id = next(iter(database.state["recommendations"]))

    restarted_sender = ScriptedDm()
    restarted_repository = _restarted_repository(database)
    result = asyncio.run(exchange_and_deliver(restarted_repository, restarted_sender, RecordingReply(), requester,
                                              "flow-restart-live", FirstPicks(1), NOW + timedelta(minutes=1),
                                              new_token=lambda: "token-restart-live"))

    assert result.delivery_status is not DeliveryStatus.DELIVERED
    assert restarted_sender.attempts == []
    assert delivery_row(database, recommendation_id)["status"] == "sending"


def test_a_process_clock_ahead_of_the_claimer_only_moves_the_uncertain_classification(two_submitters):
    """@description: Application time decides when a lost send is classified, so a recovering process whose clock runs ahead may classify it early, but it still never resends, refunds or debits again.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    requester = 101
    crashing_sender = ScriptedDm(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        start(repository, crashing_sender, requester, "flow-skew-ahead")
    recommendation_id = next(iter(database.state["recommendations"]))
    skewed_ahead_now = NOW + timedelta(minutes=9)  # [clock ahead by 2 minutes]: The real elapsed time is 7 minutes, below the lease.
    restarted_sender = ScriptedDm()
    result = asyncio.run(exchange_and_deliver(_restarted_repository(database), restarted_sender, RecordingReply(), requester,
                                              "flow-skew-ahead", FirstPicks(1), skewed_ahead_now + timedelta(minutes=2),
                                              new_token=lambda: "token-skew-ahead"))
    assert result.delivery_status is DeliveryStatus.UNCERTAIN
    assert delivery_row(database, recommendation_id)["status"] == "uncertain"
    assert restarted_sender.attempts == [] and refund_rows(database) == []
    assert len(_credit_debits(database, requester)) == 1


def test_a_process_clock_behind_the_claimer_keeps_the_sending_row_longer_without_resending(two_submitters):
    """@description: A recovering process whose clock runs behind keeps a lost send in sending for longer, which is the safe direction: nothing is resent, refunded or debited again.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    requester = 101
    crashing_sender = ScriptedDm(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        start(repository, crashing_sender, requester, "flow-skew-behind")
    recommendation_id = next(iter(database.state["recommendations"]))
    restarted_sender = ScriptedDm()
    result = asyncio.run(exchange_and_deliver(_restarted_repository(database), restarted_sender, RecordingReply(), requester,
                                              "flow-skew-behind", FirstPicks(1), NOW + timedelta(minutes=11) - timedelta(minutes=3),
                                              new_token=lambda: "token-skew-behind"))
    assert result.delivery_status is not DeliveryStatus.DELIVERED
    assert delivery_row(database, recommendation_id)["status"] == "sending"
    assert restarted_sender.attempts == [] and refund_rows(database) == []
    assert len(_credit_debits(database, requester)) == 1
