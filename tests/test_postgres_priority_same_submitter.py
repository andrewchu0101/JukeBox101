"""Real PostgreSQL behaviour of the same-submitter priority scope: an undecided case holds back only its own submitter.

The official provider is a scripted fake. Intake, review, version comparison, settlement and the books are the production tables on disposable PostgreSQL.
"""

from dataclasses import replace

import pytest

from bot.review import ReviewStatus
from bot.reward_candidates import RewardCandidateStatus
from tests.test_postgres_auto_pipeline import ALICE, BOB, VIDEO_A, VIDEO_B, VIDEO_C
from tests.test_postgres_conflict_resolution import conflicted_video
from tests.test_postgres_music_category_mode import MusicWorld

pytestmark = pytest.mark.postgres


@pytest.fixture
def world(pg_database) -> MusicWorld:
    """@description: Provide one music_category world with settlement on.
    @param pg_database: Disposable database.
    @return: World.
    """
    return MusicWorld(pg_database)


def park_alice_first_case(world: MusicWorld) -> None:
    """@description: Leave Alice's first song waiting for the Owner because it is outside the music category.
    @param world: World.
    @return: None.
    """
    world.metadata.scripted[VIDEO_A] = replace(world.metadata.scripted[VIDEO_A], category_id="24")
    assert world.share(ALICE, VIDEO_A, "discord:1").review_status is ReviewStatus.PENDING_REVIEW


def test_a_pending_case_of_one_person_does_not_stop_another_person_from_being_paid(world) -> None:
    """@description: The production jam: Bob's clean song is approved, settled and paid while Alice's earlier case is still undecided.
    @param world: Music-category world.
    @return: None.
    """
    park_alice_first_case(world)
    outcome = world.share(BOB, VIDEO_B, "discord:2")
    assert outcome.decision.status is RewardCandidateStatus.AWARDED
    assert world.balances() == {ALICE: 0, BOB: 1}
    world.assert_books_balance(1)


def test_a_pending_case_still_holds_back_the_same_persons_next_case(world) -> None:
    """@description: Alice's own later song waits behind her undecided one and is not paid.
    @param world: Music-category world.
    @return: None.
    """
    park_alice_first_case(world)
    world.share(BOB, VIDEO_B, "discord:2")
    outcome = world.share(ALICE, VIDEO_C, "discord:3")
    assert outcome.decision.status is RewardCandidateStatus.WAITING_PREDECESSOR
    assert world.balances() == {ALICE: 0, BOB: 1}
    world.assert_books_balance(1)


def test_an_unknown_version_conflict_freezes_only_the_same_person_and_the_same_video(world) -> None:
    """@description: Alice's conflicted video freezes her other songs and anyone sharing that video, but another person's different song is paid.
    @param world: Music-category world.
    @return: None.
    """
    conflicted_video(world)
    assert world.share(BOB, VIDEO_B, "discord:2").decision.status is RewardCandidateStatus.AWARDED
    assert world.share(ALICE, VIDEO_C, "discord:3").decision.status is RewardCandidateStatus.CONFLICT
    assert world.share(BOB, VIDEO_A, "discord:4").decision.status is RewardCandidateStatus.CONFLICT
    assert world.balances() == {ALICE: 0, BOB: 1}
    world.assert_books_balance(1)
