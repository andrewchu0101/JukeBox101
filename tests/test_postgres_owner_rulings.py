"""Characterization on real PostgreSQL of two existing Owner rulings that the automatic version confirmation relies on.

(b) A version conflict is frozen: confirming a different version for an already confirmed video makes the video CONFLICT, and no Owner
action can clear it (the set of review actions is pinned so a new "unfreeze" action must be a deliberate, reviewed change).
(d) A rejected case can be reexamined, approved and version-confirmed; it then pays exactly one Nickel, replays pay nothing, and it ranks
behind every submission that was already waiting (a reexamined approval joins the tail of the ordering).
These tests describe behaviour that already exists; they must pass without any change to bot/.
"""

import pytest

from bot.evidence_record import EvidenceSourceType, record_evidence
from bot.owner_review import ReviewAction
from bot.review import ReviewStatus
from bot.reward_candidates import RewardCandidateStatus
from bot.song_judgement import ContentJudgement
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB, NOW, OWNER_ID, VIDEO_A, VIDEO_B, World, found

pytestmark = pytest.mark.postgres

UNTRUSTED = "UC" + "z" * 22  # [Owner-review channel]: Not on the allowlist, so the case waits for the Owner instead of being auto-approved.
SOURCE = "https://example.test/manual-check"


@pytest.fixture
def world(pg_database) -> World:
    """@description: Provide the automatic-mode world used by the existing PostgreSQL pipeline tests.
    @param pg_database: Disposable database.
    @return: World.
    """
    return World(pg_database)


def set_settlement(world: World, enabled: bool) -> None:
    """@description: Flip the database settlement switch on the disposable database only.
    @param world: World.
    @param enabled: New value.
    @return: None.
    """
    admin_query(world.database, f"UPDATE jb2_control SET settlement_enabled = {'TRUE' if enabled else 'FALSE'}")


def version_evidence(world: World, operation_id: str, evidence_id: str, version: str):
    """@description: Build Owner evidence naming a version for the operation's video.
    @param world: World.
    @param operation_id: Operation.
    @param evidence_id: Unique evidence identifier.
    @param version: Version reference.
    @return: Evidence record.
    """
    video = world.owner.view(OWNER_ID, operation_id).review.video
    return record_evidence(evidence_id, video, EvidenceSourceType.OWNER_REVIEW, SOURCE, NOW, "人工核對", OWNER_ID, RecordingVersionIdentity(version))


def revision_of(world: World, operation_id: str) -> int:
    """@description: Read the revision the Owner would act on.
    @param world: World.
    @param operation_id: Operation.
    @return: Review revision.
    """
    return world.owner.view(OWNER_ID, operation_id).review.version


def decide(world: World, operation_id: str, action: ReviewAction) -> ReviewStatus:
    """@description: Preview and confirm an approval or rejection.
    @param world: World.
    @param operation_id: Operation.
    @param action: Approve or reject.
    @return: Review status after the decision.
    """
    review = world.owner.view(OWNER_ID, operation_id).review
    content = ContentJudgement(review.video, OWNER_ID, "人工確認是歌曲", NOW, f"content-{operation_id}", True) if action is ReviewAction.APPROVE else None
    preview = world.owner.preview(OWNER_ID, operation_id, action, "人工判斷", review.version, f"cite-{operation_id}", content=content)
    return world.owner.confirm(OWNER_ID, preview.token_id).status


def reexamine(world: World, operation_id: str, evidence_id: str, version: str) -> None:
    """@description: Preview and confirm a formal reexamination with new Owner evidence.
    @param world: World.
    @param operation_id: Operation.
    @param evidence_id: Unique evidence identifier.
    @param version: Version the evidence names.
    @return: None.
    """
    evidence = version_evidence(world, operation_id, evidence_id, version)
    preview = world.owner.preview_reexamination(OWNER_ID, operation_id, evidence, "發現新證據，正式重審", revision_of(world, operation_id))
    world.owner.confirm(OWNER_ID, preview.token_id)


def identity_status(world: World, operation_id: str) -> VersionIdentityStatus:
    """@description: Read the stored version status of the operation's video.
    @param world: World.
    @param operation_id: Operation.
    @return: Version status.
    """
    return world.owner.view(OWNER_ID, operation_id).identity.status


def rejected_case(world: World, user: int, video_id: str, operation_id: str) -> None:
    """@description: Create a case that waits for the Owner (untrusted channel) and reject it.
    @param world: World.
    @param user: Submitter.
    @param video_id: Video.
    @param operation_id: Operation.
    @return: None.
    """
    world.metadata.script[video_id] = found(video_id, channel=UNTRUSTED)
    world.share(user, video_id, operation_id)
    assert decide(world, operation_id, ReviewAction.REJECT) is ReviewStatus.REJECTED


def test_confirming_a_different_version_for_a_confirmed_video_freezes_it_in_conflict_and_pays_nothing(world) -> None:
    """@description: Two contradicting Owner confirmations make the video CONFLICT; no award follows even after settlement is switched on.
    @param world: World.
    @return: None.
    """
    set_settlement(world, False)
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "rec-A")
    assert identity_status(world, "discord:1") is VersionIdentityStatus.CONFIRMED
    world.confirm_version("discord:1", "rec-B")
    assert identity_status(world, "discord:1") is VersionIdentityStatus.CONFLICT
    set_settlement(world, True)
    assert world.pipeline.process("discord:1").decision.status is RewardCandidateStatus.CONFLICT
    assert world.balances().get(ALICE, 0) == 0
    world.assert_books_balance(0)


def test_no_owner_confirmation_or_reexamination_clears_a_conflict(world) -> None:
    """@description: Re-confirming either version, or reexamining with either version, leaves the video in CONFLICT with nothing paid.
    @param world: World.
    @return: None.
    """
    set_settlement(world, False)
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "rec-A")
    world.confirm_version("discord:1", "rec-B")
    for version, label in (("rec-A", "x1"), ("rec-B", "x2"), ("rec-C", "x3")):
        try:
            world.owner.confirm(OWNER_ID, world.owner.preview_version_confirmation(
                OWNER_ID, "discord:1", version_evidence(world, "discord:1", f"ev-{label}", version), "嘗試解除", revision_of(world, "discord:1")).token_id)
        except (ValueError, PermissionError):
            pass  # [refused is also frozen]: A refusal leaves the conflict in place, which the assertion below checks.
        assert identity_status(world, "discord:1") is VersionIdentityStatus.CONFLICT
    set_settlement(world, True)
    world.pipeline.settle_ready()
    assert world.balances().get(ALICE, 0) == 0
    world.assert_books_balance(0)


def test_the_set_of_owner_actions_has_no_way_to_clear_a_conflict() -> None:
    """@description: Pin the action list so adding an unfreeze action forces a deliberate review of this ruling.
    @param: none
    @return: None.
    """
    assert {action.value for action in ReviewAction} == {"approve", "reject", "reexamine", "confirm_version", "duration"}


def test_a_rejected_case_that_is_reexamined_approved_and_version_confirmed_pays_exactly_once(world) -> None:
    """@description: The whole reopen path ends in one award; every replay and re-run of settlement changes nothing.
    @param world: World.
    @return: None.
    """
    rejected_case(world, ALICE, VIDEO_A, "discord:1")
    assert world.balances().get(ALICE, 0) == 0
    reexamine(world, "discord:1", "ev-reexam", "rec-A")
    assert decide(world, "discord:1", ReviewAction.APPROVE) is ReviewStatus.APPROVED
    if identity_status(world, "discord:1") is not VersionIdentityStatus.CONFIRMED:
        world.confirm_version("discord:1", "rec-A")
    world.pipeline.settle_ready()
    assert world.balances()[ALICE] == 1
    world.assert_books_balance(1)
    world.pipeline.settle_ready()
    world.pipeline.process("discord:1")
    world.new_pipeline().process("discord:1")
    assert world.balances()[ALICE] == 1
    world.assert_books_balance(1)


def test_a_reexamined_approval_ranks_behind_a_submission_that_was_already_waiting(world) -> None:
    """@description: Bob accepted after Alice's rejected case but waiting with the same version before her reopen finishes; he is paid, she is not.
    @param world: World.
    @return: None.
    """
    set_settlement(world, False)
    rejected_case(world, ALICE, VIDEO_A, "discord:1")
    world.share(BOB, VIDEO_B, "discord:2")
    world.confirm_version("discord:2", "rec-X")
    reexamine(world, "discord:1", "ev-reexam", "rec-X")
    assert decide(world, "discord:1", ReviewAction.APPROVE) is ReviewStatus.APPROVED
    if identity_status(world, "discord:1") is not VersionIdentityStatus.CONFIRMED:
        world.confirm_version("discord:1", "rec-X")
    set_settlement(world, True)
    world.pipeline.settle_ready()
    assert world.balances().get(BOB, 0) == 1 and world.balances().get(ALICE, 0) == 0
    world.assert_books_balance(1)
    world.pipeline.settle_ready()
    assert world.balances().get(ALICE, 0) == 0
