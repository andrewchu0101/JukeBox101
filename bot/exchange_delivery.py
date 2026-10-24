"""Deliver a reserved exchange recommendation by direct message, refund only a proven non-delivery, and reply without the link.

Database work runs in worker threads through asyncio.to_thread; the Discord DM and ephemeral calls are awaited on the event loop, so no coroutine is ever handed to a worker and no lock is held during a network call.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Protocol

from bot.database.exchange_delivery_store import RefundContradiction
from bot.database.exchange_reservation import ExchangeStatus
from bot.database.submission_transaction import CommitOutcomeUnknown

LOGGER = logging.getLogger(__name__)
DM_TIMEOUT_SECONDS = 20.0  # [bounded attempt]: Must stay far below DELIVERY_LEASE so a live attempt is never mistaken for a lost one.
NO_CANDIDATE_TEXT = "目前沒有符合條件的歌曲"  # [reviewed wording]: S25 reply when no eligible song exists; nothing is charged.
INSUFFICIENT_TEXT = "點數不足，交換一首歌曲需要 1 Nickel。"  # [reviewed wording]: Shown before any candidate work is reported.


class DeliveryStatus(Enum):
    """Name the delivery state the requester is told about, separately from any financial treatment."""

    DELIVERED = "delivered"  # [accepted DM]: Discord accepted the message and returned its identifier.
    FAILED = "failed"  # [proven unsent]: Refund is attempted by the same flow and recorded separately.
    UNCERTAIN = "uncertain"  # [unknown outcome]: Kept with its association; never refunded or resent automatically.
    IN_PROGRESS = "in_progress"  # [claimed elsewhere]: Another attempt owns the delivery; this entry sends nothing.


class DmKind(Enum):
    """Classify one direct-message attempt by what it proves about delivery."""

    DELIVERED = "delivered"  # [accepted]: Only this kind may record a delivery.
    NOT_SENT = "not_sent"  # [proven unsent]: The adapter returned an explicit non-delivery.
    UNKNOWN = "unknown"  # [timeout, disconnect or exception]: Cannot prove either outcome.


@dataclass(frozen=True)
class DmResult:
    """Carry one direct-message outcome from an adapter to the delivery state machine."""

    kind: DmKind  # [delivery evidence]: Decides the recorded state.
    message_id: int | None  # [accepted identifier]: Present only for a delivered message.
    reason: str  # [sanitized cause]: Never a token, payload or raw exception text.

    @classmethod
    def delivered(cls, message_id: int) -> "DmResult":
        """@description: Build the accepted outcome that carries Discord's message identifier.
        @param cls: Result type being built.
        @param message_id: Identifier returned by the accepted direct message.
        @return: Delivered result.
        """
        return cls(DmKind.DELIVERED, message_id, "accepted")

    @classmethod
    def not_sent(cls, reason: str) -> "DmResult":
        """@description: Build the explicit unsent outcome for failures that prove no message was accepted.
        @param cls: Result type being built.
        @param reason: Sanitized proof category.
        @return: Not-sent result.
        """
        return cls(DmKind.NOT_SENT, None, reason)

    @classmethod
    def unknown(cls, reason: str) -> "DmResult":
        """@description: Build the uncertain outcome for timeouts, disconnects and any unexplained failure.
        @param cls: Result type being built.
        @param reason: Sanitized category.
        @return: Unknown result.
        """
        return cls(DmKind.UNKNOWN, None, reason)


class DirectMessageSender(Protocol):
    """Send one direct message and report what the attempt proves; production wraps the discord.py call here."""

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Attempt one direct message to one recipient on the event loop.
        @param self: Adapter instance.
        @param discord_user_id: Reserved requester.
        @param text: Message body carrying the verified link.
        @return: Classified outcome.
        """


class EphemeralReplier(Protocol):
    """Send a requester-only reply; failure here never changes the delivery result."""

    async def send_ephemeral(self, text: str) -> None:
        """@description: Show one private reply to the requester on the event loop.
        @param self: Reply adapter instance.
        @param text: Link-free reply text.
        @return: None.
        """


@dataclass(frozen=True)
class DeliveryOutcome:
    """Carry the recorded delivery state of one reservation."""

    status: DeliveryStatus  # [recorded state]: The state the database now holds.
    recommendation_id: int  # [operation code]: Shown to the requester for uncertain and failed outcomes.
    message_id: int | None  # [accepted DM identifier]: Present only when delivered.


@dataclass(frozen=True)
class ExchangeFlowResult:
    """Carry what the requester is told and whether the reply reached them."""

    reply_text: str  # [ephemeral body]: Never contains the song link.
    reply_failed: bool  # [reply transport]: True means the reply was lost, not the delivery.
    recommendation_id: int | None  # [reservation]: None when nothing was reserved.
    delivery_status: DeliveryStatus | None  # [delivery]: None when no delivery was attempted.
    refund_status: str | None  # [financial state]: refunded, already_released, pending, or None; separate from delivery.


_KIND_TO_STATE = {DmKind.DELIVERED: "delivered", DmKind.NOT_SENT: "failed", DmKind.UNKNOWN: "uncertain"}
_STATE_TO_STATUS = {"delivered": DeliveryStatus.DELIVERED, "failed": DeliveryStatus.FAILED,
                    "uncertain": DeliveryStatus.UNCERTAIN, "sending": DeliveryStatus.IN_PROGRESS}


async def deliver_recommendation(repository: Any, sender: DirectMessageSender, recommendation_id: int, now: datetime,
                                 claim_token: str) -> DeliveryOutcome:
    """@description: Claim one reservation, send its DM outside any transaction, and record the classified outcome.
    @param repository: Repository exposing the claim and record transitions.
    @param sender: Direct-message adapter awaited on the event loop.
    @param recommendation_id: Reservation from the exchange result.
    @param now: Time recorded for the attempt and for an accepted delivery.
    @param claim_token: Unique token of this attempt.
    @return: Recorded outcome; an already started or finished attempt sends nothing.
    """
    state, message_id, claim = await asyncio.to_thread(repository.claim_delivery, recommendation_id, claim_token, now)
    if claim is None:
        return DeliveryOutcome(_STATE_TO_STATUS[state], recommendation_id, message_id)
    result = await _attempt(sender, claim.discord_user_id, claim.youtube_url)  # [network outside lock]: Sending state is already committed.
    stored = await asyncio.to_thread(repository.record_delivery, claim, _KIND_TO_STATE[result.kind],
                                     result.message_id, now if result.kind is DmKind.DELIVERED else None)
    accepted = result.message_id if stored == "delivered" else None
    return DeliveryOutcome(_STATE_TO_STATUS[stored], recommendation_id, accepted)


async def _attempt(sender: DirectMessageSender, discord_user_id: int, youtube_url: str) -> DmResult:
    """@description: Await the adapter and convert any ordinary exception into an uncertain outcome, since the message may have been sent.
    @param sender: Direct-message adapter.
    @param discord_user_id: Recipient.
    @param youtube_url: Verified link for the message body.
    @return: Classified result; cancellation and process interruption are not caught and leave the attempt in sending.
    """
    try:
        return await asyncio.wait_for(sender.send(discord_user_id, f"你的交換推薦：{youtube_url}"), DM_TIMEOUT_SECONDS)  # [DM only]: No link in ephemeral replies.
    except TimeoutError:
        return DmResult.unknown("timeout")  # [timed-out attempt]: The request may already have been accepted.
    except Exception as error:
        LOGGER.warning("Direct message outcome unknown: %s", type(error).__name__)  # [sanitized log]: Class name only.
        return DmResult.unknown("adapter_exception")


async def exchange_and_deliver(repository: Any, sender: DirectMessageSender, replier: EphemeralReplier, discord_user_id: int,
                               request_key: str, rng: Any, now: datetime,
                               new_token: Callable[[], str] = lambda: uuid.uuid4().hex) -> ExchangeFlowResult:
    """@description: Run one exchange request end to end and report the outcome to the requester without the link.
    @param repository: Repository exposing exchange, claim, record and refund transitions.
    @param sender: Direct-message adapter.
    @param replier: Ephemeral reply sender.
    @param discord_user_id: Requesting user.
    @param request_key: Technical idempotency key for retries.
    @param rng: Injected random source for the draw.
    @param now: Time recorded for the attempt.
    @param new_token: Source of unique attempt tokens.
    @return: Reply text, reply transport status, and the recorded delivery and refund state.
    """
    await asyncio.to_thread(repository.recover_interrupted_deliveries, now)  # [lease sweep]: Expired sending rows are found on the next request too.
    await asyncio.to_thread(repository.retry_pending_refunds, now)  # [refund retry]: A failed earlier refund is retried, still once.
    exchange = await _exchange_with_one_replay(repository, discord_user_id, request_key, rng)  # [reservation first]: Debit and reservation commit together.
    if exchange.status is ExchangeStatus.NO_CANDIDATE:
        return await _finish(replier, ExchangeFlowResult(NO_CANDIDATE_TEXT, False, None, None, None))
    if exchange.status is ExchangeStatus.INSUFFICIENT_CREDITS:
        return await _finish(replier, ExchangeFlowResult(INSUFFICIENT_TEXT, False, None, None, None))
    outcome = await deliver_recommendation(repository, sender, exchange.recommendation_id, now, new_token())
    refund = await _refund_if_unsent(repository, outcome, now)
    flow = ExchangeFlowResult(_delivery_text(outcome, refund), False, outcome.recommendation_id, outcome.status, refund)
    return await _finish(replier, flow)


async def _exchange_with_one_replay(repository: Any, discord_user_id: int, request_key: str, rng: Any):
    """@description: Repeat the same durable request once after an unknown commit, so a commit that already happened is found by its key instead of debited again.
    @param repository: Repository exposing the exchange transition.
    @param discord_user_id: Requesting user.
    @param request_key: Durable technical identity, reused unchanged for the replay.
    @param rng: Injected random source.
    @return: Exchange outcome of the attempt that confirmed the durable state.
    """
    try:
        return await asyncio.to_thread(repository.exchange, discord_user_id, request_key, rng)
    except CommitOutcomeUnknown as first_unknown:
        try:
            return await asyncio.to_thread(repository.exchange, discord_user_id, request_key, rng)  # [same key, fresh connection]: Exactly one repeat.
        except Exception as replay_error:
            unknown = CommitOutcomeUnknown("Replay after an unknown commit did not confirm the outcome")
            unknown.add_note(f"replay failed with {type(replay_error).__name__}")  # [class only]: Never the message, which may echo database detail.
            raise unknown from first_unknown  # [original kept]: The first unknown commit remains the cause.


async def _refund_if_unsent(repository: Any, outcome: DeliveryOutcome, now: datetime) -> str | None:
    """@description: Refund only a reservation the delivery step recorded as proven unsent; any refund failure stays pending and visible.
    @param repository: Repository exposing the refund transition.
    @param outcome: Recorded delivery outcome.
    @param now: Release time.
    @return: Refund state, or None when no refund applies.
    """
    if outcome.status is not DeliveryStatus.FAILED:
        return None  # [proof gate]: Only failed deliveries may refund; delivered, uncertain and in-progress never do.
    try:
        result = await asyncio.to_thread(repository.refund_undelivered, outcome.recommendation_id, now)
    except RefundContradiction:
        LOGGER.error("Refund contradiction for reservation %s", outcome.recommendation_id)  # [stop]: Needs review, never a guessed repair.
        return "contradiction"
    except Exception as error:
        LOGGER.error("Refund pending for reservation: %s", type(error).__name__)  # [diagnostic]: Class name only, no payload.
        return "pending"
    return result.status


def _delivery_text(outcome: DeliveryOutcome, refund: str | None) -> str:
    """@description: Word the recorded delivery and refund state for the requester without any link.
    @param outcome: Recorded delivery outcome.
    @param refund: Refund state for failed deliveries, otherwise None.
    @return: Link-free reply text with the operation code.
    """
    code = f"操作 {outcome.recommendation_id}"
    if outcome.status is DeliveryStatus.DELIVERED:
        return f"已將推薦私訊給你，請查看私訊。{code}"
    if outcome.status is DeliveryStatus.FAILED:
        if refund in ("refunded", "already_released"):
            return f"私訊未能送出，已退回 1 點。{code}"
        if refund == "contradiction":
            return f"私訊未能送出，此筆紀錄需人工查核，尚未退款。{code}"
        return f"私訊未能送出，退款待處理，請稍後查詢。{code}"
    if outcome.status is DeliveryStatus.UNCERTAIN:
        return f"私訊送出結果未知，系統已保留此筆紀錄，不會自動重送；扣點與交付待確認。{code}"
    return f"此交換仍在交付中，請稍後查詢，不會重複送出。{code}"


async def _finish(replier: EphemeralReplier, flow: ExchangeFlowResult) -> ExchangeFlowResult:
    """@description: Send the reply after the recorded outcome exists; a failed reply is reported but changes nothing.
    @param replier: Ephemeral reply sender.
    @param flow: Flow result to deliver.
    @return: The same result with the reply transport status set.
    """
    try:
        await replier.send_ephemeral(flow.reply_text)
    except Exception as error:
        LOGGER.warning("Ephemeral reply failed: %s", type(error).__name__)  # [no state change]: Recorded delivery is final here.
        return ExchangeFlowResult(flow.reply_text, True, flow.recommendation_id, flow.delivery_status, flow.refund_status)
    return flow
