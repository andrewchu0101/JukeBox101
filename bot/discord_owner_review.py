"""Expose the Owner-only review commands for one private Guild, gating every callback before any private read.

Inputs are Discord interactions and an injected OwnerReviewService provider; blocking service calls run on worker threads and every reply is ephemeral with mentions disabled.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from functools import partial

import discord
from discord import app_commands

from bot.evidence_record import (
    MAX_REASON_LENGTH, EvidenceRecord, EvidenceSourceType, VersionClaimKind, record_evidence,
)
from bot.owner_review import (
    CONFLICT_IDENTITY_NOTE, ExpiredReviewToken, OwnerReviewService, ReviewAction, ReviewOutcomeUnknown, ReviewPreview,
    UnknownReviewToken, require_traceable_source,
)
from bot.review import ReviewSnapshot, ReviewStatus
from bot.song_judgement import ContentJudgement
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus

GATE_REFUSAL = "此指令僅限指定私人伺服器中的 Owner 使用。"  # [gate refusal]: Identical for wrong guild and non-Owner, so it reveals nothing.
BASIC_VERSION_KIND = "basic"  # [version choice]: Maps to no VersionClaimKind, meaning the version is not a named category.
VERSION_KIND_NAMES = (BASIC_VERSION_KIND, *(kind.name for kind in VersionClaimKind))  # [version choices]: Slash-command vocabulary.
VERSION_KIND_CHOICES = [app_commands.Choice(name=name, value=name) for name in VERSION_KIND_NAMES]
CONFIRM_TIMEOUT_SECONDS = 300  # [confirmation window]: Matches the service's default preview lifetime.
PAGE_SIZE = 20  # [page size]: Pending rows per list page; each button cursor is a multiple of this.
LIST_CURSOR_PREFIX = "owner-list:"  # [cursor encoding]: Each page button's custom_id carries the keyset position of the page it opens.
FIRST_PAGE_TOKEN = "f"  # [first page marker]: Stands for no after-sequence inside a cursor.
MAX_CURSOR_DEPTH = 3  # [cursor stack depth]: Earlier cursors kept per button, so the custom_id stays under Discord's 100-character limit.
MAX_MESSAGE_CHARS = 2000  # [message size]: Discord's content limit for one message.
MAX_VALUE_CHARS = MAX_REASON_LENGTH  # [value clip]: Reasons are already capped at this; longer sources are ellipsized so no line overflows.
NO_MENTIONS = discord.AllowedMentions.none()  # [mention policy]: Replies never ping anyone.
INTERNAL_ERROR_TEXT = "系統內部錯誤，操作未完成。請先使用 /owner show 確認案件狀態，再決定是否重試；若持續發生，請回報開發者。"  # [unexpected failure]: Never claims a missing case.
PREVIEW_RENDER_FAILED_TEXT = "預覽內容無法顯示，未建立確認按鈕，此次決定未送出。請回報開發者。"  # [render failure]: The issued token has no button, so it cannot be confirmed.
STALE_PAGE_TEXT = "此分頁已過期，請重新執行 /owner list。"  # [stale cursor]: Plain message; never resolves to any case.
CANCELLED_CONFIRM_TEXT = "此預覽已取消，請重新預覽。"  # [forbidden preview]: Confirm after a cancel never reaches the service.
BOTH_OR_NEITHER_CITATION_TEXT = "evidence_id 與 source 必須二擇一：既有證據填 evidence_id，新證據填 source。"  # [citation rule]: Refused before any service call.

REVIEW_LABELS = {  # [status text]: Owner-facing names for each review state.
    ReviewStatus.PENDING_VALIDATION: "待驗證",
    ReviewStatus.PENDING_REVIEW: "待審核",
    ReviewStatus.APPROVED: "已核准",
    ReviewStatus.REJECTED: "已拒絕",
    ReviewStatus.VALIDATION_FAILED: "驗證失敗",
}
IDENTITY_LABELS = {  # [identity text]: Owner-facing names for each version identity state.
    VersionIdentityStatus.UNRESOLVED: "未確認",
    VersionIdentityStatus.CONFIRMED: "已確認",
    VersionIdentityStatus.CONFLICT: "衝突",
}
ACTION_LABELS = {  # [action text]: Owner-facing name for every decision a preview can show; each ReviewAction needs one.
    ReviewAction.APPROVE: "核准為歌曲",
    ReviewAction.REJECT: "拒絕",
    ReviewAction.REEXAMINE: "重審",
    ReviewAction.CONFIRM_VERSION: "確認版本",
}
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Reply:
    """Carry one reply's text and an optional confirmation view."""

    text: str  # [reply text]: Traditional Chinese content shown only to the Owner.
    view: discord.ui.View | None = None  # [confirmation]: Present only on previews.


@dataclass(frozen=True)
class _Citation:
    """Carry the Owner's evidence citation exactly as typed, before it is checked or resolved."""

    evidence_id: str | None  # [existing evidence]: Cited record, read back through the service.
    source: str | None  # [new provenance]: Validated as traceable before any service call.
    version_name: str | None  # [new version claim]: Required only when a new record is created.
    version_kind: str  # [new version category]: Slash choice, validated before any service call.


class OwnerGate:
    """Admit an interaction only from the configured Owner inside the configured private Guild."""

    def __init__(self, owner_id: int, guild_id: int) -> None:
        """@description: Bind the only caller and place that may use the review commands.
        @param self: Gate instance shared by commands and confirmation buttons.
        @param owner_id: Configured Owner Discord user snowflake.
        @param guild_id: Configured private Guild snowflake.
        @return: None.
        """
        self._owner_id = owner_id  # [authorization authority]: Compared against the live interaction user.
        self._guild_id = guild_id  # [scope authority]: Compared against the live interaction guild.

    async def admit(self, interaction: discord.Interaction) -> bool:
        """@description: Refuse before any private data is read, with the same reply for every failed check.
        @param self: Gate instance.
        @param interaction: Interaction being admitted, including a button press's own interaction.
        @return: Whether the callback may continue.
        """
        if interaction.guild_id == self._guild_id and interaction.user.id == self._owner_id:
            return True
        await interaction.response.send_message(GATE_REFUSAL, ephemeral=True, allowed_mentions=NO_MENTIONS)
        return False


class ConfirmState(Enum):
    """Name each lifecycle state of one previewed decision so every press has exactly one meaning."""

    OPEN = "open"  # [awaiting decision]: Confirm may run, and Cancel may forbid it.
    IN_FLIGHT = "in_flight"  # [write started]: The service may already have committed, so Cancel can no longer forbid it.
    DONE = "done"  # [spent result]: The decision committed; repeat presses replay the stored text.
    UNKNOWN = "unknown"  # [commit uncertain]: Token and decision key are kept; the same confirmation retries under the same key.
    CANCELLED = "cancelled"  # [forbidden]: Terminal, reachable only from OPEN.


class ConfirmView(discord.ui.View):
    """Hold one previewed decision as an explicit state machine so Confirm and Cancel never give a wrong answer."""

    def __init__(self, gate: OwnerGate, service_provider: Callable[[], OwnerReviewService], actor_id: int,
                 operation_id: str, token_id: str) -> None:
        """@description: Bind a preview token to its Owner, case and service without reading any review data.
        @param self: Confirmation view attached to one preview reply.
        @param gate: Gate re-applied to every button press.
        @param service_provider: Returns the review service used for confirmation.
        @param actor_id: Owner who previewed the decision.
        @param operation_id: Case the preview decides, named in every status message.
        @param token_id: Single-use token issued by the preview.
        @return: None.
        """
        super().__init__(timeout=CONFIRM_TIMEOUT_SECONDS)
        self._gate = gate
        self._service_provider = service_provider
        self._actor_id = actor_id
        self._operation_id = operation_id  # [case id]: Lets the Owner inspect the same case the preview named.
        self._token_id = token_id
        self._state = ConfirmState.OPEN  # [preview state]: Mutated only on the event loop, so checks and changes never interleave.
        self._result_text: str | None = None  # [spent result]: Confirmation text replayed once the state is DONE.
        self._lock = asyncio.Lock()  # [single execution]: Serializes confirmations so each attempt reaches the service once.
        self.confirm_button = discord.ui.Button(label="確認", style=discord.ButtonStyle.danger)
        self.confirm_button.callback = self._on_confirm
        self.cancel_button = discord.ui.Button(label="取消", style=discord.ButtonStyle.secondary)
        self.cancel_button.callback = self._on_cancel
        self.add_item(self.confirm_button)
        self.add_item(self.cancel_button)

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        """@description: Re-verify the presser, then run the previewed decision through the state machine.
        @param self: Confirmation view.
        @param interaction: Button press interaction, checked on its own guild and user.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with self._lock:
            replies = await _guarded(self._confirm())
        for reply in replies:
            await _followup(interaction, reply)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        """@description: Forbid the decision only while it is still open; otherwise say what state it is really in.
        @param self: Confirmation view.
        @param interaction: Button press interaction, checked on its own guild and user.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        await interaction.response.send_message(self._cancel(), ephemeral=True, allowed_mentions=NO_MENTIONS)

    def _cancel(self) -> str:
        """@description: Move OPEN to CANCELLED and refuse every other cancel with the truth about the state.
        @param self: Confirmation view.
        @return: Owner-facing cancel text.
        """
        if self._state is ConfirmState.OPEN:
            self._state = ConfirmState.CANCELLED
            return "已取消，此預覽不會執行。"
        return self._cancel_refusal()

    def _cancel_refusal(self) -> str:
        """@description: Explain why a cancel cannot forbid the decision, naming the case and the way forward.
        @param self: Confirmation view.
        @return: Owner-facing text for IN_FLIGHT, UNKNOWN, DONE or CANCELLED.
        """
        if self._state is ConfirmState.IN_FLIGHT:
            return (f"案件 {self._operation_id} 的確認已開始，結果尚未確定，無法取消。"
                    "請重新按「確認」，以同一預覽與同一決定鍵取得結果；或使用 /owner show 查看此案件。")
        if self._state is ConfirmState.UNKNOWN:
            return f"案件 {self._operation_id} 的結果未知，無法取消，驗證資訊已保留。請重新按「確認」以取得原結果。"
        if self._state is ConfirmState.DONE:
            return "此預覽已確認執行，無法取消。"
        return "此預覽已取消。"

    async def _confirm(self) -> list[_Reply]:
        """@description: Run the previewed decision once, record its outcome, and replay a success on every later press.
        @param self: Confirmation view.
        @return: Confirmation replies.
        """
        if self._state is ConfirmState.DONE:
            return [_Reply(self._result_text)]
        if self._state is ConfirmState.CANCELLED:
            return [_Reply(CANCELLED_CONFIRM_TEXT)]
        self._state = ConfirmState.IN_FLIGHT
        try:
            snapshot = await _call_service(self._service_provider, lambda service: service.confirm(self._actor_id, self._token_id))
        except ReviewOutcomeUnknown:
            self._state = ConfirmState.UNKNOWN  # [kept verification]: The token stays so the retry replays the same decision key.
            raise
        except Exception:
            self._state = ConfirmState.OPEN  # [refused before commit]: The service did not write, so the preview is still usable.
            raise
        self._result_text = _confirmed_text(snapshot)
        self._state = ConfirmState.DONE
        return [_Reply(self._result_text)]


@dataclass(frozen=True)
class _PageCursor:
    """Carry one decoded page cursor: where the page starts and the stack of cursors that lead back to the first page."""

    after_sequence: int | None  # [keyset position]: Rows accepted after this sequence are shown; None starts at the beginning.
    history: tuple[int | None, ...] = ()  # [previous cursors]: Oldest first; the last entry is what Previous returns to. Bounded.


class PendingPageView(discord.ui.View):
    """Offer Previous and Next on one keyset page; each button's custom_id carries the cursor it opens, including the stack of earlier cursors."""

    def __init__(self, gate: OwnerGate, service_provider: Callable[[], OwnerReviewService], cursor: _PageCursor,
                 next_after_sequence: int | None) -> None:
        """@description: Build the two page buttons, each pointing at its target cursor and disabled when no page lies that way.
        @param self: Page view attached to one list reply.
        @param gate: Gate re-applied to every button press.
        @param service_provider: Returns the review service used to read the target page.
        @param cursor: Cursor of the page this view was attached to.
        @param next_after_sequence: Keyset position of the following page, or None on the last page.
        @return: None.
        """
        super().__init__(timeout=CONFIRM_TIMEOUT_SECONDS)
        self._gate = gate
        self._service_provider = service_provider
        previous_cursor = _PageCursor(cursor.history[-1] if cursor.history else None, cursor.history[:-1])
        next_cursor = _PageCursor(next_after_sequence, (*cursor.history, cursor.after_sequence))
        self.previous_button = discord.ui.Button(
            label="上一頁", style=discord.ButtonStyle.secondary, custom_id=_encode_cursor(previous_cursor),
            disabled=cursor.after_sequence is None)  # [previous cursor]: Pops the stack; the first page has nothing before it.
        self.next_button = discord.ui.Button(
            label="下一頁", style=discord.ButtonStyle.secondary, custom_id=_encode_cursor(next_cursor),
            disabled=next_after_sequence is None)  # [next cursor]: Carries the position read back as after_sequence, plus this page's stack.
        for button in (self.previous_button, self.next_button):
            button.callback = self._on_page
            self.add_item(button)

    async def _on_page(self, interaction: discord.Interaction) -> None:
        """@description: Re-verify the presser, then read only the page the pressed cursor names, refusing a malformed cursor without reading.
        @param self: Page view.
        @param interaction: Button press interaction, checked on its own guild and user before any read.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        cursor = _parse_page_cursor(interaction.data.get("custom_id", ""))
        replies = [_Reply(STALE_PAGE_TEXT)] if cursor is None else await _guarded(
            _pending_page_replies(self._gate, self._service_provider, interaction.user.id, cursor))
        for reply in replies:
            await _followup(interaction, reply)


def _encode_cursor(cursor: _PageCursor) -> str:
    """@description: Encode a page cursor into a button custom_id, keeping only the most recent earlier cursors so the id stays under Discord's limit.
    @param cursor: Page cursor to encode.
    @return: Custom_id text in the form owner-list:<position>|<stack>.
    """
    recent = cursor.history[-MAX_CURSOR_DEPTH:]
    stack = ",".join(_token(entry) for entry in recent)
    return f"{LIST_CURSOR_PREFIX}{_token(cursor.after_sequence)}|{stack}"


def _token(after_sequence: int | None) -> str:
    """@description: Name one keyset position as text, using the first-page marker for None.
    @param after_sequence: Keyset position, or None for the first page.
    @return: Position text.
    """
    return FIRST_PAGE_TOKEN if after_sequence is None else str(after_sequence)


def _parse_page_cursor(custom_id: str) -> _PageCursor | None:
    """@description: Decode a page button's custom_id, refusing anything that is not a well-formed position or stack.
    @param custom_id: Button custom_id received with the press.
    @return: The decoded cursor, or None when the cursor is malformed.
    """
    if not custom_id.startswith(LIST_CURSOR_PREFIX):
        return None
    position_text, _, stack_text = custom_id[len(LIST_CURSOR_PREFIX):].partition("|")
    position = _parse_token(position_text)
    history = _parse_stack(stack_text)
    if position is _MALFORMED or history is None:
        return None
    return _PageCursor(position, history)


_MALFORMED = object()  # [parse failure]: Distinct from None, which is a valid first-page position.


def _parse_token(text: str):
    """@description: Read one position token, accepting only the first-page marker or a non-negative decimal.
    @param text: One token from a custom_id.
    @return: The position, None for the first page, or _MALFORMED.
    """
    if text == FIRST_PAGE_TOKEN:
        return None
    return int(text) if text.isdecimal() else _MALFORMED


def _parse_stack(stack_text: str) -> tuple[int | None, ...] | None:
    """@description: Read the comma-separated stack of earlier cursors; an empty stack is the first page's.
    @param stack_text: Text after the pipe in a custom_id.
    @return: The stack, or None when any entry is malformed.
    """
    if not stack_text:
        return ()
    entries = [_parse_token(text) for text in stack_text.split(",")]
    return None if any(entry is _MALFORMED for entry in entries) else tuple(entries)


async def _pending_page_replies(gate: OwnerGate, service_provider: Callable[[], OwnerReviewService], actor_id: int,
                                cursor: _PageCursor) -> list[_Reply]:
    """@description: Read one keyset page on a worker thread and render it, attaching Previous and Next to its last message.
    @param gate: Gate handed to the page view for its own presses.
    @param service_provider: Returns the review service used to read the page.
    @param actor_id: Admitted Owner identifier.
    @param cursor: Cursor of the page to read.
    @return: Replies for the page; a later page with no rows past its position becomes a plain stale message.
    """
    page = await _call_service(service_provider, lambda service: service.list_pending(
        actor_id, after_sequence=cursor.after_sequence, limit=PAGE_SIZE))
    if cursor.after_sequence is not None and not page.items:
        return [_Reply(STALE_PAGE_TEXT)]
    texts = split_messages(render_pending(page.items))
    view = PendingPageView(gate, service_provider, cursor, page.next_after_sequence)
    return [_Reply(text) for text in texts[:-1]] + [_Reply(texts[-1], view)]


class OwnerReviewGroup(app_commands.Group):
    """Own the guild-scoped Owner command group, routing each subcommand through the gate and worker threads."""

    def __init__(self, service_provider: Callable[[], OwnerReviewService], owner_id: int, guild_id: int,
                 now: Callable[[], datetime]) -> None:
        """@description: Register the group for one private Guild and bind the Owner, service provider and clock.
        @param self: Command group instance.
        @param service_provider: Returns the review service; called on the event-loop thread, then used on workers.
        @param owner_id: Configured Owner Discord user snowflake.
        @param guild_id: The one private Guild where the group is registered.
        @param now: Clock used to stamp owner judgements and evidence.
        @return: None.
        """
        super().__init__(name="owner", description="Owner 專用案件審核指令", guild_ids=[guild_id])
        self._gate = OwnerGate(owner_id, guild_id)  # [authorization gate]: Applied before every callback and button press.
        self._service_provider = service_provider  # [service access]: Returns the process-wide review service.
        self._now = now  # [judgement clock]: Stamps content judgements and evidence records.

    @app_commands.command(name="list", description="列出待處理案件")
    async def list_pending(self, interaction: discord.Interaction) -> None:
        """@description: Show the Owner the accepted operations waiting for review.
        @param self: Command handler.
        @param interaction: Owner's slash-command interaction.
        @return: None.
        """
        await self._serve(interaction, partial(_pending_page_replies, self._gate, self._service_provider, interaction.user.id, _PageCursor(None)))

    @app_commands.command(name="show", description="查看案件審核與證據")
    @app_commands.describe(operation="案件 ID")
    async def show(self, interaction: discord.Interaction, operation: str) -> None:
        """@description: Show the Owner one operation's identity, revision, evidence and content judgements.
        @param self: Command handler.
        @param interaction: Owner's slash-command interaction.
        @param operation: Accepted operation identifier.
        @return: None.
        """
        await self._serve(interaction, partial(self._show_reply, interaction.user.id, operation))

    @app_commands.command(name="approve", description="預覽核准為歌曲（需確認）")
    @app_commands.describe(operation="案件 ID", citation="證據引用", reason="決定理由")
    async def approve(self, interaction: discord.Interaction, operation: str, citation: str, reason: str) -> None:
        """@description: Preview the Owner's declaration that the video is a song, pending confirmation.
        @param self: Command handler.
        @param interaction: Owner's slash-command interaction.
        @param operation: Accepted operation identifier.
        @param citation: Evidence reference the Owner relied on.
        @param reason: Audit explanation.
        @return: None.
        """
        await self._serve(interaction, partial(self._approve_reply, interaction.user.id, operation, citation, reason))

    @app_commands.command(name="reject", description="預覽拒絕（需確認）")
    @app_commands.describe(operation="案件 ID", citation="證據引用", reason="決定理由")
    async def reject(self, interaction: discord.Interaction, operation: str, citation: str, reason: str) -> None:
        """@description: Preview the Owner's rejection of the song, pending confirmation.
        @param self: Command handler.
        @param interaction: Owner's slash-command interaction.
        @param operation: Accepted operation identifier.
        @param citation: Evidence reference the Owner relied on.
        @param reason: Audit explanation.
        @return: None.
        """
        await self._serve(interaction, partial(self._reject_reply, interaction.user.id, operation, citation, reason))

    @app_commands.command(name="confirm_version", description="預覽確認錄音版本（需確認）")
    @app_commands.describe(operation="案件 ID", reason="決定理由", version_name="版本參考（新證據時必填）",
                           version_kind="版本類別", evidence_id="既有證據 ID", source="新證據來源（可追溯）")
    @app_commands.choices(version_kind=VERSION_KIND_CHOICES)
    async def confirm_version(self, interaction: discord.Interaction, operation: str, reason: str,
                              version_name: str | None = None, version_kind: str = BASIC_VERSION_KIND,
                              evidence_id: str | None = None, source: str | None = None) -> None:
        """@description: Preview an Owner-confirmed recording version, citing either stored evidence or a new traceable source.
        @param self: Command handler.
        @param interaction: Owner's slash-command interaction.
        @param operation: Accepted operation identifier.
        @param reason: Audit explanation.
        @param version_name: Canonical recording version reference, required with a new source.
        @param version_kind: Named version category, or basic for none.
        @param evidence_id: Existing evidence record to cite; excludes source.
        @param source: Traceable provenance for a new evidence record; excludes evidence_id.
        @return: None.
        """
        citation = _Citation(evidence_id, source, version_name, version_kind)
        await self._serve(interaction, partial(self._version_reply, interaction, operation, reason, citation,
                                               _issue_version_confirmation))

    @app_commands.command(name="reexamine", description="預覽正式重審（需確認）")
    @app_commands.describe(operation="案件 ID", reason="決定理由", version_name="版本參考（新證據時必填）",
                           version_kind="版本類別", evidence_id="既有證據 ID", source="新證據來源（可追溯）")
    @app_commands.choices(version_kind=VERSION_KIND_CHOICES)
    async def reexamine(self, interaction: discord.Interaction, operation: str, reason: str,
                        version_name: str | None = None, version_kind: str = BASIC_VERSION_KIND,
                        evidence_id: str | None = None, source: str | None = None) -> None:
        """@description: Preview a formal reopen citing either stored evidence or a new traceable source, pending confirmation.
        @param self: Command handler.
        @param interaction: Owner's slash-command interaction.
        @param operation: Accepted operation identifier.
        @param reason: Audit explanation.
        @param version_name: Canonical recording version reference, required with a new source.
        @param version_kind: Named version category, or basic for none.
        @param evidence_id: Existing evidence record to cite; excludes source.
        @param source: Traceable provenance for a new evidence record; excludes evidence_id.
        @return: None.
        """
        citation = _Citation(evidence_id, source, version_name, version_kind)
        await self._serve(interaction, partial(self._version_reply, interaction, operation, reason, citation,
                                               _issue_reexamination))

    async def _serve(self, interaction: discord.Interaction, pending: Callable[[], Awaitable[_Reply | list[_Reply]]]) -> None:
        """@description: Run the gate first, acknowledge, then deliver every guarded reply privately.
        @param self: Command handler.
        @param interaction: Interaction being served.
        @param pending: Builds the reply coroutine; invoked only after the gate admits the caller.
        @return: None.
        """
        if not await self._gate.admit(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        for reply in await _guarded(pending()):
            await _followup(interaction, reply)

    async def _show_reply(self, actor_id: int, operation_id: str) -> list[_Reply]:
        """@description: Fetch one projection on a worker thread and split its full audit text into messages.
        @param self: Command handler.
        @param actor_id: Admitted Owner identifier.
        @param operation_id: Operation to inspect, addressed by its id and never by list position.
        @return: One reply per message, each under the Discord limit.
        """
        projection = await _call_service(self._service_provider, lambda service: service.view(actor_id, operation_id))
        return [_Reply(text) for text in split_messages(render_projection(projection))]

    async def _approve_reply(self, actor_id: int, operation_id: str, citation: str, reason: str) -> _Reply:
        """@description: Preview an approval whose content judgement is the Owner's own verdict.
        @param self: Command handler.
        @param actor_id: Admitted Owner identifier.
        @param operation_id: Operation to decide.
        @param citation: Evidence reference for the judgement.
        @param reason: Audit explanation.
        @return: Approval preview reply with a confirmation view.
        """
        retrieved_at = self._now()
        return await self._preview_reply(actor_id, partial(_approve_preview, actor_id=actor_id, operation_id=operation_id,
                                                           citation=citation, reason=reason, retrieved_at=retrieved_at))

    async def _reject_reply(self, actor_id: int, operation_id: str, citation: str, reason: str) -> _Reply:
        """@description: Preview a rejection against the revision the Owner just viewed.
        @param self: Command handler.
        @param actor_id: Admitted Owner identifier.
        @param operation_id: Operation to decide.
        @param citation: Evidence reference the Owner relied on.
        @param reason: Audit explanation.
        @return: Rejection preview reply with a confirmation view.
        """
        return await self._preview_reply(actor_id, partial(_reject_preview, actor_id=actor_id, operation_id=operation_id,
                                                           citation=citation, reason=reason))

    async def _version_reply(self, interaction: discord.Interaction, operation_id: str, reason: str, citation: _Citation,
                             issue: Callable) -> _Reply:
        """@description: Check the citation before any service call, then preview the decision on the cited or newly recorded evidence.
        @param self: Command handler.
        @param interaction: Source interaction; its id is only a tracking note in the audit text.
        @param operation_id: Operation whose video the evidence describes.
        @param reason: Audit explanation.
        @param citation: Evidence citation the Owner supplied.
        @param issue: Service preview method for this action.
        @return: Preview reply with a confirmation view.
        """
        claim = _checked_claim(citation)
        actor_id = interaction.user.id
        retrieved_at = self._now()
        return await self._preview_reply(actor_id, partial(_claim_preview, actor_id=actor_id, operation_id=operation_id,
                                                           citation=citation, claim=claim, reason=reason,
                                                           retrieved_at=retrieved_at, tracking_id=interaction.id, issue=issue))

    async def _preview_reply(self, actor_id: int, preview_call: Callable[[OwnerReviewService], ReviewPreview]) -> _Reply:
        """@description: Run one preview on a worker thread and attach the single-use confirmation view.
        @param self: Command handler.
        @param actor_id: Admitted Owner identifier bound to the token.
        @param preview_call: Service operation that issues the preview.
        @return: Preview text with Confirm and Cancel buttons.
        """
        preview = await _call_service(self._service_provider, preview_call)
        try:
            text = render_preview(preview)
        except Exception:  # [issued token, no button]: The service already holds the token, so no confirm button may be offered.
            _LOGGER.exception("Owner preview rendering failed after its token was issued")
            return _Reply(PREVIEW_RENDER_FAILED_TEXT)
        view = ConfirmView(self._gate, self._service_provider, actor_id, preview.operation_id, preview.token_id)
        return _Reply(text, view)


def build_owner_group(service_provider: Callable[[], OwnerReviewService], owner_id: int, guild_id: int,
                      now: Callable[[], datetime]) -> app_commands.Group:
    """@description: Assemble the guild-scoped Owner command group whose callbacks are all gated.
    @param service_provider: Returns the review service; called on the event-loop thread, then used on workers.
    @param owner_id: Configured Owner Discord user snowflake.
    @param guild_id: The one private Guild where the group is registered.
    @param now: Clock for judgement and evidence timestamps.
    @return: Owner command group ready to add to the command tree.
    """
    return OwnerReviewGroup(service_provider, owner_id, guild_id, now)


async def _call_service[T](service_provider: Callable[[], OwnerReviewService], operation: Callable[[OwnerReviewService], T]) -> T:
    """@description: Run one synchronous service operation on a worker thread so the Discord loop keeps serving.
    @param service_provider: Returns the review service already constructed for this process.
    @param operation: Service call to execute off the event loop.
    @return: The operation's result.
    """
    return await asyncio.to_thread(operation, service_provider())


async def _guarded(pending: Awaitable[_Reply | list[_Reply]]) -> list[_Reply]:
    """@description: Turn expected review failures into owner-facing text, and report every other failure as internal without a fake missing case.
    @param pending: Reply coroutine whose service failures are expected; it may produce one reply or several messages.
    @return: The replies to deliver, in order.
    """
    try:
        result = await pending
    except KeyError:  # [bug, not a case]: A missing key inside our own code must never read as a missing operation.
        _LOGGER.exception("Owner review command failed with a key error")
        return [_Reply(INTERNAL_ERROR_TEXT)]
    except (ValueError, PermissionError, LookupError, ExpiredReviewToken, ReviewOutcomeUnknown) as expected_error:
        return [_Reply(_error_text(expected_error))]
    except Exception:
        _LOGGER.exception("Owner review command failed unexpectedly")
        return [_Reply(INTERNAL_ERROR_TEXT)]
    return result if isinstance(result, list) else [result]


def _error_text(error: Exception) -> str:
    """@description: Explain each expected failure in the words the Owner needs to act on it.
    @param error: Expected failure raised by validation or the review service.
    @return: Traditional Chinese explanation.
    """
    if isinstance(error, ExpiredReviewToken):
        return "預覽已過期，請重新預覽。"
    if isinstance(error, ReviewOutcomeUnknown):
        return "結果未知，請重新確認同一筆預覽以取得原結果。"
    if isinstance(error, UnknownReviewToken):
        return "確認憑證無效，請重新預覽。"
    if isinstance(error, LookupError):  # [genuine missing case]: Only reached for LookupErrors that are not KeyError.
        return "找不到指定案件。"
    return str(error)


async def _followup(interaction: discord.Interaction, reply: _Reply) -> None:
    """@description: Deliver the final text privately, attaching the confirmation view only when one exists.
    @param interaction: Deferred interaction awaiting its result.
    @param reply: Text and optional view to deliver.
    @return: None.
    """
    options = {"view": reply.view} if reply.view is not None else {}
    await interaction.followup.send(reply.text, ephemeral=True, allowed_mentions=NO_MENTIONS, **options)


def _checked_claim(citation: _Citation) -> tuple[RecordingVersionIdentity, VersionClaimKind | None] | None:
    """@description: Refuse an unusable citation before any service call, returning the new-record claim only when one is needed.
    @param citation: Evidence citation the Owner supplied.
    @return: Validated version claim for a new record, or None when an existing record is cited.
    """
    kind = _version_kind(citation.version_kind)
    if (citation.evidence_id is None) == (citation.source is None):
        raise ValueError(BOTH_OR_NEITHER_CITATION_TEXT)
    if citation.evidence_id is not None:
        return None
    if citation.version_name is None:
        raise ValueError("新證據需要提供版本參考 version_name。")
    require_traceable_source(citation.source)
    return RecordingVersionIdentity(citation.version_name), kind


def _version_kind(version_kind: str) -> VersionClaimKind | None:
    """@description: Map the slash-command version choice to its category, refusing anything outside the vocabulary.
    @param version_kind: Choice value, basic or a VersionClaimKind member name.
    @return: The category, where basic yields no category.
    """
    if version_kind not in VERSION_KIND_NAMES:
        raise ValueError(f"未知的版本類別：{version_kind}")
    return None if version_kind == BASIC_VERSION_KIND else VersionClaimKind[version_kind]


def _approve_preview(service: OwnerReviewService, *, actor_id: int, operation_id: str, citation: str, reason: str,
                     retrieved_at: datetime) -> ReviewPreview:
    """@description: Build the Owner's content judgement from the projected video, then preview approval.
    @param service: Review service running on a worker thread.
    @param actor_id: Admitted Owner identifier.
    @param operation_id: Operation to decide.
    @param citation: Evidence reference for the judgement.
    @param reason: Audit explanation.
    @param retrieved_at: Judgement time stamped on the event loop.
    @return: Approval preview.
    """
    projection = service.view(actor_id, operation_id)
    content = ContentJudgement(video=projection.review.video, judged_by=actor_id, reason=reason,
                               retrieved_at=retrieved_at, evidence_reference=citation, is_song=True)
    return service.preview(actor_id, operation_id, ReviewAction.APPROVE, reason, projection.review.version, citation, content)


def _reject_preview(service: OwnerReviewService, *, actor_id: int, operation_id: str, citation: str,
                    reason: str) -> ReviewPreview:
    """@description: Preview a rejection against the current revision.
    @param service: Review service running on a worker thread.
    @param actor_id: Admitted Owner identifier.
    @param operation_id: Operation to decide.
    @param citation: Evidence reference the Owner relied on.
    @param reason: Audit explanation.
    @return: Rejection preview.
    """
    revision = service.view(actor_id, operation_id).review.version
    return service.preview(actor_id, operation_id, ReviewAction.REJECT, reason, revision, citation)


def _claim_preview(service: OwnerReviewService, *, actor_id: int, operation_id: str, citation: _Citation,
                   claim: tuple[RecordingVersionIdentity, VersionClaimKind | None] | None, reason: str,
                   retrieved_at: datetime, tracking_id: int, issue: Callable) -> ReviewPreview:
    """@description: Resolve the citation to one evidence record for the projected video, then hand it to one preview method.
    @param service: Review service running on a worker thread.
    @param actor_id: Admitted Owner identifier.
    @param operation_id: Operation whose video the evidence describes.
    @param citation: Checked citation, naming an existing record or a new source.
    @param claim: Validated version claim for a new record, or None when an existing record is cited.
    @param reason: Audit explanation.
    @param retrieved_at: Evidence time stamped on the event loop.
    @param tracking_id: Discord interaction id, kept only as a tracking note in the audit text.
    @param issue: Service preview method for reexamination or version confirmation.
    @return: Preview issued by the service.
    """
    projection = service.view(actor_id, operation_id)
    if claim is None:
        evidence = service.stored_evidence(actor_id, operation_id, citation.evidence_id)
    else:
        identity, kind = claim
        evidence = record_evidence(uuid.uuid4().hex, projection.review.video, EvidenceSourceType.OWNER_REVIEW, citation.source,
                                   retrieved_at, f"{reason}（Discord 追蹤 {tracking_id}）", actor_id, identity, version_kind=kind)
    return issue(service, actor_id, operation_id, evidence, reason, projection.review.version)


def _issue_reexamination(service: OwnerReviewService, actor_id: int, operation_id: str, evidence: EvidenceRecord,
                         reason: str, revision: int) -> ReviewPreview:
    """@description: Adapt the reexamination preview to the shared evidence path.
    @param service: Review service running on a worker thread.
    @param actor_id: Admitted Owner identifier.
    @param operation_id: Operation to reopen.
    @param evidence: Owner evidence record.
    @param reason: Audit explanation.
    @param revision: Revision the Owner examined.
    @return: Reexamination preview.
    """
    return service.preview_reexamination(actor_id, operation_id, evidence, reason, revision)


def _issue_version_confirmation(service: OwnerReviewService, actor_id: int, operation_id: str, evidence: EvidenceRecord,
                                reason: str, revision: int) -> ReviewPreview:
    """@description: Adapt the version-confirmation preview to the shared evidence path.
    @param service: Review service running on a worker thread.
    @param actor_id: Admitted Owner identifier.
    @param operation_id: Operation whose version is confirmed.
    @param evidence: Owner evidence record.
    @param reason: Audit explanation.
    @param revision: Revision the Owner examined.
    @return: Version-confirmation preview.
    """
    return service.preview_version_confirmation(actor_id, operation_id, evidence, reason, revision)


def render_pending(items) -> str:
    """@description: Render one page of pending rows compactly so the Owner can pick an operation to show.
    @param items: Pending rows of one page returned by the service.
    @return: Traditional Chinese list, one row per operation.
    """
    if not items:
        return "目前沒有待處理案件。"
    rows = [_pending_row(item) for item in items]
    return "\n".join(["待處理案件：", *rows])


def split_messages(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    """@description: Pack whole lines into messages under the Discord limit so no audit line is ever cut in half.
    @param text: Full audit text, one fact per line.
    @param limit: Maximum characters in one message.
    @return: Ordered messages whose lines, joined by newlines, reproduce the original text.
    """
    messages: list[str] = []
    current_lines: list[str] = []  # [message under construction]: Whole lines packed into the next message.
    current_length = 0  # [message length]: Characters in current_lines including their separators.
    for line in text.split("\n"):
        if len(line) > limit:
            raise ValueError("An audit line exceeds the message limit")
        separator_length = 1 if current_lines else 0
        if current_lines and current_length + separator_length + len(line) > limit:
            messages.append("\n".join(current_lines))
            current_lines, current_length = [line], len(line)
        else:
            current_lines.append(line)
            current_length += separator_length + len(line)
    messages.append("\n".join(current_lines))
    return messages


def _clip(value: str) -> str:
    """@description: Bound one audit value so its line always fits in one message; the full value stays in the audited store.
    @param value: Audit text shown to the Owner.
    @return: The value, ellipsized when longer than the value limit.
    """
    if len(value) <= MAX_VALUE_CHARS:
        return value
    return value[:MAX_VALUE_CHARS - 1] + "…"


def _timestamp(moment: datetime) -> str:
    """@description: Format one audit instant the same way everywhere so Owners can compare records by eye.
    @param moment: Timezone-aware instant stored on a record.
    @return: ISO 8601 text to the second.
    """
    return moment.isoformat(timespec="seconds")


def _pending_row(item) -> str:
    """@description: Format one pending row, adding the conflict reason only when one exists.
    @param item: Pending row with status, identity, revision, evidence count and optional conflict reason.
    @return: One line of text.
    """
    row = (f"・{item.operation_id}｜{REVIEW_LABELS[item.review_status]}｜版本 {IDENTITY_LABELS[item.identity_status]}"
           f"｜修訂 {item.revision}｜證據 {item.evidence_count} 筆")
    return f"{row}｜衝突：{item.conflict_reason}" if item.conflict_reason else row


def render_projection(projection) -> str:
    """@description: Render the full audit basis of one operation, one fact per line, so each Owner decision can be checked.
    @param projection: Read-only projection returned by the service.
    @return: Traditional Chinese audit text with one fact per line.
    """
    lines = [
        f"案件 {projection.operation_id}｜受理順序 {projection.accepted_sequence}",
        f"來源連結：{_clip(projection.source_url)}",
        f"狀態：{REVIEW_LABELS[projection.review.status]}｜修訂 {projection.review.version}",
        *_identity_lines(projection),
        f"證據 {len(projection.evidence)} 筆：",
        *_evidence_lines(projection.evidence),
        f"內容判定 {len(projection.content_judgements)} 筆：",
        *_judgement_lines(projection.content_judgements),
        f"決定歷程 {len(projection.history_entries)} 筆：",
        *_history_lines(projection.history_entries),
    ]
    return "\n".join(lines)


def _identity_lines(projection) -> list[str]:
    """@description: Show the version identity with its reference, evidence and any frozen conflict reason.
    @param projection: Read-only projection.
    @return: Lines for the identity block.
    """
    identity = projection.identity
    lines = [f"版本：{IDENTITY_LABELS[identity.status]}"]
    if identity.version is not None:
        lines.append(f"版本參考：{_clip(identity.version.value)}")
    if identity.evidence_reference is not None:
        lines.append(f"版本證據：{_clip(identity.evidence_reference)}")
    if identity.status is VersionIdentityStatus.CONFLICT:
        lines.append(CONFLICT_IDENTITY_NOTE)
    if projection.conflict_reason is not None:
        lines.append(f"衝突原因：{_clip(projection.conflict_reason)}")
    return lines


def _evidence_lines(records) -> list[str]:
    """@description: List every evidence record with the facts that let the Owner judge its origin and claim.
    @param records: Evidence records of the operation's video, oldest first.
    @return: Four lines per record.
    """
    lines: list[str] = []
    for record in records:
        version = _clip(record.version.value) if record.version is not None else "無"
        kind = record.version_kind.name if record.version_kind is not None else "無"
        lines += [
            f"・證據 {record.evidence_id}｜來源類型 {record.source_type.name}",
            f"  來源參考：{_clip(record.source_reference)}｜取得時間：{_timestamp(record.retrieved_at)}",
            f"  判定者：{record.judged_by}｜理由：{_clip(record.reason)}",
            f"  聲稱版本：{version}｜版本類別：{kind}",
        ]
    return lines


def _judgement_lines(judgements) -> list[str]:
    """@description: List every content judgement with its verdict, judge, time and evidence pointer.
    @param judgements: Content judgements of the operation's video, oldest first.
    @return: Three lines per judgement.
    """
    lines: list[str] = []
    for judgement in judgements:
        verdict = "歌曲" if judgement.is_song else "非歌曲"
        lines += [
            f"・判定：{verdict}｜影片 {judgement.video.video_id}",
            f"  判定者：{judgement.judged_by}｜取得時間：{_timestamp(judgement.retrieved_at)}",
            f"  證據參考：{_clip(judgement.evidence_reference)}｜理由：{_clip(judgement.reason)}",
        ]
    return lines


def _history_lines(entries) -> list[str]:
    """@description: List each persisted review decision in order, with its revision, actor and reason.
    @param entries: Review history entries, oldest first.
    @return: One line per entry.
    """
    return [f"・修訂 {entry.revision}｜{REVIEW_LABELS[entry.status]}｜判定者 {entry.actor_id}｜理由：{_clip(entry.reason)}"
            for entry in entries]


def render_preview(preview: ReviewPreview) -> str:
    """@description: Show exactly what Confirm will do, its financial effect and its expiry.
    @param preview: Preview issued by the service.
    @return: Traditional Chinese preview text.
    """
    lines = [
        f"請確認：{ACTION_LABELS[preview.action]}，案件 {preview.operation_id}",
        f"理由：{preview.reason}",
    ]
    if preview.evidence_reference:
        lines.append(f"證據引用：{preview.evidence_reference}")
    if preview.source_reference:
        lines.append(f"來源：{preview.source_reference}")
    lines.extend([
        f"目前狀態：{REVIEW_LABELS[preview.current_status]}，預計變為：{REVIEW_LABELS[preview.projected_status]}",
        _identity_change_line(preview.current_identity, preview.projected_identity),
        f"修訂：{preview.expected_revision}",
        preview.financial_effect,
        f"預覽有效至 {preview.expires_at.isoformat(timespec='seconds')}；確認只能使用一次。",
    ])
    return "\n".join(lines)


def _identity_change_line(current, projected) -> str:
    """@description: Show the version state before the decision and the state it would store, or that it is recomputed at confirmation.
    @param current: Persisted identity before the decision, or None when not supplied.
    @param projected: Identity the decision would store, or None when it is recomputed at confirmation.
    @return: One line of Traditional Chinese text.
    """
    if current is None:
        return "版本：未提供"
    before = _identity_phrase(current)
    if projected is None:
        return f"版本：目前 {before}；確認後依全部可靠證據重算"
    return f"版本：目前 {before} → 預計 {_identity_phrase(projected)}"


def _identity_phrase(identity) -> str:
    """@description: Name one identity state with its version reference when it has one.
    @param identity: Version assignment to describe.
    @return: Short Traditional Chinese phrase.
    """
    label = IDENTITY_LABELS[identity.status]
    return f"{label}（{identity.version.value}）" if identity.version is not None else label


def _confirmed_text(snapshot: ReviewSnapshot) -> str:
    """@description: Report the revision a confirmation produced.
    @param snapshot: Review snapshot returned by the service.
    @return: Traditional Chinese confirmation text.
    """
    return f"已確認：目前狀態 {REVIEW_LABELS[snapshot.status]}，修訂 {snapshot.version}。"
