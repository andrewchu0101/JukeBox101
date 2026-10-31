"""Present the Owner's pending cases as private cards whose buttons decide only what they display, and keep working after a restart.

Inputs are Owner interactions and the injected review service; every decision goes through the service's preview and confirmation, so revisions, tokens and settlement stay in one place.
Button ids carry the acceptance order and the displayed revision, never an operation id, so an old button is refused rather than applied to a changed case.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

import discord

from bot.discord_owner_review import (
    IDENTITY_LABELS, MAX_REASON_LENGTH, MAX_DURATION_OPTION_SECONDS, NO_MENTIONS, REVIEW_LABELS, SETTLEMENT_UNCONFIRMED_TEXT,
    OwnerGate, _call_service, _confirmed_text, _guarded, _Reply,
)
from bot.owner_review import OwnerReviewService, ReviewAction, ReviewOutcomeUnknown
from bot.review import ReviewStatus, StaleReviewVersion
from bot.song_judgement import ContentJudgement
from bot.version_identity import VersionIdentityStatus

_LOGGER = logging.getLogger(__name__)
CARD_PATTERN = r"ocard:(?P<action>approve|reject|duration|next|reload):(?P<sequence>\d+):(?P<revision>\d+)"  # [button id]: Action, acceptance order and displayed revision.
APPROVE_REASON = "Owner 於案件卡片核准歌曲資格"  # [audit reason]: Stored with an approval made from a card.
DURATION_REASON = "Owner 於案件卡片補完整影片時長"  # [audit reason]: Stored with a duration entered from a card.
CITATION_PREFIX = "owner-card:"  # [audit citation]: Followed by the video id the Owner just viewed on the card.
DECIDABLE_STATES = {ReviewStatus.PENDING_VALIDATION, ReviewStatus.PENDING_REVIEW, ReviewStatus.VALIDATION_FAILED}  # [open reviews]: States a card may reject.
DURATION_STATES = {ReviewStatus.PENDING_VALIDATION, ReviewStatus.VALIDATION_FAILED}  # [missing duration]: States where a duration entry makes sense.
MAX_TRACKED_LOCKS = 1000  # [memory bound]: Unlocked per-case locks are dropped beyond this many.
BUTTON_LABELS = {"approve": "確認通過", "reject": "拒絕", "duration": "補時長", "next": "下一筆", "reload": "重新載入"}  # [button text]: One label per action.
BUTTON_STYLES = {"approve": discord.ButtonStyle.success, "reject": discord.ButtonStyle.danger, "duration": discord.ButtonStyle.secondary,
                 "next": discord.ButtonStyle.secondary, "reload": discord.ButtonStyle.primary}  # [button look]: One style per action.
EMPTY_QUEUE_TEXT = "目前沒有待處理案件。"
NO_MORE_TEXT = "目前沒有更多待處理案件。"
STALE_CARD_TEXT = "此案件已變更或已處理，這張卡片已失效，沒有執行任何操作。請按「重新載入」。"  # [stale card]: Never claims a decision.
UNKNOWN_OUTCOME_TEXT = "結果未知（連線中斷，決定可能已儲存）。請再按一次同一個按鈕，系統會以原決定查回結果，不會重複執行。"
DURATION_FORMAT_TEXT = "秒數必須是整數（0 到 86400）。"


def _duration_text(seconds: int | None) -> str:
    """@description: Show a known full-video duration the way people read it, or say none is known.
    @param seconds: Known full-video seconds, or None.
    @return: Minutes and seconds with the raw seconds, or the missing marker.
    """
    if seconds is None:
        return "尚無"
    return f"{seconds // 60}:{seconds % 60:02d}（{seconds} 秒）"


def waiting_reason(item) -> str:
    """@description: Say in plain words what the case is waiting for, from its review and version states only.
    @param item: Pending row of the card.
    @return: One Traditional Chinese phrase.
    """
    if item.identity_status is VersionIdentityStatus.CONFLICT:
        return f"版本證據衝突：{item.conflict_reason or '待 Owner 處理'}"
    reasons = {ReviewStatus.PENDING_VALIDATION: "尚無官方時長，等待自動取得或由 Owner 補時長",
               ReviewStatus.VALIDATION_FAILED: "無法取得影片資料（可能已刪除或未公開），待 Owner 處理",
               ReviewStatus.PENDING_REVIEW: "待 Owner 審核歌曲資格",
               ReviewStatus.APPROVED: "歌曲已核准，待確認錄音版本（請用 /owner confirm_version）"}
    return reasons.get(item.review_status, "待處理")


def render_card(item) -> str:
    """@description: Show one pending case with everything needed to decide it, and never its internal operation id.
    @param item: Pending row with submitter, link, duration and states.
    @return: Card text.
    """
    return "\n".join([
        "待處理案件", f"投稿者：<@{item.submitter_id}>", f"影片：{item.source_url}", f"已知時長：{_duration_text(item.duration_seconds)}",
        f"審核狀態：{REVIEW_LABELS[item.review_status]}", f"版本狀態：{IDENTITY_LABELS[item.identity_status]}",
        f"待處理：{waiting_reason(item)}", "「確認通過」只核准歌曲資格；錄音版本仍須另外確認，也不保證發獎。"])


def render_result(item, confirmed_text: str, reward_note: str) -> str:
    """@description: Replace a decided card with what really happened, so the Owner never has to infer it.
    @param item: Pending row the decision was made on.
    @param confirmed_text: The service's committed-revision sentence.
    @param reward_note: Reward and version state after the decision, or an empty string.
    @return: Result text.
    """
    lines = ["案件處理結果", f"投稿者：<@{item.submitter_id}>", f"影片：{item.source_url}", confirmed_text]
    return "\n".join(lines + ([reward_note] if reward_note else []))


class _CardButton(discord.ui.DynamicItem[discord.ui.Button], template=CARD_PATTERN):
    """Rebuild itself from its custom_id and hand every press to the handler bound by a subclass."""

    cards: "OwnerCards"  # [bound handler]: Set on the subclass the client registers; the base class is never registered.

    def __init__(self, action: str, sequence: int, revision: int, *, disabled: bool = False) -> None:
        """@description: Bind the action to the case position and revision that the card displayed.
        @param self: Button.
        @param action: One of the card actions.
        @param sequence: Acceptance order of the displayed case.
        @param revision: Review revision the card displayed.
        @param disabled: Whether the action does not apply to the displayed state.
        @return: None.
        """
        self.action, self.sequence, self.revision = action, sequence, revision
        super().__init__(discord.ui.Button(label=BUTTON_LABELS[action], style=BUTTON_STYLES[action], disabled=disabled,
                                           custom_id=f"ocard:{action}:{sequence}:{revision}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        """@description: Rebuild a button from an old message after a restart.
        @param cls: Button class.
        @param interaction: Press being routed.
        @param item: Component Discord sent.
        @param match: Template match carrying action, order and revision.
        @return: Button ready to run its callback.
        """
        return cls(match["action"], int(match["sequence"]), int(match["revision"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        """@description: Run the pressed action through the handler.
        @param self: Button.
        @param interaction: Press.
        @return: None.
        """
        await self.cards.press(interaction, self.action, self.sequence, self.revision)


def _make_button_class(cards: "OwnerCards"):
    """@description: Bind the restart-safe button class to one card handler, so its callback needs no stored message state.
    @param cards: Handler that decides what a press does.
    @return: Dynamic button class to register with the client.
    """
    return type("OwnerCardButton", (_CardButton,), {"cards": cards}, template=CARD_PATTERN)


class _CardModal(discord.ui.Modal):
    """Collect the one or two typed values a card action needs, then hand them back to the handler."""

    def __init__(self, cards: "OwnerCards", action: str, sequence: int, revision: int) -> None:
        """@description: Build the small form for rejection or duration entry, bound to the displayed case and revision.
        @param self: Form.
        @param cards: Handler that applies the submission.
        @param action: reject or duration.
        @param sequence: Acceptance order of the displayed case.
        @param revision: Review revision the card displayed.
        @return: None.
        """
        super().__init__(title="拒絕此案件" if action == "reject" else "補完整影片時長", timeout=300)
        self._cards, self._action, self._sequence, self._revision = cards, action, sequence, revision
        if action == "reject":
            self.reason = discord.ui.TextInput(label="拒絕理由", style=discord.TextStyle.paragraph, max_length=MAX_REASON_LENGTH)
            self.add_item(self.reason)
        else:
            self.seconds = discord.ui.TextInput(label="完整影片秒數", max_length=6)
            self.source = discord.ui.TextInput(label="時長來源（例如影片網址與核對方式）", max_length=MAX_REASON_LENGTH)
            self.add_item(self.seconds)
            self.add_item(self.source)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """@description: Pass the typed values to the handler, which re-checks the Owner and the case before anything is written.
        @param self: Form.
        @param interaction: Submission.
        @return: None.
        """
        values = (self.reason.value,) if self._action == "reject" else (self.seconds.value, self.source.value)
        await self._cards.submit(interaction, self._action, self._sequence, self._revision, values)


class OwnerCards:
    """Own the card flow: open a card, decide it, move to the next one, and refuse anything stale, repeated or foreign."""

    def __init__(self, service_provider: Callable[[], OwnerReviewService], owner_id: int, guild_id: int, now: Callable[[], datetime]) -> None:
        """@description: Bind the Owner gate, service and clock, and build the restart-safe button class.
        @param self: Card handler.
        @param service_provider: Returns the review service; blocking calls run on worker threads.
        @param owner_id: Configured Owner Discord user snowflake.
        @param guild_id: Private Guild where the cards are served.
        @param now: Clock stamping content judgements.
        @return: None.
        """
        self._gate = OwnerGate(owner_id, guild_id)  # [authorization]: Applied to every press and form submission.
        self._service_provider = service_provider
        self._now = now
        self._locks: dict[int, asyncio.Lock] = {}  # [single decision]: One lock per displayed case, so repeated presses serialize.
        self._open_tokens: dict[tuple[int, int, str], str] = {}  # [kept token]: Lets an unknown outcome be retried under the same decision.
        self.button_class = _make_button_class(self)

    async def first_replies(self, actor_id: int) -> list[_Reply]:
        """@description: Open the oldest pending case, which is what /owner list shows.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @return: One card reply, or the empty-queue sentence.
        """
        return [await self._card_after(actor_id, None)]

    async def press(self, interaction: discord.Interaction, action: str, sequence: int, revision: int) -> None:
        """@description: Admit the Owner, then open a form or run the pressed action and update the card with what really happened.
        @param self: Card handler.
        @param interaction: Button press.
        @param action: Pressed action.
        @param sequence: Acceptance order in the button id.
        @param revision: Displayed revision in the button id.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        if action in ("reject", "duration"):
            await interaction.response.send_modal(_CardModal(self, action, sequence, revision))
            return
        await interaction.response.defer()
        reply = (await _guarded(self._run(interaction.user.id, action, sequence, revision, ())))[0]
        await self._show(interaction, reply)

    async def submit(self, interaction: discord.Interaction, action: str, sequence: int, revision: int, values: tuple[str, ...]) -> None:
        """@description: Admit the Owner again, then apply a submitted form and update the card.
        @param self: Card handler.
        @param interaction: Form submission.
        @param action: reject or duration.
        @param sequence: Acceptance order the form was opened for.
        @param revision: Displayed revision the form was opened for.
        @param values: Typed values.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        await interaction.response.defer()
        reply = (await _guarded(self._run(interaction.user.id, action, sequence, revision, values)))[0]
        await self._show(interaction, reply)

    async def _show(self, interaction: discord.Interaction, reply: _Reply) -> None:
        """@description: Replace the card the Owner pressed with the new state.
        @param self: Card handler.
        @param interaction: Acknowledged interaction.
        @param reply: New text and buttons.
        @return: None.
        """
        await interaction.edit_original_response(content=reply.text, view=reply.view, allowed_mentions=NO_MENTIONS)

    def _lock_for(self, sequence: int) -> asyncio.Lock:
        """@description: Give every press of one displayed case the same lock, dropping idle locks so the table stays bounded.
        @param self: Card handler.
        @param sequence: Acceptance order of the case.
        @return: The case's lock.
        """
        if len(self._locks) >= MAX_TRACKED_LOCKS:
            self._locks = {key: lock for key, lock in self._locks.items() if lock.locked()}
        return self._locks.setdefault(sequence, asyncio.Lock())

    async def _run(self, actor_id: int, action: str, sequence: int, revision: int, values: tuple[str, ...]) -> _Reply:
        """@description: Serialize presses of one case and route the action.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param action: Pressed or submitted action.
        @param sequence: Acceptance order of the displayed case.
        @param revision: Displayed revision.
        @param values: Typed form values, empty for plain buttons.
        @return: The reply that replaces the card.
        """
        async with self._lock_for(sequence):
            if action == "next":
                return await self._card_after(actor_id, sequence, NO_MORE_TEXT)
            if action == "reload":
                return await self._card_after(actor_id, None if sequence <= 1 else sequence - 1)
            item = await self._item_at(actor_id, sequence)
            if item is None or item.revision != revision:
                return self._stale_reply(sequence)
            return await self._decide_action(actor_id, item, action, values)

    async def _decide_action(self, actor_id: int, item, action: str, values: tuple[str, ...]) -> _Reply:
        """@description: Check the displayed state allows the action, then run its preview and confirmation.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Current pending row, already matched to the displayed revision.
        @param action: approve, reject or duration.
        @param values: Typed form values.
        @return: Result reply, or the stale reply when the state no longer allows the action.
        """
        allowed = {"approve": {ReviewStatus.PENDING_REVIEW}, "reject": DECIDABLE_STATES, "duration": DURATION_STATES}[action]
        if item.review_status not in allowed:
            return self._stale_reply(item.accepted_sequence)
        try:
            if action == "duration":
                return await self._duration(actor_id, item, values)
            return await self._judgement(actor_id, item, action, values)
        except StaleReviewVersion:
            return self._stale_reply(item.accepted_sequence)

    async def _judgement(self, actor_id: int, item, action: str, values: tuple[str, ...]) -> _Reply:
        """@description: Approve or reject against the displayed revision, citing the video the Owner just viewed.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row at the displayed revision.
        @param action: approve or reject.
        @param values: Rejection reason, or empty for approval.
        @return: Result reply.
        """
        projection = await _call_service(self._service_provider, lambda service: service.view(actor_id, item.operation_id))
        citation = f"{CITATION_PREFIX}{projection.review.video.video_id}"
        if action == "approve":
            content = ContentJudgement(video=projection.review.video, judged_by=actor_id, reason=APPROVE_REASON,
                                       retrieved_at=self._now(), evidence_reference=citation, is_song=True)
            return await self._confirm(actor_id, item, action, lambda service: service.preview(
                actor_id, item.operation_id, ReviewAction.APPROVE, APPROVE_REASON, item.revision, citation, content))
        return await self._confirm(actor_id, item, action, lambda service: service.preview(
            actor_id, item.operation_id, ReviewAction.REJECT, values[0], item.revision, citation))

    async def _duration(self, actor_id: int, item, values: tuple[str, ...]) -> _Reply:
        """@description: Enter an Owner-stated duration, refusing anything that is not a whole number of seconds before any service call.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row at the displayed revision.
        @param values: Typed seconds and source.
        @return: Result reply, or the format sentence.
        """
        seconds_text, source = values[0].strip(), values[1].strip()
        if not (seconds_text.isascii() and seconds_text.isdecimal()) or int(seconds_text) > MAX_DURATION_OPTION_SECONDS:
            return _Reply(DURATION_FORMAT_TEXT, self._view([self._button("reload", item.accepted_sequence, 0)]))
        return await self._confirm(actor_id, item, "duration", lambda service: service.preview_duration(
            actor_id, item.operation_id, int(seconds_text), source, DURATION_REASON))

    async def _confirm(self, actor_id: int, item, action: str, preview_call: Callable[[OwnerReviewService], object]) -> _Reply:
        """@description: Preview once, confirm immediately, and keep the token if the outcome is unknown so a retry replays the same decision.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row at the displayed revision.
        @param action: Action name, part of the token key.
        @param preview_call: Service preview for this action.
        @return: Result reply, or the unknown-outcome reply with the original buttons.
        """
        key = (item.accepted_sequence, item.revision, action)
        token_id = self._open_tokens.get(key)
        if token_id is None:
            token_id = (await _call_service(self._service_provider, preview_call)).token_id
            self._open_tokens[key] = token_id
        try:
            snapshot = await _call_service(self._service_provider, lambda service: service.confirm(actor_id, token_id))
        except ReviewOutcomeUnknown:
            return _Reply(f"{render_card(item)}\n{UNKNOWN_OUTCOME_TEXT}", self._card_view(item))
        self._open_tokens.pop(key, None)
        note = await self._reward_note(actor_id, item.operation_id)
        return _Reply(render_result(item, _confirmed_text(snapshot), note), self._view([self._button("next", item.accepted_sequence, 0)]))

    async def _reward_note(self, actor_id: int, operation_id: str) -> str:
        """@description: Report the reward and version state after a committed decision; a follow-up failure never rewrites the decision.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param operation_id: Decided case.
        @return: Note text, or an empty string when settlement is off.
        """
        try:
            return await _call_service(self._service_provider, lambda service: service.settlement_note(actor_id, operation_id))
        except Exception as error:  # [committed decision]: Only the follow-up failed; the periodic sweep retries it.
            _LOGGER.error("Settlement after a card decision failed: %s", type(error).__name__)
            return SETTLEMENT_UNCONFIRMED_TEXT

    async def _item_at(self, actor_id: int, sequence: int):
        """@description: Find the pending row at an exact acceptance order, or nothing when that case left the queue.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param sequence: Acceptance order from the button id.
        @return: Pending row, or None.
        """
        page = await _call_service(self._service_provider, lambda service: service.list_pending(
            actor_id, after_sequence=None if sequence <= 1 else sequence - 1, limit=1))
        return page.items[0] if page.items and page.items[0].accepted_sequence == sequence else None

    async def _card_after(self, actor_id: int, after_sequence: int | None, empty_text: str = EMPTY_QUEUE_TEXT) -> _Reply:
        """@description: Show the first pending case after a position, without changing any case or its order.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param after_sequence: Position already seen, or None for the oldest.
        @param empty_text: Sentence when no case follows.
        @return: Card reply, or the empty sentence with a reload button when a card was already on screen.
        """
        page = await _call_service(self._service_provider, lambda service: service.list_pending(actor_id, after_sequence=after_sequence, limit=1))
        if not page.items:
            view = None if empty_text == EMPTY_QUEUE_TEXT else self._view([self._button("reload", 0, 0)])
            return _Reply(empty_text, view)
        return _Reply(render_card(page.items[0]), self._card_view(page.items[0]))

    def _stale_reply(self, sequence: int) -> _Reply:
        """@description: Refuse an out-of-date card, saying nothing was done and offering a reload.
        @param self: Card handler.
        @param sequence: Acceptance order of the card that went stale.
        @return: Stale reply.
        """
        return _Reply(STALE_CARD_TEXT, self._view([self._button("reload", sequence, 0)]))

    def _button(self, action: str, sequence: int, revision: int, disabled: bool = False):
        """@description: Build one card button.
        @param self: Card handler.
        @param action: Card action.
        @param sequence: Acceptance order.
        @param revision: Displayed revision.
        @param disabled: Whether the action does not apply.
        @return: Dynamic button.
        """
        return self.button_class(action, sequence, revision, disabled=disabled)

    def _view(self, buttons: list) -> discord.ui.View:
        """@description: Put buttons on a view that does not time out, because their ids rebuild them after any delay or restart.
        @param self: Card handler.
        @param buttons: Dynamic buttons.
        @return: View.
        """
        view = discord.ui.View(timeout=None)
        for button in buttons:
            view.add_item(button)
        return view

    def _card_view(self, item) -> discord.ui.View:
        """@description: Offer only the actions that apply to the displayed state; the duration entry appears only when the duration is missing.
        @param self: Card handler.
        @param item: Pending row.
        @return: Card view.
        """
        sequence, revision, status = item.accepted_sequence, item.revision, item.review_status
        buttons = [self._button("approve", sequence, revision, status is not ReviewStatus.PENDING_REVIEW),
                   self._button("reject", sequence, revision, status not in DECIDABLE_STATES)]
        if item.duration_seconds is None and status in DURATION_STATES:
            buttons.append(self._button("duration", sequence, revision))
        return self._view([*buttons, self._button("next", sequence, 0)])
