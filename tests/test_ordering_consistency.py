"""Verify the ordering event stays consistent inside its transaction, legacy reexamined data yields only when unawarded, and order is observable offline.

Synthetic submissions over the offline SQL-protocol fake only; no Discord, MySQL, Gateway or payout is involved.
"""

import json
from dataclasses import replace

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_codec import decode_contract, encode_contract
from bot.eligibility import DurationEvidence
from bot.review import ApprovalSource, ReviewSnapshot
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.reward_candidates import RewardDecisionReason as Reason
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
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
from tests.test_owner_ruling_conflict_ordering import decisions, reexamine

ORIGINAL = "orig-X"


def reexamined_approved_pair(service, repository) -> None:
    """@description: Create two approved submissions of the same version, where the second is blocked by the first.
    @param service: Owner review service over the fake.
    @param repository: Repository over the fake.
    @return: None.
    """
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a1", ORIGINAL))
    approve(service, 1)
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-a2", ORIGINAL))
    approve(service, 2)


def rewrite_review_json(database, operation_id: str, mutate) -> None:
    """@description: Rewrite one stored review snapshot in place, as an offline stand-in for data written before the ruling.
    @param database: Offline fake database.
    @param operation_id: Operation whose video snapshot is rewritten.
    @param mutate: Function from the decoded snapshot to the replacement snapshot.
    @return: None.
    """
    event = database.state["jb2_events"][operation_id]
    row = database.state["jb2_videos"][event["video_id"]]
    row["review_json"] = encode_contract(mutate(decode_contract(row["review_json"], ReviewSnapshot)))


# ---- D1: the persisted projection after a reexamination pass uses the new sequence and agrees with a fresh recomputation ----

def test_persisted_decisions_after_a_reexamination_pass_match_a_fresh_recomputation(world):
    """@description: The reward status stored in the same transaction as the pass equals a recomputation over the committed state, so the new sequence never reads as an incomplete snapshot.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    reexamined_approved_pair(service, repository)
    reexamine(service, 1, "ev-r1", ORIGINAL)
    approve(service, 1)
    stored = {row["operation_id"]: row["reward_status"] for row in database.state["jb2_events"].values()}
    fresh = {decision.operation_id: decision.status.value for decision in decisions(database).values()}
    assert stored == fresh
    assert "incomplete_snapshot" not in stored.values()


def test_the_passed_item_reads_back_its_new_position_and_the_snapshot_is_complete(world):
    """@description: Immediately after the pass, the public snapshot shows the new position for that submission and reports it complete.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    reexamined_approved_pair(service, repository)
    reexamine(service, 1, "ev-r1", ORIGINAL)
    approve(service, 1)
    review = service.view(OWNER_ID, "op-1").review
    snapshot = repository.snapshot()
    candidate = next(item for item in snapshot.candidates if item.operation_id == "op-1")
    assert candidate.accepted_sequence == review.ordering_sequence
    assert snapshot.manifest.through_sequence == dict(database.state["control"])["accepted_sequence"]
    assert decisions(database)["op-1"].reason is not Reason.INCOMPLETE_SNAPSHOT


# ---- legacy data: reexamined, approved, unawarded, priority_released=False ----

def test_legacy_reexamined_unawarded_submission_yields_priority_to_a_later_same_version(world):
    """@description: Data written before the ruling (reexamined, not awarded, released flag False) no longer blocks a later same-version submission.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    reexamined_approved_pair(service, repository)
    reexamine(service, 1, "ev-r1", ORIGINAL)
    rewrite_review_json(database, "op-1", lambda review: replace(review, priority_released=False))
    assert decisions(database)["op-2"].reason is not Reason.EARLIER_SUBMISSION


def test_legacy_reexamined_awarded_submission_keeps_its_priority_semantics(world):
    """@description: An already awarded legacy submission is not treated as yielded, so the derivation never changes a paid item.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a1", ORIGINAL))
    approve(service, 1)
    assert repository.settle("op-1").status is Status.AWARDED
    reexamine(service, 1, "ev-r1", ORIGINAL)
    rewrite_review_json(database, "op-1", lambda review: replace(review, priority_released=False))
    assert decisions(database)["op-1"].status is not Status.READY
    assert len(database.state["credit_ledger"]) == 1


# ---- ordering evidence ----

def test_an_ordering_event_sits_after_every_intake_and_every_earlier_ordering_event(world):
    """@description: Successive reexamination passes receive strictly increasing sequences, all after the existing intake order.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    reexamined_approved_pair(service, repository)
    last_intake = max(row["accepted_sequence"] for row in database.state["jb2_events"].values())
    reexamine(service, 1, "ev-r1", ORIGINAL)
    approve(service, 1)
    first_event = service.view(OWNER_ID, "op-1").review.ordering_sequence
    reexamine(service, 2, "ev-r2", ORIGINAL)
    approve(service, 2)
    second_event = service.view(OWNER_ID, "op-2").review.ordering_sequence
    assert first_event > last_intake
    assert second_event > first_event


def test_a_replayed_pass_does_not_allocate_another_ordering_event(world):
    """@description: Repeating the same reexamination approval key returns the original result and leaves the counter unchanged.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    reexamined_approved_pair(service, repository)
    reexamine(service, 1, "ev-r1", ORIGINAL)
    judgement = ContentJudgement(video_of(1), OWNER_ID, "合成假案件核准測試", START, "content-replay-2", True)
    revision = service.view(OWNER_ID, "op-1").review.version
    snapshot = service.view(OWNER_ID, "op-1").snapshot
    command = ReviewCommand("decision-pass-replay", "op-1", OWNER_ID, "合成假案件核准測試", revision,
                            approval_source=ApprovalSource.OWNER, song_eligible=True, evidence_reference="cite-pass",
                            content_judgement=judgement, expected_snapshot=snapshot)
    first = repository.review(command)
    counter = dict(database.state["control"])["accepted_sequence"]
    second = repository.review(command)
    assert second == first
    assert dict(database.state["control"])["accepted_sequence"] == counter


def test_unrelated_submissions_keep_their_intake_order_and_the_same_version_is_awarded_once(world):
    """@description: A reexamination pass on one submission leaves an unrelated, other-version submission at its intake position, and the recording is paid at most once.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(3))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a1", ORIGINAL))
    approve(service, 1)
    confirm_version(service, 3, owner_evidence(video_of(3), "ev-a3", "orig-other"))
    approve(service, 3)
    reexamine(service, 1, "ev-r1", ORIGINAL)
    approve(service, 1)
    by_id = {item.operation_id: item for item in repository.snapshot().candidates}
    assert by_id["op-3"].accepted_sequence == dict(database.state["jb2_events"]["op-3"])["accepted_sequence"]
    assert by_id["op-1"].accepted_sequence > by_id["op-3"].accepted_sequence
    assert repository.settle("op-1").status is Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1
    assert repository.settle("op-1").status is Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1


def test_the_derived_yield_rule_covers_each_stored_state_without_changing_awarded_items():
    """@description: The derived priority yield applies only to unawarded reexamination-pending reviews; every other state keeps its stored flag.
    @param: none
    @return: None.
    """
    from bot.review import ReviewStatus, effective_priority_released
    video = VideoIdentity("Synth0001X1")
    duration = DurationEvidence(video, 180, "synthetic-duration", True, True)
    pending_legacy = ReviewSnapshot(video, 3, ReviewStatus.PENDING_REVIEW, duration=duration,
                                    reexamination_pending=True, priority_released=False)
    pending_rejected = replace(pending_legacy, priority_released=True)
    not_reexamined = ReviewSnapshot(video, 1, ReviewStatus.PENDING_REVIEW, duration=duration)
    assert effective_priority_released(pending_legacy, awarded=False) is True
    assert effective_priority_released(pending_legacy, awarded=True) is False
    assert effective_priority_released(pending_rejected, awarded=True) is True
    assert effective_priority_released(not_reexamined, awarded=False) is False
