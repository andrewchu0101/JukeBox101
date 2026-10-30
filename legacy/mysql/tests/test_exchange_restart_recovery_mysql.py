"""Verify exchange restart recovery against the isolated MySQL instance: a reserved exchange is delivered once after a restart, a lost send is never resent, and clock skew only changes classification.

Requires the explicit isolated target and its pinned identity; every write goes to the disposable isolated schema only. Each scenario builds a new repository over the same database to model a restarted process.
"""

import asyncio
import os
from datetime import timedelta

import pytest

from bot.database.exchange_reservation import ExchangeStatus
from bot.database.submission_repository import SubmissionRepository
from bot.exchange_delivery import DeliveryStatus, DmResult, exchange_and_deliver
from tests.isolated_mysql_target import ISOLATION_SWITCH, open_verified_connection
from tests.test_exchange_delivery import NOW, LateSweepDm, RecordingReply, ScriptedDm, start
from tests.test_exchange_reservation import FirstPicks
from tests.test_exchange_mysql_integration import isolated  # noqa: F401  (fixture imported by name)
from tests.test_exchange_repository_mysql import OWNER_ID, USER_A, funded_pair, repository_world  # noqa: F401  (fixtures imported by name)

pytestmark = [pytest.mark.mysql, pytest.mark.skipif(
    os.environ.get(ISOLATION_SWITCH) != "1", reason="Requires explicitly authorized isolated MySQL target")]


def _restarted_repository(target) -> SubmissionRepository:
    """@description: Model a restarted process: a new repository with new connections over the same isolated schema.
    @param target: Verified isolated destination.
    @return: Fresh production repository.
    """
    settings, pin = target
    return SubmissionRepository(lambda: open_verified_connection(settings, pin), OWNER_ID)


def _delivery_state(target, recommendation_id: int) -> str:
    """@description: Read the committed delivery status of one reservation from the isolated database.
    @param target: Verified isolated destination.
    @param recommendation_id: Reservation identifier.
    @return: Committed status text.
    """
    settings, pin = target
    connection = open_verified_connection(settings, pin)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT status FROM recommendations WHERE recommendation_id=%s", (recommendation_id,))
            return cursor.fetchone()["status"]
    finally:
        connection.close()


def _debit_count(target, discord_user_id: int) -> int:
    """@description: Count committed exchange debits for one requester, so a second debit would be visible.
    @param target: Verified isolated destination.
    @param discord_user_id: Requesting user.
    @return: Number of exchange_debit ledger rows.
    """
    settings, pin = target
    connection = open_verified_connection(settings, pin)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM credit_ledger WHERE reason='exchange_debit' AND discord_user_id=%s",
                           (discord_user_id,))
            return cursor.fetchone()["total"]
    finally:
        connection.close()


def _refund_count(target) -> int:
    """@description: Count committed delivery refunds, which must stay zero for a lost or uncertain send.
    @param target: Verified isolated destination.
    @return: Number of delivery_refund ledger rows.
    """
    settings, pin = target
    connection = open_verified_connection(settings, pin)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM credit_ledger WHERE reason='delivery_refund'")
            return cursor.fetchone()["total"]
    finally:
        connection.close()


def _reserved_recommendation_id(target) -> int:
    """@description: Find the single reservation created by the scenario, failing loudly if there is not exactly one.
    @param target: Verified isolated destination.
    @return: Recommendation identifier.
    """
    settings, pin = target
    connection = open_verified_connection(settings, pin)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT recommendation_id FROM recommendations")
            rows = cursor.fetchall()
    finally:
        connection.close()
    assert len(rows) == 1, f"expected exactly one reservation, found {len(rows)}"
    return rows[0]["recommendation_id"]


def test_reserved_exchange_is_delivered_once_after_a_restart(funded_pair):
    """@description: An exchange reserved before a restart is replayed by its key in a new process and delivered exactly once, with one debit in the database.
    @param funded_pair: Two funded submitters over the isolated MySQL target.
    @return: None.
    """
    target, repository, _ = funded_pair
    first = repository.exchange(USER_A, "t24-restart-reserved", FirstPicks(1), NOW)
    assert first.status is ExchangeStatus.RESERVED
    assert _debit_count(target, USER_A) == 1

    sender = ScriptedDm()
    result = asyncio.run(exchange_and_deliver(_restarted_repository(target), sender, RecordingReply(), USER_A,
                                              "t24-restart-reserved", FirstPicks(1), NOW + timedelta(minutes=1),
                                              new_token=lambda: "token-t24-reserved"))

    assert result.delivery_status is DeliveryStatus.DELIVERED
    assert len(sender.attempts) == 1
    assert _delivery_state(target, first.recommendation_id) == "delivered"
    assert _debit_count(target, USER_A) == 1 and _refund_count(target) == 0


def test_lost_send_is_uncertain_after_the_lease_on_a_new_process_and_never_resent(funded_pair):
    """@description: A send interrupted by a crash stays sending while its lease is live, becomes uncertain after the lease in a new process, and is never resent, refunded or debited again.
    @param funded_pair: Two funded submitters over the isolated MySQL target.
    @return: None.
    """
    target, _, _ = funded_pair
    crashing_sender = ScriptedDm(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        start(_restarted_repository(target), crashing_sender, USER_A, "t24-lost-send")
    recommendation_id = _reserved_recommendation_id(target)
    assert _delivery_state(target, recommendation_id) == "sending"

    live_sender = ScriptedDm()
    live = asyncio.run(exchange_and_deliver(_restarted_repository(target), live_sender, RecordingReply(), USER_A,
                                            "t24-lost-send", FirstPicks(1), NOW + timedelta(minutes=1),
                                            new_token=lambda: "token-t24-live"))
    assert live.delivery_status is not DeliveryStatus.DELIVERED and live_sender.attempts == []
    assert _delivery_state(target, recommendation_id) == "sending"

    expired_sender = ScriptedDm()
    expired = asyncio.run(exchange_and_deliver(_restarted_repository(target), expired_sender, RecordingReply(), USER_A,
                                               "t24-lost-send", FirstPicks(1), NOW + timedelta(minutes=11),
                                               new_token=lambda: "token-t24-expired"))
    assert expired.delivery_status is DeliveryStatus.UNCERTAIN
    assert _delivery_state(target, recommendation_id) == "uncertain"
    assert expired_sender.attempts == [] and _refund_count(target) == 0
    assert _debit_count(target, USER_A) == 1


@pytest.mark.parametrize("offset_minutes, label", [(2, "ahead"), (-3, "behind")])
def test_clock_skew_only_moves_the_classification_on_the_real_database(funded_pair, offset_minutes, label):
    """@description: A recovering process whose clock runs ahead or behind classifies a lost send earlier or later, but the database never resends, refunds or debits it again.
    @param funded_pair: Two funded submitters over the isolated MySQL target.
    @param offset_minutes: Clock offset of the recovering process relative to the claimer.
    @param label: Human-readable direction, used only in the test id.
    @return: None.
    """
    target, _, _ = funded_pair
    request_key = f"t24-skew-{label}"
    crashing_sender = ScriptedDm(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        start(_restarted_repository(target), crashing_sender, USER_A, request_key)
    recommendation_id = _reserved_recommendation_id(target)
    skewed_now = NOW + timedelta(minutes=11) + timedelta(minutes=offset_minutes)

    sender = ScriptedDm()
    result = asyncio.run(exchange_and_deliver(_restarted_repository(target), sender, RecordingReply(), USER_A,
                                              request_key, FirstPicks(1), skewed_now,
                                              new_token=lambda: f"token-{request_key}"))

    assert sender.attempts == [] and _refund_count(target) == 0
    assert _debit_count(target, USER_A) == 1
    assert result.delivery_status is not DeliveryStatus.DELIVERED
    # [direction decides classification]: Ahead by 2 minutes crosses the 10-minute lease; behind by 3 minutes does not.
    expected_state = "uncertain" if offset_minutes > 0 else "sending"
    assert _delivery_state(target, recommendation_id) == expected_state


def test_a_late_explicit_not_sent_after_the_lease_is_refunded_once_on_the_real_database(funded_pair):
    """@description: An explicit not-sent result from the attempt that held the claim still refunds its point after a lease sweep marked it uncertain, once and on the real database.
    @param funded_pair: Two funded submitters over the isolated MySQL target.
    @return: None.
    """
    target, _, _ = funded_pair
    sender = LateSweepDm(_restarted_repository(target), DmResult.not_sent("recipient_lookup_failed"))
    flow = start(_restarted_repository(target), sender, USER_A, "t24-late-not-sent")
    assert flow.delivery_status is DeliveryStatus.FAILED and flow.refund_status == "refunded"
    assert _delivery_state(target, flow.recommendation_id) == "failed"
    assert _debit_count(target, USER_A) == 1 and _refund_count(target) == 1
    assert len(sender.attempts) == 1


def _message_id(target, recommendation_id: int):
    """@description: Read the stored Discord message identifier of one reservation for the late-delivery assertion.
    @param target: Verified isolated destination.
    @param recommendation_id: Reservation identifier.
    @return: Stored message identifier, or None.
    """
    settings, pin = target
    connection = open_verified_connection(settings, pin)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT discord_message_id FROM recommendations WHERE recommendation_id=%s", (recommendation_id,))
            return cursor.fetchone()["discord_message_id"]
    finally:
        connection.close()


def test_a_late_delivered_result_from_the_same_claim_is_recorded_once_and_never_refunded_on_the_real_database(funded_pair):
    """@description: An accepted-message result from the claim that holds the reservation completes it after a lease sweep, stores its message identifier once, and never refunds.
    @param funded_pair: Two funded submitters over the isolated MySQL target.
    @return: None.
    """
    target, _, _ = funded_pair
    sender = LateSweepDm(_restarted_repository(target), DmResult.delivered(7010))
    flow = start(_restarted_repository(target), sender, USER_A, "t24-late-delivered")
    assert flow.delivery_status is DeliveryStatus.DELIVERED and flow.refund_status is None
    assert _delivery_state(target, flow.recommendation_id) == "delivered"
    assert _message_id(target, flow.recommendation_id) == 7010
    assert _debit_count(target, USER_A) == 1 and _refund_count(target) == 0
