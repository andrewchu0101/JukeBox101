"""Run the daily sweep repeatedly inside the Gateway process while the daily switch is on.

It needs the submission repository and a connected Discord client; each pass is isolated so a failed pass never stops the next one, and a stop request ends the loop between passes.
"""

import asyncio
import logging
import random
from datetime import datetime, timezone
from functools import partial
from typing import Any

from bot.daily_schedule import run_daily_sweep
from bot.discord_exchange import DiscordDirectMessageSender

LOGGER = logging.getLogger(__name__)
SWEEP_INTERVAL_SECONDS = 60.0  # [scan cadence]: A task becomes due on the hour, so a one-minute scan delivers within about a minute of it.


async def run_daily_loop(repository: Any, client: Any, stop_requested: asyncio.Event, interval_seconds: float = SWEEP_INTERVAL_SECONDS) -> None:
    """@description: Scan for due daily tasks until a stop is requested, so scheduled delivery follows the process lifetime.
    @param repository: Submission repository exposing the daily and exchange steps.
    @param client: Connected Discord client used for DMs and live name lookups.
    @param stop_requested: Event set when the process is shutting down.
    @param interval_seconds: Pause between scans.
    @return: None.
    """
    sender = DiscordDirectMessageSender(client)
    lookup = partial(_display_name, client)
    while not stop_requested.is_set():
        try:
            await run_daily_sweep(repository, sender, lookup, datetime.now(timezone.utc).replace(tzinfo=None), random.Random())  # [naive UTC]: The repository clock.
        except Exception as error:
            LOGGER.error("Daily sweep failed: %s", type(error).__name__)  # [class only]: The next pass retries.
        await _wait_or_stop(stop_requested, interval_seconds)


async def _wait_or_stop(stop_requested: asyncio.Event, interval_seconds: float) -> None:
    """@description: Sleep until the next scan, waking early when shutdown starts.
    @param stop_requested: Shutdown event.
    @param interval_seconds: Longest pause.
    @return: None.
    """
    try:
        await asyncio.wait_for(stop_requested.wait(), interval_seconds)
    except TimeoutError:
        return  # [normal tick]: The interval elapsed without a stop request.


async def _display_name(client: Any, discord_user_id: int) -> str | None:
    """@description: Fetch the recommender's current display name live; the caller masks it and never stores it.
    @param client: Connected Discord client.
    @param discord_user_id: Recommender.
    @return: Current display name, or None when Discord gives none.
    """
    user = await client.fetch_user(discord_user_id)
    return user.display_name
