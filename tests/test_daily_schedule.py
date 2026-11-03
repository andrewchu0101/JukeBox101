"""Offline contract of the stage C daily schedule rules (issue #21): hour validation, the fixed Asia/Taipei day boundary, when a task is due, and notice throttling.

INTERFACE CONTRACT (module bot/daily_schedule.py, pure functions and constants; no I/O, no clock reads, no process time zone):
    DAILY_DEFAULT_HOUR: int = 20                       # Owner default, Asia/Taipei.
    NOTICE_SILENCE_AFTER_DAYS: int = 3                 # Notices sent on streak days 1..3, silent from day 4.
    NOT_ENOUGH_NICKEL_TEXT: str = "Nickel不夠了…快去分享歌曲，賺點Nickel！"   # Owner wording, U+2026 ellipsis.
    NO_CANDIDATE_DAILY_TEXT: str                       # Link-free wording for "credit but no song"; Owner may reword the constant only.
    class InvalidDailyHour(ValueError)
    parse_daily_hour(raw: int | str | None) -> int     # None -> 20; whole hours 0..23 only; bool, floats and "20:30" raise InvalidDailyHour.
    taipei_day(moment: datetime) -> date               # Naive means UTC; aware values are converted. Fixed UTC+8, no tzdata dependency.
    taipei_hour(moment: datetime) -> int
    is_dispatch_due(delivery_hour: int, now: datetime, last_task_day: date | None) -> bool
        # True when taipei_day(now) != last_task_day and taipei_hour(now) >= delivery_hour. A task missed while the service was down is therefore
        # still due later the same Taipei day, and a day with no task is never made up on a later day.
    notice_allowed(streak_before: int, last_notice_day: date | None, today: date) -> bool
        # At most one notice per Taipei day and none once streak_before >= NOTICE_SILENCE_AFTER_DAYS.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from bot.daily_schedule import (DAILY_DEFAULT_HOUR, NO_CANDIDATE_DAILY_TEXT, NOT_ENOUGH_NICKEL_TEXT, NOTICE_SILENCE_AFTER_DAYS,
                                InvalidDailyHour, is_dispatch_due, notice_allowed, parse_daily_hour, taipei_day, taipei_hour)

DAY = date(2026, 11, 3)  # [reference Taipei day]: A day with no daylight saving anywhere near it.


def taipei_clock(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """@description: Build the naive UTC instant that shows the given Asia/Taipei wall time, as the production clock supplies it.
    @param day: Taipei calendar day.
    @param hour: Taipei hour.
    @param minute: Minute.
    @param second: Second.
    @return: Naive UTC datetime.
    """
    return datetime(day.year, day.month, day.day, hour, minute, second) - timedelta(hours=8)


def test_the_default_hour_is_20_and_the_wording_is_the_owner_approved_text() -> None:
    """@description: Owner fixed the default hour and the exact not-enough wording.
    @return: None.
    """
    assert DAILY_DEFAULT_HOUR == 20 and parse_daily_hour(None) == 20
    assert NOT_ENOUGH_NICKEL_TEXT == "Nickel不夠了…快去分享歌曲，賺點Nickel！"
    assert NO_CANDIDATE_DAILY_TEXT.strip() and "watch?v=" not in NO_CANDIDATE_DAILY_TEXT


@pytest.mark.parametrize("hour", range(24))
def test_every_whole_hour_from_00_to_23_is_accepted(hour) -> None:
    """@description: The whole allowed range, as a number and as the digits a user may type.
    @param hour: Candidate hour.
    @return: None.
    """
    assert parse_daily_hour(hour) == hour and parse_daily_hour(str(hour)) == hour


@pytest.mark.parametrize("raw", ["20:30", "20:01", "20:00:30", "24", 24, "-1", -1, "abc", "", " ", "2.5", 2.5, "٢٠", "1e1", True, False, 99])
def test_anything_but_a_whole_hour_is_refused(raw) -> None:
    """@description: Minutes, seconds, out-of-range values, text, booleans and non-ASCII digits never become a schedule.
    @param raw: Rejected input.
    @return: None.
    """
    with pytest.raises(InvalidDailyHour):
        parse_daily_hour(raw)


def test_23_59_59_and_00_00_00_belong_to_different_taipei_days() -> None:
    """@description: The boundary is Taipei midnight, which is 16:00 UTC, not the UTC day.
    @return: None.
    """
    last_second, first_second = taipei_clock(DAY, 23, 59, 59), taipei_clock(DAY + timedelta(days=1), 0)
    assert (taipei_day(last_second), taipei_day(first_second)) == (DAY, DAY + timedelta(days=1))
    assert last_second.hour == 15 and first_second.hour == 16 and taipei_hour(first_second) == 0


@pytest.mark.parametrize("zone", ["UTC", "America/New_York", "Asia/Tokyo", "Pacific/Auckland"])
def test_the_same_instant_gives_the_same_taipei_day_whatever_zone_it_arrives_in(zone) -> None:
    """@description: An aware value from any host zone resolves to the same Taipei day and hour; the process time zone never matters.
    @param zone: IANA name of the zone the instant is expressed in.
    @return: None.
    """
    instant = datetime(2026, 11, 3, 15, 59, 59, tzinfo=timezone.utc).astimezone(ZoneInfo(zone))
    assert (taipei_day(instant), taipei_hour(instant)) == (DAY, 23)


def test_every_hour_of_the_year_maps_to_a_fixed_eight_hour_offset() -> None:
    """@description: No daylight saving shift anywhere in a full year: Taipei hour is always UTC hour plus eight.
    @return: None.
    """
    start = datetime(2026, 1, 1)
    for step in range(365 * 24):
        instant = start + timedelta(hours=step)
        assert taipei_hour(instant) == (instant.hour + 8) % 24
        assert taipei_day(instant) == (instant + timedelta(hours=8)).date()


def test_a_task_is_due_from_its_hour_until_the_end_of_the_same_taipei_day() -> None:
    """@description: Not before the chosen hour, due from it on, so a service that was down at the hour still sends later that day.
    @return: None.
    """
    assert not is_dispatch_due(20, taipei_clock(DAY, 19, 59, 59), None)
    assert is_dispatch_due(20, taipei_clock(DAY, 20), None)
    assert is_dispatch_due(20, taipei_clock(DAY, 23, 59, 59), None)


def test_a_task_that_already_ran_today_is_not_due_again_until_the_next_taipei_day() -> None:
    """@description: One task per person per Taipei day, whatever the hour is changed to.
    @return: None.
    """
    assert not is_dispatch_due(20, taipei_clock(DAY, 22), DAY) and not is_dispatch_due(0, taipei_clock(DAY, 22), DAY)
    assert not is_dispatch_due(21, taipei_clock(DAY + timedelta(days=1), 20, 59, 59), None)
    assert is_dispatch_due(21, taipei_clock(DAY + timedelta(days=1), 21), DAY)


def test_hour_00_is_due_at_the_start_of_the_new_taipei_day_not_the_utc_day() -> None:
    """@description: A midnight schedule fires at 16:00 UTC, and the previous day's task does not block it.
    @return: None.
    """
    next_day = DAY + timedelta(days=1)
    assert not is_dispatch_due(0, taipei_clock(DAY, 23, 59, 59), DAY)
    assert is_dispatch_due(0, taipei_clock(next_day, 0), DAY) and taipei_clock(next_day, 0).hour == 16


def test_a_missed_day_is_not_made_up_on_a_later_day() -> None:
    """@description: After two silent days the user is simply due on the current day at their hour, with no extra catch-up entitlement.
    @return: None.
    """
    three_days_later = DAY + timedelta(days=3)
    assert not is_dispatch_due(20, taipei_clock(three_days_later, 9), DAY)
    assert is_dispatch_due(20, taipei_clock(three_days_later, 20), DAY)


def test_notices_are_sent_at_most_once_per_day_and_for_three_days_in_a_row() -> None:
    """@description: Streak days 1, 2 and 3 may send; the fourth consecutive day is silent; a notice already sent today blocks another.
    @return: None.
    """
    assert NOTICE_SILENCE_AFTER_DAYS == 3
    yesterday = DAY - timedelta(days=1)
    assert [notice_allowed(streak, yesterday if streak else None, DAY) for streak in range(5)] == [True, True, True, False, False]
    assert notice_allowed(0, DAY, DAY) is False and notice_allowed(2, DAY, DAY) is False
