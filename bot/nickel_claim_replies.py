"""Word the outcome of one /claim for the requester, naming each grant source and never claiming an unrecorded grant.

Presentation only: the grant decision and the database write live in the policy and store modules.
"""

from bot.database.nickel_grant_store import ClaimResult, ClaimStatus
from bot.nickel_grant_policy import GrantKind

SOURCE_LABELS = {GrantKind.ENTRY: "入門額度", GrantKind.WEEKLY: "本週定期額度"}  # [reply labels]: One label per approved source.


def format_claim_reply(result: ClaimResult) -> str:
    """@description: Turn a claim outcome into a private reply that matches exactly what was recorded.
    @param result: Outcome returned by the claim store.
    @return: Reply text in Traditional Chinese, naming sources and amounts only when they were written.
    """
    if result.status is ClaimStatus.DISABLED:
        return "Nickel 領取功能尚未開放，本次未發放。"
    if result.status is ClaimStatus.ALREADY_CLAIMED:
        return "本週可領取的 Nickel 都已領取，本次未發放。"
    if result.status is ClaimStatus.REPLAYED:
        return f"此領取請求已處理（週期 {result.period}），本次未重複發放。"
    parts = [f"{SOURCE_LABELS[line.kind]} {line.amount} Nickel" for line in result.lines]
    total = sum(line.amount for line in result.lines)
    return "已領取：" + "；".join(parts) + f"，共 {total} Nickel。"
