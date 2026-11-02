"""Real PostgreSQL measurement of what a build that does not know SYSTEM_JUDGEMENT can still do once system evidence exists.

An older build refuses the unknown stored enum value (ValueError). Removing that one enum member from the lookup reproduces exactly that
refusal in the same process, so every path can be exercised against the same database. The expected outcome, from reading the code, is:
Owner tools for a video that has system evidence stop working; everything else, including settlement and balances, keeps working.
"""

from dataclasses import replace

import pytest

from bot.evidence_record import EvidenceSourceType
from bot.reward_candidates import RewardCandidateStatus
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID, VIDEO_A, VIDEO_B, VIDEO_C, url
from tests.test_postgres_auto_version_confirm import ConfirmWorld, approve_as_owner

pytestmark = pytest.mark.postgres
CAROL = 500003
UNTRUSTED = "UC" + "z" * 22


@pytest.fixture
def world(pg_database) -> ConfirmWorld:
    """@description: Provide a world with one system-confirmed video (Alice) and one Owner-confirmed video (Bob).
    @param pg_database: Disposable database.
    @return: World, with settlement switched off so the rollback sweep has something to settle.
    """
    world = ConfirmWorld(pg_database)
    admin_query(pg_database, "UPDATE jb2_control SET settlement_enabled = FALSE")
    world.share(ALICE, VIDEO_A, "discord:1")
    world.metadata.scripted[VIDEO_B] = replace(world.metadata.scripted[VIDEO_B], channel_id=UNTRUSTED)
    world.share(BOB, VIDEO_B, "discord:2")
    approve_as_owner(world, "discord:2")
    world.confirm_version("discord:2", "rec-B")
    return world


def system_rows(world: ConfirmWorld) -> int:
    """@description: Count the system evidence rows, which is the rollback check an operator runs.
    @param world: World.
    @return: Row count.
    """
    return admin_query(world.database, "SELECT count(*) AS n FROM jb2_evidence WHERE strpos(evidence_json::text, 'system_judgement') > 0")[0]["n"]


@pytest.fixture
def old_build(world, monkeypatch) -> ConfirmWorld:
    """@description: From here on the process refuses the stored value 'system_judgement', as a build without that member would.
    @param world: World with system evidence already stored.
    @param monkeypatch: Member removal.
    @return: The same world.
    """
    assert system_rows(world) >= 1
    monkeypatch.delitem(EvidenceSourceType._value2member_map_, "system_judgement")
    return world


def test_the_operator_check_finds_the_system_evidence_rows(world) -> None:
    """@description: The documented read-only query returns a positive count while rollback is unsafe for those videos.
    @param world: World.
    @return: None.
    """
    assert system_rows(world) == 1


def test_owner_tools_for_the_system_confirmed_video_fail_with_value_error(old_build) -> None:
    """@description: Viewing and version-confirming the system-confirmed video are the paths an old build cannot run.
    @param old_build: World seen by an old build.
    @return: None.
    """
    with pytest.raises(ValueError):
        old_build.owner.view(OWNER_ID, "discord:1")


def test_owner_tools_for_the_owner_confirmed_video_keep_working(old_build) -> None:
    """@description: Evidence written by the Owner is unaffected.
    @param old_build: World seen by an old build.
    @return: None.
    """
    assert old_build.owner.view(OWNER_ID, "discord:2").review.video.video_id == VIDEO_B


def test_new_shares_the_pending_queue_and_the_sweep_keep_working(old_build) -> None:
    """@description: Intake, the validation queue and the ready queue never read evidence rows.
    @param old_build: World seen by an old build.
    @return: None.
    """
    old_build.repository.accept_pending(CAROL, url(VIDEO_C), "discord:3")
    assert ("discord:3", VIDEO_C) in list(old_build.repository.pending_validation_operations(50))
    assert isinstance(list(old_build.repository.ready_operations()), list)


def test_settlement_and_balances_are_unaffected(old_build) -> None:
    """@description: Switching settlement on after the rollback still pays each confirmed video once and keeps the books consistent.
    @param old_build: World seen by an old build.
    @return: None.
    """
    admin_query(old_build.database, "UPDATE jb2_control SET settlement_enabled = TRUE")
    old_build.pipeline.settle_ready()
    assert old_build.balances() == {ALICE: 1, BOB: 1}
    old_build.assert_books_balance(2)
    assert old_build.repository.reward_state("discord:1")[1].status is RewardCandidateStatus.AWARDED
