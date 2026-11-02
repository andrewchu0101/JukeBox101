"""Real PostgreSQL behaviour of the music_category trust mode: no allowlist, every other protection, and a growing song pool.

The official provider is a scripted fake. Review, version comparison, system-judged evidence, settlement and the books are the production
tables, run on disposable PostgreSQL 17 and 18. The pool is read the way /exchange reads it, but /exchange itself stays closed.
"""

import logging
import random
from dataclasses import replace
from functools import partial

import pytest

from bot.auto_pipeline import AutoPipeline
from bot.database import exchange_reservation
from bot.database.connection import open_connection
from bot.database.submission_transaction import submission_transaction
from bot.exchange_selection import deliverable_versions, draw_exchange_candidate
from bot.review import ReviewStatus
from bot.reward_candidates import RewardCandidateStatus
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB, VIDEO_A, VIDEO_B, VIDEO_C
from tests.test_postgres_auto_version_confirm import SYSTEM_EVIDENCE, TABLES, TITLE_CANARY, ConfirmWorld, official

pytestmark = pytest.mark.postgres

CHANNEL_A, CHANNEL_B, CHANNEL_C = "UC" + "m" * 22, "UC" + "n" * 22, "UC" + "o" * 22  # [no list mentions these]: Each song has its own channel.
ALBUM_TITLE = f"{TITLE_CANARY} Alpha Beta Gamma"


class MusicWorld(ConfirmWorld):
    """A world whose pipeline runs in the requested trust mode with an empty allowlist."""

    trust_mode = "music_category"

    def __init__(self, database, mode: str | None = None) -> None:
        """@description: Choose the mode before the base class builds the pipeline, then give every video its own unlisted channel.
        @param self: World.
        @param database: Disposable database.
        @param mode: Trust mode, or None for music_category.
        @return: None.
        """
        self.trust_mode = mode or self.trust_mode
        super().__init__(database)
        for video, channel in ((VIDEO_A, CHANNEL_A), (VIDEO_B, CHANNEL_B), (VIDEO_C, CHANNEL_C)):
            self.metadata.scripted[video] = replace(self.metadata.scripted[video], channel_id=channel)

    def new_pipeline(self, repository=None) -> AutoPipeline:
        """@description: Build a pipeline with review, retention, settlement and version confirmation on, an empty allowlist and the chosen mode.
        @param self: World.
        @param repository: Optional repository override.
        @return: Pipeline.
        """
        return AutoPipeline(repository or self.repository, self.metadata, frozenset(), settlement_enabled=True, api_retention=True,
                            version_confirm=True, trust_mode=self.trust_mode)

    def pool(self):
        """@description: Read the settled, deliverable song versions exactly as the exchange reservation reads them.
        @param self: World.
        @return: Deliverable settled versions.
        """
        with submission_transaction(partial(open_connection, self.database.app)) as (cursor, _control):
            return deliverable_versions(exchange_reservation._settled_versions(cursor))


@pytest.fixture
def world(pg_database) -> MusicWorld:
    """@description: Provide one music_category world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return MusicWorld(pg_database)


def reasons(world: MusicWorld) -> str:
    """@description: Join every stored review decision reason.
    @param world: World.
    @return: One string.
    """
    return " ".join(row["reason"] for row in admin_query(world.database, "SELECT decision_reason AS reason FROM jb2_review_history"))


def test_two_different_users_each_add_a_qualifying_song_from_unlisted_channels_and_both_are_paid(world) -> None:
    """@description: With an empty allowlist the pool grows: each song is approved, version-confirmed by the system and settled once.
    @param world: Music-category world.
    @return: None.
    """
    first = world.share(ALICE, VIDEO_A, "discord:1")
    second = world.share(BOB, VIDEO_B, "discord:2")
    assert first.decision.status is RewardCandidateStatus.AWARDED and second.decision.status is RewardCandidateStatus.AWARDED
    assert world.balances() == {ALICE: 1, BOB: 1}
    world.assert_books_balance(2)
    assert "music_category_no_version_concern" in reasons(world) and "trusted_channel_music" not in reasons(world)
    assert len(admin_query(world.database, f"SELECT 1 FROM jb2_evidence WHERE {SYSTEM_EVIDENCE}")) == 2


def test_both_songs_enter_the_exchange_pool_and_each_user_would_only_get_the_other_ones(world) -> None:
    """@description: The pool read by the exchange has both deliverable songs; a third user can draw either; owners never draw their own.
    @param world: Music-category world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.share(BOB, VIDEO_B, "discord:2")
    pool = world.pool()
    assert {item.video_id for item in pool} == {VIDEO_A, VIDEO_B}
    drawn_by_carol = {draw_exchange_candidate(pool, frozenset(), frozenset(), random.Random(seed)).video_id for seed in range(60)}
    assert drawn_by_carol == {VIDEO_A, VIDEO_B}
    assert {draw_exchange_candidate(pool, frozenset(), frozenset(), random.Random(seed), frozenset({VIDEO_A})).video_id for seed in range(30)} == {VIDEO_B}
    assert {draw_exchange_candidate(pool, frozenset(), frozenset(), random.Random(seed), frozenset({VIDEO_B})).video_id for seed in range(30)} == {VIDEO_A}


def test_a_song_that_is_not_in_the_music_category_waits_for_the_owner_and_does_not_enter_the_pool(world) -> None:
    """@description: The category gate holds: pending review with its reason code, no award, no pool entry.
    @param world: Music-category world.
    @return: None.
    """
    world.metadata.scripted[VIDEO_A] = replace(world.metadata.scripted[VIDEO_A], category_id="24")
    outcome = world.share(ALICE, VIDEO_A, "discord:1")
    assert outcome.review_status is ReviewStatus.PENDING_REVIEW and "category_not_music" in reasons(world)
    assert world.balances().get(ALICE, 0) == 0 and world.pool() == ()
    world.assert_books_balance(0)


@pytest.mark.parametrize("override", [{"live_broadcast_content": "live"}, {"duration_seconds": 59}, {"duration_seconds": 721}, {"privacy_status": "private"}])
def test_live_out_of_range_and_private_videos_are_still_excluded_from_the_pool(world, override) -> None:
    """@description: Every existing exclusion keeps working without an allowlist.
    @param world: Music-category world.
    @param override: Metadata change that must keep the song out.
    @return: None.
    """
    world.metadata.scripted[VIDEO_A] = replace(world.metadata.scripted[VIDEO_A], **override)
    world.share(ALICE, VIDEO_A, "discord:1")
    assert world.balances().get(ALICE, 0) == 0 and world.pool() == ()


def test_the_stage_a_comparison_still_stops_a_look_alike_song_from_an_unlisted_channel(world) -> None:
    """@description: A near-identical title one second apart on the same channel is a doubt: the case waits for the Owner and pays nothing.
    @param world: Music-category world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.metadata.scripted[VIDEO_B] = official(VIDEO_B, "alpha beta gamma (Official Video)", 190, CHANNEL_A)
    outcome = world.share(BOB, VIDEO_B, "discord:2")
    assert outcome.review_status is ReviewStatus.PENDING_REVIEW and world.balances() == {ALICE: 1, BOB: 0}
    assert "same_channel_close_duration" in reasons(world) and len(world.pool()) == 1
    world.assert_books_balance(1)


def test_allowlist_mode_with_an_empty_list_pays_nothing_and_the_pool_stays_empty(pg_database) -> None:
    """@description: The default mode is unchanged: the same clean song waits for the Owner.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = MusicWorld(pg_database, mode="allowlist")
    outcome = world.share(ALICE, VIDEO_A, "discord:1")
    assert outcome.review_status is ReviewStatus.PENDING_REVIEW and "channel_not_trusted" in reasons(world)
    assert world.balances().get(ALICE, 0) == 0 and world.pool() == ()


def test_no_channel_id_or_title_is_stored_after_the_songs_are_decided(world) -> None:
    """@description: Channels are judged in memory; outside the short-lived table nothing keeps them, and the short-lived rows are cleared once decided.
    @param world: Music-category world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.share(BOB, VIDEO_B, "discord:2")
    durable = "\n".join(row["payload"] for table in TABLES if table != "jb2_api_observations"
                        for row in admin_query(world.database, f"SELECT row_to_json(t)::text AS payload FROM {table} t"))
    assert CHANNEL_A not in durable and CHANNEL_B not in durable and TITLE_CANARY not in durable
    live = admin_query(world.database, "SELECT video_id FROM jb2_api_observations WHERE channel_id IS NOT NULL")
    assert live == []


def test_no_log_record_contains_a_channel_id_or_a_title(world, caplog) -> None:
    """@description: Logs carry class names and counts only.
    @param world: Music-category world.
    @param caplog: Log capture.
    @return: None.
    """
    with caplog.at_level(logging.DEBUG):
        world.share(ALICE, VIDEO_A, "discord:1")
        world.share(BOB, VIDEO_B, "discord:2")
    assert CHANNEL_A not in caplog.text and CHANNEL_B not in caplog.text and TITLE_CANARY not in caplog.text


def test_replaying_a_share_in_the_open_mode_does_not_pay_twice(world) -> None:
    """@description: Idempotence is unchanged.
    @param world: Music-category world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.share(ALICE, VIDEO_A, "discord:1")
    assert world.balances() == {ALICE: 1}
    world.assert_books_balance(1)
