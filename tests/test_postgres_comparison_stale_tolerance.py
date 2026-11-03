"""Real PostgreSQL behaviour of the relaxed stale-comparison rule: a few submissions accepted during a comparison no longer send a clean case to the Owner.

A later submission always compares itself with every earlier accepted one, so a concurrent duplicate is still caught by the later of the two; only a crowd of
more than STALE_TOLERANCE_SUBMISSIONS accepted during one comparison is treated as stale.
"""

import pytest

from bot.database.auto_version_store import STALE_TOLERANCE_SUBMISSIONS
from bot.review import ReviewStatus
from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID, VIDEO_A, VIDEO_B, url
from tests.test_postgres_auto_version_confirm import ConfirmWorld
from tests.test_postgres_music_category_mode import MusicWorld
from bot.version_identity import VersionIdentityStatus

pytestmark = pytest.mark.postgres


@pytest.fixture
def world(pg_database) -> MusicWorld:
    """@description: Provide one music_category world, whose comparison hook simulates concurrent intake.
    @param pg_database: Disposable database.
    @return: World.
    """
    return MusicWorld(pg_database)


def crowd(world: ConfirmWorld, size: int) -> None:
    """@description: Make the next comparison see this many submissions accepted while it runs.
    @param world: World.
    @param size: Number of shares accepted during the comparison.
    @return: None.
    """
    def accept() -> None:
        """@description: Accept the crowd.
        @param: none
        @return: None.
        """
        for index in range(size):
            world.repository.accept_pending(ALICE, url(f"y{index:010d}"), f"discord:8{index}")

    world.metadata.during_comparison = accept


def test_the_tolerated_number_of_concurrent_submissions_does_not_send_the_case_to_the_owner(world) -> None:
    """@description: Up to the tolerance, the clean comparison still approves and pays.
    @param world: Music-category world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    crowd(world, STALE_TOLERANCE_SUBMISSIONS)
    outcome = world.share(BOB, VIDEO_B, "discord:2")
    world.metadata.during_comparison = None
    assert outcome.review_status is ReviewStatus.APPROVED and world.balances()[BOB] == 1
    world.assert_books_balance(2)


def test_one_more_than_the_tolerance_is_stale_and_waits_for_the_owner(world) -> None:
    """@description: The boundary is exact: tolerance plus one is a crowd.
    @param world: Music-category world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    crowd(world, STALE_TOLERANCE_SUBMISSIONS + 1)
    outcome = world.share(BOB, VIDEO_B, "discord:2")
    world.metadata.during_comparison = None
    assert outcome.review_status is ReviewStatus.PENDING_REVIEW
    assert world.owner.view(OWNER_ID, "discord:2").identity.status is VersionIdentityStatus.UNRESOLVED
