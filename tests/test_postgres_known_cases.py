"""Real PostgreSQL behaviour for cases the system already knows: the Owner is asked only for what is genuinely missing.

Same video id reuses the stored case and version; a different video id with a trusted mapping reuses it without a second Owner decision or a second award.
"""

import pytest

from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID, VIDEO_A, VIDEO_B, World

pytestmark = pytest.mark.postgres
CAROL = 500003
DAVE = 500004


def queue(world: World) -> list[str]:
    """@description: List the operations the Owner would be asked about, in acceptance order.
    @param world: Automatic-mode database.
    @return: Operation ids of the pending rows.
    """
    return [item.operation_id for item in world.owner.list_pending(OWNER_ID, after_sequence=None, limit=20).items]


def test_one_video_submitted_by_several_users_is_one_owner_question(pg_database):
    """@description: The decision is about the video, so a second submitter of the same video never creates a second question.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    world.share(BOB, VIDEO_A, "discord:2")
    world.share(CAROL, VIDEO_A, "discord:3")
    assert queue(world) == ["discord:1"]


def test_a_known_version_mapping_needs_no_owner_and_pays_nothing_again(pg_database):
    """@description: Once both videos carry the same confirmed version, new submissions of either video leave the Owner queue empty and pay nothing.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "recording-x")
    world.share(BOB, VIDEO_B, "discord:2")
    world.confirm_version("discord:2", "recording-x")
    before = world.balances()
    world.share(CAROL, VIDEO_A, "discord:3")
    world.share(DAVE, VIDEO_B, "discord:4")
    assert queue(world) == []
    assert {user: balance for user, balance in world.balances().items() if user in (ALICE, BOB)} == {ALICE: 1, BOB: 0}
    assert all(world.balances().get(user, 0) == 0 for user in (CAROL, DAVE)) and before[ALICE] == 1
    world.assert_books_balance(1)


def test_a_new_video_without_a_mapping_is_asked_once_and_never_made_a_new_version_by_itself(pg_database):
    """@description: A different video id alone creates no version and no mapping: it stays unresolved and in the queue until the Owner decides.
    @param pg_database: Disposable database.
    @return: None.
    """
    world = World(pg_database)
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "recording-x")
    world.share(BOB, VIDEO_B, "discord:2")
    item = world.owner.list_pending(OWNER_ID, after_sequence=None, limit=20).items[0]
    assert item.operation_id == "discord:2" and item.identity_status.value == "unresolved"
    assert world.balances().get(BOB, 0) == 0
    world.assert_books_balance(1)
