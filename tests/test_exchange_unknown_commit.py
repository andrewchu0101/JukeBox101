"""Verify offline the caller contract after an unknown database commit: one same-key replay, no second debit, no resend, and a reply that never invites a new request.

Database outcomes are scripted through a fake repository, so no MySQL is contacted. The contract under test is the caller's:
an unknown commit (CommitOutcomeUnknown) is not a definite failure, the same request key is replayed once on a fresh connection,
and any failure of that replay keeps the outcome unknown instead of reporting that nothing happened.
"""

import asyncio
import random
from datetime import datetime, timezone

import psycopg
from psycopg import errors as pg_errors
import pytest

from bot.database.exchange_delivery_store import DeliveryClaim
from bot.database.exchange_reservation import ExchangeOutcome, ExchangeStatus
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.exchange_delivery import DmResult, exchange_and_deliver

NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)  # [fixed clock]: Only used as a timestamp value.
USER = 900000000000000101  # [synthetic user]: Never a real Discord account.
URL = "https://www.youtube.com/watch?v=JBX00000008"  # [synthetic link]: Fixed test pattern.


class FakeRepository:
    """Script one result per exchange call and record every call, without any database."""

    def __init__(self, exchange_results: list) -> None:
        """@description: Prepare the scripted exchange results; each entry is an outcome or an exception to raise.
        @param exchange_results: Results in call order.
        @return: None.
        """
        self.exchange_results = list(exchange_results)
        self.exchange_calls: list = []
        self.claims: list = []
        self.records: list = []

    def recover_interrupted_deliveries(self, now) -> int:
        """@description: No interrupted delivery exists in these scenarios.
        @param now: Unused clock value.
        @return: Zero rows moved.
        """
        return 0

    def retry_pending_refunds(self, now) -> list:
        """@description: No pending refund exists in these scenarios.
        @param now: Unused clock value.
        @return: Empty sweep.
        """
        return []

    def exchange(self, discord_user_id: int, request_key: str, rng) -> ExchangeOutcome:
        """@description: Return or raise the next scripted result and remember the request key used.
        @param discord_user_id: Requesting user.
        @param request_key: Technical request identity.
        @param rng: Unused random source.
        @return: Scripted outcome.
        """
        self.exchange_calls.append(request_key)
        result = self.exchange_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def claim_delivery(self, recommendation_id: int, claim_token: str, now) -> tuple:
        """@description: Claim a reservation once; a reservation already delivered is reported without a claim.
        @param recommendation_id: Reservation identifier.
        @param claim_token: Attempt token.
        @param now: Unused clock value.
        @return: State, message identifier and claim (None when no send may happen).
        """
        if any(claim.recommendation_id == recommendation_id for claim in self.claims):
            return "delivered", 990001, None
        claim = DeliveryClaim(recommendation_id, claim_token, USER, URL)
        self.claims.append(claim)
        return "sending", None, claim

    def record_delivery(self, claim, status: str, message_id, delivered_at) -> str:
        """@description: Record the delivery result for the claim.
        @param claim: Claim that performed the send.
        @param status: Classified result.
        @param message_id: Accepted message identifier.
        @param delivered_at: Acceptance time.
        @return: Stored state.
        """
        self.records.append((claim.recommendation_id, status))
        return status

    def refund_undelivered(self, recommendation_id: int, now):
        """@description: Refunds are not expected in these scenarios; reaching this is a failure of the scenario.
        @param recommendation_id: Reservation identifier.
        @param now: Unused clock value.
        @return: Never returns.
        """
        raise AssertionError("no refund may occur for a delivered or unknown reservation")


class FakeSender:
    """Record every direct-message attempt; never contacts Discord."""

    def __init__(self) -> None:
        """@description: Start with no attempts recorded.
        @return: None.
        """
        self.sent: list = []

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Record one attempt and report a delivered result.
        @param discord_user_id: Recipient.
        @param text: Message body.
        @return: Delivered classification.
        """
        self.sent.append((discord_user_id, text))
        return DmResult.delivered(990001)


class FakeReplier:
    """Collect the ephemeral replies the requester would see."""

    def __init__(self) -> None:
        """@description: Start with no replies.
        @return: None.
        """
        self.texts: list = []

    async def send_ephemeral(self, text: str) -> None:
        """@description: Record one reply.
        @param text: Reply body.
        @return: None.
        """
        self.texts.append(text)


def run_flow(repository: FakeRepository, sender: FakeSender, replier: FakeReplier):
    """@description: Run one exchange request through the production caller with fixed random and clock inputs.
    @param repository: Scripted repository.
    @param sender: Recording sender.
    @param replier: Recording replier.
    @return: Flow result returned by the caller.
    """
    return asyncio.run(exchange_and_deliver(repository, sender, replier, USER, "discord:1001", random.Random(1), NOW,
                                            new_token=lambda: "token-1"))


def test_unknown_commit_is_replayed_once_with_the_same_key_and_delivers_the_original() -> None:
    """@description: After an unknown commit the same key is replayed; the original reservation is found and delivered exactly once.
    @return: None.
    """
    repository = FakeRepository([CommitOutcomeUnknown("commit ack lost"),
                                 ExchangeOutcome(ExchangeStatus.REPLAYED, 7, URL)])
    sender, replier = FakeSender(), FakeReplier()
    flow = run_flow(repository, sender, replier)
    assert repository.exchange_calls == ["discord:1001", "discord:1001"]
    assert len(sender.sent) == 1 and flow.recommendation_id == 7
    assert repository.records == [(7, "delivered")]


def test_unknown_commit_whose_replay_reserves_fresh_is_delivered_once() -> None:
    """@description: If the first attempt never committed, the same-key replay reserves once and that single reservation is delivered once.
    @return: None.
    """
    repository = FakeRepository([CommitOutcomeUnknown("commit not sent"),
                                 ExchangeOutcome(ExchangeStatus.RESERVED, 8, URL)])
    sender, replier = FakeSender(), FakeReplier()
    flow = run_flow(repository, sender, replier)
    assert repository.exchange_calls == ["discord:1001", "discord:1001"]
    assert len(sender.sent) == 1 and flow.recommendation_id == 8


def test_replay_of_an_already_delivered_reservation_sends_nothing_again() -> None:
    """@description: A same-key replay that finds the reservation already delivered must not send a second direct message.
    @return: None.
    """
    repository = FakeRepository([CommitOutcomeUnknown("commit ack lost"), ExchangeOutcome(ExchangeStatus.REPLAYED, 9, URL)])
    repository.claims.append(DeliveryClaim(9, "earlier", USER, URL))
    sender, replier = FakeSender(), FakeReplier()
    run_flow(repository, sender, replier)
    assert sender.sent == []


def test_two_unknown_outcomes_stay_unknown_and_keep_the_original_chained() -> None:
    """@description: When the replay also fails, the caller reports the outcome as unknown, sends nothing, and keeps the first unknown as its cause.
    @return: None.
    """
    first = CommitOutcomeUnknown("commit ack lost")
    repository = FakeRepository([first, CommitOutcomeUnknown("replay also unknown")])
    sender, replier = FakeSender(), FakeReplier()
    with pytest.raises(CommitOutcomeUnknown) as error:
        run_flow(repository, sender, replier)
    assert repository.exchange_calls == ["discord:1001", "discord:1001"]
    assert sender.sent == [] and replier.texts == []
    assert error.value.__cause__ is first or error.value.__context__ is first


def test_replay_failing_with_a_database_error_is_still_reported_as_unknown() -> None:
    """@description: A database error during the replay cannot prove the first attempt did not commit, so the outcome stays unknown.
    @return: None.
    """
    first = CommitOutcomeUnknown("commit ack lost")
    repository = FakeRepository([first, pg_errors.LockNotAvailable("Lock wait timeout exceeded")])
    with pytest.raises(CommitOutcomeUnknown) as error:
        run_flow(repository, FakeSender(), FakeReplier())
    assert error.value.__cause__ is first
    assert "LockNotAvailable" in " ".join(error.value.__notes__)  # [lock wait timeout]: SQLSTATE 55P03, an OperationalError subclass.


def test_a_definite_database_error_is_not_replayed() -> None:
    """@description: A database error before any commit is definite and is raised once, without a replay.
    @return: None.
    """
    repository = FakeRepository([pg_errors.LockNotAvailable("Lock wait timeout exceeded")])
    with pytest.raises(psycopg.OperationalError):
        run_flow(repository, FakeSender(), FakeReplier())
    assert len(repository.exchange_calls) == 1


def test_unknown_outcome_reply_never_invites_a_new_request() -> None:
    """@description: The requester is told the outcome is unknown and not to repeat the command, never that the exchange did not happen.
    @return: None.
    """
    from unittest.mock import MagicMock

    from bot.services import MusicService
    repository = FakeRepository([CommitOutcomeUnknown("commit ack lost"), CommitOutcomeUnknown("replay also unknown")])
    service = MusicService(MagicMock(), submission_repository=repository, synthetic_exchange=True)
    replier = FakeReplier()
    flow = asyncio.run(service.synthetic_exchange(USER, "discord:1001", FakeSender(), replier))
    assert "未確認" in flow.reply_text and "勿重新" in flow.reply_text
    assert "請稍後再試" not in flow.reply_text and "未完成" not in flow.reply_text
