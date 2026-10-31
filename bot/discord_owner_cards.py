"""Present the Owner's pending cases as private cards whose buttons decide only what they display, and keep working after a restart.

Inputs are Owner interactions and the injected review service; every decision goes through the service's preview and confirmation, so revisions, tokens and settlement stay in one place.
Button ids carry the acceptance order and the displayed revision, never an operation id, so an old button is refused rather than applied to a changed case.
"""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from datetime import datetime

import discord

from bot.discord_owner_review import (
    IDENTITY_LABELS, MAX_REASON_LENGTH, MAX_DURATION_OPTION_SECONDS, NO_MENTIONS, REVIEW_LABELS, SETTLEMENT_UNCONFIRMED_TEXT,
    OwnerGate, _call_service, _confirmed_text, _guarded, _Reply,
)
from bot.owner_card_versions import (
    CATEGORY_BY_ORDINAL, INSUFFICIENT_TEXT, KNOWN_VERSIONS_PER_CARD, NEW_HEADLINE, NEW_REASON, NO_VERSION_ERROR, OTHER_MENU_TEXT, PROMISE_LINES,
    REMASTER_LABEL, REMASTER_REASON, SAME_HEADLINE, SAME_REASON, SOURCE_REQUIRED_TEXT, category_headline, category_reason, manual_check_source,
    new_version_identity, render_known_versions, retention_warning, version_evidence, watch_url,
)
from bot.evidence_record import VersionClaimKind
from bot.owner_review import OwnerReviewService, ReviewAction, ReviewOutcomeUnknown, require_traceable_source
from bot.review import DurationEvidenceExpired, ReviewStatus, StaleReviewVersion
from bot.song_identity import video_identity_from_url
from bot.song_judgement import ContentJudgement
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus

_LOGGER = logging.getLogger(__name__)
CARD_PATTERN = (r"ocard:(?P<action>approve|reject|duration|next|reload|new|same|other|vpage|kind|remaster|back|opage):(?P<sequence>\d+):(?P<revision>\d+)"
                r"(?::(?P<extra>\d+))?")  # [button id]: Action, acceptance order, displayed revision and, for version buttons, an opaque choice.
EXTRA_ACTIONS = {"same", "vpage", "kind", "remaster", "opage"}  # [choice carriers]: Actions whose id also carries a known version or a page cursor.
VERSION_ACTIONS = {"new", "same", "other", "vpage", "kind", "remaster", "back", "opage"}  # [version buttons]: Offered only while the version is unresolved.
APPROVE_REASON = "Owner 於案件卡片核准歌曲資格"  # [audit reason]: Stored with an approval made from a card.
DURATION_REASON = "Owner 於案件卡片補完整影片時長"  # [audit reason]: Stored with a duration entered from a card.
CITATION_PREFIX = "owner-card:"  # [audit citation]: Followed by the video id the Owner just viewed on the card.
DECIDABLE_STATES = {ReviewStatus.PENDING_VALIDATION, ReviewStatus.PENDING_REVIEW, ReviewStatus.VALIDATION_FAILED}  # [open reviews]: States a card may reject.
DURATION_STATES = {ReviewStatus.PENDING_VALIDATION, ReviewStatus.VALIDATION_FAILED}  # [missing duration]: States where a duration entry makes sense.
MAX_TRACKED_LOCKS = 1000  # [memory bound]: Unlocked per-case locks are dropped beyond this many.
BUTTON_LABELS = {"approve": "確認通過", "reject": "拒絕", "duration": "補時長", "next": "下一筆", "reload": "重新載入",
                 "new": "確認為新的錄音版本", "same": "確認為同一錄音", "other": "其他情況…", "vpage": "更多已知版本",
                 "kind": "類別", "remaster": "重製：同一錄音", "back": "返回", "opage": "更多已知版本"}  # [button text]: One label per action.
BUTTON_STYLES = {"approve": discord.ButtonStyle.success, "reject": discord.ButtonStyle.danger, "duration": discord.ButtonStyle.secondary,
                 "next": discord.ButtonStyle.secondary, "reload": discord.ButtonStyle.primary, "new": discord.ButtonStyle.success,
                 "same": discord.ButtonStyle.success, "other": discord.ButtonStyle.secondary, "vpage": discord.ButtonStyle.secondary,
                 "kind": discord.ButtonStyle.primary, "remaster": discord.ButtonStyle.primary, "back": discord.ButtonStyle.secondary,
                 "opage": discord.ButtonStyle.secondary}  # [button look]: One style per action.
EMPTY_QUEUE_TEXT = "目前沒有待處理案件。"
NO_MORE_TEXT = "目前沒有更多待處理案件。"
STALE_CARD_TEXT = "此案件已變更或已處理，這張卡片已失效，沒有執行任何操作。請按「重新載入」。"  # [stale card]: Never claims a decision.
UNKNOWN_OUTCOME_TEXT = "結果未知（連線中斷，決定可能已儲存）。請再按一次同一個按鈕，系統會以原決定查回結果，不會重複執行。"
DURATION_FORMAT_TEXT = "秒數必須是整數（0 到 86400）。"
EXPIRED_BASIS_TEXT = "官方時長資料已過期，不能再依它核准。沒有執行任何操作；請等待系統重新驗證，或按「補時長」由你輸入時長與來源。"  # [expired API basis]: The case stays pending.
MAX_LINK_CHARS = 300  # [message budget]: A longer submitted link is shown as its canonical watch link so the message stays under Discord's 2000-character limit.
FORM_ACTIONS = {"reject", "duration", "kind", "remaster"}  # [forms]: Actions that open a modal instead of acting at once.


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


def waiting_reason(item) -> str:
    """@description: Say in plain words what the case is waiting for, from its review and version states only.
    @param item: Pending row of the card.
    @return: One Traditional Chinese phrase.
    """
    if item.identity_status is VersionIdentityStatus.CONFLICT:
        return f"版本證據衝突：{item.conflict_reason or '待 Owner 處理'}"
    if item.review_status is ReviewStatus.PENDING_REVIEW and item.api_basis == "expired":
        return "官方時長資料已過期，等待自動重新驗證，或由 Owner 補時長；在此之前不能核准"
    reasons = {ReviewStatus.PENDING_VALIDATION: "尚無官方時長，等待自動取得或由 Owner 補時長",
               ReviewStatus.VALIDATION_FAILED: "無法取得影片資料（可能已刪除或未公開），待 Owner 處理",
               ReviewStatus.PENDING_REVIEW: "待 Owner 審核歌曲資格",
               ReviewStatus.APPROVED: "歌曲已核准，版本待 Owner 判斷"}
    return reasons.get(item.review_status, "待處理")


def _may_enter_duration(item) -> bool:
    """@description: Offer the duration entry where the duration is missing, or where the only official basis has expired.
    @param item: Pending row of the card.
    @return: Whether the Owner may enter a duration for this case.
    """
    if item.duration_seconds is not None:
        return False
    return item.review_status in DURATION_STATES or (item.review_status is ReviewStatus.PENDING_REVIEW and item.api_basis == "expired")


def _card_link(url: str) -> str:
    """@description: Keep a very long submitted link from pushing the card past Discord's message limit without breaking it.
    @param url: Link exactly as submitted.
    @return: The link as submitted within the card's budget, otherwise the canonical watch link of the same video.
    """
    if len(url) <= MAX_LINK_CHARS:
        return url
    return watch_url(video_identity_from_url(url).video_id)


def _version_line(item) -> str:
    """@description: State the version status without presenting a missing mapping as a finding about the recording.
    @param item: Pending row of the card.
    @return: Version line; an unresolved version says the system lacks a mapping, not that the recording is new.
    """
    label = IDENTITY_LABELS[item.identity_status]
    if item.identity_status is VersionIdentityStatus.UNRESOLVED:
        return f"版本狀態：{label}（系統沒有這支影片的版本對應；這不代表它是新錄音）"
    return f"版本狀態：{label}"


def render_card(item, versions=None, has_more_versions: bool = False) -> str:
    """@description: Show one pending case with everything needed to decide it, and never its internal operation id.
    @param item: Pending row with submitter, link, duration and states.
    @param versions: Known versions of this page when the version is unresolved, otherwise None.
    @param has_more_versions: Whether a further page of known versions exists.
    @return: Card text; an unresolved version also lists the known versions and states what each version button promises.
    """
    lines = ["待處理案件", f"投稿者：<@{item.submitter_id}>", f"影片：{_card_link(item.source_url)}", f"已知時長：{_duration_text(item.duration_seconds, item.api_basis)}",
             f"審核狀態：{REVIEW_LABELS[item.review_status]}", _version_line(item), f"待處理：{waiting_reason(item)}",
             "「確認通過」只核准歌曲資格；錄音版本仍須另外確認，也不保證發獎。"]
    if versions is not None:
        lines += ["", render_known_versions(versions, has_more_versions), "", *PROMISE_LINES]
    return "\n".join(lines)


def render_result(item, confirmed_text: str, reward_note: str) -> str:
    """@description: Replace a decided card with what really happened, so the Owner never has to infer it.
    @param item: Pending row the decision was made on.
    @param confirmed_text: The service's committed-revision sentence.
    @param reward_note: Reward and version state after the decision, or an empty string.
    @return: Result text.
    """
    lines = ["案件處理結果", f"投稿者：<@{item.submitter_id}>", f"影片：{_card_link(item.source_url)}", confirmed_text]
    return "\n".join(lines + ([reward_note] if reward_note else []))


class _CardButton(discord.ui.DynamicItem[discord.ui.Button], template=CARD_PATTERN):
    """Rebuild itself from its custom_id and hand every press to the handler bound by a subclass."""

    cards: "OwnerCards"  # [bound handler]: Set on the subclass the client registers; the base class is never registered.

    def __init__(self, action: str, sequence: int, revision: int, *, disabled: bool = False, extra: int = 0, row: int | None = None,
                 label: str | None = None) -> None:
        """@description: Bind the action to the case position and revision that the card displayed.
        @param self: Button.
        @param action: One of the card actions.
        @param sequence: Acceptance order of the displayed case.
        @param revision: Review revision the card displayed.
        @param disabled: Whether the action does not apply to the displayed state.
        @param extra: Opaque choice of a version button: a known version's first acceptance order, or a page cursor.
        @param row: Button row, so the groups of actions stay in their own rows.
        @param label: Text overriding the action's default label.
        @return: None.
        """
        self.action, self.sequence, self.revision, self.extra = action, sequence, revision, extra
        tail = f":{extra}" if action in EXTRA_ACTIONS else ""
        super().__init__(discord.ui.Button(label=label or BUTTON_LABELS[action], style=BUTTON_STYLES[action], disabled=disabled,
                                           custom_id=f"ocard:{action}:{sequence}:{revision}{tail}"), row=row)

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match):
        """@description: Rebuild a button from an old message after a restart.
        @param cls: Button class.
        @param interaction: Press being routed.
        @param item: Component Discord sent.
        @param match: Template match carrying action, order and revision.
        @return: Button ready to run its callback.
        """
        return cls(match["action"], int(match["sequence"]), int(match["revision"]), extra=int(match["extra"] or 0))

    async def callback(self, interaction: discord.Interaction) -> None:
        """@description: Run the pressed action through the handler.
        @param self: Button.
        @param interaction: Press.
        @return: None.
        """
        await self.cards.press(interaction, self.action, self.sequence, self.revision, self.extra)


def _make_button_class(cards: "OwnerCards"):
    """@description: Bind the restart-safe button class to one card handler, so its callback needs no stored message state.
    @param cards: Handler that decides what a press does.
    @return: Dynamic button class to register with the client.
    """
    return type("OwnerCardButton", (_CardButton,), {"cards": cards}, template=CARD_PATTERN)


class _CardModal(discord.ui.Modal):
    """Collect the one or two typed values a card action needs, then hand them back to the handler."""

    def __init__(self, cards: "OwnerCards", action: str, sequence: int, revision: int, extra: int = 0) -> None:
        """@description: Build the small form for rejection, duration or a version category, bound to the displayed case and revision.
        @param self: Form.
        @param cards: Handler that applies the submission.
        @param action: reject, duration, kind or remaster.
        @param sequence: Acceptance order of the displayed case.
        @param revision: Review revision the card displayed.
        @param extra: Category ordinal, or the first acceptance order of the version to remaster.
        @return: None.
        """
        super().__init__(title=_modal_title(action, extra), timeout=300)
        self._cards, self._action, self._sequence, self._revision, self._extra = cards, action, sequence, revision, extra
        if action == "reject":
            self.reason = discord.ui.TextInput(label="拒絕理由", style=discord.TextStyle.paragraph, max_length=MAX_REASON_LENGTH)
            self.add_item(self.reason)
        elif action in ("kind", "remaster"):
            self.source = discord.ui.TextInput(label="判斷依據（可追溯的連結或說明，必填）", style=discord.TextStyle.paragraph,
                                               min_length=8, max_length=400)
            self.add_item(self.source)
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
        values = {"reject": lambda: (self.reason.value,), "kind": lambda: (self.source.value,), "remaster": lambda: (self.source.value,),
                  "duration": lambda: (self.seconds.value, self.source.value)}[self._action]()
        await self._cards.submit(interaction, self._action, self._sequence, self._revision, values, self._extra)


def _modal_title(action: str, extra: int) -> str:
    """@description: Title the form after the action, keeping it within Discord's 45-character modal title limit.
    @param action: reject, duration, kind or remaster.
    @param extra: Category ordinal for a category form.
    @return: Form title.
    """
    if action == "kind":
        return f"{CATEGORY_BY_ORDINAL.get(extra, (None, '類別'))[1]}：判斷依據"
    if action == "remaster":
        return f"{REMASTER_LABEL}：判斷依據"
    return "拒絕此案件" if action == "reject" else "補完整影片時長"


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

    async def press(self, interaction: discord.Interaction, action: str, sequence: int, revision: int, extra: int = 0) -> None:
        """@description: Admit the Owner, then open a form or run the pressed action and update the card with what really happened.
        @param self: Card handler.
        @param interaction: Button press.
        @param action: Pressed action.
        @param sequence: Acceptance order in the button id.
        @param revision: Displayed revision in the button id.
        @param extra: Opaque choice of a version button.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        if action in FORM_ACTIONS:
            await interaction.response.send_modal(_CardModal(self, action, sequence, revision, extra))
            return
        await interaction.response.defer()
        reply = (await _guarded(self._run(interaction.user.id, action, sequence, revision, (), extra)))[0]
        await self._show(interaction, reply)

    async def submit(self, interaction: discord.Interaction, action: str, sequence: int, revision: int, values: tuple[str, ...],
                     extra: int = 0) -> None:
        """@description: Admit the Owner again, then apply a submitted form and update the card.
        @param self: Card handler.
        @param interaction: Form submission.
        @param action: reject, duration, kind or remaster.
        @param sequence: Acceptance order the form was opened for.
        @param revision: Displayed revision the form was opened for.
        @param values: Typed values.
        @param extra: Category ordinal or version choice the form was opened for.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        await interaction.response.defer()
        reply = (await _guarded(self._run(interaction.user.id, action, sequence, revision, values, extra)))[0]
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

    async def _run(self, actor_id: int, action: str, sequence: int, revision: int, values: tuple[str, ...], extra: int = 0) -> _Reply:
        """@description: Serialize presses of one case and route the action.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param action: Pressed or submitted action.
        @param sequence: Acceptance order of the displayed case.
        @param revision: Displayed revision.
        @param values: Typed form values, empty for plain buttons.
        @param extra: Opaque choice of a version button.
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
            if action in VERSION_ACTIONS:
                return await self._version_action(actor_id, item, action, extra, values)
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
        allowed = {"approve": {ReviewStatus.PENDING_REVIEW}, "reject": DECIDABLE_STATES, "duration": DURATION_STATES | {ReviewStatus.PENDING_REVIEW}}[action]
        if item.review_status not in allowed or (action == "duration" and not _may_enter_duration(item)):
            return self._stale_reply(item.accepted_sequence)
        try:
            if action == "duration":
                return await self._duration(actor_id, item, values)
            return await self._judgement(actor_id, item, action, values)
        except StaleReviewVersion:
            return self._stale_reply(item.accepted_sequence)
        except DurationEvidenceExpired:
            return await self._card_reply(actor_id, item, note=EXPIRED_BASIS_TEXT)

    async def _version_action(self, actor_id: int, item, action: str, extra: int, values: tuple[str, ...] = ()) -> _Reply:
        """@description: Run a version button only while the system still lacks the version; every other state is a stale card.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Current pending row, already matched to the displayed revision.
        @param action: A version action: new, same, other, vpage, kind, remaster, back or opage.
        @param extra: Opaque choice: a known version's first acceptance order, a category ordinal, or a page cursor.
        @param values: Typed form values for a category form.
        @return: Result reply, the card or menu again, or the stale reply.
        """
        if item.identity_status is not VersionIdentityStatus.UNRESOLVED:
            return self._stale_reply(item.accepted_sequence)
        if action in ("other", "opage"):
            return await self._menu_reply(actor_id, item, versions_after=extra if action == "opage" else None)
        if action == "back":
            return await self._card_reply(actor_id, item)
        if action == "vpage":
            return await self._card_reply(actor_id, item, versions_after=extra)
        try:
            if action in ("kind", "remaster"):
                return await self._confirm_category(actor_id, item, action, extra, values[0].strip())
            return await self._confirm_version(actor_id, item, action, extra)
        except StaleReviewVersion:
            return self._stale_reply(item.accepted_sequence)

    async def _confirm_version(self, actor_id: int, item, action: str, extra: int) -> _Reply:
        """@description: Record the Owner's version judgement against the displayed revision: a new version, or the selected known one.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row at the displayed revision.
        @param action: new or same.
        @param extra: First acceptance order of the selected known version; unused for a new version.
        @return: Result reply, or the stale reply when the selected version no longer exists.
        """
        known = None if action == "new" else await self._known_version(actor_id, extra)
        if action == "same" and known is None:
            return self._stale_reply(item.accepted_sequence)
        retrieved_at, reason = self._now(), NEW_REASON if known is None else SAME_REASON

        def preview(service: OwnerReviewService):
            """@description: Build the evidence on the worker thread and preview the version confirmation at the displayed revision.
            @param service: Review service.
            @return: Version-confirmation preview.
            """
            video = service.view(actor_id, item.operation_id).review.video
            identity = new_version_identity() if known is None else RecordingVersionIdentity(known.version_id)
            source = manual_check_source(video.video_id, None if known is None else known.first_video_id)
            evidence = version_evidence(video, identity, source, retrieved_at, reason, actor_id)
            return service.preview_version_confirmation(actor_id, item.operation_id, evidence, reason, item.revision)

        key_action = "new" if known is None else f"same:{extra}"
        return await self._confirm(actor_id, item, key_action, preview, NEW_HEADLINE if known is None else SAME_HEADLINE)

    async def _confirm_category(self, actor_id: int, item, action: str, extra: int, source: str) -> _Reply:
        """@description: Record a version under an existing category with the Owner's own source, keeping the case pending when the existing rules find it insufficient.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row at the displayed revision.
        @param action: kind for a distinct version, remaster for a mapping to a known version.
        @param extra: Category ordinal, or the first acceptance order of the version to remaster.
        @param source: Judgement basis the Owner typed; never filled in by the system.
        @return: Result reply, the stale reply, or the card again with the reason nothing was recorded.
        """
        try:
            require_traceable_source(source)  # [same rule as /owner confirm_version]: Checked before any service call; no new threshold.
        except ValueError:
            return await self._card_reply(actor_id, item, note=SOURCE_REQUIRED_TEXT)
        known = await self._known_version(actor_id, extra) if action == "remaster" else None
        category = (VersionClaimKind.REMASTER, REMASTER_LABEL) if action == "remaster" else CATEGORY_BY_ORDINAL.get(extra)
        if category is None or (action == "remaster" and known is None):
            return self._stale_reply(item.accepted_sequence)
        kind, label = category
        reason = REMASTER_REASON if known else category_reason(label)
        preview = self._category_preview(actor_id, item, known, kind, source, reason)
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]  # [retry key]: The same form retries one token; a different source is a different decision.
        try:
            return await self._confirm(actor_id, item, f"{action}:{extra}:{digest}", preview, category_headline(label, known is not None))
        except ValueError as error:
            if NO_VERSION_ERROR not in str(error):
                raise
            return await self._card_reply(actor_id, item, note=INSUFFICIENT_TEXT)

    def _category_preview(self, actor_id: int, item, known, kind: VersionClaimKind, source: str, reason: str) -> Callable[[OwnerReviewService], object]:
        """@description: Build the worker-thread step that creates the categorised evidence and previews the confirmation at the displayed revision.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row at the displayed revision.
        @param known: Known version to map to, or None to create a new opaque version.
        @param kind: Existing version category the Owner chose.
        @param source: Judgement basis the Owner typed.
        @param reason: Audit explanation.
        @return: Function that takes the review service and returns the version-confirmation preview.
        """
        retrieved_at = self._now()

        def preview(service: OwnerReviewService):
            """@description: Build the evidence and preview the confirmation.
            @param service: Review service.
            @return: Version-confirmation preview.
            """
            video = service.view(actor_id, item.operation_id).review.video
            identity = new_version_identity() if known is None else RecordingVersionIdentity(known.version_id)
            evidence = version_evidence(video, identity, source, retrieved_at, reason, actor_id, kind)
            return service.preview_version_confirmation(actor_id, item.operation_id, evidence, reason, item.revision)

        return preview

    async def _menu_reply(self, actor_id: int, item, *, versions_after: int | None = None) -> _Reply:
        """@description: Show the other-cases menu: the existing categories, and the known versions a remaster can be mapped to.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row to show.
        @param versions_after: Page cursor of the known versions, or None for the first page.
        @return: Menu text and view; nothing is written.
        """
        page = await _call_service(self._service_provider, lambda service: service.list_known_versions(
            actor_id, after_sequence=versions_after, limit=KNOWN_VERSIONS_PER_CARD))
        text = f"{render_card(item)}\n\n{OTHER_MENU_TEXT}\n\n{render_known_versions(page.items, page.next_after_sequence is not None)}"
        sequence, revision = item.accepted_sequence, item.revision
        buttons = [self._button("kind", sequence, revision, extra=ordinal, row=0, label=label) for ordinal, (_, label) in CATEGORY_BY_ORDINAL.items()]
        buttons += [self._button("remaster", sequence, revision, extra=version.first_sequence, row=1, label=f"重製：同一錄音 {number}")
                    for number, version in enumerate(page.items, 1)]
        buttons.append(self._button("back", sequence, revision, row=2))
        if page.next_after_sequence is not None:
            buttons.append(self._button("opage", sequence, revision, extra=page.next_after_sequence, row=2))
        return _Reply(text, self._view(buttons))

    async def _known_version(self, actor_id: int, first_sequence: int):
        """@description: Find the known version a button refers to, or nothing when it no longer exists.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param first_sequence: First acceptance order carried by the button.
        @return: Known version, or None.
        """
        page = await _call_service(self._service_provider, lambda service: service.list_known_versions(
            actor_id, after_sequence=None if first_sequence <= 1 else first_sequence - 1, limit=1))
        return page.items[0] if page.items and page.items[0].first_sequence == first_sequence else None

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
            actor_id, item.operation_id, int(seconds_text), source, DURATION_REASON, expected_revision=item.revision))

    async def _confirm(self, actor_id: int, item, action: str, preview_call: Callable[[OwnerReviewService], object], headline: str = "") -> _Reply:
        """@description: Preview once, confirm immediately, and keep the token if the outcome is unknown so a retry replays the same decision.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row at the displayed revision.
        @param action: Action name, part of the token key.
        @param preview_call: Service preview for this action.
        @param headline: Sentence stating what the Owner's judgement recorded, shown above the committed revision.
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
            return await self._card_reply(actor_id, item, note=UNKNOWN_OUTCOME_TEXT)
        self._open_tokens.pop(key, None)
        note = await self._reward_note(actor_id, item.operation_id)
        confirmed = f"{headline}\n{_confirmed_text(snapshot)}" if headline else _confirmed_text(snapshot)
        return _Reply(render_result(item, confirmed, note), self._view([self._button("next", item.accepted_sequence, 0)]))

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
        return await self._card_reply(actor_id, page.items[0])

    async def _card_reply(self, actor_id: int, item, *, versions_after: int | None = None, note: str = "") -> _Reply:
        """@description: Build a case card; while the version is unresolved it also lists one page of known versions with a one-press button each.
        @param self: Card handler.
        @param actor_id: Admitted Owner identifier.
        @param item: Pending row to show.
        @param versions_after: Page cursor of the known versions, or None for the first page.
        @param note: Sentence appended below the card, such as guidance or an unknown outcome.
        @return: Card text and view.
        """
        versions, next_after = None, None
        if item.identity_status is VersionIdentityStatus.UNRESOLVED:
            page = await _call_service(self._service_provider, lambda service: service.list_known_versions(
                actor_id, after_sequence=versions_after, limit=KNOWN_VERSIONS_PER_CARD))
            versions, next_after = page.items, page.next_after_sequence
        warning = await self._retention_warning(actor_id)
        text = warning + render_card(item, versions, next_after is not None)
        return _Reply(f"{text}\n\n{note}" if note else text, self._card_view(item, versions or (), next_after))

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
        return _Reply(STALE_CARD_TEXT, self._view([self._button("reload", sequence, 0)]))

    def _button(self, action: str, sequence: int, revision: int, disabled: bool = False, *, extra: int = 0, row: int | None = None,
                label: str | None = None):
        """@description: Build one card button.
        @param self: Card handler.
        @param action: Card action.
        @param sequence: Acceptance order.
        @param revision: Displayed revision.
        @param disabled: Whether the action does not apply.
        @param extra: Opaque choice of a version button.
        @param row: Button row.
        @param label: Text overriding the default label.
        @return: Dynamic button.
        """
        return self.button_class(action, sequence, revision, disabled=disabled, extra=extra, row=row, label=label)

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

    def _card_view(self, item, versions=(), next_after: int | None = None) -> discord.ui.View:
        """@description: Offer only the actions that apply to the displayed state; version actions appear only while the system lacks the version.
        @param self: Card handler.
        @param item: Pending row.
        @param versions: Known versions of this page, each with its own one-press button.
        @param next_after: Cursor of the next page of known versions, or None.
        @return: Card view.
        """
        sequence, revision, status = item.accepted_sequence, item.revision, item.review_status
        buttons = [self._button("approve", sequence, revision, status is not ReviewStatus.PENDING_REVIEW or item.api_basis == "expired", row=0),
                   self._button("reject", sequence, revision, status not in DECIDABLE_STATES, row=0)]
        if _may_enter_duration(item):
            buttons.append(self._button("duration", sequence, revision, row=0))
        buttons.append(self._button("next", sequence, 0, row=0))
        if item.identity_status is VersionIdentityStatus.UNRESOLVED:
            buttons += [self._button("new", sequence, revision, row=1), self._button("other", sequence, revision, row=1)]
            buttons += [self._button("same", sequence, revision, extra=version.first_sequence, row=2, label=f"確認為同一錄音 {number}")
                        for number, version in enumerate(versions, 1)]
            if next_after is not None:
                buttons.append(self._button("vpage", sequence, revision, extra=next_after, row=3))
        return self._view(buttons)
