"""Remind the Owner by direct message that cases wait in /owner list, at most once per interval and only when something new arrived.

Input is the Owner review service (to count the queue), a direct-message sender and the Owner's Discord identity; output is one short text DM to the Owner only.
The message carries a count and the command to run, never a title, channel, official value or link, so it exposes nothing the Owner would not see in /owner list.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from bot.exchange_delivery import DmKind

LOGGER = logging.getLogger(__name__)
NOTICE_INTERVAL_SECONDS = 900.0  # [anti-flood]: At most one reminder per this long, however many cases arrive.
QUEUE_SCAN_LIMIT = 20  # [read bound]: Cases counted per check; more than this is reported as "more than".
OWNER_LIST_COMMAND = "/owner list"  # [call to action]: The only thing the reminder asks the Owner to do.


def notice_text(waiting_count: int, more_waiting: bool) -> str:
    """@description: Word the reminder so the Owner knows how many cases wait and which command shows them.
    @param waiting_count: Cases counted in this check.
    @param more_waiting: True when the queue is longer than the counted slice.
    @return: One link-free Traditional Chinese sentence.
    """
    amount = f"{waiting_count} 筆以上" if more_waiting else f"{waiting_count} 筆"
    return f"JukeBox101：有 {amount}案件等你處理（待審、待確認版本或衝突）。請到伺服器輸入 {OWNER_LIST_COMMAND} 查看。"


class OwnerNotifier:
    """Decide when the Owner needs a reminder and send it; holds only the last announced position and send time in memory."""

    def __init__(self, service_provider: Callable[[], Any], sender: Any, owner_id: int, *,
                 interval_seconds: float = NOTICE_INTERVAL_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        """@description: Bind collaborators without any I/O.
        @param self: New notifier.
        @param service_provider: Returns the Owner review service whose list_pending counts the queue.
        @param sender: Direct-message adapter with an async send(discord_user_id, text) returning a DmResult.
        @param owner_id: The only recipient.
        @param interval_seconds: Minimum time between two reminders.
        @param clock: Monotonic clock, injected for tests.
        @return: None.
        """
        self._service_provider, self._sender, self._owner_id = service_provider, sender, owner_id
        self._interval, self._clock = interval_seconds, clock
        self._announced_sequence = 0  # [already told]: Highest queue position the Owner was reminded about; lost at restart, which only repeats one reminder.
        self._last_attempt: float | None = None  # [throttle]: Time of the last attempt, successful or not, so a closed DM is not retried every round.

    async def notify_if_needed(self) -> bool:
        """@description: Send one reminder when the queue holds a case newer than the last reminder and the interval has passed.
        @param self: Notifier.
        @return: True only when a reminder was accepted by Discord.
        """
        try:
            page = await asyncio.to_thread(self._service_provider().list_pending, self._owner_id, limit=QUEUE_SCAN_LIMIT)
        except Exception as error:  # [scheduled-job boundary]: Class name only; the next round retries.
            LOGGER.error("Owner queue check failed: %s", type(error).__name__)
            return False
        newest = max((item.accepted_sequence for item in page.items), default=0)
        if newest <= self._announced_sequence or self._throttled():
            return False
        self._last_attempt = self._clock()
        result = await self._sender.send(self._owner_id, notice_text(len(page.items), page.next_after_sequence is not None))
        if result.kind is not DmKind.DELIVERED:
            LOGGER.warning("Owner reminder was not delivered: %s", result.reason)
            return False
        self._announced_sequence = newest
        return True

    def _throttled(self) -> bool:
        """@description: Tell whether the last attempt is too recent for another one.
        @param self: Notifier.
        @return: True while the interval has not passed.
        """
        return self._last_attempt is not None and self._clock() - self._last_attempt < self._interval
