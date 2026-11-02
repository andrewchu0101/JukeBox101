"""Decide which accepted submissions fall beyond a person's daily reward limit, counted per Asia/Taipei calendar day.

Input is the accepted submissions (who, in which intake order, at what database time); output is the set of operations past the limit.
The limit only withholds a reward and a YouTube lookup; it never rejects or hides a submission, which is always stored and answered.
"""

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import NamedTuple

MAX_REWARDED_SUBMISSIONS_PER_DAY = 10  # [Owner rule]: Accepted submissions per person per upload day that may still earn a reward.
UPLOAD_DAY_UTC_OFFSET_HOURS = 8  # [Owner rule]: Asia/Taipei has no daylight saving time, so a fixed offset equals the calendar there.
UPLOAD_DAY_ZONE_NAME = "Asia/Taipei"  # [database side]: PostgreSQL derives the same day from this zone; both sides must agree at the boundary.
UPLOAD_DAY_ZONE = timezone(timedelta(hours=UPLOAD_DAY_UTC_OFFSET_HOURS))  # [application side]: Fixed offset, independent of container tzdata.
DAILY_LIMIT_REPLY = "今天已達每人 10 首的獎勵上限，這首已儲存，但不會發放 Nickel"  # [draft wording]: Told to the submitter past the limit; the Owner may reword it here.


class AcceptedSubmission(NamedTuple):
    """Carry the three facts that decide a submission's place in its person's day."""

    operation_id: str  # [submission identity]: Stable business key of the accepted operation.
    submitter_id: int  # [person]: Discord user the limit applies to; one account is one person.
    intake_sequence: int  # [intake order]: Database acceptance order, never the reexamination ordering.
    accepted_at: datetime  # [database clock]: Instant of acceptance; naive values are read as UTC.


def upload_day(accepted_at: datetime) -> date:
    """@description: Turn an acceptance instant into the Taipei calendar day it belongs to, so 23:59 and 00:00 fall on different days.
    @param accepted_at: Database acceptance time; a naive value is read as UTC.
    @return: The Asia/Taipei calendar day.
    """
    aware = accepted_at.replace(tzinfo=timezone.utc) if accepted_at.tzinfo is None else accepted_at
    return aware.astimezone(UPLOAD_DAY_ZONE).date()


def over_limit_operation_ids(submissions: Iterable[AcceptedSubmission], limit: int = MAX_REWARDED_SUBMISSIONS_PER_DAY) -> frozenset[str]:
    """@description: Name the submissions past the limit, so the reward rule and the lookup budget can both leave them out.
    @param submissions: Every accepted submission in scope, in any order.
    @param limit: Rewardable submissions per person per day.
    @return: Operations whose place in their person's day is after the limit.
    """
    by_person_and_day: dict[tuple[int, date], list[AcceptedSubmission]] = defaultdict(list)  # [one day of one person]: Ranked by intake order below.
    for submission in submissions:
        by_person_and_day[(submission.submitter_id, upload_day(submission.accepted_at))].append(submission)
    exceeded: set[str] = set()
    for day_submissions in by_person_and_day.values():
        ordered = sorted(day_submissions, key=lambda item: item.intake_sequence)
        exceeded.update(item.operation_id for item in ordered[limit:])
    return frozenset(exceeded)
