"""Verify the daily reward limit as pure rules: the Taipei day boundary, the ranking, and its two effects on the reward evaluator.

A submission past the limit is stored and answered but earns nothing and holds nobody back; a payment already made is never taken back.
Nothing here touches a database, the network or Discord.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from bot.auto_pipeline import PipelineOutcome, format_share_outcome
from bot.daily_limit import (DAILY_LIMIT_REPLY, MAX_REWARDED_SUBMISSIONS_PER_DAY, UPLOAD_DAY_UTC_OFFSET_HOURS, AcceptedSubmission, over_limit_operation_ids,
                             upload_day)
from bot.review import ReviewStatus
from bot.reward_candidates import RewardCandidateStatus as Status, RewardDecision, RewardDecisionReason as Reason, evaluate_reward_candidates
from tests.version_candidate_support import make_candidate, make_context

UTC = timezone.utc
NOON_UTC = datetime(2026, 11, 3, 4, 0, tzinfo=UTC)  # [12:00 in Taipei]: Well inside one upload day.


def accepted(sequence: int, person: int = 1, at: datetime = NOON_UTC) -> AcceptedSubmission:
    """@description: Build one accepted submission.
    @param sequence: Intake order.
    @param person: Discord user.
    @param at: Acceptance time.
    @return: Submission.
    """
    return AcceptedSubmission(f"op-{person}-{sequence}", person, sequence, at)


def test_the_constants_are_the_owner_decisions() -> None:
    """@description: Lock the rule so a change is a reviewed change.
    @param: none
    @return: None.
    """
    assert (MAX_REWARDED_SUBMISSIONS_PER_DAY, UPLOAD_DAY_UTC_OFFSET_HOURS) == (10, 8)
    assert "10" in DAILY_LIMIT_REPLY and "+1" not in DAILY_LIMIT_REPLY


@pytest.mark.parametrize("utc_time,taipei_date", [(datetime(2026, 11, 3, 15, 59, 59, tzinfo=UTC), (2026, 11, 3)),
                                                  (datetime(2026, 11, 3, 16, 0, 0, tzinfo=UTC), (2026, 11, 4)),
                                                  (datetime(2026, 11, 3, 0, 0, 0, tzinfo=UTC), (2026, 11, 3)),
                                                  (datetime(2026, 12, 31, 16, 0, 0, tzinfo=UTC), (2027, 1, 1))])
def test_the_upload_day_changes_at_midnight_taipei(utc_time, taipei_date) -> None:
    """@description: 23:59:59 and 00:00:00 in Taipei belong to different days; a naive time is read as UTC.
    @param utc_time: Acceptance instant.
    @param taipei_date: Expected calendar day.
    @return: None.
    """
    assert (upload_day(utc_time).year, upload_day(utc_time).month, upload_day(utc_time).day) == taipei_date
    assert upload_day(utc_time.replace(tzinfo=None)) == upload_day(utc_time)


def test_the_tenth_submission_of_a_day_is_within_the_limit_and_the_eleventh_is_not() -> None:
    """@description: Ranking is by intake order inside one person's day.
    @param: none
    @return: None.
    """
    submissions = [accepted(sequence) for sequence in range(1, 12)]
    assert over_limit_operation_ids(reversed(submissions)) == {"op-1-11"}


def test_people_and_days_are_counted_separately() -> None:
    """@description: One person's busy day never affects another person or the next day.
    @param: none
    @return: None.
    """
    midnight = datetime(2026, 11, 3, 16, 0, 0, tzinfo=UTC)
    busy = [accepted(sequence) for sequence in range(1, 11)]
    other_person = [accepted(sequence, person=2) for sequence in range(11, 21)]
    next_day = [accepted(21, at=midnight), accepted(22, at=midnight + timedelta(hours=1))]
    assert over_limit_operation_ids(busy + other_person + next_day) == frozenset()
    late = accepted(23, at=midnight - timedelta(seconds=1))
    assert over_limit_operation_ids(busy + [late]) == {late.operation_id}


def test_the_same_video_resubmitted_still_counts_every_time() -> None:
    """@description: The limit counts accepted submissions, not distinct videos (the conservative reading).
    @param: none
    @return: None.
    """
    assert len(over_limit_operation_ids(accepted(sequence) for sequence in range(1, 14))) == 3


def evaluate(*candidates, exceeded=()):
    """@description: Evaluate a complete synthetic scope with the given operations past the limit.
    @param candidates: Candidates.
    @param exceeded: Operations past the limit.
    @return: Decisions in intake order.
    """
    return evaluate_reward_candidates(replace(make_context(*candidates), daily_limit_exceeded=frozenset(exceeded)))


def test_without_any_excess_the_evaluator_behaves_exactly_as_before() -> None:
    """@description: The default keeps the previous priority behaviour.
    @param: none
    @return: None.
    """
    decisions = evaluate_reward_candidates(make_context(make_candidate(1), make_candidate(2)))
    assert [item.status for item in decisions] == [Status.READY, Status.WAITING_PREDECESSOR]
    assert decisions == evaluate(make_candidate(1), make_candidate(2))


def test_an_excess_submission_is_ineligible_with_its_own_reason() -> None:
    """@description: Past the limit means no reward, whatever the review says.
    @param: none
    @return: None.
    """
    decision = evaluate(make_candidate(1), exceeded={"op-1"})[0]
    assert (decision.status, decision.reason) == (Status.INELIGIBLE, Reason.DAILY_LIMIT)


def test_an_excess_earliest_submission_does_not_block_a_later_submitter_of_the_same_version() -> None:
    """@description: Without releasing priority, a second account could park the first claim and starve everyone else.
    @param: none
    @return: None.
    """
    excess, later = make_candidate(1), make_candidate(2)
    decisions = evaluate(excess, later, exceeded={excess.operation_id})
    assert [item.status for item in decisions] == [Status.INELIGIBLE, Status.READY]


def test_a_payment_already_made_is_never_taken_back() -> None:
    """@description: An awarded candidate stays awarded even if the ranking would now put it past the limit.
    @param: none
    @return: None.
    """
    paid = make_candidate(1, awarded=True)
    decision = evaluate(paid, exceeded={paid.operation_id})[0]
    assert (decision.status, decision.reason) == (Status.AWARDED, Reason.PREVIOUSLY_AWARDED)


def test_an_excess_submission_of_an_already_awarded_version_is_still_ineligible() -> None:
    """@description: Either reason withholds the reward; the limit simply comes first.
    @param: none
    @return: None.
    """
    decisions = evaluate(make_candidate(1, awarded=True), make_candidate(2), exceeded={"op-2"})
    assert decisions[1].status is Status.INELIGIBLE


def test_an_excess_submission_does_not_release_priority_for_a_different_person_unless_it_is_excess() -> None:
    """@description: Only the excess operation is released; an in-limit earlier submission still holds its place.
    @param: none
    @return: None.
    """
    first, second, third = make_candidate(1), make_candidate(2), make_candidate(3)
    decisions = evaluate(first, second, third, exceeded={second.operation_id})
    assert [item.status for item in decisions] == [Status.READY, Status.INELIGIBLE, Status.WAITING_PREDECESSOR]


def outcome(reason: Reason, status: Status = Status.INELIGIBLE) -> PipelineOutcome:
    """@description: Build a pipeline outcome around one decision.
    @param reason: Decision reason.
    @param status: Decision status.
    @return: Outcome.
    """
    return PipelineOutcome(ReviewStatus.PENDING_VALIDATION, RewardDecision("op-1", status, reason))


def test_the_reply_for_an_excess_submission_says_saved_and_unpaid_and_never_plus_one() -> None:
    """@description: The submitter is told the truth in one sentence.
    @param: none
    @return: None.
    """
    reply = format_share_outcome("op-1", outcome(Reason.DAILY_LIMIT))
    assert DAILY_LIMIT_REPLY in reply and "已儲存投稿" in reply and "+1" not in reply and "op-1" in reply


def test_other_replies_are_unchanged() -> None:
    """@description: Only the daily-limit reason changes the wording.
    @param: none
    @return: None.
    """
    assert DAILY_LIMIT_REPLY not in format_share_outcome("op-1", outcome(Reason.REVIEW_REJECTED))
