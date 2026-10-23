"""Verify the labelled synthetic exchange path through the real service and repository, including refund retry, uncertainty and lease recovery.

Offline fake database, mock Discord adapters and a fixed clock; no Discord, MySQL, environment file, network or real points are involved.
"""

import asyncio
import random
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bot import exchange_delivery
from bot.database.exchange_delivery_store import DELIVERY_LEASE
from bot.exchange_delivery import DM_TIMEOUT_SECONDS, DmResult
from bot.services import SYNTHETIC_LABEL, MusicService, ServiceUnavailable
from tests.test_exchange_reservation import (  # noqa: F401  (two_submitters is a pytest fixture imported by name)
    balance,
    two_submitters,
    video_of,
)
from tests.test_identity_service_path import world  # noqa: F401  (world is a pytest fixture imported by name)

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
LINK = "https://www.youtube.com/watch?v="


class Dm:
    """@description: Scripted awaitable DM adapter that records each attempt, so tests can prove no resend.
    @param outcomes: Ordered DM results or exceptions.
    @return: Adapter with an attempts list.
    """

    def __init__(self, *outcomes) -> None:
        """@description: Store the scripted outcomes.
        @param self: Adapter instance.
        @param outcomes: Ordered results or exceptions.
        @return: None.
        """
        self.outcomes = list(outcomes)
        self.attempts = []

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Record the attempt and return or raise the next scripted outcome.
        @param self: Adapter instance.
        @param discord_user_id: Recipient.
        @param text: Message body.
        @return: Scripted result.
        """
        self.attempts.append((discord_user_id, text))
        outcome = self.outcomes.pop(0) if self.outcomes else DmResult.delivered(9100 + len(self.attempts))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class Reply:
    """@description: Recording ephemeral adapter that can fail, to model a lost reply after a successful DM.
    @param fail: Whether sends raise.
    @return: Adapter with a sent list.
    """

    def __init__(self, fail: bool = False) -> None:
        """@description: Start with an empty transcript.
        @param self: Adapter instance.
        @param fail: Whether sends raise.
        @return: None.
        """
        self.fail = fail
        self.sent = []

    async def send_ephemeral(self, text: str) -> None:
        """@description: Record or refuse one reply.
        @param self: Adapter instance.
        @param text: Reply body.
        @return: None.
        """
        if self.fail:
            raise ConnectionError("synthetic")
        self.sent.append(text)


def service_for(repository) -> MusicService:
    """@description: Build the real service with the synthetic route enabled over the injected repository.
    @param repository: Offline repository.
    @return: Service whose /exchange route is the labelled synthetic flow.
    """
    return MusicService(MagicMock(), submission_repository=repository, synthetic_exchange=True)


def run(service, sender, replier, user_id: int, key: str):
    """@description: Run one synthetic exchange request through the service entry point.
    @param service: Synthetic-enabled service.
    @param sender: DM adapter.
    @param replier: Reply adapter.
    @param user_id: Requester.
    @param key: Request key.
    @return: Flow result.
    """
    return asyncio.run(service.synthetic_exchange(user_id, key, sender, replier))


def row(database, recommendation_id: int) -> dict:
    """@description: Read one committed reservation row.
    @param database: Offline fake database.
    @param recommendation_id: Reservation identifier.
    @return: Committed row.
    """
    return database.state["recommendations"][recommendation_id]


def test_success_is_labelled_delivered_and_its_reply_has_no_link(two_submitters) -> None:
    """@description: The synthetic success path delivers one labelled DM and a labelled, link-free reply.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender, replier = Dm(DmResult.delivered(7001)), Reply()
    flow = run(service_for(repository), sender, replier, 101, "discord:1")
    assert flow.delivery_status.value == "delivered" and flow.refund_status is None
    assert sender.attempts[0][1].startswith(SYNTHETIC_LABEL) and LINK + video_of(2).video_id in sender.attempts[0][1]
    assert replier.sent and all(text.startswith(SYNTHETIC_LABEL) and LINK not in text for text in replier.sent)
    assert row(database, flow.recommendation_id)["discord_message_id"] == 7001 and balance(database, 101) == 0


def test_a_proven_unsent_dm_is_refunded_and_the_reply_says_so_only_after_commit(two_submitters) -> None:
    """@description: After a proven non-delivery the reply reports the refund only because it was committed.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    replier = Reply()
    flow = run(service_for(repository), Dm(DmResult.not_sent("dm_closed_50007")), replier, 101, "discord:2")
    assert flow.refund_status == "refunded" and balance(database, 101) == 1
    assert "已退回 1 點" in replier.sent[0]


def test_a_failed_refund_is_reported_as_pending_and_retried_once_without_resending(two_submitters) -> None:
    """@description: If the refund transaction fails, the reply says pending, the failure stays durable, and a later request refunds once without a new DM.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    service = service_for(repository)
    database.sql_fault = "UPDATE recommendations SET released_at"
    sender, replier = Dm(DmResult.not_sent("dm_closed_50007")), Reply()
    flow = run(service, sender, replier, 101, "discord:3")
    assert flow.refund_status == "pending" and "已退回" not in replier.sent[0] and "退款待處理" in replier.sent[0]
    assert balance(database, 101) == 0 and row(database, flow.recommendation_id)["released_at"] is None
    run(service, sender, Reply(), 102, "discord:4")  # [later request]: Sweeps the pending refund first.
    assert balance(database, 101) == 1 and row(database, flow.recommendation_id)["released_at"] is not None
    assert [attempt[0] for attempt in sender.attempts].count(101) == 1


def test_an_uncertain_outcome_keeps_its_code_and_is_never_refunded_or_resent(two_submitters) -> None:
    """@description: A timed-out DM is uncertain, shows its operation code, and neither a retry nor a later sweep refunds or resends it.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    service = service_for(repository)
    sender = Dm(DmResult.unknown("timeout"))
    replier = Reply()
    flow = run(service, sender, replier, 101, "discord:5")
    run(service, sender, Reply(), 101, "discord:5")
    run(service, sender, Reply(), 102, "discord:6")
    assert flow.delivery_status.value == "uncertain" and [attempt[0] for attempt in sender.attempts].count(101) == 1
    assert str(flow.recommendation_id) in replier.sent[0] and "不會自動重送" in replier.sent[0]
    assert balance(database, 101) == 0 and row(database, flow.recommendation_id)["released_at"] is None


def test_a_lost_reply_after_a_successful_dm_changes_nothing(two_submitters) -> None:
    """@description: The reply failing after a successful DM leaves the delivery, the debit and the absence of any refund unchanged.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    sender = Dm(DmResult.delivered(7002))
    flow = run(service_for(repository), sender, Reply(fail=True), 101, "discord:7")
    assert flow.reply_failed is True and flow.delivery_status.value == "delivered"
    assert len(sender.attempts) == 1 and balance(database, 101) == 0
    assert row(database, flow.recommendation_id)["status"] == "delivered"


def test_a_dm_that_outlives_its_timeout_is_uncertain(two_submitters, monkeypatch: pytest.MonkeyPatch) -> None:
    """@description: A DM attempt that does not finish within the bound is uncertain rather than refused.
    @param two_submitters: Two settled submitters with verified songs.
    @param monkeypatch: Shrinks the bound for this test only.
    @return: None.
    """
    database, repository, _ = two_submitters
    monkeypatch.setattr(exchange_delivery, "DM_TIMEOUT_SECONDS", 0.01)

    class Slow(Dm):
        async def send(self, discord_user_id, text):
            await asyncio.sleep(1)
            return DmResult.delivered(1)

    flow = run(service_for(repository), Slow(), Reply(), 101, "discord:8")
    assert flow.delivery_status.value == "uncertain" and row(database, flow.recommendation_id)["status"] == "uncertain"
    assert balance(database, 101) == 0


def test_the_dm_bound_is_far_below_the_recovery_lease() -> None:
    """@description: A live attempt must always finish well before the lease expires, so the sweep never mistakes it for a lost one.
    @param: none
    @return: None.
    """
    assert DM_TIMEOUT_SECONDS * 2 < DELIVERY_LEASE.total_seconds()


def test_an_expired_sending_row_is_found_by_the_next_request_and_never_resent(two_submitters) -> None:
    """@description: A sending row left by a crashed process becomes uncertain on the next request after its lease, and nothing is sent for it.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "lease-stale", random.Random(1))
    row(database, reservation.recommendation_id).update(status="sending", claim_token="t" * 32,
                                                        claimed_at=NOW - DELIVERY_LEASE - timedelta(seconds=1))
    sender = Dm()
    run(service_for(repository), sender, Reply(), 102, "discord:9")
    assert row(database, reservation.recommendation_id)["status"] == "uncertain"
    assert not any(attempt[0] == 101 for attempt in sender.attempts)


def test_a_fresh_sending_row_is_left_alone_by_the_sweep(two_submitters) -> None:
    """@description: A claim younger than the lease, possibly still live in another worker, is never converted by the sweep.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    reservation = repository.exchange(101, "lease-fresh", random.Random(1))
    row(database, reservation.recommendation_id).update(status="sending", claim_token="u" * 32, claimed_at=datetime.now(timezone.utc))
    run(service_for(repository), Dm(), Reply(), 102, "discord:10")
    assert row(database, reservation.recommendation_id)["status"] == "sending"


def test_the_gate_stays_closed_without_the_explicit_synthetic_route() -> None:
    """@description: The production service keeps /exchange gated and reports no debit, no reservation and no delivery.
    @param: none
    @return: None.
    """
    service = MusicService(MagicMock())
    assert service.synthetic_exchange_enabled is False
    gate_text = asyncio.run(service.exchange(101))
    assert "未扣點" in gate_text
    with pytest.raises(ServiceUnavailable):
        asyncio.run(service.synthetic_exchange(101, "discord:0", Dm(), Reply()))


def test_the_owner_summary_and_the_requester_view_expose_no_token_or_link_and_no_other_user(two_submitters) -> None:
    """@description: The requester sees only their own results, and the Owner summary contains counts only.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = two_submitters
    run(service_for(repository), Dm(DmResult.not_sent("dm_closed_50007")), Reply(), 101, "discord:11")
    mine = repository.user_exchange_results(101)
    theirs = repository.user_exchange_results(102)
    assert mine and all(set(item) == {"operation_code", "delivery_state", "refund_state"} for item in mine)
    assert mine[0]["refund_state"] == "refunded" and theirs == []
    summary = repository.owner_exchange_summary()
    assert set(summary) == {"reserved", "sending", "delivered", "failed", "uncertain", "refund_pending"}
    assert all(isinstance(value, int) for value in summary.values())
