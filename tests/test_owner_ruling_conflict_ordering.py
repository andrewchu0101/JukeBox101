"""Verify the Owner ruling of 2026-10-23: CONFLICT forbids approval, and a reexamination releases the old priority and approves into a new ordering event.

Synthetic submissions over the offline SQL-protocol fake only; no Discord, MySQL, Gateway or payout is involved.
"""

from copy import deepcopy
import pytest

from bot.database.reward_settlement import current_decisions, settle_reward
from bot.eligibility import DurationEvidence
from bot.database.submission_codec import decode_contract, encode_contract
from bot.database.submission_transaction import submission_transaction
from bot.owner_review import ReviewAction
from bot.review import ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus
from bot.reward_candidates import RewardCandidateStatus, RewardDecisionReason
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.database.review_persistence import ReviewCommand
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.version_identity import VersionIdentityStatus
from tests.intake_fixture import seed_intake
from tests.test_identity_service_path import (  # noqa: F401  (world is a pytest fixture imported by name)
    OWNER_ID,
    START,
    approve,
    confirm_version,
    owner_evidence,
    submission,
    video_of,
    world,
)


def preview_approve(service, number: int) -> str:
    """@description: Preview an approval for one synthetic submission and return its confirmation handle.
    @param service: Owner review service over the fake.
    @param number: Synthetic submission number.
    @return: Token identifier.
    """
    judgement = ContentJudgement(video_of(number), OWNER_ID, "synthetic song judgement", START, f"content-{number}", True)
    revision = service.view(OWNER_ID, f"op-{number}").review.version
    return service.preview(OWNER_ID, f"op-{number}", ReviewAction.APPROVE, "合成假案件核准測試", revision,
                           f"cite-{number}", content=judgement).token_id


def reexamine(service, number: int, evidence_id: str, version: str) -> None:
    """@description: Preview and confirm a formal reexamination of one approved synthetic submission with new reliable evidence.
    @param service: Owner review service over the fake.
    @param number: Synthetic submission number.
    @param evidence_id: Stable identifier of the new evidence.
    @param version: Version name the new evidence asserts.
    @return: None.
    """
    revision = service.view(OWNER_ID, f"op-{number}").review.version
    evidence = owner_evidence(video_of(number), evidence_id, version, hours=1)
    preview = service.preview_reexamination(OWNER_ID, f"op-{number}", evidence, "合成假案件：正式重審測試", revision)
    service.confirm(OWNER_ID, preview.token_id)


def decisions(database) -> dict:
    """@description: Read the current reward decisions from the locked fake state, keyed by operation.
    @param database: Offline fake database.
    @return: Mapping from operation identifier to its decision.
    """
    with submission_transaction(database.connect) as (cursor, control):
        return {decision.operation_id: decision for decision in current_decisions(cursor, control)}


def conflict_in_place(service, number: int) -> None:
    """@description: Form a version CONFLICT on one synthetic submission by confirming two contradicting versions.
    @param service: Owner review service over the fake.
    @param number: Synthetic submission number.
    @return: None.
    """
    confirm_version(service, number, owner_evidence(video_of(number), f"ev-a{number}", "orig-A"))
    confirm_version(service, number, owner_evidence(video_of(number), f"ev-b{number}", "orig-B", hours=1))


# ---- CONFLICT: approval is refused before any write; rejection is allowed and keeps the conflict ----

def test_approval_preview_is_refused_when_the_version_is_in_conflict_before_any_token_or_write(world):
    """@description: An approval against a version in CONFLICT is refused at preview, so no token and no write exist.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    conflict_in_place(service, 1)
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFLICT
    before = deepcopy(database.state)
    with pytest.raises(InvalidReviewTransition):
        preview_approve(service, 1)
    assert database.state == before


def test_repository_refuses_an_owner_approval_while_the_version_is_in_conflict_before_any_write(world):
    """@description: The persistence boundary also refuses an approval during CONFLICT, even without the service preview.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    conflict_in_place(service, 1)
    judgement = ContentJudgement(video_of(1), OWNER_ID, "synthetic song judgement", START, "content-1", True)
    command = ReviewCommand("decision-conflict-approve", "op-1", OWNER_ID, "合成假案件核准測試", 1,
                            approval_source=ApprovalSource.OWNER, song_eligible=True, evidence_reference="cite-1",
                            content_judgement=judgement)
    before = deepcopy(database.state)
    with pytest.raises(InvalidReviewTransition):
        repository.review(command)
    assert database.state == before


def test_rejection_is_allowed_during_conflict_keeps_the_conflict_and_pays_nothing(world):
    """@description: A rejection during CONFLICT succeeds, leaves the identity in conflict and writes no payout.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    conflict_in_place(service, 1)
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "合成假案件：衝突中拒絕測試", revision, "cite-reject").token_id
    service.confirm(OWNER_ID, token)
    view = service.view(OWNER_ID, "op-1")
    assert view.review.status is ReviewStatus.REJECTED
    assert view.identity.status is VersionIdentityStatus.CONFLICT
    assert database.state["credit_ledger"] == {}


# ---- reexamination releases the old priority of an approved, unawarded submission ----

def test_reexamining_an_approved_unawarded_submission_releases_its_old_priority(world):
    """@description: Entering reexamination from approved-but-unawarded immediately releases the original priority.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a1", "orig-X"))
    approve(service, 1)
    reexamine(service, 1, "ev-r1", "orig-X")
    review = service.view(OWNER_ID, "op-1").review
    assert review.status is ReviewStatus.PENDING_REVIEW
    assert review.reexamination_pending is True
    assert review.priority_released is True


def test_a_later_same_version_submission_is_no_longer_blocked_by_a_reexamined_predecessor(world):
    """@description: After the predecessor is reexamined, the later same-version approved submission is not held back by its old position.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a1", "orig-X"))
    approve(service, 1)
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-a2", "orig-X"))
    approve(service, 2)
    assert decisions(database)["op-2"].reason is RewardDecisionReason.EARLIER_SUBMISSION
    reexamine(service, 1, "ev-r1", "orig-X")
    assert decisions(database)["op-2"].reason is not RewardDecisionReason.EARLIER_SUBMISSION
    assert decisions(database)["op-2"].status is RewardCandidateStatus.READY


# ---- reexamination approval is one operation that creates a new ordering event at the tail ----

def test_approving_a_reexamined_submission_creates_a_new_tail_ordering_event_in_the_same_operation(world):
    """@description: Approving a reexamined submission assigns a new system ordering sequence after every existing submission and clears the block.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a1", "orig-X"))
    approve(service, 1)
    reexamine(service, 1, "ev-r1", "orig-X")
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-a2", "orig-X"))
    approve(service, 2)
    approve(service, 1)
    review = service.view(OWNER_ID, "op-1").review
    assert review.status is ReviewStatus.APPROVED
    assert review.reexamination_pending is False
    assert review.ordering_sequence is not None
    assert review.ordering_sequence > service.view(OWNER_ID, "op-2").accepted_sequence
    decision = decisions(database)["op-1"]
    assert decision.reason is RewardDecisionReason.EARLIER_SUBMISSION
    assert decision.blocking_operation_ids == ("op-2",)


def test_a_normal_approval_does_not_create_an_ordering_event(world):
    """@description: Ordinary approval keeps the accepted order and creates no ordering event (guard).
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    approve(service, 1)
    review = service.view(OWNER_ID, "op-1").review
    assert review.ordering_sequence is None
    assert review.status is ReviewStatus.APPROVED


def test_replaying_the_reexamination_approval_returns_the_original_result_without_a_second_ordering_event(world):
    """@description: A repeated decision key replays the original approval and allocates no second sequence.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a1", "orig-X"))
    approve(service, 1)
    reexamine(service, 1, "ev-r1", "orig-X")
    judgement = ContentJudgement(video_of(1), OWNER_ID, "合成假案件核准測試", START, "content-replay", True)
    revision = service.view(OWNER_ID, "op-1").review.version
    snapshot = service.view(OWNER_ID, "op-1").snapshot
    command = ReviewCommand("decision-replay-approve", "op-1", OWNER_ID, "合成假案件核准測試", revision,
                            approval_source=ApprovalSource.OWNER, song_eligible=True, evidence_reference="cite-replay",
                            content_judgement=judgement, expected_snapshot=snapshot)
    first = repository.review(command)
    counter = dict(database.state["control"])["accepted_sequence"]
    second = repository.review(command)
    assert second == first
    assert database.state["control"]["accepted_sequence"] == counter


def test_settlement_stays_refused_for_a_reexamined_approval_while_settlement_is_disabled(world):
    """@description: Reexamination and ordering do not enable payout: with settlement disabled, settling the new winner is refused and no ledger row appears.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    database.state["control"]["settlement_enabled"] = False
    seed_intake(repository, submission(1))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a1", "orig-X"))
    approve(service, 1)
    reexamine(service, 1, "ev-r1", "orig-X")
    approve(service, 1)
    with pytest.raises(PermissionError):
        with submission_transaction(database.connect) as (cursor, control):
            settle_reward(cursor, control, "op-1")
    assert database.state["credit_ledger"] == {}


# ---- compatibility of stored review contracts ----

def test_a_stored_review_without_the_ordering_field_decodes_with_no_ordering_event():
    """@description: Review contracts stored before the ordering field existed still decode, with no ordering event.
    @param: none
    @return: None.
    """
    import json
    video = VideoIdentity("Synth0001X1")
    snapshot = ReviewSnapshot(video, 2, ReviewStatus.APPROVED, approval_source=ApprovalSource.OWNER,
                              duration=DurationEvidence(video, 180, "synthetic-duration", True, True))
    document = json.loads(encode_contract(snapshot))
    document["value"]["fields"].pop("ordering_sequence", None)
    decoded = decode_contract(json.dumps(document), ReviewSnapshot)
    assert decoded.ordering_sequence is None
    assert decoded.status is ReviewStatus.APPROVED


def test_reexamining_an_already_awarded_submission_keeps_its_priority_semantics_unchanged(world):
    """@description: Owner ruling 2026-10-23 excludes already-paid items: reopening an awarded submission does not release priority.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a1", "orig-X"))
    approve(service, 1)
    assert repository.settle("op-1").status is Status.AWARDED
    reexamine(service, 1, "ev-r1", "orig-X")
    assert service.view(OWNER_ID, "op-1").review.priority_released is False
    assert len(database.state["credit_ledger"]) == 1
