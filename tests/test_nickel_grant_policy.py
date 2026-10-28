"""Pin the approved Nickel grant calendar and claim plan without any database or Discord dependency.

These tests guard the Asia/Taipei weekly boundary, the one-time entry grant and the rule that /claim
returns every currently eligible grant in one plan while never repeating a claimed one.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.nickel_grant_policy import (
    ENTRY_GRANT_NICKEL,
    WEEKLY_GRANT_NICKEL,
    GrantKind,
    next_period_start,
    plan_claim,
    weekly_period_key,
)

UTC = timezone.utc


def test_monday_midnight_taipei_starts_a_new_weekly_period():
    # Taipei is UTC+8 with no daylight saving; Monday 2026-10-26 00:00 Taipei is Sunday 16:00 UTC.
    assert weekly_period_key(datetime(2026, 10, 25, 16, 0, tzinfo=UTC)) == "2026-10-26"


def test_sunday_last_second_taipei_still_belongs_to_the_previous_week():
    assert weekly_period_key(datetime(2026, 10, 25, 15, 59, 59, tzinfo=UTC)) == "2026-10-19"


def test_year_boundary_uses_the_monday_that_starts_the_week():
    # 2027-01-03 is a Sunday in Taipei; its week started Monday 2026-12-28.
    assert weekly_period_key(datetime(2027, 1, 3, 12, 0, tzinfo=UTC)) == "2026-12-28"


def test_naive_datetime_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        weekly_period_key(datetime(2026, 10, 25, 16, 0))


def test_next_period_start_is_the_following_monday_midnight_taipei():
    # Sunday 2026-10-25 23:00 Taipei (15:00 UTC) -> Monday 2026-10-26 00:00 Taipei (Sunday 16:00 UTC).
    assert next_period_start(datetime(2026, 10, 25, 15, 0, tzinfo=UTC)) == datetime(2026, 10, 25, 16, 0, tzinfo=UTC)


def test_first_claim_returns_the_entry_grant_and_this_weeks_grant_together():
    lines = plan_claim(entry_claimed=False, weekly_claimed_for_period=False)
    assert [(line.kind, line.amount) for line in lines] == [
        (GrantKind.ENTRY, ENTRY_GRANT_NICKEL),
        (GrantKind.WEEKLY, WEEKLY_GRANT_NICKEL),
    ]
    assert sum(line.amount for line in lines) == 3


def test_repeat_claim_in_the_same_week_returns_nothing():
    assert plan_claim(entry_claimed=True, weekly_claimed_for_period=True) == []


def test_only_the_missing_grant_is_returned_when_the_other_is_already_claimed():
    assert [line.kind for line in plan_claim(entry_claimed=True, weekly_claimed_for_period=False)] == [GrantKind.WEEKLY]
    assert [line.kind for line in plan_claim(entry_claimed=False, weekly_claimed_for_period=True)] == [GrantKind.ENTRY]


def test_grant_amounts_are_the_approved_values():
    assert ENTRY_GRANT_NICKEL == 2
    assert WEEKLY_GRANT_NICKEL == 1


def test_calendar_uses_the_named_asia_taipei_zone_not_a_fixed_offset():
    # [named zone]: A fixed +08:00 offset is only a stand-in; the approved rule names Asia/Taipei.
    from zoneinfo import ZoneInfo

    from bot import nickel_grant_policy

    assert nickel_grant_policy.TAIPEI_TIMEZONE == ZoneInfo("Asia/Taipei")


def test_a_timezone_aware_input_in_another_zone_gives_the_same_taipei_period():
    from zoneinfo import ZoneInfo

    # 2026-10-26 00:00 Taipei == 2026-10-25 12:00 New York (EDT, UTC-4).
    new_york_instant = datetime(2026, 10, 25, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    assert weekly_period_key(new_york_instant) == "2026-10-26"
