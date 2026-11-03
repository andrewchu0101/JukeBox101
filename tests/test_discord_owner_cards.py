"""Verify the Owner case cards decide only what they display, hide internal ids, and refuse stale, repeated or foreign presses.

Inputs are fake interactions and a fake review service that behaves like the real one for revisions and tokens; no Discord connection is made.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord

from bot.discord_owner_cards import OwnerCards
from bot.owner_review import ReviewAction, ReviewOutcomeUnknown, ReviewPreview
from bot.review import DurationEvidenceExpired, ReviewStatus, StaleReviewVersion
from bot.song_identity import VideoIdentity
from bot.version_concerns import ConcernCode, VersionConcern
from bot.version_identity import VersionIdentityStatus
from tests.test_discord_owner_cards_core_fake import Answer, FakeCore, cards_with_core

OWNER_ID = 1001
GUILD_ID = 2002
OTHER_USER_ID = 4004
SUBMITTER_ID = 5555
VIDEO = VideoIdentity("dQw4w9WgXcQ")
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
DOUBT = (VersionConcern(ConcernCode.SIMILAR_TITLE, "abcdefghijk"),)  # [version doubt]: A stored automatic-comparison concern, which keeps approval separate from the version decision.
PENDING_STATES = {ReviewStatus.PENDING_VALIDATION, ReviewStatus.PENDING_REVIEW, ReviewStatus.VALIDATION_FAILED}


def case(sequence: int = 1, revision: int = 3, review: ReviewStatus = ReviewStatus.PENDING_REVIEW,
         identity: VersionIdentityStatus = VersionIdentityStatus.UNRESOLVED, duration: int | None = 189, conflict: str | None = None, concerns=()):
    """@description: Build one pending row shaped like the service's PendingItem, with an internal id the Owner must never see.
    @param sequence: Acceptance order, which the card uses instead of the operation id.
    @param revision: Review revision the card must decide.
    @param review: Review status.
    @param identity: Version identity status.
    @param duration: Known full-video seconds, or None when missing.
    @param conflict: Frozen conflict reason, when any.
    @param concerns: Stored automatic-comparison doubts.
    @return: Pending row.
    """
    return SimpleNamespace(operation_id=f"discord:internal-{sequence}", source_url=VIDEO_URL, accepted_sequence=sequence,
                           review_status=review, identity_status=identity, revision=revision, evidence_count=0,
                           conflict_reason=conflict, submitter_id=SUBMITTER_ID, duration_seconds=duration, api_basis=None, concerns=concerns)


class CardService:
    """Behave like the review service for pending rows, revisions and single-use tokens, recording every call."""

    def __init__(self, *items) -> None:
        """@description: Start with the given pending rows and no failures.
        @param self: Fake service.
        @param items: Pending rows.
        @return: None.
        """
        self.items = {item.accepted_sequence: item for item in items}
        self.calls: list[tuple] = []
        self.tokens: dict[str, tuple] = {}
        self.confirm_failures: list[Exception] = []  # [scripted failures]: Raised by successive confirms, oldest first.
        self.versions: list = []  # [known versions]: Rows shaped like the service's KnownVersion, oldest first.
        self.evidence: list = []  # [recorded evidence]: Evidence records handed to version confirmation previews.
        self.version_preview_error: Exception | None = None  # [scripted refusal]: Raised by the next version preview, like the real rules do.
        self.retention = None  # [scripted retention health]: Returned by retention_status; None means nothing is kept.
        self.preview_error: Exception | None = None  # [scripted refusal]: Raised by the next approval or rejection preview.

    def called(self, name: str) -> list[tuple]:
        """@description: Select recorded calls by method name.
        @param self: Fake service.
        @param name: Method name.
        @return: Matching calls.
        """
        return [call for call in self.calls if call[0] == name]

    def _pending(self, item) -> bool:
        """@description: Mirror the real queue rule: open reviews, or approved cases still waiting for a version.
        @param self: Fake service.
        @param item: Row to test.
        @return: Whether the row is pending.
        """
        waiting_version = item.review_status is ReviewStatus.APPROVED and item.identity_status is not VersionIdentityStatus.CONFIRMED
        return item.review_status in PENDING_STATES or waiting_version

    def list_pending(self, actor_id: int, *, after_sequence=None, limit: int = 20):
        """@description: Return pending rows after the keyset position, oldest first.
        @param self: Fake service.
        @param actor_id: Caller.
        @param after_sequence: Position already seen.
        @param limit: Maximum rows.
        @return: Page object.
        """
        self.calls.append(("list_pending", actor_id, after_sequence, limit))
        rows = sorted((i for i in self.items.values() if self._pending(i) and (after_sequence is None or i.accepted_sequence > after_sequence)),
                      key=lambda i: i.accepted_sequence)
        return SimpleNamespace(items=tuple(SimpleNamespace(**vars(row)) for row in rows[:limit]), next_after_sequence=None)  # [immutable rows]: Copies, like the real dataclasses.

    def view(self, actor_id: int, operation_id: str):
        """@description: Return the minimal projection the card needs: the video of the case.
        @param self: Fake service.
        @param actor_id: Caller.
        @param operation_id: Case.
        @return: Projection.
        """
        self.calls.append(("view", actor_id, operation_id))
        return SimpleNamespace(review=SimpleNamespace(video=VIDEO))

    def _issue(self, name: str, operation_id: str, action: ReviewAction, expected_revision: int, reference: str) -> ReviewPreview:
        """@description: Refuse a stale revision like the real service, otherwise issue a single-use token.
        @param self: Fake service.
        @param name: Method name for the call record.
        @param operation_id: Case.
        @param action: Decision kind.
        @param expected_revision: Revision the caller examined.
        @param reference: Citation or source.
        @return: Preview.
        """
        item = next(i for i in self.items.values() if i.operation_id == operation_id)
        if expected_revision != item.revision:
            raise StaleReviewVersion("stale")
        token = f"token-{len(self.tokens) + 1}"
        self.tokens[token] = (item, action)
        return ReviewPreview(token, operation_id, action, expected_revision, reference, item.review_status, ReviewStatus.APPROVED,
                             "不發 Nickel", NOW + timedelta(minutes=5))

    def preview(self, actor_id, operation_id, action, reason, expected_revision, evidence_reference, content=None):
        """@description: Issue a decision token bound to the revision the caller examined.
        @param self: Fake service.
        @param actor_id: Caller.
        @param operation_id: Case.
        @param action: Decision kind.
        @param reason: Audit reason.
        @param expected_revision: Revision examined.
        @param evidence_reference: Citation.
        @param content: Content judgement, when given.
        @return: Preview.
        """
        self.calls.append(("preview", actor_id, operation_id, action, reason, expected_revision, evidence_reference, content))
        if self.preview_error is not None:
            raise self.preview_error
        return self._issue("preview", operation_id, action, expected_revision, evidence_reference)

    def preview_duration(self, actor_id, operation_id, seconds, source, reason, expected_revision=None):
        """@description: Issue a duration token bound to the given revision, or to the current one when none is given, as the real service does.
        @param self: Fake service.
        @param actor_id: Caller.
        @param operation_id: Case.
        @param seconds: Stated duration.
        @param source: Traceable source.
        @param reason: Audit reason.
        @param expected_revision: Revision the caller examined, if any.
        @return: Preview.
        """
        self.calls.append(("preview_duration", actor_id, operation_id, seconds, source, reason, expected_revision))
        item = next(i for i in self.items.values() if i.operation_id == operation_id)
        return self._issue("preview_duration", operation_id, ReviewAction.DURATION, item.revision if expected_revision is None else expected_revision, source)

    def retention_status(self, actor_id: int):
        """@description: Return the scripted retention health.
        @param self: Fake service.
        @param actor_id: Caller.
        @return: Scripted status, or None.
        """
        if isinstance(self.retention, Exception):
            raise self.retention
        return self.retention

    def list_known_versions(self, actor_id: int, *, after_sequence=None, limit: int = 5):
        """@description: Return known versions after the keyset position, oldest first, with the next cursor when more follow.
        @param self: Fake service.
        @param actor_id: Caller.
        @param after_sequence: Position already seen.
        @param limit: Maximum rows.
        @return: Page object.
        """
        self.calls.append(("list_known_versions", actor_id, after_sequence, limit))
        rows = [row for row in sorted(self.versions, key=lambda row: row.first_sequence) if after_sequence is None or row.first_sequence > after_sequence]
        page = tuple(rows[:limit])
        return SimpleNamespace(items=page, next_after_sequence=page[-1].first_sequence if len(rows) > limit else None)

    def preview_version_confirmation(self, actor_id, operation_id, evidence, reason, expected_revision):
        """@description: Issue a version-confirmation token bound to the examined revision, like the real service.
        @param self: Fake service.
        @param actor_id: Caller.
        @param operation_id: Case.
        @param evidence: Evidence record built by the card.
        @param reason: Audit reason.
        @param expected_revision: Revision examined.
        @return: Preview.
        """
        self.calls.append(("preview_version_confirmation", actor_id, operation_id, reason, expected_revision, evidence))
        if self.version_preview_error is not None:
            raise self.version_preview_error
        self.evidence.append(evidence)
        return self._issue("preview_version_confirmation", operation_id, ReviewAction.CONFIRM_VERSION, expected_revision, evidence.source_reference)

    def confirm(self, actor_id: int, token_id: str):
        """@description: Apply the previewed decision once, or raise the scripted failure.
        @param self: Fake service.
        @param actor_id: Caller.
        @param token_id: Token issued by a preview.
        @return: Resulting snapshot.
        """
        self.calls.append(("confirm", actor_id, token_id))
        if self.confirm_failures:
            raise self.confirm_failures.pop(0)
        item, action = self.tokens[token_id]
        if action is ReviewAction.DURATION:
            item.duration_seconds, item.review_status = 120, ReviewStatus.PENDING_REVIEW
        elif action is ReviewAction.CONFIRM_VERSION:
            item.identity_status = VersionIdentityStatus.CONFIRMED
        else:
            item.review_status = ReviewStatus.APPROVED if action is ReviewAction.APPROVE else ReviewStatus.REJECTED
        item.revision += 1
        return SimpleNamespace(status=item.review_status, version=item.revision)

    def settlement_note(self, actor_id: int, operation_id: str) -> str:
        """@description: Say that nothing was paid, as the real service does while no version is confirmed.
        @param self: Fake service.
        @param actor_id: Caller.
        @param operation_id: Case.
        @return: Note text.
        """
        self.calls.append(("settlement_note", actor_id, operation_id))
        return "待 Owner 確認錄音版本；目前未發放 Nickel。"


class FakeResponse:
    """Record acknowledgements, direct replies and modals."""

    def __init__(self) -> None:
        """@description: Start empty.
        @param self: Fake response.
        @return: None.
        """
        self.deferrals: list[dict] = []
        self.messages: list[tuple] = []
        self.modals: list = []

    async def defer(self, **kwargs) -> None:
        """@description: Record an acknowledgement.
        @param self: Fake response.
        @param kwargs: Defer options.
        @return: None.
        """
        self.deferrals.append(kwargs)

    async def edit_message(self, **kwargs) -> None:
        """@description: Record the acknowledgement that updates the pressed card, which removes its buttons while the work runs.
        @param self: Fake response.
        @param kwargs: Update options.
        @return: None.
        """
        self.locks = getattr(self, "locks", []) + [kwargs]

    async def send_message(self, content=None, **kwargs) -> None:
        """@description: Record a direct reply.
        @param self: Fake response.
        @param content: Text.
        @param kwargs: Options.
        @return: None.
        """
        self.messages.append((content, kwargs))

    async def send_modal(self, modal) -> None:
        """@description: Record an opened modal.
        @param self: Fake response.
        @param modal: Modal shown to the Owner.
        @return: None.
        """
        self.modals.append(modal)


class Press:
    """Carry the guild, user and response objects that a card button press reads."""

    def __init__(self, user_id: int = OWNER_ID, guild_id: int | None = GUILD_ID) -> None:
        """@description: Build one press from a caller and a guild.
        @param self: Fake interaction.
        @param user_id: Caller.
        @param guild_id: Guild of the press.
        @return: None.
        """
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = guild_id
        self.id = 9001
        self.response = FakeResponse()
        self.edits: list[dict] = []

    async def edit_original_response(self, **kwargs) -> None:
        """@description: Record the card update.
        @param self: Fake interaction.
        @param kwargs: New content and view.
        @return: None.
        """
        self.edits.append(kwargs)

    def shown(self) -> str:
        """@description: Return the text of the last card update.
        @param self: Fake interaction.
        @return: Card text.
        """
        return self.edits[-1]["content"]


def cards_for(service: CardService) -> OwnerCards:
    """@description: Build the card handler for the private Guild over the fake service.
    @param service: Fake service.
    @return: Card handler.
    """
    return OwnerCards(lambda: service, OWNER_ID, GUILD_ID, lambda: NOW)


def press(cards: OwnerCards, action: str, sequence: int, revision: int, interaction: Press | None = None, extra: int = 0) -> Press:
    """@description: Run one button callback to completion.
    @param cards: Card handler.
    @param action: Button action.
    @param sequence: Acceptance order in the button's custom_id.
    @param revision: Revision in the button's custom_id.
    @param interaction: Press to use, or a default Owner press.
    @param extra: Opaque choice carried by version buttons: a known version's first acceptance order, or a page cursor.
    @return: The press, for inspecting its replies.
    """
    interaction = interaction or Press()
    asyncio.run(cards.button_class(action, sequence, revision, extra=extra).callback(interaction))
    return interaction


def first_card(cards: OwnerCards):
    """@description: Open the single-case card of the oldest pending case the way the Owner does: from the list page, press its detail button.
    @param cards: Card handler.
    @return: Object with the card's text and view.
    """
    page = asyncio.run(cards.first_replies(OWNER_ID))[0]
    detail = next(child for child in page.view.children if child.custom_id.startswith("ocard:detail:"))
    _, _, sequence, revision, *_ = detail.custom_id.split(":")
    opened = Press()
    asyncio.run(cards.button_class("detail", int(sequence), int(revision)).callback(opened))  # [standalone card]: The same detail button without a list marker, so the card has no list to return to.
    return SimpleNamespace(text=opened.shown(), view=opened.edits[-1]["view"])


def labels(view) -> dict[str, bool]:
    """@description: Map each button label to whether it is enabled.
    @param view: Card view.
    @return: Label to enabled flag.
    """
    return {child.item.label: not child.item.disabled for child in view.children}


def test_the_first_card_shows_what_the_owner_needs_and_never_an_internal_id():
    """@description: The list names the sender, the unexpanded link and the question, and the detail card adds the duration, without the operation id.
    @param: none
    @return: None.
    """
    service = CardService(case())
    cards = cards_for(service)
    replies = asyncio.run(cards.first_replies(OWNER_ID))
    text = replies[0].text
    assert f"<@{SUBMITTER_ID}>" in text and f"<{VIDEO_URL}>" in text and "這是一首新的歌嗎？" in text and "internal" not in text
    assert all("internal" not in child.custom_id and len(child.custom_id) <= 100 for child in replies[0].view.children)
    assert "3:09" in first_card(cards).text


def test_an_empty_queue_says_so_without_buttons():
    """@description: With nothing pending the Owner gets a plain sentence and no decision buttons.
    @param: none
    @return: None.
    """
    replies = asyncio.run(cards_for(CardService()).first_replies(OWNER_ID))
    assert replies[0].text == "目前沒有待處理案件。" and replies[0].view is None


def test_only_the_applicable_buttons_are_enabled_for_each_state():
    """@description: The core's choices decide the buttons: a reviewable case gets yes and no, a missing duration gets the duration entry and no answer, and an approved case still waiting gets yes and no.
    @param: none
    @return: None.
    """
    reviewable = first_card(cards_for(CardService(case()))).view
    assert labels(reviewable) == {"是，發獎": True, "不是": True, "下一筆": True}
    missing = first_card(cards_for(CardService(case(review=ReviewStatus.PENDING_VALIDATION, duration=None)))).view
    assert labels(missing) == {"補時長": True, "下一筆": True}
    waiting = first_card(cards_for(CardService(case(review=ReviewStatus.APPROVED)))).view
    assert labels(waiting) == {"是，發獎": True, "不是": True, "下一筆": True}


def test_a_press_from_a_non_owner_or_another_guild_reads_and_writes_nothing():
    """@description: The gate runs before any service call, so foreign presses see only the refusal.
    @param: none
    @return: None.
    """
    for foreign in (Press(OTHER_USER_ID), Press(OWNER_ID, 9999), Press(OWNER_ID, None)):
        service = CardService(case())
        press(cards_for(service), "approve", 1, 3, foreign)
        assert service.calls == [] and foreign.response.messages and foreign.edits == []


def test_yes_hands_the_displayed_case_to_the_core_once_and_shows_its_sentence():
    """@description: One press of yes calls the core once with the displayed case and the card's token dictionary, then shows the core's sentence and a next button.
    @param: none
    @return: None.
    """
    service, core = CardService(case(revision=3)), FakeCore()
    cards = cards_with_core(service, core)
    shown = press(cards, "yes", 1, 3)
    order, answer, duplicate_of, tokens = core.decisions[0]
    assert (order, answer, duplicate_of, len(core.decisions)) == (1, Answer.YES, None, 1) and tokens is cards._open_tokens
    assert "#1 已處理" in shown.shown() and "internal" not in shown.shown() and labels(shown.edits[-1]["view"]) == {"下一筆": True}


def test_the_card_itself_writes_nothing_when_an_answer_is_given():
    """@description: Every write belongs to the core: the card makes no preview or confirmation call of its own for a yes or a no.
    @param: none
    @return: None.
    """
    service = CardService(case(), case(2))
    cards = cards_with_core(service, FakeCore())
    press(cards, "yes", 1, 3)
    press(cards, "no", 2, 3)
    assert {call[0] for call in service.calls} <= {"list_pending", "list_known_versions", "retention_status"}


def test_a_card_older_than_the_case_is_refused_without_a_write():
    """@description: When the case moved to a newer revision the old button changes nothing and tells the Owner to reload.
    @param: none
    @return: None.
    """
    service = CardService(case(revision=4))
    shown = press(cards_for(service), "approve", 1, 3)
    assert not service.called("preview") and not service.called("confirm")
    assert "重新載入" in shown.shown() and labels(shown.edits[-1]["view"]) == {"重新載入": True}


def test_a_button_for_a_case_that_left_the_queue_is_refused():
    """@description: A case already handled is no longer pending, so its old buttons only offer a reload.
    @param: none
    @return: None.
    """
    service = CardService(case(review=ReviewStatus.REJECTED))
    shown = press(cards_for(service), "approve", 1, 3)
    assert not service.called("confirm") and "重新載入" in shown.shown()


def test_a_stale_error_from_the_core_is_shown_as_stale_and_never_confirms():
    """@description: If the case changes between the card check and the core's preview, the refusal is shown as a stale card and no confirmation runs.
    @param: none
    @return: None.
    """
    service, core = CardService(case(revision=3)), FakeCore()
    core.failures.append(StaleReviewVersion("stale"))
    shown = press(cards_with_core(service, core), "yes", 1, 3)
    assert not service.called("confirm") and "重新載入" in shown.shown()


def test_two_simultaneous_presses_decide_once():
    """@description: A double click or a concurrent press serialises: the core decides once, and the second press reports the case as already handled.
    @param: none
    @return: None.
    """
    service, core = CardService(case(revision=3)), FakeCore()
    cards = cards_with_core(service, core)
    first, second = Press(), Press()

    async def both():
        """@description: Run two presses of the same button at the same time.
        @param: none
        @return: None.
        """
        button = cards.button_class("yes", 1, 3)
        await asyncio.gather(button.callback(first), button.callback(second))

    asyncio.run(both())
    assert len(core.decisions) == 1
    assert sorted(["案件處理結果" in p.shown() for p in (first, second)]) == [False, True] and sum("已失效" in p.shown() for p in (first, second)) == 1


def test_an_unknown_outcome_is_retried_with_the_same_token():
    """@description: After a lost acknowledgement the buttons stay, and the next press hands the same token dictionary to the core again.
    @param: none
    @return: None.
    """
    service, core = CardService(case(revision=3)), FakeCore()
    core.failures.append(ReviewOutcomeUnknown("lost"))
    cards = cards_with_core(service, core)
    first = press(cards, "yes", 1, 3)
    assert "未知" in first.shown() and "已處理" not in first.shown() and labels(first.edits[-1]["view"])["是，發獎"]
    second = press(cards, "yes", 1, 3)
    assert len(core.decisions) == 2 and core.decisions[0][3] is core.decisions[1][3] is cards._open_tokens and "已處理" in second.shown()


def test_next_only_changes_the_displayed_case():
    """@description: Next reads the following pending case and shows it, with no preview, confirmation or state change.
    @param: none
    @return: None.
    """
    service = CardService(case(sequence=1), case(sequence=2, revision=7))
    shown = press(cards_for(service), "next", 1, 0)
    assert service.called("list_pending")[-1][2] == 1
    assert not (service.called("preview") or service.called("confirm") or service.called("preview_duration"))
    assert labels(shown.edits[-1]["view"])["是，發獎"] and "ocard:yes:2:7" in [c.custom_id for c in shown.edits[-1]["view"].children]


def test_next_after_the_last_case_says_so_and_offers_a_reload():
    """@description: Past the end the card says there is nothing more instead of repeating a case.
    @param: none
    @return: None.
    """
    shown = press(cards_for(CardService(case(sequence=1))), "next", 1, 0)
    assert "沒有更多" in shown.shown() and labels(shown.edits[-1]["view"]) == {"重新載入": True}


def test_no_decides_at_once_without_asking_for_a_reason():
    """@description: The no button opens no form: it hands the displayed case to the core as a no right away.
    @param: none
    @return: None.
    """
    service, core = CardService(case(revision=3)), FakeCore()
    shown = press(cards_with_core(service, core), "no", 1, 3)
    assert shown.response.modals == [] and core.decisions[0][:3] == (1, Answer.NO, None) and "已處理" in shown.shown()


def test_the_duration_form_is_gated_like_the_button():
    """@description: A foreign user who somehow submits the duration form is refused before any read or write.
    @param: none
    @return: None.
    """
    service = CardService(case(review=ReviewStatus.PENDING_VALIDATION, duration=None, revision=2))
    modal = press(cards_for(service), "duration", 1, 2).response.modals[0]
    modal.seconds._value, modal.source._value = "120", "https://example.test/check"
    foreign = Press(OTHER_USER_ID)
    asyncio.run(modal.on_submit(foreign))
    assert service.calls == [] and foreign.response.messages


def test_missing_duration_can_be_entered_in_discord_and_uses_the_service_preview():
    """@description: The duration form passes seconds and a traceable source to the service preview and then confirms it.
    @param: none
    @return: None.
    """
    service = CardService(case(review=ReviewStatus.PENDING_VALIDATION, duration=None, revision=2))
    opened = press(cards_for(service), "duration", 1, 2)
    modal = opened.response.modals[0]
    modal.seconds._value, modal.source._value = "120", "https://example.test/check"
    submitted = Press()
    asyncio.run(modal.on_submit(submitted))
    assert service.called("preview_duration")[0][3:5] == (120, "https://example.test/check")
    assert len(service.called("confirm")) == 1 and "已確認" in submitted.shown()


def test_a_non_numeric_duration_is_refused_before_any_service_call():
    """@description: Text that is not a whole number of seconds never reaches the service.
    @param: none
    @return: None.
    """
    service = CardService(case(review=ReviewStatus.PENDING_VALIDATION, duration=None, revision=2))
    modal = press(cards_for(service), "duration", 1, 2).response.modals[0]
    modal.seconds._value, modal.source._value = "abc", "https://example.test/check"
    submitted = Press()
    asyncio.run(modal.on_submit(submitted))
    assert not service.called("preview_duration") and "整數" in submitted.shown()


def test_the_registered_button_pattern_rebuilds_a_button_from_its_custom_id_after_a_restart():
    """@description: Buttons carry action, order and revision in their custom_id, so a fresh process can answer an old message.
    @param: none
    @return: None.
    """
    service = CardService(case(revision=4))
    cards = cards_for(service)
    match = cards.button_class.__discord_ui_compiled_template__.fullmatch("ocard:approve:1:3")
    rebuilt = asyncio.run(cards.button_class.from_custom_id(SimpleNamespace(), discord.ui.Button(custom_id="ocard:approve:1:3"), match))
    foreign = Press()
    asyncio.run(rebuilt.callback(foreign))
    assert "重新載入" in foreign.shown() and not service.called("confirm")


def test_the_displayed_revision_reaches_the_duration_preview_so_a_late_change_is_refused():
    """@description: The duration preview is bound to the revision the card showed, so a change made after the card's own check is refused by the service.
    @param: none
    @return: None.
    """
    service = CardService(case(review=ReviewStatus.PENDING_VALIDATION, duration=None, revision=2))
    original_list = service.list_pending

    def list_then_change(actor_id, *, after_sequence=None, limit=20):
        """@description: Return the row as displayed, then move the stored case to a newer revision.
        @param actor_id: Caller.
        @param after_sequence: Position already seen.
        @param limit: Maximum rows.
        @return: Page as it was before the change.
        """
        page = original_list(actor_id, after_sequence=after_sequence, limit=limit)
        service.items[1].revision = 3
        return page

    modal = press(cards_for(service), "duration", 1, 2).response.modals[0]
    modal.seconds._value, modal.source._value = "120", "https://example.test/check"
    service.list_pending = list_then_change
    submitted = Press()
    asyncio.run(modal.on_submit(submitted))
    assert service.called("preview_duration")[0][6] == 2
    assert not service.called("confirm") and "重新載入" in submitted.shown()


def known(sequence: int, video_id: str, version: str = "rv-existing"):
    """@description: Build a known-version row shaped like the service's KnownVersion, with an internal id the Owner must never see.
    @param sequence: Acceptance order of the version's first video, which is the card's opaque choice.
    @param video_id: First video of the version.
    @param version: Internal version id.
    @return: Known version row.
    """
    return SimpleNamespace(version_id=version, first_sequence=sequence, first_video_id=video_id, created_at=datetime(2026, 10, 20, tzinfo=timezone.utc))


def version_service(*versions, **case_fields) -> CardService:
    """@description: Build a service with one approved case whose version the system does not know, and the given known versions.
    @param versions: Known version rows.
    @param case_fields: Overrides for the case row.
    @return: Fake service.
    """
    service = CardService(case(**{"review": ReviewStatus.APPROVED, **case_fields}))
    service.versions = list(versions)
    return service


MV_ID, OTHER_ID = "MMMMMMMMMMM", "OOOOOOOOOOO"


def test_version_actions_are_offered_only_while_the_version_is_unresolved():
    """@description: A known or conflicting version offers no version button, because the system already knows or must not be overridden here.
    @param: none
    @return: None.
    """
    for identity in (VersionIdentityStatus.CONFIRMED, VersionIdentityStatus.CONFLICT):
        view = asyncio.run(cards_for(version_service(known(5, MV_ID), identity=identity, review=ReviewStatus.PENDING_REVIEW)).first_replies(OWNER_ID))[0].view
        assert not any(name.startswith(("確認為", "其他情況", "更多")) for name in labels(view))


def test_a_choice_that_no_longer_exists_is_refused_without_a_write():
    """@description: A version button whose version disappeared or never existed writes nothing and offers a reload.
    @param: none
    @return: None.
    """
    service = version_service(known(5, MV_ID))
    shown = press(cards_for(service), "same", 1, 3, extra=99)
    assert not service.called("preview_version_confirmation") and not service.called("confirm")
    assert "重新載入" in shown.shown()


def test_version_buttons_on_an_old_or_resolved_card_write_nothing():
    """@description: A newer revision or an already resolved version makes the old version buttons stale.
    @param: none
    @return: None.
    """
    for fields in ({"revision": 4}, {"identity": VersionIdentityStatus.CONFIRMED, "review": ReviewStatus.PENDING_REVIEW}):  # [still queued]: The case stays pending, so only the version guard can refuse.
        service = version_service(known(5, MV_ID), **fields)
        shown = press(cards_for(service), "new", 1, 3)
        assert not service.called("preview_version_confirmation") and "重新載入" in shown.shown()


def test_a_foreign_press_on_a_version_button_reads_and_writes_nothing():
    """@description: The gate runs before any version read or write.
    @param: none
    @return: None.
    """
    service = version_service(known(5, MV_ID))
    foreign = press(cards_for(service), "new", 1, 3, Press(OTHER_USER_ID))
    assert service.calls == [] and foreign.edits == [] and foreign.response.messages


def test_every_card_variant_fits_discord_component_and_message_limits():
    """@description: Each screen respects the documented limits: at most 5 rows, 5 buttons per row, custom_id up to 100 characters, labels up to 80, and 2000 characters of content, even with a very long link.
    @param: none
    @return: None.
    """
    long_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&" + "t=1&" * 500
    service = version_service(*[known(1000000000 + number, f"V{number:010d}", f"rv-{number}") for number in range(8)])
    service.items[1].source_url = long_url
    cards = cards_for(service)
    detail = first_card(cards)
    shots = [asyncio.run(cards.first_replies(OWNER_ID))[0], detail]
    picker = press(cards, "no", 1, 3)
    shots.append(SimpleNamespace(text=picker.shown(), view=picker.edits[-1]["view"]))
    for shot in shots:
        rows: dict[int, int] = {}
        for child in shot.view.children:
            rows[child.row] = rows.get(child.row, 0) + 1
            assert len(child.custom_id) <= 100 and len(child.item.label) <= 80
        assert len(rows) <= 5 and max(rows.values()) <= 5 and len(shot.text) <= 2000


def test_long_submitted_link_is_shown_as_the_canonical_watch_link_never_a_cut_url():
    """@description: A link too long for the message is replaced by its canonical watch link, which still opens the same video, instead of being cut.
    @param: none
    @return: None.
    """
    long_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&" + "t=1&" * 500
    service = version_service(known(1000000000, "V0000000000", "rv-0"))
    service.items[1].source_url = long_url
    text = first_card(cards_for(service)).text
    assert "\n<https://www.youtube.com/watch?v=dQw4w9WgXcQ>\n" in text
    assert "…" not in text and "t=1" not in text


def test_short_submitted_link_is_shown_unchanged():
    """@description: A link within the budget is displayed exactly as submitted.
    @param: none
    @return: None.
    """
    service = version_service(known(1000000000, "V0000000000", "rv-0"))
    service.items[1].source_url = "https://youtu.be/dQw4w9WgXcQ?t=5"
    text = first_card(cards_for(service)).text
    assert "\n<https://youtu.be/dQw4w9WgXcQ?t=5>\n" in text


def test_a_case_resting_on_an_expired_api_conclusion_cannot_be_answered_yes_from_the_card():
    """@description: The card says the official data expired, offers the Owner's duration entry instead of yes, and keeps the case pending when a yes is attempted anyway.
    @param: none
    @return: None.
    """
    item = case(review=ReviewStatus.PENDING_REVIEW, duration=None)
    item.api_basis = "expired"
    service, core = CardService(item), FakeCore()
    cards = cards_for(service)
    first = first_card(cards)
    assert "官方" in first.text and "已過期" in first.text
    shown = labels(first.view)
    assert "是，發獎" not in shown and shown["補時長"] is True
    attempted = press(cards, "approve", 1, 3)
    assert "已失效" in attempted.shown() and not service.called("confirm")
    assert item.review_status is ReviewStatus.PENDING_REVIEW
    core.failures.append(DurationEvidenceExpired("expired"))
    late = press(cards_with_core(CardService(case(revision=3)), core), "yes", 1, 3)
    assert "官方時長資料已過期" in late.shown()


def test_a_valid_api_conclusion_shows_no_seconds_and_keeps_yes_available():
    """@description: While the conclusion stands the card shows that the official data passed, without inventing seconds.
    @param: none
    @return: None.
    """
    item = case(review=ReviewStatus.PENDING_REVIEW, duration=None)
    item.api_basis = "valid"
    first = first_card(cards_for(CardService(item)))
    assert "官方資料已驗證在範圍內" in first.text and labels(first.view)["是，發獎"] is True and "補時長" not in labels(first.view)


def test_the_card_warns_when_the_purge_is_overdue_and_escalates_with_the_days():
    """@description: The warning appears from one day overdue, names the stopped lookups at three, and the manual command at seven and twelve.
    @param: none
    @return: None.
    """
    texts = {}
    for days in (0.5, 2, 4, 8, 13):
        service = CardService(case())
        service.retention = SimpleNamespace(overdue_rows=3, oldest_overdue_days=days)
        texts[days] = asyncio.run(cards_for(service).first_replies(OWNER_ID))[0].text
    assert "逾期" not in texts[0.5] and "系統會自動重試" in texts[2] and "已停止新的官方查詢" in texts[4]
    assert "purge --apply" in texts[8] and "JUKEBOX_AUTO_REVIEW" in texts[13]


def test_an_unreadable_retention_status_never_hides_the_card():
    """@description: The warning is a courtesy: a failing status read, such as a table that does not exist yet, leaves the card intact.
    @param: none
    @return: None.
    """
    service = CardService(case())
    service.retention = RuntimeError("relation does not exist")
    first = asyncio.run(cards_for(service).first_replies(OWNER_ID))[0]
    assert "待處理案件" in first.text and "逾期" not in first.text


def test_a_plain_button_press_removes_the_buttons_before_any_work_runs():
    """@description: The acknowledgement itself removes the card's buttons, so nothing can be pressed during the slow database work.
    @param: none
    @return: None.
    """
    service = CardService(case())
    seen = []
    interaction = Press()
    original = interaction.response.edit_message

    async def recording_edit(**kwargs):
        """@description: Note how many service calls had happened when the acknowledgement was sent.
        @param kwargs: Update options.
        @return: None.
        """
        seen.append(len(service.calls))
        await original(**kwargs)

    interaction.response.edit_message = recording_edit
    press(cards_with_core(service, FakeCore()), "yes", 1, 3, interaction)
    assert seen == [0] and interaction.response.locks == [{"view": None}] and interaction.response.deferrals == []
    assert interaction.edits and interaction.edits[-1]["view"] is not None


def test_pressing_the_same_button_twice_at_once_decides_only_once_and_the_second_press_is_stale():
    """@description: Two presses racing for one case serialize; the second sees the changed case and changes nothing.
    @param: none
    @return: None.
    """
    service, core = CardService(case(concerns=DOUBT)), FakeCore()
    cards = cards_with_core(service, core)
    first, second = Press(), Press()

    async def both():
        """@description: Run two callbacks of the same displayed button concurrently.
        @param: none
        @return: None.
        """
        await asyncio.gather(cards.button_class("yes", 1, 3).callback(first), cards.button_class("yes", 1, 3).callback(second))

    asyncio.run(both())
    assert len(core.decisions) == 1
    assert "已失效" in second.shown() or "已變更" in second.shown() or "已處理" in second.shown()


