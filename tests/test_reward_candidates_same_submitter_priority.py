"""Exercise the same-submitter scope of unknown-version priority with synthetic intake snapshots.

An earlier case whose version is still unknown (pending, unconfirmed or in conflict) holds back only its own submitter's later cases, never other people's.
"""

from dataclasses import replace

from bot.review import ReviewStatus
from bot.reward_candidates import RewardCandidateStatus as Status, RewardDecisionReason as Reason, evaluate_reward_candidates
from bot.version_identity import RecordingVersionIdentity, VersionExclusion, VersionIdentityStatus
from tests.version_candidate_support import make_candidate, make_context

ALICE_ID, BOB_ID = 11, 22  # [synthetic people]: Two different submitters of the scenarios.


def test_an_unknown_version_pending_case_does_not_block_another_persons_later_case() -> None:
    """@description: The jam fix: one person's undecided case no longer stops everybody else from being paid.
    @param: none
    @return: None.
    """
    earlier = make_candidate(1, None, submitter_id=ALICE_ID, review_status=ReviewStatus.PENDING_REVIEW)  # [unknown earlier case]: Alice's undecided song.
    later = make_candidate(2, "original", submitter_id=BOB_ID)  # [other person]: Bob's approved, confirmed song.
    decisions = evaluate_reward_candidates(make_context(earlier, later))
    assert [item.status for item in decisions] == [Status.WAITING_IDENTITY, Status.READY]
    assert decisions[1].blocking_operation_ids == ()


def test_an_unknown_version_case_still_blocks_the_same_persons_later_case() -> None:
    """@description: A person's own undecided case keeps holding that person's later cases.
    @param: none
    @return: None.
    """
    earlier = make_candidate(1, None, submitter_id=ALICE_ID, review_status=ReviewStatus.PENDING_REVIEW)
    own_later = make_candidate(2, "original", submitter_id=ALICE_ID)  # [same person]: Alice's own later song.
    decision = evaluate_reward_candidates(make_context(earlier, own_later))[1]
    assert decision.status is Status.WAITING_PREDECESSOR and decision.reason is Reason.EARLIER_SUBMISSION
    assert decision.blocking_operation_ids == (earlier.operation_id,)


def test_only_the_own_cases_are_listed_as_blockers_among_several_unknown_ones() -> None:
    """@description: The blocker list names exactly the same person's earlier unknown cases.
    @param: none
    @return: None.
    """
    alice_first = make_candidate(1, None, submitter_id=ALICE_ID, review_status=ReviewStatus.PENDING_REVIEW)
    bob_first = make_candidate(2, None, submitter_id=BOB_ID, review_status=ReviewStatus.PENDING_REVIEW)
    bob_later = make_candidate(3, "original", submitter_id=BOB_ID)
    assert evaluate_reward_candidates(make_context(alice_first, bob_first, bob_later))[2].blocking_operation_ids == (bob_first.operation_id,)


def test_confirmed_same_version_order_is_unchanged_across_people() -> None:
    """@description: Two people on one confirmed version still keep the earlier one first, and the version is paid once.
    @param: none
    @return: None.
    """
    earlier = make_candidate(1, "original", submitter_id=ALICE_ID, review_status=ReviewStatus.PENDING_REVIEW)
    later = make_candidate(2, "original", submitter_id=BOB_ID)
    waiting = evaluate_reward_candidates(make_context(earlier, later))[1]
    assert waiting.status is Status.WAITING_PREDECESSOR and waiting.blocking_operation_ids == (earlier.operation_id,)
    settled = evaluate_reward_candidates(make_context(replace(earlier, review=make_candidate(1).review, awarded=True), later))[1]
    assert settled.status is Status.INELIGIBLE and settled.reason is Reason.VERSION_ALREADY_AWARDED


def test_a_later_person_may_win_first_and_the_earlier_unknown_one_then_gets_no_second_award() -> None:
    """@description: The accepted consequence: the later person is paid first, and the same version is never paid twice.
    @param: none
    @return: None.
    """
    earlier = make_candidate(1, None, submitter_id=ALICE_ID, review_status=ReviewStatus.PENDING_REVIEW)
    paid = make_candidate(2, "original", submitter_id=BOB_ID, awarded=True)
    confirmed_later = make_candidate(1, "original", submitter_id=ALICE_ID)  # [confirmed afterwards]: The earlier case turned out to be the paid version.
    assert evaluate_reward_candidates(make_context(earlier, paid))[1].status is Status.AWARDED
    decisions = evaluate_reward_candidates(make_context(confirmed_later, paid))
    assert decisions[0].status is Status.INELIGIBLE and decisions[0].reason is Reason.VERSION_ALREADY_AWARDED


def test_a_later_payment_does_not_freeze_another_persons_earlier_unknown_case() -> None:
    """@description: An award above a different person's unknown case is no longer a priority violation.
    @param: none
    @return: None.
    """
    earlier = make_candidate(1, None, submitter_id=ALICE_ID)
    decisions = evaluate_reward_candidates(make_context(earlier, make_candidate(2, "original", submitter_id=BOB_ID, awarded=True)))
    assert [item.status for item in decisions] == [Status.WAITING_IDENTITY, Status.AWARDED]


def test_a_later_payment_above_the_same_persons_unknown_case_is_still_a_violation() -> None:
    """@description: The existing priority-violation freeze stays for one person's own cases.
    @param: none
    @return: None.
    """
    earlier = make_candidate(1, None, submitter_id=ALICE_ID, review_status=ReviewStatus.PENDING_VALIDATION)
    decisions = evaluate_reward_candidates(make_context(earlier, make_candidate(2, "original", submitter_id=ALICE_ID, awarded=True)))
    assert [item.status for item in decisions] == [Status.CONFLICT, Status.AWARDED]


def test_an_unknown_version_conflict_freezes_only_its_own_person_and_its_own_video() -> None:
    """@description: A conflicted video of unknown version freezes the same video and the same person's other cases, not other people's.
    @param: none
    @return: None.
    """
    conflict = make_candidate(1, None, submitter_id=ALICE_ID, identity_status=VersionIdentityStatus.CONFLICT)
    other_person = make_candidate(2, "original", submitter_id=BOB_ID)
    same_video_other_person = make_candidate(3, "original", submitter_id=BOB_ID, video_id=conflict.identity.video.video_id)
    own_other_video = make_candidate(4, "original", submitter_id=ALICE_ID)
    decisions = evaluate_reward_candidates(make_context(conflict, other_person, same_video_other_person, own_other_video))
    assert [item.status for item in decisions] == [Status.CONFLICT, Status.READY, Status.CONFLICT, Status.CONFLICT]


def test_a_conflict_never_revokes_an_award_and_spares_other_people() -> None:
    """@description: Existing awards stay awarded, and a different person's unsettled case is not frozen.
    @param: none
    @return: None.
    """
    awarded = make_candidate(1, "first", submitter_id=ALICE_ID, awarded=True)
    conflict = make_candidate(2, None, submitter_id=ALICE_ID, identity_status=VersionIdentityStatus.CONFLICT)
    other_person = make_candidate(3, "original", submitter_id=BOB_ID)
    assert [item.status for item in evaluate_reward_candidates(make_context(awarded, conflict, other_person))] == [Status.AWARDED, Status.CONFLICT, Status.READY]


def test_a_released_daily_limit_case_still_releases_its_own_person() -> None:
    """@description: The daily-limit release is unchanged: an over-limit case holds nobody, including its own person.
    @param: none
    @return: None.
    """
    earlier = make_candidate(1, None, submitter_id=ALICE_ID)
    own_later = make_candidate(2, "original", submitter_id=ALICE_ID)
    context = replace(make_context(earlier, own_later), daily_limit_exceeded=frozenset({earlier.operation_id}))
    decisions = evaluate_reward_candidates(context)
    assert [item.status for item in decisions] == [Status.INELIGIBLE, Status.READY]
    assert decisions[0].reason is Reason.DAILY_LIMIT


def test_an_exclusion_still_releases_the_same_persons_unknown_case() -> None:
    """@description: Scoped negative evidence keeps working for the one person it applies to.
    @param: none
    @return: None.
    """
    earlier = make_candidate(1, None, submitter_id=ALICE_ID)
    own_later = make_candidate(2, "original", submitter_id=ALICE_ID)
    exclusion = VersionExclusion(earlier.identity.video, RecordingVersionIdentity("original"), "synthetic-distinction")
    assert evaluate_reward_candidates(make_context(earlier, own_later, exclusions=(exclusion,)))[1].status is Status.READY
