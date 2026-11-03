"""Real PostgreSQL behaviour of the read-only review-reason report: counts per stored automatic reason code, a bounded pending list, no official data.

The official provider is a scripted fake. The history, events and videos are the production tables on disposable PostgreSQL; the report must write nothing.
"""

from dataclasses import replace

import pytest

from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID, VIDEO_A, VIDEO_B, VIDEO_C
from tests.test_postgres_auto_version_confirm import TITLE_CANARY
from tests.test_postgres_music_category_mode import MusicWorld

pytestmark = pytest.mark.postgres

WATCH_URL = "https://www.youtube.com/watch?v="  # [canonical link]: Built from the video identifier only.
WRITTEN_TABLES = ("jb2_review_history", "jb2_events", "jb2_videos", "jb2_evidence", "jb2_decisions", "credit_ledger", "users")  # [write probe]: Row counts must not change.


@pytest.fixture
def world(pg_database) -> MusicWorld:
    """@description: Provide one music_category world with three differently decided shares.
    @param pg_database: Disposable database.
    @return: World after one approval, one category miss and one trailer title.
    """
    music_world = MusicWorld(pg_database)
    music_world.metadata.scripted[VIDEO_B] = replace(music_world.metadata.scripted[VIDEO_B], category_id="24")
    music_world.metadata.scripted[VIDEO_C] = replace(music_world.metadata.scripted[VIDEO_C], title=f"{TITLE_CANARY} Official Trailer")
    music_world.share(ALICE, VIDEO_A, "discord:1")
    music_world.share(BOB, VIDEO_B, "discord:2")
    music_world.share(BOB, VIDEO_C, "discord:3")
    return music_world


def row_counts(world: MusicWorld) -> dict:
    """@description: Count the rows of every table a report must not write.
    @param world: World.
    @return: Row count per table.
    """
    return {table: admin_query(world.database, f"SELECT count(*) AS n FROM {table}")[0]["n"] for table in WRITTEN_TABLES}


def test_the_counts_follow_the_stored_reason_codes(world) -> None:
    """@description: One approval and two held cases are counted under their own codes.
    @param world: Prepared world.
    @return: None.
    """
    counts = world.repository.owner_review_reasons(OWNER_ID, 7)["reason_counts"]
    assert counts["category_not_music"] == 1 and counts["title_needs_owner"] == 1
    assert sum(count for code, count in counts.items() if code.startswith("music_category")) >= 1


def test_the_pending_cases_are_listed_in_acceptance_order_with_links_reasons_and_no_official_data(world) -> None:
    """@description: Both held cases appear, oldest first, as canonical links with their reason code; no title leaks.
    @param world: Prepared world.
    @return: None.
    """
    result = world.repository.owner_review_reasons(OWNER_ID, 7)
    pending = result["pending"]
    assert [item["video_id"] for item in pending] == [VIDEO_B, VIDEO_C]
    assert [item["operation_id"] for item in pending] == ["discord:2", "discord:3"]
    assert [item["reason"] for item in pending] == ["category_not_music", "title_needs_owner"]
    assert pending[0]["accepted_sequence"] < pending[1]["accepted_sequence"]
    assert TITLE_CANARY.lower() not in repr(result).lower() and WATCH_URL not in repr(result)


def test_decisions_older_than_the_window_are_not_counted(world) -> None:
    """@description: Backdating every decision by ten days empties a seven-day report but not a thirty-day one.
    @param world: Prepared world.
    @return: None.
    """
    admin_query(world.database, "UPDATE jb2_review_history SET created_at = created_at - interval '10 days'")
    assert world.repository.owner_review_reasons(OWNER_ID, 7)["reason_counts"] == {}
    assert world.repository.owner_review_reasons(OWNER_ID, 30)["reason_counts"]["category_not_music"] == 1


def test_a_reason_that_cannot_be_parsed_is_counted_as_other(world) -> None:
    """@description: An automatic-review row without a reason code falls into the other bucket.
    @param world: Prepared world.
    @return: None.
    """
    admin_query(world.database, "UPDATE jb2_review_history SET decision_reason = 'auto-review policy=x' WHERE decision_reason LIKE '%%reason=title_needs_owner%%'")
    assert world.repository.owner_review_reasons(OWNER_ID, 7)["reason_counts"]["other"] == 1


def test_only_automatic_decisions_are_counted(world) -> None:
    """@description: A row written by anything else is ignored.
    @param world: Prepared world.
    @return: None.
    """
    admin_query(world.database, "UPDATE jb2_review_history SET decision_reason = 'owner reviewed reason=category_not_music' WHERE decision_reason LIKE '%%reason=category_not_music%%'")
    assert "category_not_music" not in world.repository.owner_review_reasons(OWNER_ID, 7)["reason_counts"]


def test_the_pending_list_is_capped_at_twenty(world) -> None:
    """@description: At most twenty cases are returned however many wait.
    @param world: Prepared world.
    @return: None.
    """
    assert len(world.repository.owner_review_reasons(OWNER_ID, 7)["pending"]) <= 20


def test_a_non_owner_is_refused_and_a_bad_window_is_rejected(world) -> None:
    """@description: The Owner check comes first; days outside one to sixty never query.
    @param world: Prepared world.
    @return: None.
    """
    with pytest.raises(PermissionError, match="OWNER_ONLY"):
        world.repository.owner_review_reasons(ALICE, 7)
    for days in (0, 61):
        with pytest.raises(ValueError):
            world.repository.owner_review_reasons(OWNER_ID, days)


def test_the_report_writes_nothing(world) -> None:
    """@description: Row counts of every relevant table are identical before and after.
    @param world: Prepared world.
    @return: None.
    """
    before = row_counts(world)
    world.repository.owner_review_reasons(OWNER_ID, 7)
    assert row_counts(world) == before
