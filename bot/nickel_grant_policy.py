"""Decide which approved Nickel grants a claim may return, using the Asia/Taipei weekly calendar.

Pure calendar and eligibility rules only: no database, no Discord, no clock reads. The claim service supplies
the instant and the already-recorded claim state, so every rule here can be tested with fixed timestamps.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

ENTRY_GRANT_NICKEL = 2  # [approved entry grant]: One lifetime grant per Discord account.
WEEKLY_GRANT_NICKEL = 1  # [approved weekly grant]: One grant per Discord account per weekly period.
TAIPEI_TIMEZONE = ZoneInfo("Asia/Taipei")  # [calendar zone]: Named zone from the IANA database; weekly periods start Monday 00:00 here.
PERIOD_START_WEEKDAY = 0  # [Monday]: datetime.weekday() value of the period start.


class GrantKind(Enum):
    """Name the two approved Nickel grant sources so each one keeps its own ledger reason and claim record."""

    ENTRY = "entry"
    WEEKLY = "weekly"


@dataclass(frozen=True)
class GrantLine:
    """Carry one grant that a claim returns, so the reply can name its source and amount."""

    kind: GrantKind  # [grant source]: Entry or weekly; decides the ledger reason and the reply label.
    amount: int  # [Nickel amount]: Approved value for this source.


def _require_aware(instant: datetime) -> None:
    """@description: Refuse naive datetimes so a local clock can never shift the Taipei boundary.
    @param instant: Moment to check.
    @return: None; raises ValueError for a naive datetime.
    """
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("instant must be timezone-aware")


def _taipei_period_start(instant: datetime) -> datetime:
    """@description: Floor an instant to the Monday 00:00 Taipei that starts its weekly period.
    @param instant: Timezone-aware moment.
    @return: Timezone-aware datetime in Asia/Taipei at the period start.
    """
    local = instant.astimezone(TAIPEI_TIMEZONE)
    days_since_monday = (local.weekday() - PERIOD_START_WEEKDAY) % 7
    period_date = local.date() - timedelta(days=days_since_monday)
    return datetime(period_date.year, period_date.month, period_date.day, tzinfo=TAIPEI_TIMEZONE)


def weekly_period_key(instant: datetime) -> str:
    """@description: Name the weekly period an instant belongs to, so one claim per account per period can be enforced.
    @param instant: Timezone-aware moment of the claim.
    @return: ISO date of the Monday that starts the Taipei week, for example 2026-10-26.
    """
    _require_aware(instant)
    return _taipei_period_start(instant).date().isoformat()


def next_period_start(instant: datetime) -> datetime:
    """@description: Tell the user when the next weekly grant opens, expressed in UTC for storage and display.
    @param instant: Timezone-aware moment inside the current period.
    @return: Timezone-aware UTC datetime of the following Monday 00:00 Taipei.
    """
    _require_aware(instant)
    following_start = _taipei_period_start(instant) + timedelta(days=7)
    return following_start.astimezone(timezone.utc)


def plan_claim(entry_claimed: bool, weekly_claimed_for_period: bool) -> list:
    """@description: Return every approved grant the account may still receive now, so one /claim gives all of them at once.
    @param entry_claimed: True when the account already holds its one-time entry grant.
    @param weekly_claimed_for_period: True when the account already claimed the current weekly period.
    @return: Ordered list of GrantLine values; empty when nothing is missing.
    """
    lines = []
    if not entry_claimed:
        lines.append(GrantLine(GrantKind.ENTRY, ENTRY_GRANT_NICKEL))
    if not weekly_claimed_for_period:
        lines.append(GrantLine(GrantKind.WEEKLY, WEEKLY_GRANT_NICKEL))
    return lines
