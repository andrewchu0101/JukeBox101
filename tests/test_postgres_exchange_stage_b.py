"""Real PostgreSQL behaviour of the formal /exchange flow (stage B): one debit, one DM, exact refund of a proven non-delivery, and no guessing.

Intake, review, settlement, reservation, delivery claim, refund and the books are the production code and tables on disposable PostgreSQL; only the direct message
and the ephemeral reply are scripted fakes. The contract under test: an unknown DM result never refunds and never resends, only a late proof of delivery or of
non-delivery may rewrite it, a delivered exchange is never refunded, and the reply claims a debit or refund only after the database committed it.
"""

import asyncio
import random
import threading
from datetime import datetime, timedelta, timezone
from functools import partial
from uuid import uuid4

import pytest

from bot.database.connection import open_connection
from bot.database.exchange_delivery_store import DELIVERY_LEASE, DeliveryClaim
from bot.database.exchange_reservation import ExchangeStatus
from bot.database.submission_repository import SubmissionRepository
from bot.exchange_delivery import IN_PROGRESS_TEXT, INSUFFICIENT_TEXT, NO_CANDIDATE_TEXT, DeliveryStatus, DmResult, exchange_and_deliver
from bot.ledger_integrity import STATUS_PASS, evaluate_ledger_integrity
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID, VIDEO_A, VIDEO_B, VIDEO_C, url
from tests.test_postgres_daily_limit import LimitWorld

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 11, 3, 12, 0)  # [naive UTC clock]: The production repository clock; session TimeZone is UTC.
MESSAGE_ID = 7_000_000_001  # [accepted DM id]: Any positive Discord message identifier.
LINK_MARKER = "watch?v="  # [link leak marker]: Must never appear in an ephemeral reply.


class ScriptedSender:
    """Replay scripted direct-message results and record every attempt so a second send is visible."""

    def __init__(self, *results: DmResult) -> None:
        """@description: Store the ordered results; once exhausted every send is accepted.
        @param self: Fake adapter.
        @param results: Ordered DM results.
        @return: None.
        """
        self.results = list(results)
        self.calls = []  # [attempt log]: One (recipient, text) per send.

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Record one attempt and answer with the next scripted result.
        @param self: Fake adapter.
        @param discord_user_id: Recipient.
        @param text: Message body.
        @return: Scripted result.
        """
        self.calls.append((discord_user_id, text))
        return self.results.pop(0) if self.results else DmResult.delivered(MESSAGE_ID)


class Replier:
    """Record the ephemeral replies the requester would see."""

    def __init__(self) -> None:
        """@description: Start with no replies.
        @param self: Fake replier.
        @return: None.
        """
        self.texts = []

    async def send_ephemeral(self, text: str) -> None:
        """@description: Keep one reply.
        @param self: Fake replier.
        @param text: Reply text.
        @return: None.
        """
        self.texts.append(text)


class ExchangeWorld(LimitWorld):
    """A music_category world where Alice owns song A and Bob owns song B, each holding one Nickel."""

    def __init__(self, database) -> None:
        """@description: Settle one song per user so the exchange pool holds A and B.
        @param self: World.
        @param database: Disposable database.
        @return: None.
        """
        super().__init__(database)
        self.share(ALICE, VIDEO_A, "discord:alice:a")
        self.share(BOB, VIDEO_B, "discord:bob:b")

    def new_repository(self) -> SubmissionRepository:
        """@description: Model a fresh process or worker thread: its own connection factory, nothing shared.
        @param self: World.
        @return: New repository over the same database.
        """
        return SubmissionRepository(partial(open_connection, self.database.app), OWNER_ID, automatic_settlement=True)

    def run(self, sender: ScriptedSender, replier: Replier | None = None, *, user: int = ALICE, key: str = "exchange-1",
            repository: SubmissionRepository | None = None, now: datetime = NOW):
        """@description: Run one /exchange request end to end, as the Discord command does.
        @param self: World.
        @param sender: Fake DM adapter.
        @param replier: Fake reply sink, or None for a throwaway one.
        @param user: Requester.
        @param key: Idempotency key of the request.
        @param repository: Repository to use, or None for the world's own.
        @param now: Clock for the request.
        @return: Flow result.
        """
        flow = exchange_and_deliver(repository or self.repository, sender, replier or Replier(), user, key, random.Random(7), now)
        return asyncio.run(flow)

    def rows(self, user: int = ALICE) -> list[dict]:
        """@description: Read one user's reservations in order.
        @param self: World.
        @param user: Requester.
        @return: Reservation rows.
        """
        return admin_query(self.database, "SELECT recommendation_id, song_id, status, discord_message_id, claim_token, released_at, delivered_at "
                                          "FROM recommendations WHERE discord_user_id = %s ORDER BY recommendation_id", (user,))

    def ledger(self, reason: str, user: int = ALICE) -> list[dict]:
        """@description: Read one user's ledger rows of one reason.
        @param self: World.
        @param reason: Ledger reason.
        @param user: Account.
        @return: Ledger rows.
        """
        return admin_query(self.database, "SELECT credit_delta, recommendation_id FROM credit_ledger WHERE reason = %s AND discord_user_id = %s "
                                          "ORDER BY ledger_id", (reason, user))

    def assert_books_consistent(self) -> None:
        """@description: Check every balance equals its ledger sum, including exchange debits and refunds.
        @param self: World.
        @return: None.
        """
        users = admin_query(self.database, "SELECT discord_user_id, credit_balance FROM users")
        ledger = admin_query(self.database, "SELECT ledger_id, discord_user_id, credit_delta, reason FROM credit_ledger")
        claims = admin_query(self.database, "SELECT version_id, ledger_id, discord_user_id FROM jb2_claims")
        assert evaluate_ledger_integrity(users, ledger, claims).status == STATUS_PASS

    def reserve_and_claim(self, token: str | None = None, at: datetime = NOW, user: int = ALICE, key: str = "reserved-1") -> tuple[int, DeliveryClaim]:
        """@description: Reserve an exchange and claim it as sending, as a process does just before the DM, without sending anything.
        @param self: World.
        @param token: Claim token, or None for a fresh one.
        @param at: Claim time.
        @param user: Requester.
        @param key: Request key.
        @return: Reservation identifier and the claim.
        """
        recommendation_id = self.repository.exchange(user, key, random.Random(7), at).recommendation_id
        _state, _message, claim = self.repository.claim_delivery(recommendation_id, token or uuid4().hex, at)
        return recommendation_id, claim


@pytest.fixture
def world(pg_database) -> ExchangeWorld:
    """@description: Provide the two-song world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return ExchangeWorld(pg_database)


def test_a_normal_exchange_debits_one_point_sends_one_dm_and_keeps_the_song_in_the_pool(world) -> None:
    """@description: The happy path: one debit, one DM carrying the other user's song, a delivered row, and the pool unchanged for everyone else.
    @param world: Two-song world.
    @return: None.
    """
    sender, replier = ScriptedSender(), Replier()
    flow = world.run(sender, replier)
    assert flow.delivery_status is DeliveryStatus.DELIVERED and flow.refund_status is None
    assert [(call[0], url(VIDEO_B) in call[1]) for call in sender.calls] == [(ALICE, True)]
    assert world.balances() == {ALICE: 0, BOB: 1}
    assert [row["credit_delta"] for row in world.ledger("exchange_debit")] == [-1]
    assert [(row["status"], row["discord_message_id"]) for row in world.rows()] == [("delivered", MESSAGE_ID)]
    assert len(world.pool()) == 2 and LINK_MARKER not in replier.texts[0]
    world.assert_books_consistent()


def test_a_song_the_user_submitted_or_already_received_is_never_drawn_again(world) -> None:
    """@description: Alice owns A, received B, then also owns C: nothing is left for her, so the next request is free and sends nothing.
    @param world: Two-song world.
    @return: None.
    """
    world.run(ScriptedSender())
    world.share(ALICE, VIDEO_C, "discord:alice:c")
    sender, replier = ScriptedSender(), Replier()
    flow = world.run(sender, replier, key="exchange-2")
    assert flow.reply_text == NO_CANDIDATE_TEXT and sender.calls == []
    assert world.balances()[ALICE] == 1 and len(world.rows()) == 1


def test_an_exchange_without_enough_points_charges_nothing_and_sends_nothing(world) -> None:
    """@description: Alice spends her only point; the next request is refused before any candidate work.
    @param world: Two-song world.
    @return: None.
    """
    world.run(ScriptedSender())
    sender = ScriptedSender()
    flow = world.run(sender, key="exchange-2")
    assert flow.reply_text == INSUFFICIENT_TEXT and flow.recommendation_id is None and sender.calls == []
    assert len(world.ledger("exchange_debit")) == 1 and world.balances()[ALICE] == 0


def test_a_proven_unsent_dm_refunds_exactly_one_point_and_the_books_stay_consistent(world) -> None:
    """@description: Explicit non-delivery: one refund row pointing at the debit, balance restored, release marker set, reply says refunded after the commit.
    @param world: Two-song world.
    @return: None.
    """
    sender, replier = ScriptedSender(DmResult.not_sent("dm_closed")), Replier()
    flow = world.run(sender, replier)
    assert (flow.delivery_status, flow.refund_status) == (DeliveryStatus.FAILED, "refunded") and "已退還 1 Nickel" in replier.texts[0]
    debit, refund = world.ledger("exchange_debit"), world.ledger("delivery_refund")
    assert [row["credit_delta"] for row in debit] == [-1] and [row["credit_delta"] for row in refund] == [1]
    assert refund[0]["recommendation_id"] == debit[0]["recommendation_id"] and world.balances()[ALICE] == 1
    assert world.rows()[0]["status"] == "failed" and world.rows()[0]["released_at"] is not None
    world.assert_books_consistent()


def test_a_refunded_song_is_released_and_can_be_reserved_and_delivered_again(world) -> None:
    """@description: After the refund the same song is eligible again for the same user, with a second debit and a second DM.
    @param world: Two-song world.
    @return: None.
    """
    world.run(ScriptedSender(DmResult.not_sent("dm_closed")))
    sender = ScriptedSender()
    flow = world.run(sender, key="exchange-2")
    assert flow.delivery_status is DeliveryStatus.DELIVERED and len(sender.calls) == 1
    assert [row["status"] for row in world.rows()] == ["failed", "delivered"] and world.balances()[ALICE] == 0
    world.assert_books_consistent()


def test_an_unknown_dm_result_keeps_the_debit_sends_no_second_dm_and_blocks_a_new_exchange(world) -> None:
    """@description: Uncertain: no refund, no resend, an Owner-visible open reservation, and a later request is told it is in progress.
    @param world: Two-song world.
    @return: None.
    """
    sender, replier = ScriptedSender(DmResult.unknown("timeout")), Replier()
    flow = world.run(sender, replier)
    assert (flow.delivery_status, flow.refund_status) == (DeliveryStatus.UNCERTAIN, None) and "不會自動重送" in replier.texts[0]
    assert world.balances()[ALICE] == 0 and world.ledger("delivery_refund") == []
    second = world.run(sender, replier, key="exchange-2")
    assert second.reply_text == IN_PROGRESS_TEXT and len(sender.calls) == 1
    assert [row["status"] for row in world.rows()] == ["uncertain"] and len(world.ledger("exchange_debit")) == 1


@pytest.mark.parametrize("first_result", [None, DmResult.unknown("timeout")], ids=["delivered", "uncertain"])
def test_replaying_the_same_request_key_never_debits_or_sends_twice(world, first_result) -> None:
    """@description: A technical retry of the same request returns the original reservation without a second debit or DM.
    @param world: Two-song world.
    @param first_result: DM result of the first attempt, or None for acceptance.
    @return: None.
    """
    sender = ScriptedSender(*([first_result] if first_result else []))
    first = world.run(sender)
    replay = world.run(sender)
    assert replay.recommendation_id == first.recommendation_id and replay.delivery_status is first.delivery_status
    assert len(sender.calls) == 1 and len(world.ledger("exchange_debit")) == 1 and len(world.rows()) == 1
    world.assert_books_consistent()


def test_after_a_restart_an_expired_sending_attempt_becomes_uncertain_and_is_never_resent(world) -> None:
    """@description: The process died after the claim and before any DM result; a new repository marks it uncertain once the lease expires and sends nothing.
    @param world: Two-song world.
    @return: None.
    """
    recommendation_id, _claim = world.reserve_and_claim()
    restarted = world.new_repository()
    assert restarted.recover_interrupted_deliveries(NOW + DELIVERY_LEASE - timedelta(seconds=1)) == 0
    assert restarted.recover_interrupted_deliveries(NOW + DELIVERY_LEASE + timedelta(seconds=1)) == 1
    sender = ScriptedSender()
    flow = world.run(sender, key="reserved-1", repository=restarted, now=NOW + DELIVERY_LEASE + timedelta(minutes=1))
    assert flow.recommendation_id == recommendation_id and flow.delivery_status is DeliveryStatus.UNCERTAIN
    assert sender.calls == [] and world.balances()[ALICE] == 0 and len(world.ledger("exchange_debit")) == 1


def test_a_late_proof_of_delivery_rewrites_uncertain_without_a_refund(world) -> None:
    """@description: The accepted message id arrives after the fact: the row becomes delivered and the point stays spent.
    @param world: Two-song world.
    @return: None.
    """
    recommendation_id, claim = world.reserve_and_claim()
    assert world.repository.record_delivery(claim, "uncertain", None, None) == "uncertain"
    assert world.repository.record_delivery(claim, "delivered", MESSAGE_ID, NOW) == "delivered"
    assert world.rows()[0]["discord_message_id"] == MESSAGE_ID
    assert world.repository.refund_undelivered(recommendation_id, NOW).status == "not_refundable"
    assert world.balances()[ALICE] == 0 and world.ledger("delivery_refund") == []
    world.assert_books_consistent()


def test_a_late_proof_of_non_delivery_rewrites_uncertain_and_refunds_exactly_once(world) -> None:
    """@description: Proof that nothing was sent arrives after the fact: failed, then one refund however often it is retried.
    @param world: Two-song world.
    @return: None.
    """
    recommendation_id, claim = world.reserve_and_claim()
    world.repository.record_delivery(claim, "uncertain", None, None)
    assert world.repository.record_delivery(claim, "failed", None, None) == "failed"
    outcomes = [world.repository.refund_undelivered(recommendation_id, NOW).status for _ in range(3)]
    assert outcomes == ["refunded", "already_released", "already_released"]
    assert world.balances()[ALICE] == 1 and len(world.ledger("delivery_refund")) == 1
    world.assert_books_consistent()


@pytest.mark.parametrize("terminal, late", [("delivered", "failed"), ("delivered", "uncertain"), ("failed", "delivered"), ("failed", "uncertain")])
def test_a_terminal_state_is_never_overwritten_by_a_later_result(world, terminal, late) -> None:
    """@description: Only sending and uncertain may be rewritten; delivered and failed stand against every later result of the same attempt.
    @param world: Two-song world.
    @param terminal: State reached first.
    @param late: Conflicting result arriving afterwards.
    @return: None.
    """
    _recommendation_id, claim = world.reserve_and_claim()
    message_id, delivered_at = (MESSAGE_ID, NOW) if terminal == "delivered" else (None, None)
    assert world.repository.record_delivery(claim, terminal, message_id, delivered_at) == terminal
    late_message_id, late_delivered_at = (MESSAGE_ID, NOW) if late == "delivered" else (None, None)
    assert world.repository.record_delivery(claim, late, late_message_id, late_delivered_at) == terminal
    assert world.rows()[0]["status"] == terminal


def test_a_result_carrying_another_attempts_token_changes_nothing(world) -> None:
    """@description: Ownership of the sending state is by claim token; a stale attempt cannot record anything.
    @param world: Two-song world.
    @return: None.
    """
    recommendation_id, claim = world.reserve_and_claim()
    stale = DeliveryClaim(recommendation_id, uuid4().hex, claim.discord_user_id, claim.youtube_url)
    assert world.repository.record_delivery(stale, "delivered", MESSAGE_ID, NOW) == "sending"
    assert world.rows()[0]["status"] == "sending"


def test_a_refund_failing_midway_rolls_back_ledger_balance_and_release_and_is_retried_once(world) -> None:
    """@description: The release step is made to fail after the refund row and balance update: nothing of the refund survives, the reply does not claim a refund,
    and after the fault is removed the retry sweep refunds exactly once.
    @param world: Two-song world.
    @return: None.
    """
    admin_query(world.database, "CREATE FUNCTION jukebox101.fail_release() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                                "IF NEW.released_at IS NOT NULL THEN RAISE EXCEPTION 'injected release failure'; END IF; RETURN NEW; END $$")
    admin_query(world.database, "CREATE TRIGGER fail_release BEFORE UPDATE ON jukebox101.recommendations FOR EACH ROW EXECUTE FUNCTION jukebox101.fail_release()")
    replier = Replier()
    flow = world.run(ScriptedSender(DmResult.not_sent("dm_closed")), replier)
    assert (flow.delivery_status, flow.refund_status) == (DeliveryStatus.FAILED, "pending")
    assert "已退還" not in replier.texts[0] and "退款待處理" in replier.texts[0]
    assert world.balances()[ALICE] == 0 and world.ledger("delivery_refund") == [] and world.rows()[0]["released_at"] is None
    admin_query(world.database, "DROP TRIGGER fail_release ON jukebox101.recommendations")
    admin_query(world.database, "DROP FUNCTION jukebox101.fail_release()")
    assert [item.status for item in world.repository.retry_pending_refunds(NOW)] == ["refunded"]
    assert world.repository.retry_pending_refunds(NOW) == [] and world.balances()[ALICE] == 1
    world.assert_books_consistent()


def _run_concurrently(jobs: list) -> list:
    """@description: Start every job on its own thread behind one barrier and collect the results in order.
    @param jobs: Zero-argument callables.
    @return: Results of the jobs.
    """
    barrier, results = threading.Barrier(len(jobs)), [None] * len(jobs)

    def work(index: int) -> None:
        """@description: Wait for the others, then run one job.
        @param index: Job position.
        @return: None.
        """
        barrier.wait()
        results[index] = jobs[index]()

    workers = [threading.Thread(target=work, args=(index,)) for index in range(len(jobs))]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    return results


@pytest.mark.parametrize("keys", [("same", "same"), ("first", "second")], ids=["same-key", "different-keys"])
def test_two_concurrent_requests_of_one_user_debit_and_send_only_once(world, keys) -> None:
    """@description: Two workers race the same user; the writer gate and the open-reservation rule leave exactly one debit and one DM.
    @param world: Two-song world.
    @param keys: Request keys of the two racers.
    @return: None.
    """
    sender = ScriptedSender()
    jobs = [partial(world.run, sender, key=key, repository=world.new_repository()) for key in keys]
    flows = _run_concurrently(jobs)
    assert len(sender.calls) == 1 and len(world.ledger("exchange_debit")) == 1 and len(world.rows()) == 1
    assert world.balances()[ALICE] == 0 and any(flow.delivery_status is DeliveryStatus.DELIVERED for flow in flows)
    world.assert_books_consistent()


def test_concurrent_refund_handlers_refund_exactly_once(world) -> None:
    """@description: Two workers refund the same proven-unsent reservation at the same time.
    @param world: Two-song world.
    @return: None.
    """
    recommendation_id, claim = world.reserve_and_claim()
    world.repository.record_delivery(claim, "failed", None, None)
    jobs = [partial(lambda repository: repository.refund_undelivered(recommendation_id, NOW).status, world.new_repository()) for _ in range(2)]
    assert sorted(_run_concurrently(jobs)) == ["already_released", "refunded"]
    assert world.balances()[ALICE] == 1 and len(world.ledger("delivery_refund")) == 1
    world.assert_books_consistent()


def test_results_and_summary_cope_with_a_reserved_record_that_has_not_been_claimed(world) -> None:
    """@description: A reservation committed but not yet claimed must be readable by the user's results, the submission history and the Owner summary.
    @param world: Two-song world.
    @return: None.
    """
    outcome = world.repository.exchange(ALICE, "reserved-only", random.Random(7), NOW)
    assert outcome.status is ExchangeStatus.RESERVED
    results = world.repository.user_exchange_results(ALICE)
    assert [(row["operation_code"], row["delivery_state"], row["refund_state"]) for row in results] == [(outcome.recommendation_id, "reserved", "none")]
    assert world.repository.owner_exchange_summary()["reserved"] == 1
    assert [row["video_id"] for row in world.repository.history(ALICE)] == [VIDEO_A]


def test_the_flow_accepts_a_timezone_aware_clock_as_the_offline_tests_pass_one(world) -> None:
    """@description: The clock handed to the flow may be aware UTC; on PostgreSQL it must still reserve, claim and deliver.
    @param world: Two-song world.
    @return: None.
    """
    aware_now = NOW.replace(tzinfo=timezone.utc)
    flow = world.run(ScriptedSender(), now=aware_now)
    assert flow.delivery_status is DeliveryStatus.DELIVERED and world.rows()[0]["status"] == "delivered"
