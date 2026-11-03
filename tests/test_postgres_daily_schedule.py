"""Real PostgreSQL behaviour of the stage C scheduled /daily delivery (issue #21): preferences, the Taipei-day task, charging, notices, failures and races.

Intake, review, settlement, reservation, delivery claim, refund and the books are the production code and tables on disposable PostgreSQL; only the
direct message and the display-name lookup are scripted fakes. The clock is always injected as a naive UTC instant (the production repository clock).

INTERFACE CONTRACT (everything below is defined by these tests; names are suggestions the implementer must follow or ask to change):
    bot/database/daily_store.py (SQL functions taking a cursor; SubmissionRepository wraps each in submission_transaction like the exchange ones)
        class DailyPreference(NamedTuple): discord_user_id: int; enabled: bool; delivery_hour: int
        SubmissionRepository.save_daily_preference(discord_user_id, *, enabled: bool | None, delivery_hour: int | None, now=None) -> DailyPreference
            # None leaves a field unchanged; first save creates the account row if missing, with enabled False and hour 20 as the starting values.
            # An hour outside 0..23 raises ValueError (InvalidDailyHour) before any write.
        SubmissionRepository.daily_preference(discord_user_id) -> DailyPreference | None
    bot/daily_schedule.py
        class DailyOutcome(Enum): DELIVERED, FAILED, UNCERTAIN, IN_PROGRESS, NOTICE_SENT, NOTICE_SILENCED, NOTICE_FAILED   (value = lower-case name)
        @dataclass(frozen=True) DailyUserResult: discord_user_id: int; outcome: DailyOutcome; recommendation_id: int | None
        @dataclass(frozen=True) DailySweepReport: results: tuple[DailyUserResult, ...]     # only users whose task ran in this pass
        async run_daily_sweep(repository, sender, resolve_display_name, now, rng, new_token=lambda: uuid4().hex) -> DailySweepReport
            # sender: the same DirectMessageSender protocol as exchange. resolve_display_name: async (discord_user_id) -> str | None, called live for the
            # recommender of each delivered song, never stored; if it raises, the DM still goes out with ANONYMOUS_NAME.
            # Per due user: recover interrupted deliveries and retry pending refunds first (as exchange_and_deliver does), then ONE task for the Taipei day:
            #   open exchange -> IN_PROGRESS (no debit, no DM, task consumed)
            #   balance < 1   -> DM NOT_ENOUGH_NICKEL_TEXT, no debit
            #   no candidate  -> DM NO_CANDIDATE_DAILY_TEXT, no debit
            #   else          -> the stage B path: reserve + debit 1, claim, DM build_daily_dm_text, record, refund only a proven non-delivery.
            # Every task is claimed in the database before any DM, so repeated or concurrent sweeps and restarts never repeat a task.
            # A failed DM is never retried the same day; one user's failure never stops the loop for the others.
    tables (migration 0005, additive; privileges for jukebox101_app_role: SELECT, INSERT, UPDATE; never DELETE; readonly: SELECT)
        daily_preferences(discord_user_id BIGINT PK -> users, enabled BOOLEAN NOT NULL DEFAULT FALSE, delivery_hour SMALLINT NOT NULL DEFAULT 20
                          CHECK 0..23, updated_at TIMESTAMPTZ NOT NULL)
        daily_notices(discord_user_id BIGINT -> users, notice_day DATE (Taipei), kind TEXT CHECK IN ('insufficient_credit','no_candidate'),
                      delivery TEXT CHECK IN ('sent','failed','uncertain','silenced'), consecutive_days INTEGER CHECK >= 1, created_at TIMESTAMPTZ,
                      PRIMARY KEY (discord_user_id, notice_day))
        daily_dispatches (existing since 0001, needs the app grants): one row per reservation task, status pending/sending/delivered/failed/uncertain.
        A user's Taipei day holds at most ONE row across daily_dispatches and daily_notices together.
"""

import asyncio
import logging
import random
from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest

from bot.daily_mask import ANONYMOUS_NAME, mask_display_name
from bot.daily_schedule import NO_CANDIDATE_DAILY_TEXT, NOT_ENOUGH_NICKEL_TEXT, DailyOutcome, run_daily_sweep
from bot.database.exchange_delivery_store import DELIVERY_LEASE
from bot.exchange_delivery import DmResult
from bot.reward_candidates import RewardCandidateStatus
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID, VIDEO_B, url
from tests.test_postgres_exchange_stage_b import LINK_MARKER, MESSAGE_ID, ExchangeWorld, ScriptedSender, _run_concurrently

pytestmark = pytest.mark.postgres

CAROL = 500003  # [third account]: A third participant for hour and isolation scenarios.
CANARY_NAME = "ZzCanaryRecommenderName"  # [improbable full name]: Must never reach a DM, a log or a table.
DAY0 = date(2026, 11, 3)  # [first Taipei day]: The day of the stage B clock NOW (12:00 UTC is 20:00 Taipei).


def at(day_offset: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """@description: Build the naive UTC instant of a Taipei wall time, counted in days from the first scenario day.
    @param day_offset: Whole Taipei days after DAY0.
    @param hour: Taipei hour.
    @param minute: Minute.
    @param second: Second.
    @return: Naive UTC datetime.
    """
    return datetime(2026, 11, 3) + timedelta(days=day_offset, hours=hour - 8, minutes=minute, seconds=second)

NOW_DAY0_EVENING = at(0, 20)  # [default delivery instant]: 20:00 Taipei on the first day.


class Crash(BaseException):
    """Stand in for a process dying in the middle of a send; not an Exception, so no handler may swallow it."""


class CrashingSender:
    """Record the attempt, then die before any result exists."""

    def __init__(self) -> None:
        """@description: Start with no attempts.
        @return: None.
        """
        self.calls = []  # [attempt log]: One (recipient, text) per send.

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Log and crash.
        @param discord_user_id: Recipient.
        @param text: Body.
        @return: Never returns.
        """
        self.calls.append((discord_user_id, text))
        raise Crash()


class PerUserSender:
    """Answer per recipient: a scripted result, a raised exception, or acceptance."""

    def __init__(self, scripted: dict) -> None:
        """@description: Keep the per-recipient script.
        @param scripted: Recipient to DmResult or Exception instance.
        @return: None.
        """
        self.scripted = scripted
        self.calls = []  # [attempt log]: One (recipient, text) per send.

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Record the attempt and apply the recipient's script.
        @param discord_user_id: Recipient.
        @param text: Body.
        @return: Scripted result, or acceptance.
        """
        self.calls.append((discord_user_id, text))
        outcome = self.scripted.get(discord_user_id, DmResult.delivered(MESSAGE_ID))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class Names:
    """Stand in for the live Discord name lookup and record who was asked."""

    def __init__(self, names: dict | None = None, failing: frozenset = frozenset()) -> None:
        """@description: Keep the name book and the ids whose lookup fails.
        @param names: User id to display name.
        @param failing: Ids whose lookup raises.
        @return: None.
        """
        self.names = names or {}
        self.failing = failing
        self.calls = []  # [lookup log]: Every id the sweep asked about.

    async def __call__(self, discord_user_id: int) -> str | None:
        """@description: Answer one lookup.
        @param discord_user_id: Recommender being named.
        @return: Display name or None.
        """
        self.calls.append(discord_user_id)
        if discord_user_id in self.failing:
            raise RuntimeError("lookup failed")
        return self.names.get(discord_user_id)


class DailyWorld(ExchangeWorld):
    """The stage B two-song world plus daily scheduling helpers."""

    def __init__(self, database) -> None:
        """@description: Start the song counter after the two stage B songs.
        @param database: Disposable database.
        @return: None.
        """
        super().__init__(database)
        self.next_song = 0  # [fresh song index]: Each add_song uses a new unrelated scripted song.

    def add_song(self, user: int) -> None:
        """@description: Let a user share one more settled song, which pays them one point and adds a candidate for everyone else.
        @param user: Submitter.
        @return: None.
        """
        outcome = self.share_index(user, self.next_song)
        assert outcome.decision.status is RewardCandidateStatus.AWARDED
        self.next_song += 1

    def enable(self, user: int, hour: int | None = None) -> None:
        """@description: Subscribe a user to daily delivery, optionally at an hour.
        @param user: Subscriber.
        @param hour: Taipei hour or None for the default.
        @return: None.
        """
        self.repository.save_daily_preference(user, enabled=True, delivery_hour=hour if hour is not None else 20)

    def sweep(self, sender, now: datetime, names: Names | None = None, repository=None):
        """@description: Run one scheduler pass as the Bot's background task would.
        @param sender: Fake DM adapter.
        @param now: Naive UTC clock.
        @param names: Fake name lookup, or a default one that knows nobody.
        @param repository: Repository to use, or the world's own.
        @return: Sweep report.
        """
        run = run_daily_sweep(repository or self.repository, sender, names or Names(), now, random.Random(7), lambda: uuid4().hex)
        return asyncio.run(run)

    def outcomes(self, report) -> dict:
        """@description: Flatten a report to user and outcome.
        @param report: Sweep report.
        @return: Outcome per user.
        """
        return {result.discord_user_id: result.outcome for result in report.results}

    def dispatches(self, user: int = ALICE) -> list[tuple]:
        """@description: Read one user's reservation tasks in day order.
        @param user: Subscriber.
        @return: (Taipei day, status) pairs.
        """
        rows = admin_query(self.database, "SELECT dispatch_date, status FROM daily_dispatches WHERE discord_user_id = %s ORDER BY dispatch_date", (user,))
        return [(row["dispatch_date"], row["status"]) for row in rows]

    def notices(self, user: int = ALICE) -> list[tuple]:
        """@description: Read one user's notice rows in day order.
        @param user: Subscriber.
        @return: (Taipei day, kind, delivery) triples.
        """
        rows = admin_query(self.database, "SELECT notice_day, kind, delivery FROM daily_notices WHERE discord_user_id = %s ORDER BY notice_day", (user,))
        return [(row["notice_day"], row["kind"], row["delivery"]) for row in rows]

    def task_days(self, user: int = ALICE) -> list[date]:
        """@description: List the Taipei days that hold a task of either kind, so a double task on one day shows as a repeated date.
        @param user: Subscriber.
        @return: Sorted days, one entry per row across both tables.
        """
        return sorted([day for day, _status in self.dispatches(user)] + [day for day, _kind, _delivery in self.notices(user)])

    def spend_alice_balance(self) -> None:
        """@description: Let Alice use her only point on a manual exchange, leaving her with zero points and song B received.
        @return: None.
        """
        self.run(ScriptedSender())
        assert self.balances()[ALICE] == 0

    def debit_count(self, user: int = ALICE) -> int:
        """@description: Count a user's exchange debits.
        @param user: Account.
        @return: Number of debit rows.
        """
        return len(self.ledger("exchange_debit", user))


@pytest.fixture
def world(pg_database) -> DailyWorld:
    """@description: Provide the daily world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return DailyWorld(pg_database)


# --- preferences ------------------------------------------------------------------------------------------------------------


def test_a_new_user_has_no_preference_and_turning_on_without_an_hour_stores_20(world) -> None:
    """@description: Defaults: nothing stored until asked; on without an hour is 20; the value survives a fresh repository (a restart).
    @param world: Daily world.
    @return: None.
    """
    assert world.repository.daily_preference(ALICE) is None
    saved = world.repository.save_daily_preference(ALICE, enabled=True, delivery_hour=20)
    assert (saved.discord_user_id, saved.enabled, saved.delivery_hour) == (ALICE, True, 20)
    assert tuple(world.new_repository().daily_preference(ALICE)) == (ALICE, True, 20)


def test_each_user_keeps_their_own_hour_and_off_keeps_the_hour_but_stops_the_subscription(world) -> None:
    """@description: Hours are per user; turning off changes only the flag.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE, 8)
    world.enable(BOB, 23)
    world.repository.save_daily_preference(ALICE, enabled=False, delivery_hour=None)
    assert tuple(world.repository.daily_preference(ALICE))[1:] == (False, 8)
    assert tuple(world.repository.daily_preference(BOB))[1:] == (True, 23)


@pytest.mark.parametrize("hour", [24, -1, 99])
def test_an_hour_outside_0_to_23_is_refused_and_the_old_setting_stays(world, hour) -> None:
    """@description: Invalid hours raise before any write, and neither the hour nor the subscription changes.
    @param world: Daily world.
    @param hour: Rejected hour.
    @return: None.
    """
    world.enable(ALICE, 9)
    with pytest.raises(ValueError):
        world.repository.save_daily_preference(ALICE, enabled=True, delivery_hour=hour)
    assert tuple(world.repository.daily_preference(ALICE))[1:] == (True, 9)


def test_changing_only_the_hour_never_turns_the_subscription_on(world) -> None:
    """@description: A user who is off and sets a time stays off, and no sweep ever sends to them.
    @param world: Daily world.
    @return: None.
    """
    world.repository.save_daily_preference(ALICE, enabled=False, delivery_hour=None)
    world.repository.save_daily_preference(ALICE, enabled=None, delivery_hour=9)
    assert tuple(world.repository.daily_preference(ALICE))[1:] == (False, 9)
    sender = ScriptedSender()
    assert world.sweep(sender, NOW_DAY0_EVENING).results == () and sender.calls == []


def test_a_user_who_never_shared_gets_an_account_row_when_subscribing(world) -> None:
    """@description: Subscribing is possible before any submission; the account exists with zero points afterwards.
    @param world: Daily world.
    @return: None.
    """
    world.enable(CAROL, 12)
    assert world.balances()[CAROL] == 0 and tuple(world.repository.daily_preference(CAROL))[1:] == (True, 12)


# --- the daily task: when, once, and what it charges ------------------------------------------------------------------------


def test_the_default_20_00_delivers_one_dm_charges_one_point_and_shows_only_the_masked_recommender(world) -> None:
    """@description: Nothing before 20:00; at 20:00 one DM with the other user's link and a half-masked name, one debit, a delivered task row.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    sender, names = ScriptedSender(), Names({BOB: CANARY_NAME})
    assert world.sweep(sender, at(0, 19, 59, 59), names).results == () and sender.calls == []
    report = world.sweep(sender, at(0, 20), names)
    assert world.outcomes(report) == {ALICE: DailyOutcome.DELIVERED}
    text = sender.calls[0][1]
    assert [call[0] for call in sender.calls] == [ALICE] and url(VIDEO_B) in text
    assert mask_display_name(CANARY_NAME) in text and CANARY_NAME not in text and str(BOB) not in text
    assert names.calls == [BOB] and world.dispatches() == [(DAY0, "delivered")]
    assert world.balances()[ALICE] == 0 and world.debit_count() == 1
    world.assert_books_consistent()


def test_each_user_is_served_only_at_their_own_hour(world) -> None:
    """@description: Three subscribers at 08, 20 and 23: each sweep instant picks only the matching person.
    @param world: Daily world.
    @return: None.
    """
    world.add_song(CAROL)
    for user, hour in ((ALICE, 8), (BOB, 20), (CAROL, 23)):
        world.enable(user, hour)
    served = []
    for hour in (7, 8, 19, 20, 22, 23):
        sender = ScriptedSender()
        world.sweep(sender, at(0, hour))
        served.append([call[0] for call in sender.calls])
    assert served == [[], [ALICE], [], [BOB], [], [CAROL]]


def test_repeated_sweeps_and_restarts_in_one_day_deliver_charge_and_record_once(world) -> None:
    """@description: Five sweeps, two of them from fresh repositories: still one DM, one debit, one task row for the day.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    sender = ScriptedSender()
    for minute in range(5):
        repository = world.new_repository() if minute in (2, 4) else None
        world.sweep(sender, at(0, 20, minute), repository=repository)
    assert len(sender.calls) == 1 and world.debit_count() == 1 and world.task_days() == [DAY0]
    world.assert_books_consistent()


def test_the_taipei_midnight_starts_a_new_day_for_the_task(world) -> None:
    """@description: Delivered at 23:59:59 Taipei, then an hour-00 schedule fires at 00:00:00 the next Taipei day (16:00 UTC), recorded under that date.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE, 23)
    world.sweep(ScriptedSender(), at(0, 23, 59, 59))
    world.add_song(ALICE)
    world.add_song(BOB)
    world.repository.save_daily_preference(ALICE, enabled=None, delivery_hour=0)
    sender = ScriptedSender()
    world.sweep(sender, at(1, 0, 0, 0))
    assert len(sender.calls) == 1 and world.dispatches() == [(DAY0, "delivered"), (DAY0 + timedelta(days=1), "delivered")]


def test_moving_the_hour_earlier_before_todays_delivery_takes_effect_today(world) -> None:
    """@description: Today's task has not run, so the new hour applies today.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE, 22)
    sender = ScriptedSender()
    world.sweep(sender, at(0, 20))
    assert sender.calls == []
    world.repository.save_daily_preference(ALICE, enabled=None, delivery_hour=20)
    world.sweep(sender, at(0, 20, 1))
    assert len(sender.calls) == 1 and world.dispatches() == [(DAY0, "delivered")]


def test_changing_the_hour_after_todays_delivery_takes_effect_tomorrow(world) -> None:
    """@description: After today's task ran, a new hour never sends again today and applies from the next Taipei day.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    sender = ScriptedSender()
    world.sweep(sender, at(0, 20))
    world.add_song(ALICE)
    world.add_song(BOB)
    world.repository.save_daily_preference(ALICE, enabled=None, delivery_hour=21)
    for moment in (at(0, 21), at(0, 23, 59), at(1, 20, 59)):
        world.sweep(sender, moment)
    assert len(sender.calls) == 1
    world.sweep(sender, at(1, 21))
    assert len(sender.calls) == 2 and world.task_days() == [DAY0, DAY0 + timedelta(days=1)]


def test_a_task_missed_while_the_service_was_down_is_sent_later_the_same_day_and_a_missed_day_is_not_made_up(world) -> None:
    """@description: First sweep at 22:30 still delivers day 0; nothing runs on day 1; on day 2 the user is served at their hour with no extra catch-up.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    sender = ScriptedSender()
    world.sweep(sender, at(0, 22, 30))
    world.add_song(ALICE)
    world.add_song(BOB)
    world.sweep(sender, at(2, 9))
    assert len(sender.calls) == 1
    world.sweep(sender, at(2, 20))
    assert len(sender.calls) == 2 and world.task_days() == [DAY0, DAY0 + timedelta(days=2)]


def test_turning_daily_off_stops_later_deliveries_and_keeps_the_history(world) -> None:
    """@description: After off there is no DM, no debit, no notice; the earlier recommendation and task rows stay.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    world.sweep(ScriptedSender(), at(0, 20))
    world.add_song(ALICE)
    world.add_song(BOB)
    world.repository.save_daily_preference(ALICE, enabled=False, delivery_hour=None)
    sender = ScriptedSender()
    assert world.sweep(sender, at(1, 20)).results == () and sender.calls == []
    assert world.dispatches() == [(DAY0, "delivered")] and [row["status"] for row in world.rows()] == ["delivered"]


# --- not enough Nickel and no candidate -------------------------------------------------------------------------------------


def test_a_user_without_a_point_gets_the_not_enough_message_once_a_day_and_is_not_charged(world) -> None:
    """@description: Zero points: the Owner wording by DM, no debit, no reservation, and extra sweeps the same day add nothing.
    @param world: Daily world.
    @return: None.
    """
    world.spend_alice_balance()
    world.enable(ALICE)
    sender = ScriptedSender()
    for minute in range(3):
        world.sweep(sender, at(0, 20, minute))
    assert sender.calls == [(ALICE, NOT_ENOUGH_NICKEL_TEXT)] and LINK_MARKER not in NOT_ENOUGH_NICKEL_TEXT
    assert world.balances()[ALICE] == 0 and world.debit_count() == 1 and len(world.rows()) == 1
    assert world.notices() == [(DAY0, "insufficient_credit", "sent")] and world.task_days() == [DAY0]
    world.assert_books_consistent()


def test_the_not_enough_message_is_silenced_after_three_days_and_resumes_after_a_top_up_cycle(world) -> None:
    """@description: Days 0-2 send, days 3-4 are silent; a top-up after the day's task does not trigger a same-day delivery; the next day delivers; running out again notifies again.
    @param world: Daily world.
    @return: None.
    """
    world.spend_alice_balance()
    world.enable(ALICE)
    sender = ScriptedSender()
    for day in range(5):
        world.sweep(sender, at(day, 20))
    assert [call[1] for call in sender.calls] == [NOT_ENOUGH_NICKEL_TEXT] * 3
    assert [delivery for _day, _kind, delivery in world.notices()] == ["sent", "sent", "sent", "silenced", "silenced"]
    world.add_song(ALICE)
    world.add_song(BOB)
    world.sweep(sender, at(4, 22))
    assert len(sender.calls) == 3 and world.balances()[ALICE] == 1
    world.sweep(sender, at(5, 20))
    assert LINK_MARKER in sender.calls[3][1] and world.balances()[ALICE] == 0
    world.sweep(sender, at(6, 20))
    assert sender.calls[4][1] == NOT_ENOUGH_NICKEL_TEXT and world.notices()[-1][2] == "sent"


def test_a_user_with_a_point_but_no_song_gets_the_no_candidate_message_with_the_same_throttle_and_keeps_the_point(world) -> None:
    """@description: Three notices, then silence; a new song later delivers normally and only then charges.
    @param world: Daily world.
    @return: None.
    """
    world.spend_alice_balance()
    world.add_song(ALICE)
    world.enable(ALICE)
    sender = ScriptedSender()
    for day in range(4):
        world.sweep(sender, at(day, 20))
    assert [call[1] for call in sender.calls] == [NO_CANDIDATE_DAILY_TEXT] * 3 and world.balances()[ALICE] == 1
    assert [row[1:] for row in world.notices()] == [("no_candidate", "sent")] * 3 + [("no_candidate", "silenced")]
    assert world.debit_count() == 1
    world.add_song(BOB)
    world.sweep(sender, at(4, 20))
    assert LINK_MARKER in sender.calls[3][1] and world.balances()[ALICE] == 0 and world.debit_count() == 2
    world.assert_books_consistent()


def test_a_not_enough_message_that_cannot_be_delivered_is_recorded_not_retried_and_charges_nothing(world) -> None:
    """@description: A refused notice DM is not retried in a loop that day; nothing is charged; the other subscriber is still served.
    @param world: Daily world.
    @return: None.
    """
    world.spend_alice_balance()
    world.enable(ALICE)
    world.enable(BOB)
    sender = PerUserSender({ALICE: DmResult.not_sent("dm_closed")})
    report = world.sweep(sender, at(0, 20))
    world.sweep(sender, at(0, 21))
    assert world.outcomes(report) == {ALICE: DailyOutcome.NOTICE_FAILED, BOB: DailyOutcome.DELIVERED}
    assert sorted(call[0] for call in sender.calls) == [ALICE, BOB] and world.notices()[0][2] == "failed"
    assert world.balances()[ALICE] == 0 and world.debit_count() == 1
    world.assert_books_consistent()


def test_a_disabled_user_with_no_points_receives_no_notice(world) -> None:
    """@description: Notices only go to subscribers.
    @param world: Daily world.
    @return: None.
    """
    world.spend_alice_balance()
    sender = ScriptedSender()
    world.sweep(sender, at(0, 20))
    assert sender.calls == [] and world.notices() == [] and world.dispatches() == []


# --- delivery failures follow the stage B contract --------------------------------------------------------------------------


def test_a_proven_unsent_daily_dm_is_refunded_not_retried_that_day_and_tried_again_the_next_day(world) -> None:
    """@description: Explicit non-delivery: exactly one refund, a failed task row, no second attempt on the same day, a normal delivery the next day.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    first = ScriptedSender(DmResult.not_sent("dm_closed"))
    report = world.sweep(first, at(0, 20))
    assert world.outcomes(report) == {ALICE: DailyOutcome.FAILED} and world.balances()[ALICE] == 1
    assert len(world.ledger("delivery_refund")) == 1 and world.dispatches() == [(DAY0, "failed")]
    later = ScriptedSender()
    world.sweep(later, at(0, 22))
    assert later.calls == [] and world.balances()[ALICE] == 1
    world.sweep(later, at(1, 20))
    assert len(later.calls) == 1 and world.dispatches()[-1] == (DAY0 + timedelta(days=1), "delivered") and world.balances()[ALICE] == 0
    world.assert_books_consistent()


def test_an_unknown_dm_result_keeps_the_debit_sends_nothing_more_and_blocks_the_next_day_without_a_new_debit(world) -> None:
    """@description: Uncertain: no refund, no resend, an Owner-visible stuck case; the next day finds the open exchange, sends nothing and debits nothing.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    first = ScriptedSender(DmResult.unknown("timeout"))
    assert world.outcomes(world.sweep(first, at(0, 20))) == {ALICE: DailyOutcome.UNCERTAIN}
    assert world.balances()[ALICE] == 0 and world.ledger("delivery_refund") == [] and world.dispatches() == [(DAY0, "uncertain")]
    later = ScriptedSender()
    report = world.sweep(later, at(1, 20))
    assert world.outcomes(report) == {ALICE: DailyOutcome.IN_PROGRESS} and later.calls == [] and world.debit_count() == 1
    stuck = world.repository.owner_stuck_exchanges(OWNER_ID, at(1, 21))
    assert [(row["requester"], row["reason"]) for row in stuck] == [(ALICE, "delivery_result_unknown")]


def test_a_crash_between_the_claim_and_the_result_is_never_resent_after_the_restart(world) -> None:
    """@description: The process dies inside the send; a new process after the lease sends nothing, charges nothing more, and leaves one task row for the day.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    crashing = CrashingSender()
    with pytest.raises(Crash):
        world.sweep(crashing, at(0, 20))
    assert world.rows()[0]["status"] == "sending" and len(crashing.calls) == 1
    later = ScriptedSender()
    world.sweep(later, at(0, 20) + DELIVERY_LEASE + timedelta(minutes=1), repository=world.new_repository())
    assert later.calls == [] and world.rows()[0]["status"] == "uncertain" and world.debit_count() == 1
    assert world.balances()[ALICE] == 0 and world.task_days() == [DAY0]


@pytest.mark.parametrize("alice_script, expected_outcome, alice_balance", [
    (DmResult.not_sent("dm_closed"), DailyOutcome.FAILED, 1),
    (RuntimeError("adapter blew up"), DailyOutcome.UNCERTAIN, 0),
], ids=["proven-unsent", "adapter-exception"])
def test_one_users_dm_failure_does_not_block_anyone_else(world, alice_script, expected_outcome, alice_balance) -> None:
    """@description: Alice cannot receive; Bob, due at the same time, is still delivered and charged exactly once.
    @param world: Daily world.
    @param alice_script: What the sender does for Alice.
    @param expected_outcome: Alice's recorded outcome.
    @param alice_balance: Alice's balance afterwards (refund only for a proven non-delivery).
    @return: None.
    """
    world.enable(ALICE)
    world.enable(BOB)
    report = world.sweep(PerUserSender({ALICE: alice_script}), at(0, 20))
    assert world.outcomes(report) == {ALICE: expected_outcome, BOB: DailyOutcome.DELIVERED}
    assert world.balances() == {ALICE: alice_balance, BOB: 0}
    world.assert_books_consistent()


def test_a_failed_name_lookup_still_delivers_with_the_anonymous_placeholder(world) -> None:
    """@description: The name is a courtesy: if Discord cannot give it, the song still arrives and the charge stands.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    sender = ScriptedSender()
    report = world.sweep(sender, at(0, 20), Names(failing=frozenset({BOB})))
    assert world.outcomes(report) == {ALICE: DailyOutcome.DELIVERED} and ANONYMOUS_NAME in sender.calls[0][1]


# --- races and privacy ------------------------------------------------------------------------------------------------------


def test_two_concurrent_sweeps_deliver_charge_and_record_once(world) -> None:
    """@description: Two workers with their own repositories race the same due user; the database claim leaves one DM, one debit, one task row.
    @param world: Daily world.
    @return: None.
    """
    world.enable(ALICE)
    sender = ScriptedSender()
    _run_concurrently([lambda: world.sweep(sender, at(0, 20), repository=world.new_repository()) for _ in range(2)])
    assert len(sender.calls) == 1 and world.debit_count() == 1 and world.task_days() == [DAY0]
    world.assert_books_consistent()


def test_a_daily_exchange_and_a_manual_exchange_cannot_both_charge_in_one_open_state(world) -> None:
    """@description: A manual exchange left uncertain blocks the scheduled task: in progress, no second debit, no DM.
    @param world: Daily world.
    @return: None.
    """
    world.run(ScriptedSender(DmResult.unknown("timeout")))
    world.enable(ALICE)
    sender = ScriptedSender()
    report = world.sweep(sender, at(0, 20))
    assert world.outcomes(report) == {ALICE: DailyOutcome.IN_PROGRESS} and sender.calls == [] and world.debit_count() == 1


def test_the_recommenders_name_and_id_never_reach_a_log_or_any_table(world, caplog) -> None:
    """@description: Run a full delivery with a canary name and scan every log record and every text column of every table for the full name and the id.
    @param world: Daily world.
    @param caplog: Pytest log capture.
    @return: None.
    """
    caplog.set_level(logging.DEBUG)
    world.enable(ALICE)
    sender = ScriptedSender()
    world.sweep(sender, at(0, 20), Names({BOB: CANARY_NAME}))
    assert CANARY_NAME not in caplog.text and CANARY_NAME not in sender.calls[0][1]
    columns = admin_query(world.database, "SELECT table_name, column_name FROM information_schema.columns "
                                          "WHERE table_schema = 'jukebox101' AND data_type IN ('text', 'character varying', 'character')")
    for column in columns:
        statement = f'SELECT count(*) AS hits FROM "{column["table_name"]}" WHERE "{column["column_name"]}" LIKE %s'
        assert admin_query(world.database, statement, (f"%{CANARY_NAME}%",))[0]["hits"] == 0
