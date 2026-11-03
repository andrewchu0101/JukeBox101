"""Present the Owner's pending cases as private cards that ask one plain question and answer it through the shared decision core.

Inputs are Owner interactions, the injected review service and bot.owner_decisions; every rule about what a case allows or how it is settled lives in that core, and this module only shows it.
Button ids carry the acceptance order and the displayed revision, never an operation id, so an old button is refused rather than applied to a changed case.
"""

import asyncio
import contextvars
import logging
from collections.abc import Callable
from datetime import datetime

import discord

from bot import owner_decisions
from bot.discord_owner_review import MAX_DURATION_OPTION_SECONDS, NO_MENTIONS, OwnerGate, _call_service, _confirmed_text, _guarded, _Reply
from bot.owner_card_versions import KNOWN_VERSIONS_PER_CARD, retention_warning, watch_url
from bot.owner_review import OwnerReviewService, ReviewOutcomeUnknown
from bot.review import DurationEvidenceExpired, ReviewStatus, StaleReviewVersion
from bot.song_identity import video_identity_from_url

_LOGGER = logging.getLogger(__name__)
CARD_PATTERN = (r"ocard:(?P<action>approve|reject|duration|next|reload|new|same|other|vpage|kind|remaster|back|opage|detail|list|prev|yes|no|dup|dpage)"
                r":(?P<sequence>\d+):(?P<revision>\d+)"
                r"(?::(?P<extra>\d+))?(?::p(?P<page>\d+))?")  # [button id]: Action, acceptance order, displayed revision, an opaque choice for song buttons, and the list page the card was opened from.
EXTRA_ACTIONS = {"same", "vpage", "kind", "remaster", "opage", "dup", "dpage"}  # [choice carriers]: Actions whose id also carries a chosen song or a page cursor.
LEGACY_ACTIONS = {"new", "same", "other", "vpage", "kind", "remaster", "opage"}  # [retired buttons]: Ids on old messages; they match but do nothing except ask for a reload.
YES_ACTIONS = {"yes", "approve"}  # [yes ids]: The new id and the id issued before the redesign.
DURATION_REASON = "Owner 於案件卡片補完整影片時長"  # [audit reason]: Stored with a duration entered from a card.
MAX_TRACKED_LOCKS = 1000  # [memory bound]: Unlocked per-case locks are dropped beyond this many.
BUTTON_LABELS = {"approve": "是，發獎", "reject": "不是", "yes": "是，發獎", "no": "不是", "duration": "補時長", "next": "下一筆", "reload": "重新載入",
                 "back": "返回", "dup": "是這首", "dpage": "更多已收錄的歌", "detail": "詳情", "list": "重新載入", "prev": "上一頁"}  # [button text]: One label per action; retired actions fall back to the reload text.
BUTTON_STYLES = {"approve": discord.ButtonStyle.success, "yes": discord.ButtonStyle.success, "reject": discord.ButtonStyle.danger,
                 "no": discord.ButtonStyle.danger, "duration": discord.ButtonStyle.primary, "reload": discord.ButtonStyle.primary,
                 "dup": discord.ButtonStyle.success, "list": discord.ButtonStyle.primary}  # [button look]: Other actions are secondary.
EMPTY_QUEUE_TEXT = "目前沒有待處理案件。"
NO_MORE_TEXT = "目前沒有更多待處理案件。"
STALE_CARD_TEXT = "此案件已變更或已處理，這張卡片已失效，沒有執行任何操作。請按「重新載入」。"  # [stale card]: Never claims a decision.
REDESIGNED_CARD_TEXT = "這張卡片已改版，沒有執行任何操作。請按「重新載入」。"  # [retired buttons]: Shown for ids of the old card design.
UNKNOWN_OUTCOME_TEXT = "結果未知（連線中斷，決定可能已儲存）。請再按一次同一個按鈕，系統會以原決定查回結果，不會重複執行。"
DURATION_FORMAT_TEXT = "秒數必須是整數（0 到 86400）。"
EXPIRED_BASIS_TEXT = "官方時長資料已過期，不能再依它通過。沒有執行任何操作；請等待系統重新驗證，或按「補時長」由你輸入時長與來源。"  # [expired API basis]: The case stays pending.
FROZEN_TEXT = "需要先解除衝突（用命令列工具）"  # [frozen case]: The only thing the Owner can do here is outside the card.
PICK_QUESTION = "這首和哪一首已收錄的歌是同一首？"  # [duplicate pick]: Asked before a no is recorded on an already approved case.
NO_KNOWN_SONGS_TEXT = "目前沒有已收錄的歌可以選。"
MAX_LINK_CHARS = 300  # [message budget]: A longer submitted link is shown as its canonical watch link so the message stays under Discord's 2000-character limit.
PAGE_CAPACITY = 3  # [list size]: Owner-approved number of cases in one /owner list message; each gets its own button row, plus one row for paging.
LIST_LINK_CHARS = 100  # [list budget]: A longer submitted link is shown as its canonical watch link on the list page.
LIST_TEXT_CHARS = 100  # [list budget]: A longer doubt or status line is cut with an ellipsis so three cases fit in one message.
DETAIL_TEXT_CHARS = 300  # [detail budget]: Cap of the doubt and status lines on a single card.
LIST_LEAD_CHARS = 400  # [list budget]: Cap of the "just handled" line above the list.
LIST_WARNING_CHARS = 300  # [list budget]: Cap of the retention warning above the list.
MAX_PRIOR_PAGE_CASES = 20  # [read size]: Rows read per request while finding the page before the current one.
_PAGE_CONTEXT: contextvars.ContextVar[int | None] = contextvars.ContextVar("owner_card_page", default=None)  # [list page]: Cursor of the list page an action came from, so its replies return there.
FORM_ACTIONS = {"duration"}  # [forms]: Actions that open a modal instead of acting at once.


def _duration_text(seconds: int | None, api_basis: str | None = None) -> str:
    """@description: Show a known full-video duration the way people read it, or say none is known or only the official conclusion remains.
    @param seconds: Known full-video seconds, or None.
    @param api_basis: "valid" or "expired" when only an official-data conclusion backs the duration.
    @return: Minutes and seconds with the raw seconds, or the conclusion or missing marker.
    """
    if seconds is None and api_basis is not None:
        return "官方資料已驗證在範圍內（不保存秒數）" if api_basis == "valid" else "官方資料已過期，需重新驗證或由你補時長"
    if seconds is None:
        return "尚無"
    return f"{seconds // 60}:{seconds % 60:02d}（{seconds} 秒）"


def _card_link(url: str, limit: int = MAX_LINK_CHARS) -> str:
    """@description: Keep a very long submitted link from pushing the card past Discord's message limit without breaking it, and keep Discord from expanding it into a large preview.
    @param url: Link exactly as submitted.
    @param limit: Longest link shown as submitted.
    @return: The link in angle brackets (still clickable, never expanded); the canonical watch link of the same video when the submitted one exceeds the limit.
    """
    if len(url) <= limit:
        return f"<{url}>"
    return f"<{watch_url(video_identity_from_url(url).video_id)}>"


def _clip(text: str, limit: int) -> str:
    """@description: Cut a text that would push the message past Discord's limit.
    @param text: Text to fit.
    @param limit: Longest allowed length.
    @return: The text, or its beginning followed by an ellipsis.
    """
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _missing_duration_line(item) -> str:
    """@description: Tell the Owner the system fetches a missing duration by itself, so entering one is an optional shortcut and not the only way forward.
    @param item: Pending row without a usable duration.
    @return: One phrase naming the automatic lookup, or why it could not run.
    """
    if item.api_basis == "expired":
        return "官方時長資料已過期，系統會自動重新驗證；也可以先補時長"
    if item.review_status is ReviewStatus.VALIDATION_FAILED:
        return "系統無法取得影片資料（可能已刪除或未公開）；可以補時長，或回答「不是」"
    return "系統正在自動取得影片時長；也可以先補時長"


def _status_line(item, choices) -> str:
    """@description: Say in plain words why a case needs more than a yes or no, or that it is already half decided.
    @param item: Pending row of the card.
    @param choices: What the core allows for the case.
    @return: One phrase, or an empty string for an ordinary waiting case.
    """
    if choices.frozen_conflict:
        return FROZEN_TEXT
    if choices.needs_duration:
        return _missing_duration_line(item)
    if item.review_status is ReviewStatus.APPROVED:
        return "已通過，尚未確認是否重複"
    if item.review_status is ReviewStatus.VALIDATION_FAILED:
        return "無法取得影片資料（可能已刪除或未公開）"
    return ""


def _case_lines(item, choices, link_limit: int, text_limit: int) -> list[str]:
    """@description: Write the lines every case shows, in the list and on its own card: sender, link, question, doubt and status.
    @param item: Pending row.
    @param choices: What the core allows for the case.
    @param link_limit: Longest link shown as submitted.
    @param text_limit: Longest doubt or status line.
    @return: Non-empty lines, headed by the acceptance order the case's buttons carry.
    """
    lines = [f"**#{item.accepted_sequence}**　投稿者 <@{item.submitter_id}>", _card_link(item.source_url, link_limit)]
    if not choices.frozen_conflict:
        lines.append(choices.question)
    extras = (choices.doubt_text, _status_line(item, choices))
    return lines + [_clip(text, text_limit) for text in extras if text]


def render_card(item, choices) -> str:
    """@description: Show one pending case on its own with the question and everything needed to answer it, and never its internal operation id.
    @param item: Pending row with submitter, link, duration and states.
    @param choices: What the core allows for the case.
    @return: Card text.
    """
    lines = _case_lines(item, choices, MAX_LINK_CHARS, DETAIL_TEXT_CHARS)
    return "\n".join([*lines, f"已知時長：{_duration_text(item.duration_seconds, item.api_basis)}"])


def render_result(item, text: str) -> str:
    """@description: Replace a decided card with what really happened, so the Owner never has to infer it.
    @param item: Pending row the decision was made on.
    @param text: Result sentence of the decision.
    @return: Result text.
    """
    return "\n".join(["案件處理結果", f"投稿者：<@{item.submitter_id}>", f"影片：{_card_link(item.source_url)}", text])


class _CardButton(discord.ui.DynamicItem[discord.ui.Button], template=CARD_PATTERN):
    """Rebuild itself from its custom_id and hand every press to the handler bound by a subclass."""

    cards: "OwnerCards"  # [bound handler]: Set on the subclass the client registers; the base class is never registered.

    def __init__(self, action: str, sequence: int, revision: int, *, disabled: bool = False, extra: int = 0, row: int | None = None,
                 label: str | None = None, page: int | None = None) -> None:
        """@description: Bind the action to the case position and revision that the card displayed.
        @param self: Button.
        @param action: One of the card actions.
        @param sequence: Acceptance order of the displayed case.
        @param revision: Review revision the card displayed.
        @param disabled: Whether the action does not apply to the displayed state.
        @param extra: Opaque choice of a song button: an already collected song's acceptance order, or a page cursor.
        @param row: Button row, so the groups of actions stay in their own rows.
        @param label: Text overriding the action's default label.
        @param page: Cursor of the list page the card came from, or None for a card opened on its own.
        @return: None.
        """
        self.action, self.sequence, self.revision, self.extra, self.page = action, sequence, revision, extra, page
        tail = (f":{extra}" if action in EXTRA_ACTIONS else "") + ("" if page is None else f":p{page}")
        super().__init__(discord.ui.Button(label=label or BUTTON_LABELS.get(action, "重新載入"), style=BUTTON_STYLES.get(action, discord.ButtonStyle.secondary),
                                           disabled=disabled, custom_id=f"ocard:{action}:{sequence}:{revision}{tail}"), row=row)

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        """@description: Rebuild a button from an old message after a restart.
        @param cls: Button class.
        @param interaction: Press being routed.
        @param item: Component Discord sent.
        @param match: Template match carrying action, order and revision.
        @return: Button ready to run its callback.
        """
        page = None if match["page"] is None else int(match["page"])
        return cls(match["action"], int(match["sequence"]), int(match["revision"]), extra=int(match["extra"] or 0), page=page)

    async def callback(self, interaction: discord.Interaction) -> None:
        """@description: Run the pressed action through the handler.
        @param self: Button.
        @param interaction: Press.
        @return: None.
        """
        await self.cards.press(interaction, self.action, self.sequence, self.revision, self.extra, self.page)


def _make_button_class(cards: "OwnerCards"):
    """@description: Bind the restart-safe button class to one card handler, so its callback needs no stored message state.
    @param cards: Handler that decides what a press does.
    @return: Dynamic button class to register with the client.
    """
    return type("OwnerCardButton", (_CardButton,), {"cards": cards}, template=CARD_PATTERN)


class _CardModal(discord.ui.Modal):
    """Collect the full-video duration and its source, then hand them back to the handler."""

    def __init__(self, cards: "OwnerCards", sequence: int, revision: int, page: int | None = None) -> None:
        """@description: Build the small duration form, bound to the displayed case and revision.
        @param self: Form.
        @param cards: Handler that applies the submission.
        @param sequence: Acceptance order of the displayed case.
        @param revision: Review revision the card displayed.
        @param page: Cursor of the list page the form was opened from, or None.
        @return: None.
        """
        super().__init__(title="補完整影片時長", timeout=300)
        self._cards, self._sequence, self._revision = cards, sequence, revision
        self._page = page  # [return target]: The list page refreshed after the form decides the case.
        self.seconds = discord.ui.TextInput(label="完整影片秒數", max_length=6)
        self.source = discord.ui.TextInput(label="時長來源（例如影片網址與核對方式）", max_length=200)
        self.add_item(self.seconds)
        self.add_item(self.source)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """@description: Pass the typed values to the handler, which re-checks the Owner and the case before anything is written.
        @param self: Form.
        @param interaction: Submission.
        @return: None.
        """
        await self._cards.submit(interaction, "duration", self._sequence, self._revision, (self.seconds.value, self.source.value), 0, self._page)


class OwnerCards:
    """Own the card flow: open a card, ask its question, pass the answer to the decision core, and refuse anything stale, repeated or foreign."""

    def __init__(self, service_provider: Callable[[], OwnerReviewService], owner_id: int, guild_id: int, now: Callable[[], datetime],
                 decisions=owner_decisions) -> None:
        """@description: Bind the Owner gate, service, clock and decision core, and build the restart-safe button class.
        @param self: Card handler.
        @param service_provider: Returns the review service; blocking calls run on worker threads.
        @param owner_id: Configured Owner Discord user snowflake.
        @param guild_id: Private Guild where the cards are served.
        @param now: Clock stamping duration entries.
        @param decisions: Decision core offering Answer, case_choices and decide; replaceable in tests.
        @return: None.
        """
        self._gate = OwnerGate(owner_id, guild_id)  # [authorization]: Applied to every press and form submission.
        self._service_provider = service_provider
        self._now = now
        self._core = decisions
        self._locks: dict[int, asyncio.Lock] = {}  # [single decision]: One lock per displayed case, so repeated presses serialize.
        self._open_tokens: dict[tuple[int, int, str], str] = {}  # [kept token]: Lets an unknown outcome be retried under the same decision.
        self.button_class = _make_button_class(self)

    async def first_replies(self, actor_id: int) -> list[_Reply]:
        """@description: Open the first page of up to three pending cases, which is what /owner list shows.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @return: One page reply, or the empty-queue sentence.
        """
        return [await self._page_reply(actor_id, 0)]

    async def press(self, interaction: discord.Interaction, action: str, sequence: int, revision: int, extra: int = 0,
                    page: int | None = None) -> None:
        """@description: Admit the Owner, then open a form or run the pressed action and update the card with what really happened.
        @param self: Card handler.
        @param interaction: Button press.
        @param action: Pressed action.
        @param sequence: Acceptance order in the button id.
        @param revision: Displayed revision in the button id.
        @param extra: Opaque choice of a song button.
        @param page: Cursor of the list page the button came from, or None.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        if action in FORM_ACTIONS:
            await interaction.response.send_modal(_CardModal(self, sequence, revision, page))
            return
        await interaction.response.edit_message(view=None)  # [lock the card]: Acknowledge by removing the buttons, so nothing can be pressed while the slow work runs.
        pending = self._run(interaction.user.id, action, sequence, revision, (), extra, page)
        await self._show(interaction, await self._reply_with_buttons(pending, sequence, page))

    async def submit(self, interaction: discord.Interaction, action: str, sequence: int, revision: int, values: tuple[str, ...],
                     extra: int = 0, page: int | None = None) -> None:
        """@description: Admit the Owner again, then apply a submitted form and update the card.
        @param self: Card handler.
        @param interaction: Form submission.
        @param action: duration.
        @param sequence: Acceptance order the form was opened for.
        @param revision: Displayed revision the form was opened for.
        @param values: Typed values.
        @param extra: Unused choice carried for symmetry with presses.
        @param page: Cursor of the list page the form was opened from, or None.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        await interaction.response.defer()
        pending = self._run(interaction.user.id, action, sequence, revision, values, extra, page)
        await self._show(interaction, await self._reply_with_buttons(pending, sequence, page))

    async def _reply_with_buttons(self, pending, sequence: int, page: int | None) -> _Reply:
        """@description: Run an action behind the error guard and make sure its reply always has buttons, so a failure never strands the Owner.
        @param self: Card handler.
        @param pending: Coroutine producing the reply.
        @param sequence: Acceptance order of the pressed case, for the reload button of a single card.
        @param page: Cursor of the list page the press came from, or None.
        @return: The reply, with a reload button added when it had none.
        """
        reply = (await _guarded(pending))[0]
        if reply.view is not None:
            return reply
        return _Reply(reply.text, self._view([self._reload_button(sequence, page)]))

    def _reload_button(self, sequence: int, page: int | None):
        """@description: Build the button that brings the Owner back to a working screen after a failure.
        @param self: Card handler.
        @param sequence: Acceptance order of the case the Owner was on.
        @param page: Cursor of the list page the Owner was on, or None.
        @return: Reload button for the list page, or for a single card.
        """
        if page is not None:
            return self.button_class("list", page, 0, label="重新載入")
        return self.button_class("reload", sequence, 0)

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

    async def _run(self, actor_id: int, action: str, sequence: int, revision: int, values: tuple[str, ...], extra: int = 0,
                   page: int | None = None) -> _Reply:
        """@description: Serialize presses of one case and route the action, remembering the list page the press came from.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param action: Pressed or submitted action.
        @param sequence: Acceptance order of the displayed case, or the list cursor for list actions.
        @param revision: Displayed revision.
        @param values: Typed form values, empty for plain buttons.
        @param extra: Opaque choice of a song button.
        @param page: Cursor of the list page the press came from, or None.
        @return: The reply that replaces the card.
        """
        token = _PAGE_CONTEXT.set(page)
        try:
            async with self._lock_for(sequence):
                return await self._route(actor_id, action, sequence, revision, values, extra)
        finally:
            _PAGE_CONTEXT.reset(token)

    async def _navigate(self, actor_id: int, action: str, sequence: int) -> _Reply | None:
        """@description: Answer the actions that only move between screens and never touch a case.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param action: Pressed action.
        @param sequence: Acceptance order of the displayed case, or the list cursor for list actions.
        @return: The screen to show, or None when the action concerns one case.
        """
        page = _PAGE_CONTEXT.get()
        if action == "list" or (action == "reload" and page is not None):
            return await self._page_reply(actor_id, sequence if action == "list" else page)
        if action == "prev":
            return await self._previous_page_reply(actor_id, sequence)
        if action == "next":
            return await self._card_after(actor_id, sequence, NO_MORE_TEXT)
        if action == "reload":
            return await self._card_after(actor_id, None if sequence <= 1 else sequence - 1)
        return None

    async def _route(self, actor_id: int, action: str, sequence: int, revision: int, values: tuple[str, ...], extra: int) -> _Reply:
        """@description: Send a navigation action to its screen, check a case action against the live case, then run it.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param action: Pressed or submitted action.
        @param sequence: Acceptance order of the displayed case, or the list cursor for list actions.
        @param revision: Displayed revision.
        @param values: Typed form values.
        @param extra: Opaque choice of a song button.
        @return: The reply that replaces the card.
        """
        screen = await self._navigate(actor_id, action, sequence)
        if screen is not None:
            return screen
        item = await self._item_at(actor_id, sequence)
        if item is None or item.revision != revision:
            return self._stale_reply(sequence)
        try:
            return await self._act(actor_id, item, action, values, extra)
        except StaleReviewVersion:
            return self._stale_reply(sequence)
        except DurationEvidenceExpired:
            return await self._card_reply(actor_id, item, note=EXPIRED_BASIS_TEXT)

    async def _act(self, actor_id: int, item, action: str, values: tuple[str, ...], extra: int) -> _Reply:
        """@description: Run an action on a case already matched to the displayed revision.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Current pending row.
        @param action: detail, back, dpage, duration, a retired action, or an answer.
        @param values: Typed form values.
        @param extra: Opaque choice of a song button.
        @return: The reply that replaces the card.
        """
        if action in ("detail", "back"):
            return await self._card_reply(actor_id, item)
        if action == "dpage":
            return await self._picker_reply(actor_id, item, extra)
        if action in LEGACY_ACTIONS:
            return _Reply(REDESIGNED_CARD_TEXT, self._view([self._reload_button(item.accepted_sequence, _PAGE_CONTEXT.get())]))
        choices = await self._choices_of(actor_id, item)
        if action == "duration":
            return await self._duration(actor_id, item, choices, values)
        return await self._answer(actor_id, item, choices, action, extra)

    async def _answer(self, actor_id: int, item, choices, action: str, extra: int) -> _Reply:
        """@description: Pass the Owner's yes or no to the decision core, or ask which collected song a no repeats, and show the core's own sentence.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Current pending row at the displayed revision.
        @param choices: What the core allows for the case now.
        @param action: yes, no, dup or an old approve or reject id.
        @param extra: The chosen collected song's acceptance order for dup.
        @return: Result reply, the picker, or the stale reply when the core no longer offers this answer.
        """
        answer = self._core.Answer.YES if action in YES_ACTIONS else self._core.Answer.NO
        if not (choices.can_yes if action in YES_ACTIONS else choices.can_no):
            return self._stale_reply(item.accepted_sequence)
        if answer is self._core.Answer.NO and action != "dup" and choices.no_requires_duplicate_pick:
            return await self._picker_reply(actor_id, item, None)
        duplicate_of = extra if action == "dup" else None
        try:
            result = await _call_service(self._service_provider, lambda service: self._core.decide(
                service, actor_id, item, answer, duplicate_of_sequence=duplicate_of, tokens=self._open_tokens))
        except ReviewOutcomeUnknown:
            return await self._after_answer(actor_id, item, UNKNOWN_OUTCOME_TEXT, decided=False)
        return await self._after_answer(actor_id, item, result.text, decided=not result.still_pending)

    async def _after_answer(self, actor_id: int, item, text: str, *, decided: bool) -> _Reply:
        """@description: Show what happened: the refreshed list with a short line on top, or the case's own screen.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row the answer was given on.
        @param text: Result sentence from the core or a failure sentence.
        @param decided: Whether the case left the Owner's hands; false keeps the wording neutral.
        @return: Reply with buttons.
        """
        page = _PAGE_CONTEXT.get()
        if page is not None:
            prefix = "剛處理 " if decided else ""
            lead = " ".join(f"{prefix}#{item.accepted_sequence}：{text}".split())
            return await self._page_reply(actor_id, page, lead=_clip(lead, LIST_LEAD_CHARS))
        current = await self._item_at(actor_id, item.accepted_sequence)
        if current is None:
            return _Reply(render_result(item, text), self._view([self._button("next", item.accepted_sequence, 0)]))
        return await self._card_reply(actor_id, current, note=text)

    async def _picker_reply(self, actor_id: int, item, after: int | None) -> _Reply:
        """@description: Ask which already collected song this case repeats, showing each by its link so the Owner can compare.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row to answer no for.
        @param after: Page cursor of the collected songs, or None for the first page.
        @return: Picker text and buttons; nothing is written.
        """
        page = await _call_service(self._service_provider, lambda service: service.list_known_versions(
            actor_id, after_sequence=after, limit=KNOWN_VERSIONS_PER_CARD))
        sequence, revision = item.accepted_sequence, item.revision
        rows = [f" {number}. <{watch_url(song.first_video_id)}>（#{song.first_sequence}）" for number, song in enumerate(page.items, 1)]
        text = "\n".join([f"**#{sequence}**　{_card_link(item.source_url, LIST_LINK_CHARS)}", PICK_QUESTION, *(rows or [NO_KNOWN_SONGS_TEXT])])
        buttons = [self._button("dup", sequence, revision, extra=song.first_sequence, row=0, label=f"是第 {number} 首") for number, song in enumerate(page.items, 1)]
        buttons.append(self._button("back", sequence, revision, row=1))
        if page.next_after_sequence is not None:
            buttons.append(self._button("dpage", sequence, revision, extra=page.next_after_sequence, row=1))
        return _Reply(text, self._view(buttons))

    async def _choices_of(self, actor_id: int, item):
        """@description: Ask the decision core what the Owner may answer for one case now.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row.
        @return: The core's choices.
        """
        return (await self._choices_for(actor_id, [item]))[0]

    async def _choices_for(self, actor_id: int, items) -> list:
        """@description: Ask the core for the choices of several cases, giving it the collected songs' orders once so its doubt lines can name them.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param items: Pending rows.
        @return: One choices object per row, in order.
        """
        sequence_by_video_id = await self._known_orders(actor_id)
        return [self._core.case_choices(item, sequence_by_video_id) for item in items]

    async def _known_orders(self, actor_id: int) -> dict[str, int]:
        """@description: Read the first page of collected songs as a courtesy for the doubt lines; a failed read only makes those lines less specific.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @return: Map from a collected song's video id to its acceptance order.
        """
        try:
            page = await _call_service(self._service_provider, lambda service: service.list_known_versions(actor_id, limit=KNOWN_VERSIONS_PER_CARD))
        except Exception as error:  # [presentation only]: Without the map the doubt lines say "某首已收錄的歌".
            _LOGGER.warning("Collected songs unavailable for the card: %s", type(error).__name__)
            return {}
        return {song.first_video_id: song.first_sequence for song in page.items}

    async def _duration(self, actor_id: int, item, choices, values: tuple[str, ...]) -> _Reply:
        """@description: Enter an Owner-stated duration, refusing anything that is not a whole number of seconds before any service call.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row at the displayed revision.
        @param choices: What the core allows for the case now; a duration is only valid while it asks for one.
        @param values: Typed seconds and source.
        @return: Result reply, the format sentence, or the stale reply.
        """
        if not choices.needs_duration:
            return self._stale_reply(item.accepted_sequence)
        seconds_text, source = values[0].strip(), values[1].strip()
        if not (seconds_text.isascii() and seconds_text.isdecimal()) or int(seconds_text) > MAX_DURATION_OPTION_SECONDS:
            return _Reply(DURATION_FORMAT_TEXT, self._view([self._reload_button(item.accepted_sequence, _PAGE_CONTEXT.get())]))
        snapshot = await self._commit(actor_id, item, "duration", lambda service: service.preview_duration(
            actor_id, item.operation_id, int(seconds_text), source, DURATION_REASON, expected_revision=item.revision))
        if snapshot is None:
            return await self._after_answer(actor_id, item, UNKNOWN_OUTCOME_TEXT, decided=False)
        return await self._after_answer(actor_id, item, _confirmed_text(snapshot), decided=False)

    async def _commit(self, actor_id: int, item, action: str, preview_call: Callable[[OwnerReviewService], object]):
        """@description: Preview once and confirm, keeping the token when the outcome is unknown so a retry replays the same decision.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row at the displayed revision.
        @param action: Action name, part of the token key.
        @param preview_call: Service preview for this action.
        @return: The committed snapshot, or None when the outcome is unknown.
        """
        key = (item.accepted_sequence, item.revision, action)
        token_id = self._open_tokens.get(key)
        if token_id is None:
            token_id = (await _call_service(self._service_provider, preview_call)).token_id
            self._open_tokens[key] = token_id
        try:
            snapshot = await _call_service(self._service_provider, lambda service: service.confirm(actor_id, token_id))
        except ReviewOutcomeUnknown:
            return None
        self._open_tokens.pop(key, None)
        return snapshot

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

    async def _page_reply(self, actor_id: int, cursor: int, lead: str = "") -> _Reply:
        """@description: Show up to three pending cases after a list cursor, each with its own question and buttons, plus paging.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param cursor: Acceptance order the page starts after; 0 is the first page.
        @param lead: Short line above the list saying what was just handled, or an empty string.
        @return: Page text and view; an emptied later page falls back to the first page, an empty queue says so.
        """
        after = None if cursor <= 0 else cursor
        page = await _call_service(self._service_provider, lambda service: service.list_pending(actor_id, after_sequence=after, limit=PAGE_CAPACITY))
        if not page.items and cursor > 0:
            return await self._page_reply(actor_id, 0, lead)
        if not page.items:
            return _Reply(f"{lead}\n\n{EMPTY_QUEUE_TEXT}" if lead else EMPTY_QUEUE_TEXT)
        warning = _clip(await self._retention_warning(actor_id), LIST_WARNING_CHARS)
        choices = await self._choices_for(actor_id, page.items)
        entries = ["\n".join(_case_lines(item, picked, LIST_LINK_CHARS, LIST_TEXT_CHARS)) for item, picked in zip(page.items, choices)]
        parts = (lead, warning.strip(), f"待處理案件（本頁 {len(page.items)} 筆）", *entries)
        return _Reply("\n\n".join(part for part in parts if part), self._page_view(page, choices, cursor))

    def _page_view(self, page, choices, cursor: int) -> discord.ui.View:
        """@description: Give every listed case its own row of buttons bound to its own order and revision, then a paging row.
        @param self: Card handler.
        @param page: Pending page being shown.
        @param choices: The core's choices for each listed case, in order.
        @param cursor: Acceptance order the page starts after; carried by each case button so its replies return here.
        @return: View with one row per case and a last row of Previous, Reload and Next.
        """
        buttons = [button for row, (item, picked) in enumerate(zip(page.items, choices)) for button in self._answer_buttons(item, picked, row, cursor, True)]
        paging_row, first, last = len(page.items), page.items[0].accepted_sequence, page.items[-1].accepted_sequence
        buttons.append(self.button_class("prev", first, 0, disabled=cursor <= 0, row=paging_row))
        buttons.append(self.button_class("list", cursor, 0, row=paging_row))
        buttons.append(self.button_class("list", last, 0, disabled=page.next_after_sequence is None, row=paging_row, label="下一頁"))
        return self._view(buttons)

    def _answer_buttons(self, item, choices, row: int, page: int | None, with_detail: bool) -> list:
        """@description: Offer, in one row, the answers the core allows: the duration entry or yes first, then no, and optionally the detail button.
        @param self: Card handler.
        @param item: Pending row.
        @param choices: What the core allows for the case.
        @param row: Button row of this case.
        @param page: List page cursor the buttons carry, or None on a card opened on its own.
        @param with_detail: Whether to add the detail button, which the list needs and the card itself does not.
        @return: Buttons labelled with the case's acceptance order when listed, so three cases can be told apart.
        """
        sequence, revision, suffix = item.accepted_sequence, item.revision, f" #{item.accepted_sequence}" if with_detail else ""
        offered = [] if choices.frozen_conflict else [("duration", BUTTON_LABELS["duration"]) if choices.needs_duration else None,
                                                      ("yes", choices.yes_label) if choices.can_yes and not choices.needs_duration else None,
                                                      ("no", choices.no_label) if choices.can_no else None]
        wanted = [pair for pair in offered if pair] + ([("detail", BUTTON_LABELS["detail"])] if with_detail else [])
        return [self.button_class(action, sequence, revision, row=row, label=f"{label}{suffix}", page=page) for action, label in wanted]

    async def _previous_page_reply(self, actor_id: int, first_sequence: int) -> _Reply:
        """@description: Show the page that ends just before a case, found from the live queue so it still works after cases were decided.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param first_sequence: Acceptance order of the first case on the page being left.
        @return: The page holding the last three cases before it, or the first page.
        """
        earlier, after = [], None
        while True:
            page = await _call_service(self._service_provider, lambda service: service.list_pending(
                actor_id, after_sequence=after, limit=MAX_PRIOR_PAGE_CASES))
            earlier += [row.accepted_sequence for row in page.items if row.accepted_sequence < first_sequence]
            if page.next_after_sequence is None or page.items[-1].accepted_sequence >= first_sequence:
                break
            after = page.next_after_sequence
        return await self._page_reply(actor_id, earlier[-(PAGE_CAPACITY + 1)] if len(earlier) > PAGE_CAPACITY else 0)

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
        return await self._card_reply(actor_id, page.items[0])

    async def _card_reply(self, actor_id: int, item, *, note: str = "") -> _Reply:
        """@description: Build a case's own screen: the same question as the list plus the known duration, and the answers the core allows.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row to show.
        @param note: Sentence appended below the card, such as guidance or an unknown outcome.
        @return: Card text and view.
        """
        choices = await self._choices_of(actor_id, item)
        text = await self._retention_warning(actor_id) + render_card(item, choices)
        page = _PAGE_CONTEXT.get()
        nav = self._button("next", item.accepted_sequence, 0, row=0) if page is None else self.button_class("list", page, 0, row=0, label="返回清單")
        buttons = [*self._answer_buttons(item, choices, 0, page, False), nav]
        return _Reply(f"{text}\n\n{note}" if note else text, self._view(buttons))

    async def _retention_warning(self, actor_id: int) -> str:
        """@description: Read the retention warning as a courtesy; it can never stop the Owner from working, even before the table exists.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @return: Warning text, or an empty string when nothing is overdue or the status cannot be read.
        """
        try:
            return retention_warning(await _call_service(self._service_provider, lambda service: service.retention_status(actor_id)))
        except Exception as error:  # [presentation only]: A failed read hides the warning, never the card.
            _LOGGER.warning("Retention status unavailable for the card: %s", type(error).__name__)
            return ""

    def _stale_reply(self, sequence: int) -> _Reply:
        """@description: Refuse an out-of-date card, saying nothing was done and offering a reload.
        @param self: Card handler.
        @param sequence: Acceptance order of the card that went stale.
        @return: Stale reply.
        """
        return _Reply(STALE_CARD_TEXT, self._view([self._reload_button(sequence, _PAGE_CONTEXT.get())]))

    def _button(self, action: str, sequence: int, revision: int, disabled: bool = False, *, extra: int = 0, row: int | None = None,
                label: str | None = None):
        """@description: Build one card button that remembers the list page the current action came from.
        @param self: Card handler.
        @param action: Card action.
        @param sequence: Acceptance order.
        @param revision: Displayed revision.
        @param disabled: Whether the action does not apply.
        @param extra: Opaque choice of a song button.
        @param row: Button row.
        @param label: Text overriding the default label.
        @return: Dynamic button.
        """
        return self.button_class(action, sequence, revision, disabled=disabled, extra=extra, row=row, label=label, page=_PAGE_CONTEXT.get())

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
