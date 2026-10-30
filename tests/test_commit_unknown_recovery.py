"""Verify the recovery of unknown commits at the claim and result-recording steps of exchange delivery, offline and against the real repository.

The production SubmissionRepository runs over the in-memory fake database; a proxy injects one commit fault per scripted call, so a commit can be applied and unacknowledged, or not applied at all. Database writes that are replayed carry the same claim token; the direct message is never replayed.
"""

import pytest

from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.exchange_delivery import DeliveryStatus, DmResult
from tests.test_exchange_delivery import NOW, RecordingReply, ScriptedDm, balance, delivery_row, refund_rows, start
from tests.test_exchange_reservation import (  # noqa: F401  (two_submitters is a pytest fixture imported by name)
    two_submitters,
    world,
)

USER = 101  # [synthetic requester]: The first settled submitter from the shared fixture.


class FaultPerCall:
    """@description: Arm one commit fault per call of a named repository method, so a replay meets a healthy commit once the scripted faults run out.
    @param repository: Production repository over the offline fake database.
    @param database: Fake database whose one-shot commit fault is armed before each faulted call.
    @param method_name: Repository method whose commits are faulted.
    @param modes: Fault per successive call: 'after' commits and loses the acknowledgement, 'before' loses the commit itself.
    @return: Proxy exposing the repository surface unchanged for every other method.
    """

    def __init__(self, repository, database, method_name: str, modes: tuple) -> None:
        """@description: Store the proxied repository and the scripted fault sequence.
        @param self: Proxy instance.
        @param repository: Production repository.
        @param database: Fake database whose commit fault is armed.
        @param method_name: Faulted method name.
        @param modes: Scripted fault per call.
        @return: None.
        """
        self._repository = repository
        self._database = database
        self._method_name = method_name
        self._modes = list(modes)
        self.calls = 0  # [call count]: Selects the scripted fault for the next call.

    def __getattr__(self, name: str):
        """@description: Forward every attribute, wrapping only the faulted method so each call arms its scripted fault.
        @param self: Proxy instance.
        @param name: Attribute requested by the production caller.
        @return: The repository attribute, or a wrapper that arms the next fault first.
        """
        attribute = getattr(self._repository, name)
        if name != self._method_name:
            return attribute

        def faulted_call(*args, **kwargs):
            """@description: Arm the scripted fault for this call and forward it to the real repository.
            @param args: Positional arguments of the production call.
            @param kwargs: Keyword arguments of the production call.
            @return: Whatever the real repository method returns.
            """
            if self.calls < len(self._modes):
                self._database.commit_fault = self._modes[self.calls]  # [one-shot fault]: Consumed by the commit of this call.
            self.calls += 1
            return attribute(*args, **kwargs)

        return faulted_call


def test_claim_commit_applied_but_unacknowledged_still_sends_once_and_delivers(two_submitters) -> None:
    """@description: A claim whose commit landed but whose acknowledgement was lost is replayed with the same token, so the one DM is sent and recorded as delivered, never refunded.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.delivered(7101), database=database)
    flow = start(FaultPerCall(repository, database, "claim_delivery", ("after",)), sender, USER, "flow-claim-after")
    assert database.commit_fault is None  # [stage reached]: The fault was consumed by the claim commit.
    assert len(sender.attempts) == 1
    assert flow.delivery_status is DeliveryStatus.DELIVERED and flow.refund_status is None
    row = delivery_row(database, flow.recommendation_id)
    assert row["status"] == "delivered" and row["discord_message_id"] == 7101
    assert refund_rows(database) == [] and balance(database, USER) == 0


def test_claim_commit_not_applied_is_replayed_once_and_still_delivers(two_submitters) -> None:
    """@description: A claim whose commit never happened is claimed by the replay with the same token, then delivered exactly once.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.delivered(7102), database=database)
    flow = start(FaultPerCall(repository, database, "claim_delivery", ("before",)), sender, USER, "flow-claim-before")
    assert database.commit_fault is None
    assert len(sender.attempts) == 1 and flow.delivery_status is DeliveryStatus.DELIVERED
    assert delivery_row(database, flow.recommendation_id)["discord_message_id"] == 7102
    assert refund_rows(database) == [] and balance(database, USER) == 0


def test_claim_unknown_twice_sends_nothing_and_raises_the_unknown_outcome(two_submitters) -> None:
    """@description: When the claim replay is also unknown, no direct message is attempted and the unknown outcome reaches the caller.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(database=database)
    with pytest.raises(CommitOutcomeUnknown):
        start(FaultPerCall(repository, database, "claim_delivery", ("after", "after")), sender, USER, "flow-claim-twice")
    assert sender.attempts == [] and refund_rows(database) == []


def test_not_sent_commit_applied_but_unacknowledged_is_refunded_once_in_the_same_flow(two_submitters) -> None:
    """@description: A proven non-delivery whose recording committed without acknowledgement is replayed, found already failed, and refunded exactly once from the original debit.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.not_sent("dm_closed"), database=database)
    flow = start(FaultPerCall(repository, database, "record_delivery", ("after",)), sender, USER, "flow-not-sent-after")
    assert database.commit_fault is None
    assert len(sender.attempts) == 1
    assert flow.delivery_status is DeliveryStatus.FAILED and flow.refund_status == "refunded"
    row = delivery_row(database, flow.recommendation_id)
    assert row["status"] == "failed" and row["released_at"] is not None
    assert [entry["credit_delta"] for entry in refund_rows(database)] == [1] and balance(database, USER) == 1


def test_not_sent_commit_not_applied_is_replayed_and_then_refunded_once(two_submitters) -> None:
    """@description: A proven non-delivery whose recording never committed is recorded by the replay with the same token, then refunded exactly once.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.not_sent("dm_closed"), database=database)
    flow = start(FaultPerCall(repository, database, "record_delivery", ("before",)), sender, USER, "flow-not-sent-before")
    assert database.commit_fault is None
    assert flow.delivery_status is DeliveryStatus.FAILED and flow.refund_status == "refunded"
    assert len(sender.attempts) == 1 and balance(database, USER) == 1
    assert [entry["credit_delta"] for entry in refund_rows(database)] == [1]


def test_not_sent_unknown_twice_is_left_to_the_refund_sweep_and_refunded_once(two_submitters) -> None:
    """@description: When both recordings of a proven non-delivery are unknown, the request does not guess a refund; the committed failure is refunded by the sweep exactly once.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.not_sent("dm_closed"), database=database)
    with pytest.raises(CommitOutcomeUnknown):
        start(FaultPerCall(repository, database, "record_delivery", ("after", "after")), sender, USER, "flow-not-sent-twice")
    assert refund_rows(database) == [] and balance(database, USER) == 0
    repository.retry_pending_refunds(NOW)
    repository.retry_pending_refunds(NOW)
    assert [entry["credit_delta"] for entry in refund_rows(database)] == [1]
    assert balance(database, USER) == 1 and len(sender.attempts) == 1


def test_delivered_commit_applied_but_unacknowledged_keeps_the_message_and_never_refunds(two_submitters) -> None:
    """@description: An accepted DM whose recording committed without acknowledgement is recorded with its message identifier by the replay and is never refunded.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.delivered(7201), database=database)
    flow = start(FaultPerCall(repository, database, "record_delivery", ("after",)), sender, USER, "flow-delivered-after")
    assert database.commit_fault is None
    assert flow.delivery_status is DeliveryStatus.DELIVERED
    row = delivery_row(database, flow.recommendation_id)
    assert row["status"] == "delivered" and row["discord_message_id"] == 7201
    assert len(sender.attempts) == 1 and refund_rows(database) == []


def test_delivered_commit_not_applied_is_replayed_and_keeps_the_message(two_submitters) -> None:
    """@description: An accepted DM whose recording never committed is stored by the replay with its message identifier, once.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.delivered(7202), database=database)
    flow = start(FaultPerCall(repository, database, "record_delivery", ("before",)), sender, USER, "flow-delivered-before")
    assert flow.delivery_status is DeliveryStatus.DELIVERED
    assert delivery_row(database, flow.recommendation_id)["discord_message_id"] == 7202
    assert len(sender.attempts) == 1 and refund_rows(database) == []


def test_uncertain_commit_applied_but_unacknowledged_stays_uncertain_and_is_never_refunded(two_submitters) -> None:
    """@description: An unknown DM outcome whose recording committed without acknowledgement stays uncertain; the replay neither refunds nor resends it.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = ScriptedDm(DmResult.unknown("timeout"), database=database)
    flow = start(FaultPerCall(repository, database, "record_delivery", ("after",)), sender, USER, "flow-uncertain-after")
    assert database.commit_fault is None
    assert flow.delivery_status is DeliveryStatus.UNCERTAIN and flow.refund_status is None
    assert delivery_row(database, flow.recommendation_id)["status"] == "uncertain"
    repository.retry_pending_refunds(NOW)
    assert len(sender.attempts) == 1 and refund_rows(database) == [] and balance(database, USER) == 0
