"""Decide when a scheduled daily recommendation is due and carry out one pass for every due subscriber.

Pure rules (hour validation, the fixed Asia/Taipei day, due time, notice throttling) sit beside the sweep, which reuses the stage B reserve, claim, send, record and refund steps through the repository instead of copying their rules. Database work runs in worker threads and every Discord call is awaited on the event loop.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from bot.daily_limit import UPLOAD_DAY_ZONE, upload_day
from bot.daily_mask import mask_display_name
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.exchange_delivery import DM_TIMEOUT_SECONDS, DeliveryStatus, DirectMessageSender, DmKind, DmResult, deliver_recommendation, refund_if_unsent

LOGGER = logging.getLogger(__name__)
DAILY_DEFAULT_HOUR = 20  # [Owner default]: Taipei hour used when a user turns daily on without choosing one.
LAST_HOUR_OF_DAY = 23  # [valid range]: Whole hours 0..23 only.
MAX_HOUR_TEXT_LENGTH = 2  # [bounded input]: "07" is the longest accepted digit string, so no huge number is ever converted.
NOTICE_SILENCE_AFTER_DAYS = 3  # [Owner rule]: Notices go out on streak days 1..3 and stay silent from day 4.
NAME_LOOKUP_TIMEOUT_SECONDS = 5.0  # [courtesy lookup]: A slow name lookup falls back to the anonymous placeholder.
NOT_ENOUGH_NICKEL_TEXT = "Nickel不夠了…快去分享歌曲，賺點Nickel！"  # [Owner wording]: Exact text, U+2026 ellipsis.
NO_CANDIDATE_DAILY_TEXT = "今天暫時沒有適合你的新歌曲，沒有扣除 Nickel。有新歌曲時會再為你推薦。"  # [draft wording]: Link-free; the Owner may reword it here.
NOTICE_TEXT_BY_KIND = {"insufficient_credit": NOT_ENOUGH_NICKEL_TEXT, "no_candidate": NO_CANDIDATE_DAILY_TEXT}  # [notice wording]: One text per notice kind.


class InvalidDailyHour(ValueError):
    """Refuse an hour that is not a whole Taipei hour from 0 to 23."""


def parse_daily_hour(raw: int | str | None) -> int:
    """@description: Turn a typed hour into a valid whole Taipei hour, so a bad input can never become a schedule.
    @param raw: Integer, ASCII digit text, or None for the default.
    @return: Hour from 0 to 23.
    """
    if raw is None:
        return DAILY_DEFAULT_HOUR
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise InvalidDailyHour("The hour must be a whole number")
    if isinstance(raw, str) and not (raw.isascii() and raw.isdigit() and len(raw) <= MAX_HOUR_TEXT_LENGTH):
        raise InvalidDailyHour("The hour must be digits only")
    hour = int(raw)  # [validated number]: Digits only and at most two of them, or already an int.
    if not 0 <= hour <= LAST_HOUR_OF_DAY:
        raise InvalidDailyHour("The hour must be between 0 and 23")
    return hour


def taipei_day(moment: datetime) -> date:
    """@description: Give the Asia/Taipei calendar day of an instant with the same fixed offset the daily limit uses.
    @param moment: Naive UTC or timezone-aware instant.
    @return: Taipei calendar day.
    """
    return upload_day(moment)


def taipei_hour(moment: datetime) -> int:
    """@description: Give the Asia/Taipei wall-clock hour of an instant.
    @param moment: Naive UTC or timezone-aware instant.
    @return: Hour from 0 to 23.
    """
    aware = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment
    return aware.astimezone(UPLOAD_DAY_ZONE).hour


def is_dispatch_due(delivery_hour: int, now: datetime, last_task_day: date | None) -> bool:
    """@description: Allow one task per Taipei day from the chosen hour on, so a service outage is made up the same day and never on a later day.
    @param delivery_hour: The subscriber's Taipei hour.
    @param now: Current instant.
    @param last_task_day: Taipei day of the subscriber's most recent task, if any.
    @return: True when today has no task yet and the hour has been reached.
    """
    return taipei_day(now) != last_task_day and taipei_hour(now) >= delivery_hour


def notice_allowed(streak_before: int, last_notice_day: date | None, today: date) -> bool:
    """@description: Limit notices to one per Taipei day and to the first consecutive days, so a user who never tops up is not nagged.
    @param streak_before: Consecutive notice days immediately before today.
    @param last_notice_day: Day of the most recent notice, if any.
    @param today: Current Taipei day.
    @return: True when a notice may be sent today.
    """
    return last_notice_day != today and streak_before < NOTICE_SILENCE_AFTER_DAYS


def build_daily_dm_text(youtube_url: str, recommender_display_name: str | None) -> str:
    """@description: Compose the daily message with the verified link and only the half-masked recommender name.
    @param youtube_url: Verified delivery link.
    @param recommender_display_name: Live display name of the recommender, or None.
    @return: Message body that never contains the full name or an identifier.
    """
    return f"今日推薦：{youtube_url}\n推薦者：{mask_display_name(recommender_display_name)}"


class DailyOutcome(Enum):
    """Name what one scheduled task did for one subscriber."""

    DELIVERED = "delivered"  # [accepted DM]: The recommendation was sent and charged.
    FAILED = "failed"  # [proven unsent]: The charge was refunded by the shared refund step.
    UNCERTAIN = "uncertain"  # [unknown result]: Kept charged; never resent or refunded automatically.
    IN_PROGRESS = "in_progress"  # [open exchange]: Nothing was charged or sent; the day's task is used up.
    NOTICE_SENT = "notice_sent"  # [notice DM]: A not-enough or no-candidate message was accepted.
    NOTICE_SILENCED = "notice_silenced"  # [throttled]: Three notice days passed, so nothing was sent.
    NOTICE_FAILED = "notice_failed"  # [notice not confirmed]: Not retried the same day.


@dataclass(frozen=True)
class DailyUserResult:
    """Carry the outcome of one subscriber's task in a sweep."""

    discord_user_id: int  # [subscriber]: Whose task ran.
    outcome: DailyOutcome  # [task result]: What the sweep did.
    recommendation_id: int | None  # [reservation]: Present only when a song was reserved.


@dataclass(frozen=True)
class DailySweepReport:
    """Carry every task that ran in one sweep."""

    results: tuple[DailyUserResult, ...]  # [ran this pass]: Users that were not due or already served do not appear.


_OUTCOME_OF_STATUS = {DeliveryStatus.DELIVERED: DailyOutcome.DELIVERED, DeliveryStatus.FAILED: DailyOutcome.FAILED,
                      DeliveryStatus.UNCERTAIN: DailyOutcome.UNCERTAIN, DeliveryStatus.IN_PROGRESS: DailyOutcome.IN_PROGRESS,
                      DeliveryStatus.HELD: DailyOutcome.IN_PROGRESS}  # [state mapping]: A held order is reported as still open.
_DELIVERY_OF_KIND = {DmKind.DELIVERED: "sent", DmKind.NOT_SENT: "failed", DmKind.UNKNOWN: "uncertain"}  # [notice record]: What the notice row stores.


class _PreparedTextSender:
    """Send the prepared daily text in place of the shared exchange wording, through the same classified adapter."""

    def __init__(self, inner: DirectMessageSender, text: str) -> None:
        """@description: Keep the adapter and the daily message body.
        @param self: Wrapper instance.
        @param inner: Real or fake direct-message adapter.
        @param text: Daily body with the link and the masked name.
        @return: None.
        """
        self._inner = inner
        self._text = text  # [daily body]: Replaces whatever text the shared delivery step proposes.

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Forward the prepared daily body to the adapter.
        @param self: Wrapper instance.
        @param discord_user_id: Recipient.
        @param text: Shared exchange text, deliberately ignored.
        @return: The adapter's classified outcome.
        """
        return await self._inner.send(discord_user_id, self._text)


async def run_daily_sweep(repository: Any, sender: DirectMessageSender, resolve_display_name: Callable[[int], Awaitable[str | None]],
                          now: datetime, rng: Any, new_token: Callable[[], str] = lambda: uuid.uuid4().hex) -> DailySweepReport:
    """@description: Serve every due subscriber once for the current Taipei day, isolating each person's failure from the others.
    @param repository: Repository exposing the daily task steps and the shared exchange delivery steps.
    @param sender: Direct-message adapter awaited on the event loop.
    @param resolve_display_name: Live name lookup for the recommender; its result is never stored.
    @param now: Naive UTC clock.
    @param rng: Injected random source for the draw.
    @param new_token: Source of unique delivery attempt tokens.
    @return: The tasks that ran in this pass.
    """
    await asyncio.to_thread(repository.recover_interrupted_deliveries, now)  # [lease sweep]: Expired sending rows become uncertain, never resent.
    await asyncio.to_thread(repository.retry_pending_refunds, now)  # [refund retry]: A failed earlier refund is retried, still once.
    candidates = await asyncio.to_thread(repository.daily_schedule_candidates)
    results = []
    for discord_user_id, delivery_hour, last_task_day in candidates:
        if not is_dispatch_due(delivery_hour, now, last_task_day):
            continue
        result = await _serve_one(repository, sender, resolve_display_name, discord_user_id, now, rng, new_token)
        if result is not None:
            results.append(result)
    return DailySweepReport(tuple(results))


async def _serve_one(repository: Any, sender: DirectMessageSender, resolve: Callable[[int], Awaitable[str | None]], discord_user_id: int,
                     now: datetime, rng: Any, new_token: Callable[[], str]) -> DailyUserResult | None:
    """@description: Run one subscriber's task and contain an ordinary failure so the remaining subscribers are still served.
    @param repository: Repository with the daily and exchange steps.
    @param sender: Direct-message adapter.
    @param resolve: Live name lookup.
    @param discord_user_id: Due subscriber.
    @param now: Naive UTC clock.
    @param rng: Injected random source.
    @param new_token: Attempt token source.
    @return: The task result, or None when the task was already used or failed before any outcome existed.
    """
    try:
        start = await _begin_with_one_replay(repository, discord_user_id, now, rng)
        return await _carry_out(repository, sender, resolve, start, now, new_token)
    except Exception as error:
        LOGGER.error("Daily task failed for one subscriber: %s", type(error).__name__)  # [class only]: No identifier or payload.
        return None


async def _begin_with_one_replay(repository: Any, discord_user_id: int, now: datetime, rng: Any) -> Any:
    """@description: Claim the day's task, repeating once after an unknown commit so a commit that landed is found instead of lost.
    @param repository: Repository exposing begin_daily_task.
    @param discord_user_id: Due subscriber.
    @param now: Naive UTC clock.
    @param rng: Injected random source.
    @return: The task start describing what to do next.
    """
    try:
        return await asyncio.to_thread(repository.begin_daily_task, discord_user_id, now, rng)
    except CommitOutcomeUnknown:
        return await asyncio.to_thread(repository.begin_daily_task, discord_user_id, now, rng)  # [same day key]: The landed row is found, never doubled.


async def _carry_out(repository: Any, sender: DirectMessageSender, resolve: Callable[[int], Awaitable[str | None]], start: Any,
                     now: datetime, new_token: Callable[[], str]) -> DailyUserResult | None:
    """@description: Act on the claimed task: nothing, a notice, a used-up day, or the delivery of a reserved song.
    @param repository: Repository with the daily and exchange steps.
    @param sender: Direct-message adapter.
    @param resolve: Live name lookup.
    @param start: Claimed task description from the repository.
    @param now: Naive UTC clock.
    @param new_token: Attempt token source.
    @return: The task result, or None when the day's task was already used.
    """
    if start.kind == "skipped":
        return None
    if start.kind == "in_progress":
        return DailyUserResult(start.discord_user_id, DailyOutcome.IN_PROGRESS, None)
    if start.kind == "notice":
        return await _send_notice(repository, sender, start)
    return await _deliver_reserved(repository, sender, resolve, start, now, new_token)


async def _send_notice(repository: Any, sender: DirectMessageSender, start: Any) -> DailyUserResult:
    """@description: Send the not-enough or no-candidate message once, or stay silent after the throttle, and record what happened.
    @param repository: Repository exposing finish_daily_notice.
    @param sender: Direct-message adapter.
    @param start: Claimed notice task.
    @return: Notice outcome; nothing is charged either way.
    """
    if start.silenced:
        return DailyUserResult(start.discord_user_id, DailyOutcome.NOTICE_SILENCED, None)
    kind = await _notice_dm_kind(sender, start.discord_user_id, NOTICE_TEXT_BY_KIND[start.notice_kind])
    delivery = _DELIVERY_OF_KIND[kind]  # [recorded evidence]: sent, failed or uncertain.
    await asyncio.to_thread(repository.finish_daily_notice, start.discord_user_id, start.notice_day, delivery)
    outcome = DailyOutcome.NOTICE_SENT if kind is DmKind.DELIVERED else DailyOutcome.NOTICE_FAILED
    return DailyUserResult(start.discord_user_id, outcome, None)


async def _notice_dm_kind(sender: DirectMessageSender, discord_user_id: int, text: str) -> DmKind:
    """@description: Send one notice DM and classify it, treating any ordinary exception or timeout as unknown.
    @param sender: Direct-message adapter.
    @param discord_user_id: Recipient.
    @param text: Link-free notice body.
    @return: What the attempt proves about delivery.
    """
    try:
        result = await asyncio.wait_for(sender.send(discord_user_id, text), DM_TIMEOUT_SECONDS)
    except Exception as error:
        LOGGER.warning("Daily notice outcome unknown: %s", type(error).__name__)  # [class only]: No payload.
        return DmKind.UNKNOWN
    return result.kind


async def _deliver_reserved(repository: Any, sender: DirectMessageSender, resolve: Callable[[int], Awaitable[str | None]], start: Any,
                            now: datetime, new_token: Callable[[], str]) -> DailyUserResult:
    """@description: Deliver the reserved song with the shared claim, send and record steps, refund only a proven non-delivery, and mirror the result on the task row.
    @param repository: Repository with the exchange and daily steps.
    @param sender: Direct-message adapter.
    @param resolve: Live name lookup.
    @param start: Claimed reservation task.
    @param now: Naive UTC clock.
    @param new_token: Attempt token source.
    @return: Delivery outcome of the task.
    """
    name = await _recommender_name(repository, resolve, start.recommendation_id)
    prepared = _PreparedTextSender(sender, build_daily_dm_text(start.youtube_url, name))
    outcome = await deliver_recommendation(repository, prepared, start.recommendation_id, now, new_token())
    await refund_if_unsent(repository, outcome, now)
    await asyncio.to_thread(repository.finish_daily_dispatch, start.recommendation_id, outcome.status.value)
    return DailyUserResult(start.discord_user_id, _OUTCOME_OF_STATUS[outcome.status], start.recommendation_id)


async def _recommender_name(repository: Any, resolve: Callable[[int], Awaitable[str | None]], recommendation_id: int) -> str | None:
    """@description: Look up the recommender's current display name live, falling back to no name when Discord cannot give one.
    @param repository: Repository exposing daily_recommender_id.
    @param resolve: Live name lookup.
    @param recommendation_id: Reserved recommendation.
    @return: Display name, or None for the anonymous placeholder.
    """
    recommender_id = await asyncio.to_thread(repository.daily_recommender_id, recommendation_id)
    if recommender_id is None:
        return None
    try:
        return await asyncio.wait_for(resolve(recommender_id), NAME_LOOKUP_TIMEOUT_SECONDS)
    except Exception as error:
        LOGGER.warning("Recommender name lookup failed: %s", type(error).__name__)  # [class only]: Neither the id nor any name.
        return None
