"""Dispatch one approved free redelivery: claim first under the writer gate, send outside any transaction, then record only against that claim.

A grant that is not open sends nothing. An unknown result is recorded as unknown and is never sent again automatically; a proven non-delivery
waits for the Owner. The message text names the free redelivery, so a compensation is never mistaken for a paid exchange.
"""

import asyncio
from typing import Any

from bot.exchange_delivery import DM_TIMEOUT_SECONDS, DmKind, DmResult

_FACT_BY_KIND = {DmKind.DELIVERED: "sent", DmKind.NOT_SENT: "not_sent", DmKind.UNKNOWN: "unknown"}  # [classified evidence -> recorded result].


async def _send_free_redelivery(sender: Any, discord_user_id: int, youtube_url: str) -> DmResult:
    """@description: Await one direct-message adapter with the same bound and classification as a normal delivery.
    @param sender: Direct-message adapter.
    @param discord_user_id: Recipient.
    @param youtube_url: Verified link for the free redelivery.
    @return: Classified result; a timeout or an ordinary failure is unknown, because the message may have been accepted.
    """
    try:
        return await asyncio.wait_for(sender.send(discord_user_id, f"免費補送：你的歌曲連結：{youtube_url}"), DM_TIMEOUT_SECONDS)
    except TimeoutError:
        return DmResult.unknown("timeout")
    except Exception:
        return DmResult.unknown("adapter_error")


async def deliver_grant(repository: Any, sender: Any, grant_id: int, now: Any, claim_token: str) -> str:
    """@description: Claim one open grant, make at most one direct-message attempt, and record its classified result against that claim.
    @param repository: Repository exposing the grant claim and result transitions.
    @param sender: Direct-message adapter.
    @param grant_id: Grant to dispatch.
    @param now: Attempt time, naive UTC.
    @param claim_token: Unique token of this attempt.
    @return: The stored grant state; a grant that was not open reports its state and sends nothing.
    """
    state, claim = await asyncio.to_thread(repository.claim_grant, grant_id, claim_token, now)
    if claim is None:
        return state
    result = await _send_free_redelivery(sender, claim["discord_user_id"], claim["youtube_url"])  # [network outside the gate]: The claim is already committed.
    message_id = result.message_id if result.kind is DmKind.DELIVERED else None
    return await asyncio.to_thread(repository.record_grant_delivery, grant_id, claim_token, _FACT_BY_KIND[result.kind], message_id, now)
