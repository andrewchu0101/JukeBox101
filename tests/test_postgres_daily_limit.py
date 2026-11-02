"""Real PostgreSQL behaviour of the daily reward limit: ten rewarded submissions per person per Taipei day, the rest stored but unpaid.

The official provider is a scripted fake. Intake, review, version comparison, settlement and the books are the production tables on disposable PostgreSQL.
"""

import pytest

from bot.auto_pipeline import SWEEP_CANDIDATE_LIMIT, format_share_outcome
from bot.daily_limit import DAILY_LIMIT_REPLY, MAX_REWARDED_SUBMISSIONS_PER_DAY
from bot.review import ReviewStatus
from bot.reward_candidates import RewardCandidateStatus, RewardDecisionReason
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB
from tests.test_postgres_auto_version_confirm import official
from tests.test_postgres_music_category_mode import MusicWorld

pytestmark = pytest.mark.postgres

TITLES = ("Amber Whisper", "Basalt Carnival", "Cobalt Lullaby", "Dune Horizon", "Ember Parade", "Fjord Symphony", "Glacier Tango", "Harbor Lantern", "Island Reverie", "Jungle Compass", "Kestrel Mirage", "Lagoon Ballad", "Meadow Thunder", "Nebula Postcard")  # [unrelated titles]: Every pair is below the similarity threshold.


def video(index: int) -> str:
    """@description: Name the synthetic video with a valid eleven-character identifier.
    @param index: Position in the scenario.
    @return: Video identifier.
    """
    return f"vid{index:08d}"


class LimitWorld(MusicWorld):
    """A music_category world with enough distinct, unrelated, unlisted songs to cross the daily limit."""

    def __init__(self, database) -> None:
        """@description: Script one unrelated song per index, each on its own channel and with its own duration.
        @param self: World.
        @param database: Disposable database.
        @return: None.
        """
        super().__init__(database)
        for index, title in enumerate(TITLES):
            channel = "UC" + chr(ord("a") + index) * 22
            self.metadata.scripted[video(index)] = official(video(index), title, 100 + index * 20, channel)

    def share_index(self, user: int, index: int):
        """@description: Share the scripted song with a unique operation key.
        @param self: World.
        @param user: Submitter.
        @param index: Song index.
        @return: Pipeline outcome.
        """
        return self.share(user, video(index), f"discord:{user}:{index}")

    def lookups_of(self, index: int) -> int:
        """@description: Count the official lookups made for one song.
        @param self: World.
        @param index: Song index.
        @return: Number of lookups.
        """
        return self.metadata.calls.count(video(index))


@pytest.fixture
def world(pg_database) -> LimitWorld:
    """@description: Provide one limit world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return LimitWorld(pg_database)


def test_the_tenth_song_of_the_day_is_paid_and_the_eleventh_is_stored_but_unpaid_without_a_lookup(world) -> None:
    """@description: The headline rule, end to end: stored, answered with the limit wording, not paid, and no YouTube quota spent on it.
    @param world: Limit world.
    @return: None.
    """
    outcomes = [world.share_index(ALICE, index) for index in range(MAX_REWARDED_SUBMISSIONS_PER_DAY)]
    assert all(item.decision.status is RewardCandidateStatus.AWARDED for item in outcomes)
    eleventh = world.share_index(ALICE, 10)
    assert (eleventh.decision.status, eleventh.decision.reason) == (RewardCandidateStatus.INELIGIBLE, RewardDecisionReason.DAILY_LIMIT)
    assert DAILY_LIMIT_REPLY in format_share_outcome("discord:x", eleventh) and "+1" not in format_share_outcome("discord:x", eleventh)
    assert world.lookups_of(10) == 0
    assert world.balances() == {ALICE: 10}
    assert len(admin_query(world.database, "SELECT 1 FROM jb2_events WHERE discord_user_id = %s", (ALICE,))) == 11
    world.assert_books_balance(10)


def test_another_person_is_not_affected_by_someone_elses_limit(world) -> None:
    """@description: The limit is per person.
    @param world: Limit world.
    @return: None.
    """
    for index in range(MAX_REWARDED_SUBMISSIONS_PER_DAY + 1):
        world.share_index(ALICE, index)
    assert world.share_index(BOB, 11).decision.status is RewardCandidateStatus.AWARDED
    assert world.balances() == {ALICE: 10, BOB: 1}
    world.assert_books_balance(11)


def test_a_new_taipei_day_starts_a_new_allowance(world) -> None:
    """@description: Moving the earlier submissions to a previous day frees the allowance; the database clock decides, not the caller.
    @param world: Limit world.
    @return: None.
    """
    for index in range(MAX_REWARDED_SUBMISSIONS_PER_DAY):
        world.share_index(ALICE, index)
    admin_query(world.database, "UPDATE jb2_events SET accepted_at = accepted_at - INTERVAL '2 days'")
    assert world.share_index(ALICE, 10).decision.status is RewardCandidateStatus.AWARDED
    world.assert_books_balance(11)


def test_an_excess_first_submitter_does_not_block_a_later_person_who_shares_the_same_song(world) -> None:
    """@description: A second account cannot be used to park a song's first claim: the excess claim is released, so the next person is paid.
    @param world: Limit world.
    @return: None.
    """
    for index in range(MAX_REWARDED_SUBMISSIONS_PER_DAY):
        world.share_index(ALICE, index)
    assert world.share_index(ALICE, 10).decision.reason is RewardDecisionReason.DAILY_LIMIT
    bob = world.share_index(BOB, 10)
    assert bob.decision.status is RewardCandidateStatus.AWARDED
    assert world.balances() == {ALICE: 10, BOB: 1}
    world.assert_books_balance(11)


def test_the_sweep_never_looks_up_an_excess_submission(world) -> None:
    """@description: The background path spends no quota on the excess and does not let it fill the read window.
    @param world: Limit world.
    @return: None.
    """
    for index in range(MAX_REWARDED_SUBMISSIONS_PER_DAY + 1):
        world.repository.accept_pending(ALICE, f"https://www.youtube.com/watch?v={video(index)}", f"discord:{ALICE}:{index}")
    rows = world.repository.pending_validation_operations(SWEEP_CANDIDATE_LIMIT, ())
    assert len(rows) == MAX_REWARDED_SUBMISSIONS_PER_DAY and video(10) not in {item[1] for item in rows}
    for _ in range(4):
        world.pipeline.sweep()
    assert world.lookups_of(10) == 0 and all(world.lookups_of(index) >= 1 for index in range(MAX_REWARDED_SUBMISSIONS_PER_DAY))
    assert world.balances() == {ALICE: 10}


def test_replaying_an_operation_changes_nothing_and_takes_nothing_back(world) -> None:
    """@description: Replays keep their original rank, and a payment already made stays.
    @param world: Limit world.
    @return: None.
    """
    for index in range(MAX_REWARDED_SUBMISSIONS_PER_DAY + 1):
        world.share_index(ALICE, index)
    first = world.share_index(ALICE, 0)
    eleventh = world.share_index(ALICE, 10)
    assert first.decision.status is RewardCandidateStatus.AWARDED and eleventh.decision.reason is RewardDecisionReason.DAILY_LIMIT
    assert world.balances() == {ALICE: 10}
    world.assert_books_balance(10)


def test_the_limit_leaves_the_review_of_the_excess_pending_and_visible_to_the_owner(world) -> None:
    """@description: Nothing is rejected or hidden: the excess song simply waits.
    @param world: Limit world.
    @return: None.
    """
    for index in range(MAX_REWARDED_SUBMISSIONS_PER_DAY + 1):
        world.share_index(ALICE, index)
    status = admin_query(world.database, "SELECT review_status FROM jb2_videos WHERE video_id = %s", (video(10),))[0]["review_status"]
    assert status == ReviewStatus.PENDING_VALIDATION.value


def test_the_sql_day_and_the_python_day_agree_at_the_taipei_midnight_boundary(world) -> None:
    """@description: The sweep counts in SQL and the reward rule counts in Python; at 23:59:59 and 00:00:00 Taipei both must give the same answer.
    @param world: Limit world.
    @return: None.
    """
    operations = []
    for index in range(MAX_REWARDED_SUBMISSIONS_PER_DAY + 1):
        operations.append(f"discord:{ALICE}:{index}")
        world.repository.accept_pending(ALICE, f"https://www.youtube.com/watch?v={video(index)}", operations[-1])
    admin_query(world.database, "UPDATE jb2_events SET accepted_at = TIMESTAMPTZ '2026-11-03 15:59:59+00'")
    admin_query(world.database, "UPDATE jb2_events SET accepted_at = TIMESTAMPTZ '2026-11-03 16:00:00+00' WHERE operation_id = %s", (operations[-1],))
    assert len(world.repository.pending_validation_operations(SWEEP_CANDIDATE_LIMIT, ())) == MAX_REWARDED_SUBMISSIONS_PER_DAY + 1
    assert world.repository.reward_state(operations[-1])[1].reason is not RewardDecisionReason.DAILY_LIMIT
    admin_query(world.database, "UPDATE jb2_events SET accepted_at = TIMESTAMPTZ '2026-11-03 15:59:59+00' WHERE operation_id = %s", (operations[-1],))
    assert len(world.repository.pending_validation_operations(SWEEP_CANDIDATE_LIMIT, ())) == MAX_REWARDED_SUBMISSIONS_PER_DAY
    assert world.repository.reward_state(operations[-1])[1].reason is RewardDecisionReason.DAILY_LIMIT
