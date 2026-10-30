"""Verify exchange delivery records every DM outcome, refunds only proven non-delivery, never resends a started delivery, and keeps ephemeral replies link-free.

Synthetic reservations, a scripted async DM adapter and an in-memory reply recorder; no Discord, MySQL, environment file or network is involved.
"""

import asyncio
import dataclasses
import random
from datetime import datetime, timedelta, timezone

import pytest

from bot.database.exchange_reservation import ExchangeStatus
from bot.exchange_delivery import IN_PROGRESS_TEXT, DeliveryStatus, DmResult, deliver_recommendation, exchange_and_deliver
from tests.intake_fixture import seed_intake
from tests.test_exchange_reservation import (  # noqa: F401  (two_submitters is a pytest fixture imported by name)
    ORIGINAL_A,
    ORIGINAL_B,
    FirstPicks,
    balance,
    settled_submitter,
    two_submitters,
    verify_all_songs,
    video_of,
    world,
)
from tests.test_identity_service_path import submission

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
LINK = "https://www.youtube.com/watch?v="


class ScriptedDm:
    """@description: Replay scripted DM outcomes as an awaitable adapter and record every attempt, so tests can prove resend prevention.
    @param outcomes: Ordered results or exceptions for successive sends; default is a successful delivery.
    @param database: Fake database whose writer gate must not be held while sending.
    @return: Adapter satisfying the direct-message sender protocol.
    """

    def __init__(self, *outcomes, database=None) -> None:
        """@description: Store the scripted outcomes and the optional fake database for lock inspection.
        @param self: Adapter instance.
        @param outcomes: Ordered results or exceptions.
        @param database: Fake database whose gate must not be held while sending.
        @return: None.
        """
        self.outcomes = list(outcomes)
        self.attempts = []
        self.database = database
        self.gate_held_during_send = False

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Record one attempt and return or raise the next scripted outcome.
        @param self: Adapter instance.
        @param discord_user_id: Synthetic recipient.
        @param text: Direct-message body that must carry the verified link.
        @return: Scripted DM result.
        """
        self.attempts.append((discord_user_id, text))
        if self.database is not None and self.database.gate._is_owned():
            self.gate_held_during_send = True  # [no transaction over network]: Recorded and asserted by the test.
        outcome = self.outcomes.pop(0) if self.outcomes else DmResult.delivered(9001)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RecordingReply:
    """@description: Capture ephemeral replies and optionally fail, to model a reply that cannot be delivered after DM success.
    @param fail: Whether every reply attempt raises.
    @return: Reply sender satisfying the ephemeral protocol.
    """

    def __init__(self, fail: bool = False) -> None:
        """@description: Start with an empty transcript.
        @param self: Reply recorder instance.
        @param fail: Whether sends raise.
        @return: None.
        """
        self.fail = fail
        self.sent = []

    async def send_ephemeral(self, text: str) -> None:
        """@description: Record or refuse one ephemeral reply.
        @param self: Reply recorder instance.
        @param text: Reply body shown only to the requester.
        @return: None.
        """
        if self.fail:
            raise ConnectionError("synthetic reply failure")
        self.sent.append(text)


def start(repository, sender, user_id: int, key: str, replier=None, now: datetime = NOW):
    """@description: Run the complete exchange and delivery flow with the reviewed deterministic draw.
    @param repository: Real repository over the fake.
    @param sender: Scripted DM adapter.
    @param user_id: Requesting synthetic user.
    @param key: Request idempotency key.
    @param replier: Ephemeral reply recorder.
    @param now: Flow time.
    @return: Flow result.
    """
    tokens = iter(f"token-{key}-{index}" for index in range(100))
    return asyncio.run(exchange_and_deliver(repository, sender, replier or RecordingReply(), user_id, key, FirstPicks(1), now,
                                            new_token=lambda: next(tokens)))


def delivery_row(database, recommendation_id: int) -> dict:
    """@description: Read one committed reservation for status assertions.
    @param database: Offline fake database.
    @param recommendation_id: Reservation identifier.
    @return: Committed recommendation row.
    """
    return database.state["recommendations"][recommendation_id]


def refund_rows(database) -> list:
    """@description: List committed refund ledger entries for assertions.
    @param database: Offline fake database.
    @return: Refund ledger rows.
    """
    return [row for row in database.state["credit_ledger"].values() if row["reason"] == "delivery_refund"]


def test_success_delivers_the_verified_link_once_and_records_the_message(two_submitters) -> None:
    """@description: A successful DM is stored as delivered with its message identifier, and the link is sent only in the DM.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender, replier = ScriptedDm(DmResult.delivered(7001)), RecordingReply()
    flow = start(repository, sender, 101, "flow-success", replier)
    assert flow.delivery_status is DeliveryStatus.DELIVERED and flow.refund_status is None
    assert [attempt[0] for attempt in sender.attempts] == [101] and LINK + video_of(2).video_id in sender.attempts[0][1]
    row = delivery_row(database, flow.recommendation_id)
    assert row["status"] == "delivered" and row["discord_message_id"] == 7001 and row["delivered_at"] is not None
    assert balance(database, 101) == 0 and refund_rows(database) == []
    assert all(LINK not in text for text in replier.sent) and replier.sent


def test_explicit_not_sent_is_refunded_once_and_the_song_is_released(two_submitters) -> None:
    """@description: A definitively unsent DM is recorded as failed, refunded from its original debit, and released in the same flow.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    flow = start(repository, ScriptedDm(DmResult.not_sent("dm_closed")), 101, "flow-not-sent")
    assert flow.delivery_status is DeliveryStatus.FAILED and flow.refund_status == "refunded"
    row = delivery_row(database, flow.recommendation_id)
    assert row["status"] == "failed" and row["released_at"] is not None
    assert balance(database, 101) == 1
    assert [(entry["discord_user_id"], entry["credit_delta"]) for entry in refund_rows(database)] == [(101, 1)]
    assert "已退還 1 Nickel" in flow.reply_text and str(flow.recommendation_id) in flow.reply_text


def test_unknown_outcome_becomes_uncertain_and_is_never_refunded_nor_resent(two_submitters) -> None:
    """@description: A timeout is uncertain, keeps its association and debit, and a retry neither refunds nor sends again.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.unknown("timeout"))
    first = start(repository, sender, 101, "flow-unknown")
    second = start(repository, sender, 101, "flow-unknown")
    assert first.delivery_status is DeliveryStatus.UNCERTAIN and second.delivery_status is DeliveryStatus.UNCERTAIN
    assert len(sender.attempts) == 1 and refund_rows(database) == []
    assert delivery_row(database, first.recommendation_id)["status"] == "uncertain"
    assert balance(database, 101) == 0
    assert "不會自動重送" in first.reply_text and str(first.recommendation_id) in first.reply_text


def test_an_exception_from_the_adapter_is_uncertain_not_refused(two_submitters) -> None:
    """@description: Any ordinary exception after the send started cannot prove non-delivery, so it is uncertain and never refunded.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    flow = start(repository, ScriptedDm(ConnectionError("synthetic disconnect")), 101, "flow-disconnect")
    assert flow.delivery_status is DeliveryStatus.UNCERTAIN and flow.refund_status is None
    assert delivery_row(database, flow.recommendation_id)["status"] == "uncertain" and refund_rows(database) == []


def test_the_sending_state_is_committed_before_the_network_call_and_no_lock_is_held_during_it(two_submitters) -> None:
    """@description: The sending state is durable before the DM call, and the writer gate is released while the DM is in flight.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    observed = {}

    class Inspecting(ScriptedDm):
        async def send(self, discord_user_id, text):
            observed["status"] = next(iter(database.state["recommendations"].values()))["status"]
            return await super().send(discord_user_id, text)

    sender = Inspecting(DmResult.delivered(7002), database=database)
    start(repository, sender, 101, "flow-durable")
    assert observed["status"] == "sending" and sender.gate_held_during_send is False


def test_a_crash_during_send_is_recovered_as_uncertain_after_the_lease_and_never_resent(two_submitters) -> None:
    """@description: A process interruption mid-send stays sending until its lease expires, then becomes uncertain, and is never resent or refunded.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        start(repository, sender, 101, "flow-crash")
    recommendation_id = next(iter(database.state["recommendations"]))
    assert delivery_row(database, recommendation_id)["status"] == "sending"
    assert repository.recover_interrupted_deliveries(NOW + timedelta(minutes=1)) == 0  # [live lease]: A fresh claim is never touched.
    assert repository.recover_interrupted_deliveries(NOW + timedelta(minutes=11)) == 1
    assert delivery_row(database, recommendation_id)["status"] == "uncertain"
    assert repository.recover_interrupted_deliveries(NOW + timedelta(minutes=12)) == 0
    again = start(repository, sender, 101, "flow-crash", now=NOW + timedelta(minutes=12))
    assert again.delivery_status is DeliveryStatus.UNCERTAIN and len(sender.attempts) == 1 and refund_rows(database) == []


def test_retry_after_delivery_replays_without_a_second_message_or_debit(two_submitters) -> None:
    """@description: A retried request returns the delivered result and sends nothing new.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.delivered(7003))
    first = start(repository, sender, 101, "flow-retry")
    second = start(repository, sender, 101, "flow-retry")
    assert second.delivery_status is DeliveryStatus.DELIVERED and second.recommendation_id == first.recommendation_id
    assert len(sender.attempts) == 1 and balance(database, 101) == 0


def test_a_second_entry_while_another_worker_is_sending_does_not_send(two_submitters) -> None:
    """@description: A delivery already in the sending state is reported as in progress and is not sent by a second entry.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "flow-busy", random.Random(1))
    database.state["recommendations"][reservation.recommendation_id]["status"] = "sending"
    sender = ScriptedDm(DmResult.delivered(7005))
    result = asyncio.run(deliver_recommendation(repository, sender, reservation.recommendation_id, NOW, "token-busy"))
    assert result.status is DeliveryStatus.IN_PROGRESS and sender.attempts == []


def test_a_late_result_from_another_claim_never_overwrites_the_uncertain_state(two_submitters) -> None:
    """@description: A delivered result carrying a claim other than the one that holds the reservation cannot replace the uncertain state the recovery recorded.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "flow-late", random.Random(1))
    claim = repository.claim_delivery(reservation.recommendation_id, "token-late", NOW)[2]
    repository.recover_interrupted_deliveries(NOW + timedelta(minutes=11))
    other_claim = dataclasses.replace(claim, claim_token="token-late-other")
    stored = repository.record_delivery(other_claim, "delivered", 7008, NOW + timedelta(minutes=12))
    assert stored == "uncertain"
    assert delivery_row(database, reservation.recommendation_id)["discord_message_id"] is None


class LateSweepDm:
    """@description: Model an attempt that stalls past its lease: a recovery sweep runs during the send, then the adapter returns the given result for that same attempt.
    @param repository: Repository the concurrent recovery sweep runs against.
    @param outcome: Result the adapter returns after the sweep.
    @return: Adapter satisfying the direct-message sender protocol.
    """

    def __init__(self, repository, outcome: DmResult) -> None:
        """@description: Keep the repository used by the in-flight sweep, the outcome to return and the attempts it saw.
        @param self: Adapter instance.
        @param repository: Repository the sweep runs against.
        @param outcome: Result returned after the sweep.
        @return: None.
        """
        self.repository = repository
        self.outcome = outcome
        self.attempts = []

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Run the lease sweep mid-send, then return this attempt's result.
        @param self: Adapter instance.
        @param discord_user_id: Synthetic recipient.
        @param text: Direct-message body.
        @return: Scripted result.
        """
        self.attempts.append((discord_user_id, text))
        self.repository.recover_interrupted_deliveries(NOW + timedelta(minutes=11))
        return self.outcome


def test_a_late_explicit_not_sent_after_the_lease_is_refunded_once(two_submitters) -> None:
    """@description: An explicit not-sent result from the same attempt still refunds its point after a recovery sweep marked that attempt uncertain, and a repeat changes nothing.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    balance_before_exchange = balance(database, 101)
    sender = LateSweepDm(repository, DmResult.not_sent("recipient_lookup_failed"))
    flow = start(repository, sender, 101, "flow-late-not-sent")
    assert flow.delivery_status is DeliveryStatus.FAILED and flow.refund_status == "refunded"
    assert delivery_row(database, flow.recommendation_id)["status"] == "failed"
    assert balance(database, 101) == balance_before_exchange and len(refund_rows(database)) == 1
    assert len(sender.attempts) == 1
    again = repository.refund_undelivered(flow.recommendation_id, NOW + timedelta(minutes=12))
    assert again.status == "already_released" and len(refund_rows(database)) == 1


def test_a_not_sent_from_a_different_attempt_never_moves_an_uncertain_reservation(two_submitters) -> None:
    """@description: Only the attempt that holds the claim may move an uncertain reservation to failed, so a stale or foreign not-sent result changes nothing and refunds nothing.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "flow-stale-token", FirstPicks(1))
    claim = repository.claim_delivery(reservation.recommendation_id, "token-current", NOW)[2]
    repository.recover_interrupted_deliveries(NOW + timedelta(minutes=11))
    stale_claim = dataclasses.replace(claim, claim_token="token-stale")
    assert repository.record_delivery(stale_claim, "failed", None, NOW + timedelta(minutes=12)) == "uncertain"
    assert delivery_row(database, reservation.recommendation_id)["status"] == "uncertain"
    assert repository.refund_undelivered(reservation.recommendation_id, NOW + timedelta(minutes=12)).status == "not_refundable"
    assert refund_rows(database) == []


def test_a_late_delivered_result_from_the_same_claim_records_the_message_and_never_refunds(two_submitters) -> None:
    """@description: A late accepted-message result from the claim that holds the reservation completes it as delivered, stores the message identifier and time, and never refunds.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "flow-late-delivered", FirstPicks(1))
    claim = repository.claim_delivery(reservation.recommendation_id, "token-late-delivered", NOW)[2]
    repository.recover_interrupted_deliveries(NOW + timedelta(minutes=11))
    assert repository.record_delivery(claim, "delivered", 7010, NOW + timedelta(minutes=12)) == "delivered"
    row = delivery_row(database, reservation.recommendation_id)
    assert row["status"] == "delivered" and row["discord_message_id"] == 7010 and row["delivered_at"] == NOW + timedelta(minutes=12)
    assert repository.refund_undelivered(reservation.recommendation_id, NOW + timedelta(minutes=12)).status == "not_refundable"
    assert refund_rows(database) == []


def test_a_repeated_delivered_result_changes_nothing_and_keeps_the_first_message(two_submitters) -> None:
    """@description: A second delivered result for the same claim is idempotent: it reports the stored delivery and keeps the first message identifier.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "flow-repeat-delivered", FirstPicks(1))
    claim = repository.claim_delivery(reservation.recommendation_id, "token-repeat-delivered", NOW)[2]
    repository.recover_interrupted_deliveries(NOW + timedelta(minutes=11))
    repository.record_delivery(claim, "delivered", 7010, NOW + timedelta(minutes=12))
    assert repository.record_delivery(claim, "delivered", 7011, NOW + timedelta(minutes=13)) == "delivered"
    row = delivery_row(database, reservation.recommendation_id)
    assert row["discord_message_id"] == 7010 and row["delivered_at"] == NOW + timedelta(minutes=12)


def test_a_late_delivered_result_from_a_different_claim_never_completes_an_uncertain_reservation(two_submitters) -> None:
    """@description: Only the claim that owns the reservation may complete it, so a delivered result carrying a foreign token leaves the uncertain state and its message identifier untouched.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "flow-foreign-delivered", FirstPicks(1))
    claim = repository.claim_delivery(reservation.recommendation_id, "token-owner", NOW)[2]
    repository.recover_interrupted_deliveries(NOW + timedelta(minutes=11))
    foreign_claim = dataclasses.replace(claim, claim_token="token-foreign")
    assert repository.record_delivery(foreign_claim, "delivered", 7012, NOW + timedelta(minutes=12)) == "uncertain"
    row = delivery_row(database, reservation.recommendation_id)
    assert row["status"] == "uncertain" and row["discord_message_id"] is None


@pytest.mark.parametrize("later_status", ["failed", "uncertain"])
def test_a_later_not_sent_or_unknown_result_from_the_same_claim_never_overwrites_delivered(two_submitters, later_status) -> None:
    """@description: Once the same claim has recorded delivered, a later not-sent or unknown result cannot change the delivered state, its message identifier, or the refund ledger.
    @param two_submitters: Two settled submitters with verified songs.
    @param later_status: Later result from the same claim, not-sent or unknown.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "flow-delivered-then-" + later_status, FirstPicks(1))
    claim = repository.claim_delivery(reservation.recommendation_id, "token-delivered-then-" + later_status, NOW)[2]
    assert repository.record_delivery(claim, "delivered", 7014, NOW + timedelta(minutes=1)) == "delivered"
    assert repository.record_delivery(claim, later_status, None, None) == "delivered"
    row = delivery_row(database, reservation.recommendation_id)
    assert row["status"] == "delivered" and row["discord_message_id"] == 7014 and row["released_at"] is None
    assert refund_rows(database) == []


def test_a_delivered_result_after_a_refund_never_overwrites_the_released_failure(two_submitters) -> None:
    """@description: Delivery and refund never both hold: once a same-claim not-sent result has been refunded, a later delivered result for that claim changes nothing.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "flow-refund-then-delivered", FirstPicks(1))
    claim = repository.claim_delivery(reservation.recommendation_id, "token-refund-then-delivered", NOW)[2]
    repository.recover_interrupted_deliveries(NOW + timedelta(minutes=11))
    assert repository.record_delivery(claim, "failed", None, NOW + timedelta(minutes=12)) == "failed"
    assert repository.refund_undelivered(reservation.recommendation_id, NOW + timedelta(minutes=12)).status == "refunded"
    assert repository.record_delivery(claim, "delivered", 7013, NOW + timedelta(minutes=13)) == "failed"
    row = delivery_row(database, reservation.recommendation_id)
    assert row["status"] == "failed" and row["discord_message_id"] is None and row["released_at"] is not None
    assert len(refund_rows(database)) == 1


def test_reply_failure_after_a_successful_dm_changes_nothing(two_submitters) -> None:
    """@description: If the ephemeral reply fails after the DM succeeded, the delivery stays delivered, nothing is refunded, and nothing is resent.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.delivered(7006))
    flow = start(repository, sender, 101, "flow-reply-fail", RecordingReply(fail=True))
    assert flow.delivery_status is DeliveryStatus.DELIVERED and flow.reply_failed is True
    assert delivery_row(database, flow.recommendation_id)["status"] == "delivered"
    assert len(sender.attempts) == 1 and balance(database, 101) == 0 and refund_rows(database) == []


def test_no_candidate_and_insufficient_credits_send_nothing_and_say_so(world) -> None:
    """@description: Both refusals reply with the reviewed wording and never send a DM or change a balance.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    settled_submitter(database, service, repository, 1, ORIGINAL_A)
    seed_intake(repository, submission(3))  # [zero-point requester]: A real account with no settled award and no points.
    verify_all_songs(database)
    sender = ScriptedDm()
    own_only = start(repository, sender, 101, "flow-own-only")
    assert own_only.reply_text == "目前沒有符合條件的歌曲"
    broke = start(repository, sender, 103, "flow-broke")
    assert broke.reply_text == "Nickel 不足，交換一首歌曲需要 1 Nickel。"
    assert sender.attempts == [] and balance(database, 101) == 1


@pytest.mark.parametrize("outcome", [DmResult.not_sent("dm_closed"), DmResult.unknown("timeout"), DmResult.delivered(7007)])
def test_ephemeral_text_never_contains_the_link_in_any_dm_outcome(two_submitters, outcome) -> None:
    """@description: The requester's ephemeral reply is link-free in every outcome, so a failed DM never leaks the link into the channel.
    @param two_submitters: Two settled submitters with verified songs.
    @param outcome: Scripted DM outcome.
    @return: None.
    """
    _, repository, _ = two_submitters
    flow = start(repository, ScriptedDm(outcome), 101, "flow-link")
    assert LINK not in flow.reply_text


def test_a_new_request_while_an_exchange_is_open_is_told_to_wait_and_sends_and_refunds_nothing(two_submitters) -> None:
    """@description: A requester with an open reservation gets the wait-and-retry reply before any balance check, with no DM, no refund and no second reservation.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    first = repository.exchange(101, "exchange-first", FirstPicks(1))
    assert first.status is ExchangeStatus.RESERVED
    sender, replier = ScriptedDm(), RecordingReply()
    start(repository, sender, 101, "discord:second", replier)
    assert replier.sent == [IN_PROGRESS_TEXT]
    assert sender.attempts == [] and refund_rows(database) == []
    assert delivery_row(database, first.recommendation_id)["status"] == "reserved"
    assert len(database.state["recommendations"]) == 1
