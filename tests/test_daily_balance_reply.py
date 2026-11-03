"""Verify the /daily reply and the daily direct messages tell the requester their remaining Nickel without ever guessing one.

Offline: a recording fake repository and the real service and message builders; no Discord, database or network is involved.
"""

import asyncio
from unittest.mock import MagicMock

from bot.daily_schedule import build_daily_dm_text
from bot.services import MusicService
from tests.test_daily_command_entry import PreferenceRepository

LINK = "https://www.youtube.com/watch?v=abcdefghijk"  # [verified delivery link]: The only link a DM may carry.


class RepositoryWithBalance(PreferenceRepository):
    """@description: Add the requester's own balance read to the recording repository.
    @param balance: Credits the fake account holds.
    @return: Repository for the service.
    """

    def __init__(self, balance: int | None) -> None:
        """@description: Start with no saves and the given balance.
        @param self: Repository instance.
        @param balance: Credits held, or None for an unknown account.
        @return: None.
        """
        super().__init__()
        self.balance = balance  # [account state]: What user_exchange_balance reports.

    def user_exchange_balance(self, discord_user_id: int):
        """@description: Report the requester's own balance like the real repository.
        @param self: Repository instance.
        @param discord_user_id: Requester.
        @return: Credits held, or None.
        """
        return self.balance


def daily_reply(repository, enabled: bool, hour=None) -> str:
    """@description: Run /daily through the real service with the formal switch on.
    @param repository: Fake submission repository.
    @param enabled: Requested subscription state.
    @param hour: Typed hour or None.
    @return: Reply text.
    """
    service = MusicService(MagicMock(), submission_repository=repository, formal_daily=True)
    return asyncio.run(service.daily(123, enabled, hour))


def test_turning_daily_on_tells_the_remaining_nickel() -> None:
    """@description: The confirmation ends with the balance so the user sees what the daily draw will spend from.
    @return: None.
    """
    assert "目前可用 Nickel：3" in daily_reply(RepositoryWithBalance(3), True)


def test_turning_daily_off_also_tells_the_remaining_nickel() -> None:
    """@description: Both directions of the command report the balance.
    @return: None.
    """
    assert "目前可用 Nickel：0" in daily_reply(RepositoryWithBalance(0), False)


def test_an_unreadable_or_unknown_balance_is_omitted_not_guessed() -> None:
    """@description: The saved-preference confirmation stays correct when the balance cannot be shown.
    @return: None.
    """
    assert "Nickel：" not in daily_reply(RepositoryWithBalance(None), True)
    assert "目前可用 Nickel" not in daily_reply(PreferenceRepository(), True)


def test_a_bad_hour_reply_is_unchanged_and_shows_no_balance() -> None:
    """@description: A refused input saves nothing and keeps its guidance text only.
    @return: None.
    """
    assert "目前可用 Nickel" not in daily_reply(RepositoryWithBalance(5), True, 99)


def test_the_daily_message_can_carry_the_remaining_nickel_after_the_charge() -> None:
    """@description: The scheduled message shows the balance that the committed charge left, next to the link and masked name.
    @return: None.
    """
    text = build_daily_dm_text(LINK, "SomeName", 2)
    assert LINK in text and "目前可用 Nickel：2" in text


def test_the_daily_message_without_a_balance_is_unchanged() -> None:
    """@description: Callers that cannot read a balance get the previous text exactly.
    @return: None.
    """
    assert "Nickel" not in build_daily_dm_text(LINK, "SomeName") and build_daily_dm_text(LINK, "SomeName", None) == build_daily_dm_text(LINK, "SomeName")

