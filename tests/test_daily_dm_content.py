"""Offline contract of what a daily direct message contains and how it may be sent (issue #21).

INTERFACE CONTRACT:
    bot/daily_schedule.py
        build_daily_dm_text(youtube_url: str, recommender_display_name: str | None) -> str
            # One DM body: the verified YouTube link plus the recommender's name through mask_display_name. The raw name never appears.
    bot/discord_exchange.py (existing adapter, behaviour extended for every DM it sends, exchange and daily alike)
        DiscordDirectMessageSender.send(...) calls user.send(text, allowed_mentions=discord.AllowedMentions.none()).
        A fake user whose send takes only the text will fail this test on purpose; the contract is "no DM may ever ping".

NOTE for the lead: tests/test_discord_exchange_adapter.py builds users with AsyncMock sends, which accept the extra keyword, so they should stay green.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from bot.daily_mask import ANONYMOUS_NAME, mask_display_name
from bot.daily_schedule import build_daily_dm_text
from bot.discord_exchange import DiscordDirectMessageSender

LINK = "https://www.youtube.com/watch?v=abcdefghijk"  # [verified delivery link]: The only link a DM may carry.
CANARY_NAME = "ZzCanaryRecommenderName"  # [improbable full name]: Must never appear anywhere it is not masked.
RECOMMENDER_ID = 777_000_111_222_333  # [recommender identity]: Must never be shown.


def test_the_dm_carries_the_link_and_only_the_masked_recommender() -> None:
    """@description: The body has the YouTube link, the half-masked name, and neither the full name nor any Discord id.
    @return: None.
    """
    text = build_daily_dm_text(LINK, CANARY_NAME)
    assert LINK in text and mask_display_name(CANARY_NAME) in text
    assert CANARY_NAME not in text and "Canary" not in text and str(RECOMMENDER_ID) not in text


def test_a_missing_or_empty_name_is_shown_as_anonymous() -> None:
    """@description: When Discord gives no usable name, the placeholder is shown instead of nothing or an id.
    @return: None.
    """
    assert ANONYMOUS_NAME in build_daily_dm_text(LINK, None) and ANONYMOUS_NAME in build_daily_dm_text(LINK, "  ")


def test_a_mention_shaped_name_cannot_become_a_mention_in_the_dm() -> None:
    """@description: Even a name that looks like a mention or an everyone ping is masked into inert text.
    @return: None.
    """
    for name in ("<@123456789012345678>", "@everyone", "@here"):
        text = build_daily_dm_text(LINK, name)
        assert name not in text and "<@" not in text and "@everyone" not in text and "@here" not in text


def test_the_dm_is_sent_with_every_mention_disabled() -> None:
    """@description: The adapter must pass allowed_mentions none on the send, so no text can ping anyone even if masking were bypassed.
    @return: None.
    """
    user = MagicMock()
    user.send = AsyncMock(return_value=MagicMock(id=555))
    client = MagicMock()
    client.fetch_user = AsyncMock(return_value=user)
    asyncio.run(DiscordDirectMessageSender(client).send(42, build_daily_dm_text(LINK, "@everyone")))
    sent_mentions = user.send.call_args.kwargs["allowed_mentions"]
    assert sent_mentions.to_dict() == discord.AllowedMentions.none().to_dict()
