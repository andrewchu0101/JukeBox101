"""Prove reopened reviews keep their reexamination block without regaining released reward priority.

Scenarios use synthetic reward candidates and the pure review transitions; no database, Discord or external service is involved.
"""

from dataclasses import replace

import pytest

from bot.review import ReviewSnapshot, ReviewStatus, reopen_for_reexamination
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.reward_candidates import RewardDecisionReason as Reason
from bot.reward_candidates import evaluate_reward_candidates
from bot.version_identity import VersionIdentityStatus
from tests.version_candidate_support import make_candidate, make_context


def reopened(candidate, evidence_reference: str = "ev-reexam-1"):
    """@description: Reopen a candidate's review through the production reexamination entry.
    @param candidate: Terminal candidate whose recorded decision is being challenged.
    @param evidence_reference: Trusted synthetic evidence reference.
    @return: Candidate carrying the reopened review and otherwise identical facts.
    """
    # [reopened review]: Same revision token the Owner examined.
    review = reopen_for_reexamination(candidate.review, candidate.review.version, evidence_reference)
    return replace(candidate, review=review)


@pytest.mark.parametrize("earlier_version, expected_status, expected_reason", [
    (None, Status.WAITING_IDENTITY, Reason.IDENTITY_UNRESOLVED),
    ("original", Status.WAITING_VALIDATION, Reason.REEXAMINATION_PENDING),
])
def test_reopened_rejected_predecessor_does_not_block_later_same_version(
        earlier_version: str | None, expected_status: Status, expected_reason: Reason) -> None:
    """@description: Keep a released predecessor released after the Owner reopens its rejection.
    @param earlier_version: Known or unresolved identity of the reopened predecessor.
    @param expected_status: The reopened predecessor's own gate outcome, which stays unchanged.
    @param expected_reason: Stable reason for the reopened predecessor's own gate outcome.
    @return: None.
    """
    # [released predecessor]: Rejected first, then reopened for reexamination.
    earlier = reopened(make_candidate(1, earlier_version, review_status=ReviewStatus.REJECTED))
    decisions = evaluate_reward_candidates(make_context(earlier, make_candidate(2)))
    assert [item.status for item in decisions] == [expected_status, Status.READY]
    assert decisions[0].reason is expected_reason


def test_legacy_rejected_predecessor_without_stored_flag_still_releases_later_candidate() -> None:
    """@description: Release a predecessor rejected before the priority flag existed, once its review is reopened.
    @param: none
    @return: None.
    """
    rejected = make_candidate(1, review_status=ReviewStatus.REJECTED)
    # [legacy payload]: The stored flag was absent, so it decodes to False and must not decide priority alone.
    legacy = replace(rejected, review=replace(rejected.review, priority_released=False))
    decisions = evaluate_reward_candidates(make_context(reopened(legacy), make_candidate(2)))
    assert [item.status for item in decisions] == [Status.WAITING_VALIDATION, Status.READY]


def test_reopened_rejected_predecessor_keeps_original_sequence_as_history() -> None:
    """@description: Preserve the accepted sequence after reopening while denying it priority.
    @param: none
    @return: None.
    """
    earlier = reopened(make_candidate(1, review_status=ReviewStatus.REJECTED))
    assert earlier.accepted_sequence == 1
    assert earlier.review.priority_released is True
    assert earlier.review.reexamination_pending is True


def test_reopened_approved_predecessor_yields_its_priority_to_a_later_same_version() -> None:
    """@description: Owner ruling 2026-10-23: an approved, unawarded predecessor that is reopened no longer blocks later same-version submissions.
    @param: none
    @return: None.
    """
    earlier = reopened(make_candidate(1, review_status=ReviewStatus.APPROVED))
    decisions = evaluate_reward_candidates(make_context(earlier, make_candidate(2)))
    assert decisions[1].reason is not Reason.EARLIER_SUBMISSION
    assert decisions[1].blocking_operation_ids == ()


def test_reopened_predecessor_identity_conflict_still_freezes_later_candidate() -> None:
    """@description: Keep identity conflict separate from priority release after reopening.
    @param: none
    @return: None.
    """
    earlier = reopened(make_candidate(1, review_status=ReviewStatus.REJECTED,
                                      identity_status=VersionIdentityStatus.CONFLICT))
    decisions = evaluate_reward_candidates(make_context(earlier, make_candidate(2)))
    assert decisions[1].status is Status.CONFLICT
    assert decisions[1].reason is Reason.EVIDENCE_CONFLICT


def test_duration_rejected_predecessor_reopened_releases_later_candidate() -> None:
    """@description: Let a duration-rejected predecessor, once reopened, still avoid blocking later candidates.
    @param: none
    @return: None.
    """
    from bot.eligibility import DurationEvidence
    from bot.review import record_validation

    earlier = make_candidate(1)
    # [duration rejection]: Reliable full-video evidence outside the inclusive 60..720 range.
    rejected_for_duration = record_validation(
        ReviewSnapshot(earlier.identity.video),
        DurationEvidence(earlier.identity.video, 999, "synthetic-long", reliable=True, full_video=True), 0)
    earlier = replace(earlier, review=rejected_for_duration)
    decisions = evaluate_reward_candidates(make_context(reopened(earlier), make_candidate(2)))
    assert decisions[0].status is Status.WAITING_VALIDATION
    assert decisions[1].status is Status.READY
