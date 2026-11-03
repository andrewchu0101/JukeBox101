"""Verify /history also lists the recommendations the caller received, and labels every section so own submissions and received songs are never mixed up.

Inputs are a fake submission repository; no database or Discord connection is made.
"""

import asyncio
import re
from datetime import datetime, timezone
from unittest.mock import MagicMock

from bot.models import MAX_REPLY_LENGTH
from bot.services import MusicService

BARE_LINK = re.compile(r"(?<!<)https?://")  # [bare link]: An http link not directly preceded by an angle bracket, which Discord would expand.
OWN_HEADER = "最近本人投稿與目前審核狀態："  # [existing header]: Unchanged so the own-submission section keeps its wording.
RECEIVED_HEADER = "你收到的推薦"  # [new section label]: Marks songs that other members recommended to the caller.


def _submission_rows(count: int) -> list[dict]:
    """@description: Build submission rows shaped like the repository projection.
    @param count: Number of rows.
    @return: Rows with distinct video ids.
    """
    return [dict(operation_id=f"discord:{1000 + number}", video_id=f"Own_123-X{number:02d}", review_status="approved",
                 reward_status="waiting_identity") for number in range(count)]


def _received_rows(count: int) -> list[dict]:
    """@description: Build received-recommendation rows shaped like the repository projection, newest first.
    @param count: Number of rows.
    @return: Rows with a link and a delivery time.
    """
    return [dict(youtube_url=f"https://www.youtube.com/watch?v=Rcv_123-X{number:02d}",
                 delivered_at=datetime(2026, 11, 3, 12, number, tzinfo=timezone.utc)) for number in range(count)]


def _reply(submissions: int, received: int) -> str:
    """@description: Ask the Beta service for history over a repository holding the given rows.
    @param submissions: Own submission rows returned.
    @param received: Received recommendation rows returned.
    @return: The reply text.
    """
    repository = MagicMock()
    repository.history.return_value = _submission_rows(submissions)
    repository.received_history.return_value = _received_rows(received)
    return asyncio.run(MusicService(MagicMock(), submission_repository=repository).history(123))


def test_both_sections_are_shown_and_labelled() -> None:
    """@description: Own submissions and received songs each sit under their own label.
    @return: None.
    """
    reply = _reply(2, 2)
    assert OWN_HEADER in reply and RECEIVED_HEADER in reply
    assert reply.index(OWN_HEADER) < reply.index("Own_123-X00") < reply.index(RECEIVED_HEADER) < reply.index("Rcv_123-X00")


def test_every_received_line_is_tagged_and_links_do_not_expand() -> None:
    """@description: A received line says it was received, shows its time in UTC, and wraps the link.
    @return: None.
    """
    reply = _reply(1, 3)
    received_lines = [line for line in reply.splitlines() if "Rcv_123" in line]
    assert len(received_lines) == 3 and all(line.startswith("收到 · ") and "<https://www.youtube.com/watch?v=" in line for line in received_lines)
    assert "2026-11-03 12:00" in received_lines[0] and not BARE_LINK.search(reply)


def test_only_five_received_songs_are_shown() -> None:
    """@description: The received section has the same display size as the submission section.
    @return: None.
    """
    reply = _reply(1, 9)
    assert reply.count("Rcv_123") == 5 and "Rcv_123-X04" in reply and "Rcv_123-X05" not in reply


def test_a_caller_with_only_received_songs_still_sees_them() -> None:
    """@description: Having never submitted does not hide the received list.
    @return: None.
    """
    reply = _reply(0, 2)
    assert RECEIVED_HEADER in reply and OWN_HEADER not in reply and reply.count("Rcv_123") == 2


def test_a_caller_with_only_submissions_sees_no_empty_received_section() -> None:
    """@description: No received songs means no received heading.
    @return: None.
    """
    reply = _reply(2, 0)
    assert OWN_HEADER in reply and RECEIVED_HEADER not in reply


def test_a_caller_with_nothing_keeps_the_previous_empty_message() -> None:
    """@description: The existing wording for no history is unchanged.
    @return: None.
    """
    assert _reply(0, 0) == "目前沒有你的投稿紀錄。"


def test_the_reply_stays_within_the_discord_limit() -> None:
    """@description: Two full sections still fit one message.
    @return: None.
    """
    assert len(_reply(10, 10)) <= MAX_REPLY_LENGTH


def test_an_unreadable_stored_link_is_shown_as_pending_not_echoed() -> None:
    """@description: A stored value that is not a valid YouTube link never appears raw.
    @return: None.
    """
    repository = MagicMock()
    repository.history.return_value = []
    repository.received_history.return_value = [dict(youtube_url="javascript:alert(1)", delivered_at=datetime(2026, 11, 3, tzinfo=timezone.utc))]
    reply = asyncio.run(MusicService(MagicMock(), submission_repository=repository).history(123))
    assert "網址待確認" in reply and "javascript" not in reply
