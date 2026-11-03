"""Verify the Beta /history shows five submissions and keeps every link clickable without Discord expanding it into a large thumbnail.

Inputs are a fake submission repository; no database or Discord connection is made.
"""

import asyncio
import re
from unittest.mock import MagicMock

from bot.models import HISTORY_LIMIT, SUBMISSION_HISTORY_DISPLAY_LIMIT
from bot.services import MusicService

BARE_LINK = re.compile(r"(?<!<)https?://")  # [bare link]: An http link not directly preceded by an angle bracket, which Discord would expand.


def _submission_rows(count: int) -> list[dict]:
    """@description: Build submission rows shaped like the repository projection, newest first.
    @param count: Number of rows.
    @return: Rows with distinct video ids.
    """
    return [dict(operation_id=f"discord:{1000 + number}", video_id=f"Abc_123-X{number:02d}", review_status="approved",
                 reward_status="waiting_identity") for number in range(count)]


def _history_reply(count: int) -> str:
    """@description: Ask the Beta service for history over a repository holding the given number of rows.
    @param count: Rows the repository returns.
    @return: The reply text.
    """
    submissions = MagicMock()
    submissions.history.return_value = _submission_rows(count)
    return asyncio.run(MusicService(MagicMock(), submission_repository=submissions).history(123))


def test_history_shows_five_submissions():
    """@description: The Beta history is limited to five rows, from a dedicated constant that does not change the shared repository limit.
    @param: none
    @return: None.
    """
    reply = _history_reply(10)
    assert SUBMISSION_HISTORY_DISPLAY_LIMIT == 5 and HISTORY_LIMIT == 10
    assert reply.count("discord:") == 5
    assert "Abc_123-X04" in reply and "Abc_123-X05" not in reply


def test_history_links_are_wrapped_so_discord_does_not_expand_them():
    """@description: Every link is wrapped in angle brackets, so none is a bare http link, yet each stays clickable.
    @param: none
    @return: None.
    """
    reply = _history_reply(5)
    assert reply.count("<https://www.youtube.com/watch?v=") == 5
    assert not BARE_LINK.search(reply)


def test_history_with_fewer_rows_shows_them_all():
    """@description: Fewer than five rows are all shown, and none is invented.
    @param: none
    @return: None.
    """
    assert _history_reply(2).count("discord:") == 2
