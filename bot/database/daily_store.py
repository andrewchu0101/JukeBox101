"""Persist daily subscriptions and the one-task-per-Taipei-day schedule inside short serialized transactions.

Each function takes a cursor that already holds the global writer gate, so the existing-task check, the shared exchange reservation and the task row commit together. Nothing here sends a message.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from random import Random
from typing import Any, NamedTuple

from bot.daily_schedule import is_dispatch_due, notice_allowed, taipei_day
from bot.database.exchange_reservation import ExchangeOutcome, ExchangeStatus, reserve_exchange

FINAL_DISPATCH_STATUSES = frozenset({"delivered", "failed", "uncertain"})  # [mirrored states]: The only delivery results a task row records.
NOTICE_KIND_BY_STATUS = {ExchangeStatus.INSUFFICIENT_CREDITS: "insufficient_credit", ExchangeStatus.NO_CANDIDATE: "no_candidate"}  # [notice cases]: Statuses that send a message instead of a song.


class DailyPreference(NamedTuple):
    """Carry one subscriber's stored schedule."""

    discord_user_id: int  # [subscriber]: Owner of the setting.
    enabled: bool  # [subscription]: Whether scheduled delivery is on.
    delivery_hour: int  # [Taipei hour]: Whole hour from 0 to 23.


@dataclass(frozen=True)
class DailyTaskStart:
    """Describe what the sweep must do after the day's task was claimed in the database."""

    discord_user_id: int  # [subscriber]: Whose task this is.
    kind: str  # [next step]: skipped, in_progress, notice or reserved.
    recommendation_id: int | None = None  # [reservation]: Present for kind reserved.
    youtube_url: str | None = None  # [delivery link]: Present for kind reserved.
    notice_kind: str | None = None  # [notice case]: insufficient_credit or no_candidate for kind notice.
    notice_day: date | None = None  # [Taipei day]: Key of the notice row.
    silenced: bool = False  # [throttled]: True when the notice must not be sent.


def save_preference(cursor: Any, discord_user_id: int, enabled: bool | None, delivery_hour: int | None, now: datetime) -> DailyPreference:
    """@description: Create or update one subscriber's schedule, leaving any field given as None unchanged.
    @param cursor: Cursor inside the serialized transaction.
    @param discord_user_id: Subscriber.
    @param enabled: New subscription state, or None to keep it.
    @param delivery_hour: New Taipei hour already validated, or None to keep it.
    @param now: Naive UTC time of the change.
    @return: The stored preference.
    """
    cursor.execute("INSERT INTO users (discord_user_id) VALUES (%s) ON CONFLICT (discord_user_id) DO NOTHING", (discord_user_id,))
    cursor.execute("INSERT INTO daily_preferences (discord_user_id, enabled, delivery_hour, updated_at) "
                   "VALUES (%s, COALESCE(%s::boolean, FALSE), COALESCE(%s::smallint, 20), %s) "
                   "ON CONFLICT (discord_user_id) DO UPDATE SET enabled = COALESCE(%s::boolean, daily_preferences.enabled), "
                   "delivery_hour = COALESCE(%s::smallint, daily_preferences.delivery_hour), updated_at = EXCLUDED.updated_at "
                   "RETURNING discord_user_id, enabled, delivery_hour",
                   (discord_user_id, enabled, delivery_hour, now, enabled, delivery_hour))
    row = cursor.fetchone()  # [stored result]: The reply is built from what the database holds.
    return DailyPreference(row["discord_user_id"], row["enabled"], row["delivery_hour"])


def read_preference(cursor: Any, discord_user_id: int) -> DailyPreference | None:
    """@description: Read one subscriber's schedule.
    @param cursor: Cursor inside a transaction.
    @param discord_user_id: Subscriber.
    @return: The preference, or None when none was ever saved.
    """
    cursor.execute("SELECT discord_user_id, enabled, delivery_hour FROM daily_preferences WHERE discord_user_id = %s", (discord_user_id,))
    row = cursor.fetchone()
    return None if row is None else DailyPreference(row["discord_user_id"], row["enabled"], row["delivery_hour"])


def schedule_candidates(cursor: Any) -> list[tuple[int, int, date | None]]:
    """@description: List every subscriber with the Taipei day of their latest task, so the sweep can apply the due rule in one place.
    @param cursor: Cursor inside a transaction.
    @return: (user, Taipei hour, latest task day or None) per enabled subscriber, by user.
    """
    cursor.execute("SELECT p.discord_user_id, p.delivery_hour, GREATEST("
                   "(SELECT max(d.dispatch_date) FROM daily_dispatches d WHERE d.discord_user_id = p.discord_user_id), "
                   "(SELECT max(n.notice_day) FROM daily_notices n WHERE n.discord_user_id = p.discord_user_id)) AS last_task_day "
                   "FROM daily_preferences p WHERE p.enabled ORDER BY p.discord_user_id")
    return [(row["discord_user_id"], row["delivery_hour"], row["last_task_day"]) for row in cursor.fetchall()]


def begin_task(cursor: Any, control: dict, discord_user_id: int, now: datetime, rng: Random, compensation_enabled: bool = False) -> DailyTaskStart:
    """@description: Claim the subscriber's single task for today before any message, using the shared exchange reservation for the debit.
    @param cursor: Cursor holding the global writer gate.
    @param control: Authoritative control row used by the shared reservation.
    @param discord_user_id: Subscriber whose task is claimed.
    @param now: Naive UTC clock.
    @param rng: Injected random source for the draw.
    @param compensation_enabled: Same compensation switch the exchange uses.
    @return: What to do next; skipped when today's task already exists or the subscription no longer applies.
    """
    today = taipei_day(now)  # [task key]: One task per subscriber per Taipei day.
    existing = _existing_task(cursor, discord_user_id, today)
    if existing is not None:
        return existing
    if not _still_subscribed_and_due(cursor, discord_user_id, now):
        return DailyTaskStart(discord_user_id, "skipped")
    key = f"daily:{discord_user_id}:{today.isoformat()}"  # [reservation key]: Deterministic, so a replay finds the same reservation.
    outcome = reserve_exchange(cursor, control, discord_user_id, key, rng, now, compensation_enabled=compensation_enabled)
    return _record_task(cursor, discord_user_id, today, now, outcome)


def _existing_task(cursor: Any, discord_user_id: int, today: date) -> DailyTaskStart | None:
    """@description: Find today's task if it already exists, resuming only a reservation that no attempt has claimed yet.
    @param cursor: Cursor inside the transaction.
    @param discord_user_id: Subscriber.
    @param today: Taipei day.
    @return: A reserved start to resume, a skipped start for any other existing task, or None when today is free.
    """
    cursor.execute("SELECT d.recommendation_id, r.status, s.youtube_url FROM daily_dispatches d "
                   "LEFT JOIN recommendations r ON r.recommendation_id = d.recommendation_id "
                   "LEFT JOIN songs s ON s.song_id = d.song_id WHERE d.discord_user_id = %s AND d.dispatch_date = %s", (discord_user_id, today))
    row = cursor.fetchone()  # [reservation task]: At most one by the unique key.
    if row is not None:
        resumable = row["status"] == "reserved"  # [unclaimed]: The claim step still arbitrates a single sender.
        return DailyTaskStart(discord_user_id, "reserved", row["recommendation_id"], row["youtube_url"]) if resumable else DailyTaskStart(discord_user_id, "skipped")
    cursor.execute("SELECT 1 FROM daily_notices WHERE discord_user_id = %s AND notice_day = %s", (discord_user_id, today))
    return DailyTaskStart(discord_user_id, "skipped") if cursor.fetchone() is not None else None


def _still_subscribed_and_due(cursor: Any, discord_user_id: int, now: datetime) -> bool:
    """@description: Re-check under the gate that the subscriber is still on and the hour has been reached, since the setting may have changed.
    @param cursor: Cursor inside the transaction.
    @param discord_user_id: Subscriber.
    @param now: Naive UTC clock.
    @return: True when the task may start now.
    """
    preference = read_preference(cursor, discord_user_id)
    return preference is not None and preference.enabled and is_dispatch_due(preference.delivery_hour, now, None)


def _record_task(cursor: Any, discord_user_id: int, today: date, now: datetime, outcome: ExchangeOutcome) -> DailyTaskStart:
    """@description: Write the task row that matches the reservation result, in the same transaction as the reservation.
    @param cursor: Cursor holding the writer gate.
    @param discord_user_id: Subscriber.
    @param today: Taipei day.
    @param now: Naive UTC clock.
    @param outcome: Result of the shared exchange reservation.
    @return: What the sweep must do next.
    """
    if outcome.status in (ExchangeStatus.RESERVED, ExchangeStatus.REPLAYED):
        cursor.execute("INSERT INTO daily_dispatches (discord_user_id, song_id, recommendation_id, dispatch_date, status, attempt_count) "
                       "SELECT %s, song_id, recommendation_id, %s, 'sending', 1 FROM recommendations WHERE recommendation_id = %s",
                       (discord_user_id, today, outcome.recommendation_id))
        return DailyTaskStart(discord_user_id, "reserved", outcome.recommendation_id, outcome.youtube_url)
    if outcome.status in NOTICE_KIND_BY_STATUS:
        return _record_notice(cursor, discord_user_id, today, now, NOTICE_KIND_BY_STATUS[outcome.status])
    cursor.execute("INSERT INTO daily_dispatches (discord_user_id, dispatch_date, status, last_error_code) VALUES (%s, %s, 'pending', 'exchange_in_progress')",
                   (discord_user_id, today))
    return DailyTaskStart(discord_user_id, "in_progress")


def _record_notice(cursor: Any, discord_user_id: int, today: date, now: datetime, notice_kind: str) -> DailyTaskStart:
    """@description: Claim today's notice row with the throttle applied; the row starts as uncertain until the message result is known.
    @param cursor: Cursor holding the writer gate.
    @param discord_user_id: Subscriber.
    @param today: Taipei day.
    @param now: Naive UTC clock.
    @param notice_kind: insufficient_credit or no_candidate.
    @return: A notice start, silenced when three consecutive notice days already passed.
    """
    yesterday = today - timedelta(days=1)
    cursor.execute("SELECT consecutive_days FROM daily_notices WHERE discord_user_id = %s AND notice_day = %s", (discord_user_id, yesterday))
    previous = cursor.fetchone()  # [streak source]: Only the directly preceding day continues a streak.
    streak_before = 0 if previous is None else previous["consecutive_days"]
    allowed = notice_allowed(streak_before, None if previous is None else yesterday, today)
    cursor.execute("INSERT INTO daily_notices (discord_user_id, notice_day, kind, delivery, consecutive_days, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                   (discord_user_id, today, notice_kind, "uncertain" if allowed else "silenced", streak_before + 1, now))
    return DailyTaskStart(discord_user_id, "notice", notice_kind=notice_kind, notice_day=today, silenced=not allowed)


def recommender_of(cursor: Any, recommendation_id: int) -> int | None:
    """@description: Find the person whose settled submission earned the recommended song, so their current name can be looked up live.
    @param cursor: Cursor inside a transaction.
    @param recommendation_id: Reservation.
    @return: The recommender's Discord id, or None when it cannot be determined.
    """
    cursor.execute("SELECT c.discord_user_id FROM recommendations r JOIN jb2_videos v ON v.song_id = r.song_id "
                   "JOIN jb2_events e ON e.video_id = v.video_id JOIN jb2_claims c ON c.operation_id = e.operation_id "
                   "WHERE r.recommendation_id = %s ORDER BY e.accepted_sequence LIMIT 1", (recommendation_id,))
    row = cursor.fetchone()
    return None if row is None else row["discord_user_id"]


def finish_dispatch(cursor: Any, recommendation_id: int, status: str) -> None:
    """@description: Mirror the recorded delivery result on the task row; other states leave it as sending.
    @param cursor: Cursor inside a transaction.
    @param recommendation_id: Reservation of the task.
    @param status: Recorded delivery status.
    @return: None.
    """
    if status in FINAL_DISPATCH_STATUSES:
        cursor.execute("UPDATE daily_dispatches SET status = %s WHERE recommendation_id = %s", (status, recommendation_id))


def finish_notice(cursor: Any, discord_user_id: int, notice_day: date, delivery: str) -> None:
    """@description: Record whether the notice message was accepted, refused or unknown; only a still-unknown row changes.
    @param cursor: Cursor inside a transaction.
    @param discord_user_id: Subscriber.
    @param notice_day: Taipei day of the notice.
    @param delivery: sent, failed or uncertain.
    @return: None.
    """
    cursor.execute("UPDATE daily_notices SET delivery = %s WHERE discord_user_id = %s AND notice_day = %s AND delivery = 'uncertain'",
                   (delivery, discord_user_id, notice_day))
