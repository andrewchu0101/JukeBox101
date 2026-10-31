"""Verify the Owner case cards decide only what they display, hide internal ids, and refuse stale, repeated or foreign presses.

Inputs are fake interactions and a fake review service that behaves like the real one for revisions and tokens; no Discord connection is made.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import discord
import pytest

from bot.discord_owner_cards import OwnerCards
from bot.evidence_record import VersionClaimKind
from bot.owner_review import ReviewAction, ReviewOutcomeUnknown, ReviewPreview
from bot.review import ReviewStatus, StaleReviewVersion
from bot.song_identity import VideoIdentity
from bot.version_identity import VersionIdentityStatus

OWNER_ID = 1001
GUILD_ID = 2002
OTHER_USER_ID = 4004
SUBMITTER_ID = 5555
VIDEO = VideoIdentity("dQw4w9WgXcQ")
VIDEO_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
NOW = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)
PENDING_STATES = {ReviewStatus.PENDING_VALIDATION, ReviewStatus.PENDING_REVIEW, ReviewStatus.VALIDATION_FAILED}


def case(sequence: int = 1, revision: int = 3, review: ReviewStatus = ReviewStatus.PENDING_REVIEW,
         identity: VersionIdentityStatus = VersionIdentityStatus.UNRESOLVED, duration: int | None = 189, conflict: str | None = None):
    """@description: Build one pending row shaped like the service's PendingItem, with an internal id the Owner must never see.
    @param sequence: Acceptance order, which the card uses instead of the operation id.
    @param revision: Review revision the card must decide.
    @param review: Review status.
    @param identity: Version identity status.
    @param duration: Known full-video seconds, or None when missing.
    @param conflict: Frozen conflict reason, when any.
    @return: Pending row.
    """
    return SimpleNamespace(operation_id=f"discord:internal-{sequence}", source_url=VIDEO_URL, accepted_sequence=sequence,
                           review_status=review, identity_status=identity, revision=revision, evidence_count=0,
                           conflict_reason=conflict, submitter_id=SUBMITTER_ID, duration_seconds=duration)


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


def labels(view) -> dict[str, bool]:
    """@description: Map each button label to whether it is enabled.
    @param view: Card view.
    @return: Label to enabled flag.
    """
    return {child.item.label: not child.item.disabled for child in view.children}


def test_the_first_card_shows_what_the_owner_needs_and_never_an_internal_id():
    """@description: The opening card names submitter, link, duration, review state, version state and the waiting reason, without the operation id.
    @param: none
    @return: None.
    """
    service = CardService(case())
    replies = asyncio.run(cards_for(service).first_replies(OWNER_ID))
    text = replies[0].text
    assert f"<@{SUBMITTER_ID}>" in text and VIDEO_URL in text and "3:09" in text
    assert "待審核" in text and "未確認" in text and "internal" not in text
    assert all("internal" not in child.custom_id and len(child.custom_id) <= 100 for child in replies[0].view.children)


def test_an_empty_queue_says_so_without_buttons():
    """@description: With nothing pending the Owner gets a plain sentence and no decision buttons.
    @param: none
    @return: None.
    """
    replies = asyncio.run(cards_for(CardService()).first_replies(OWNER_ID))
    assert replies[0].text == "目前沒有待處理案件。" and replies[0].view is None


def test_only_the_applicable_buttons_are_enabled_for_each_state():
    """@description: Approval needs a reviewable song, the duration entry appears only when the duration is missing, and a waiting-version case offers no decision.
    @param: none
    @return: None.
    """
    reviewable = asyncio.run(cards_for(CardService(case())).first_replies(OWNER_ID))[0].view
    assert labels(reviewable) == {"確認通過": True, "拒絕": True, "確認為新的錄音版本": True, "其他情況…": True, "下一筆": True}
    missing = asyncio.run(cards_for(CardService(case(review=ReviewStatus.PENDING_VALIDATION, duration=None))).first_replies(OWNER_ID))[0].view
    assert labels(missing) == {"確認通過": False, "拒絕": True, "補時長": True, "確認為新的錄音版本": True, "其他情況…": True, "下一筆": True}
    waiting = asyncio.run(cards_for(CardService(case(review=ReviewStatus.APPROVED))).first_replies(OWNER_ID))[0].view
    assert labels(waiting) == {"確認通過": False, "拒絕": False, "確認為新的錄音版本": True, "其他情況…": True, "下一筆": True}


def test_a_press_from_a_non_owner_or_another_guild_reads_and_writes_nothing():
    """@description: The gate runs before any service call, so foreign presses see only the refusal.
    @param: none
    @return: None.
    """
    for foreign in (Press(OTHER_USER_ID), Press(OWNER_ID, 9999), Press(OWNER_ID, None)):
        service = CardService(case())
        press(cards_for(service), "approve", 1, 3, foreign)
        assert service.calls == [] and foreign.response.messages and foreign.edits == []


def test_approve_decides_the_displayed_revision_in_one_press_and_confirms_once():
    """@description: One press previews against the shown revision and confirms immediately, then shows the committed result and the reward state.
    @param: none
    @return: None.
    """
    service = CardService(case(revision=3))
    shown = press(cards_for(service), "approve", 1, 3)
    preview = service.called("preview")[0]
    assert preview[3] is ReviewAction.APPROVE and preview[5] == 3 and preview[7].is_song is True
    assert len(service.called("confirm")) == 1
    assert "已確認" in shown.shown() and "未發放 Nickel" in shown.shown() and "internal" not in shown.shown()
    assert labels(shown.edits[-1]["view"]) == {"下一筆": True}


def test_approving_a_song_never_confirms_a_recording_version():
    """@description: Song approval is not a version decision: no version-confirmation call is made and the card says the version is still unconfirmed.
    @param: none
    @return: None.
    """
    service = CardService(case())
    shown = press(cards_for(service), "approve", 1, 3)
    assert {call[0] for call in service.calls} <= {"list_pending", "view", "preview", "confirm", "settlement_note"}
    assert "錄音版本" in shown.shown()


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


def test_a_service_stale_error_after_the_check_is_shown_as_stale_and_never_confirms():
    """@description: If the case changes between the card check and the preview, the service refuses and no confirmation runs.
    @param: none
    @return: None.
    """
    service = CardService(case(revision=3))
    original_view = service.view

    def view_then_change(actor_id, operation_id):
        """@description: Simulate a concurrent change right after the card verified the revision.
        @param actor_id: Caller.
        @param operation_id: Case.
        @return: Projection.
        """
        projection = original_view(actor_id, operation_id)
        service.items[1].revision = 4
        return projection

    service.view = view_then_change
    shown = press(cards_for(service), "approve", 1, 3)
    assert not service.called("confirm") and "重新載入" in shown.shown()


def test_two_simultaneous_presses_decide_once():
    """@description: A double click or a concurrent press serialises: exactly one confirmation, and the second press reports the case as already handled.
    @param: none
    @return: None.
    """
    service = CardService(case(revision=3))
    cards = cards_for(service)
    first, second = Press(), Press()

    async def both():
        """@description: Run two presses of the same button at the same time.
        @param: none
        @return: None.
        """
        button = cards.button_class("approve", 1, 3)
        await asyncio.gather(button.callback(first), button.callback(second))

    asyncio.run(both())
    assert len(service.called("confirm")) == 1 and len(service.called("preview")) == 1
    assert sorted(["已確認" in p.shown() for p in (first, second)]) == [False, True]


def test_an_unknown_outcome_is_retried_with_the_same_token():
    """@description: After a lost acknowledgement the next press confirms the same token instead of previewing a second decision.
    @param: none
    @return: None.
    """
    service = CardService(case(revision=3))
    service.confirm_failures = [ReviewOutcomeUnknown("lost")]
    cards = cards_for(service)
    first = press(cards, "approve", 1, 3)
    assert "未知" in first.shown() and "已確認" not in first.shown()
    second = press(cards, "approve", 1, 3)
    assert len(service.called("preview")) == 1 and len(service.called("confirm")) == 2
    assert service.called("confirm")[0][2] == service.called("confirm")[1][2] and "已確認" in second.shown()


def test_next_only_changes_the_displayed_case():
    """@description: Next reads the following pending case and shows it, with no preview, confirmation or state change.
    @param: none
    @return: None.
    """
    service = CardService(case(sequence=1), case(sequence=2, revision=7))
    shown = press(cards_for(service), "next", 1, 0)
    assert service.called("list_pending")[-1][2] == 1
    assert not (service.called("preview") or service.called("confirm") or service.called("preview_duration"))
    assert labels(shown.edits[-1]["view"])["確認通過"] and "ocard:approve:2:7" in [c.custom_id for c in shown.edits[-1]["view"].children]


def test_next_after_the_last_case_says_so_and_offers_a_reload():
    """@description: Past the end the card says there is nothing more instead of repeating a case.
    @param: none
    @return: None.
    """
    shown = press(cards_for(CardService(case(sequence=1))), "next", 1, 0)
    assert "沒有更多" in shown.shown() and labels(shown.edits[-1]["view"]) == {"重新載入": True}


def test_reject_opens_a_reason_form_and_decides_the_displayed_revision_on_submit():
    """@description: Rejection asks for a reason in a simple form, then previews and confirms against the shown revision.
    @param: none
    @return: None.
    """
    service = CardService(case(revision=3))
    cards = cards_for(service)
    opened = press(cards, "reject", 1, 3)
    assert len(opened.response.modals) == 1 and not service.called("preview")
    submitted = Press()
    modal = opened.response.modals[0]
    modal.reason._value = "不是歌曲"
    asyncio.run(modal.on_submit(submitted))
    preview = service.called("preview")[0]
    assert preview[3] is ReviewAction.REJECT and preview[4] == "不是歌曲" and preview[5] == 3
    assert len(service.called("confirm")) == 1 and "已確認" in submitted.shown()


def test_the_reason_form_is_gated_like_the_button():
    """@description: A foreign user who somehow submits the form is refused before any read or write.
    @param: none
    @return: None.
    """
    service = CardService(case())
    modal = press(cards_for(service), "reject", 1, 3).response.modals[0]
    modal.reason._value = "x"
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


def test_a_missing_version_mapping_is_never_described_as_a_new_recording():
    """@description: The card says the system has no mapping for the video and that this is not a claim that it is new, and says nothing of the sort when the version is known.
    @param: none
    @return: None.
    """
    unresolved = asyncio.run(cards_for(CardService(case(review=ReviewStatus.APPROVED))).first_replies(OWNER_ID))[0].text
    assert "沒有這支影片的版本對應" in unresolved and "不代表它是新錄音" in unresolved
    known = asyncio.run(cards_for(CardService(case(identity=VersionIdentityStatus.CONFIRMED))).first_replies(OWNER_ID))[0].text
    assert "沒有這支影片的版本對應" not in known and "版本狀態：已確認" in known


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


def test_an_unknown_version_lists_known_versions_by_link_and_never_by_internal_id():
    """@description: The card shows each known version as its first video link with a one-press button, without any internal version id.
    @param: none
    @return: None.
    """
    service = version_service(known(5, MV_ID, "rv-secret-a"), known(9, OTHER_ID, "rv-secret-b"))
    reply = asyncio.run(cards_for(service).first_replies(OWNER_ID))[0]
    assert f"watch?v={MV_ID}" in reply.text and f"watch?v={OTHER_ID}" in reply.text and "rv-secret" not in reply.text
    assert "不代表它是新錄音" in reply.text and "不宣稱特定類別" in reply.text
    ids = [child.custom_id for child in reply.view.children]
    assert "ocard:same:1:3:5" in ids and "ocard:same:1:3:9" in ids and all("rv-secret" not in item for item in ids)
    assert labels(reply.view)["確認為同一錄音 1"] and labels(reply.view)["確認為同一錄音 2"]


def test_version_actions_are_offered_only_while_the_version_is_unresolved():
    """@description: A known or conflicting version offers no version button, because the system already knows or must not be overridden here.
    @param: none
    @return: None.
    """
    for identity in (VersionIdentityStatus.CONFIRMED, VersionIdentityStatus.CONFLICT):
        view = asyncio.run(cards_for(version_service(known(5, MV_ID), identity=identity, review=ReviewStatus.PENDING_REVIEW)).first_replies(OWNER_ID))[0].view
        assert not any(name.startswith(("確認為", "其他情況", "更多")) for name in labels(view))


def test_a_new_version_is_created_only_when_the_owner_presses_and_gets_an_opaque_id():
    """@description: Opening the card creates nothing; the press creates one version whose id is random, not derived from the video, with no category claimed.
    @param: none
    @return: None.
    """
    service = version_service()
    cards = cards_for(service)
    asyncio.run(cards.first_replies(OWNER_ID))
    assert not service.called("preview_version_confirmation")
    shown = press(cards, "new", 1, 3)
    evidence = service.evidence[0]
    assert evidence.version.value.startswith("rv-") and "dQw4w9WgXcQ" not in evidence.version.value and evidence.version_kind is None
    assert "watch?v=dQw4w9WgXcQ" in evidence.source_reference and service.called("preview_version_confirmation")[0][4] == 3
    assert len(service.called("confirm")) == 1 and "已確認" in shown.shown() and "未發放 Nickel" in shown.shown()
    assert labels(shown.edits[-1]["view"]) == {"下一筆": True}


def test_choosing_a_known_version_records_the_same_recording_without_naming_a_category():
    """@description: The selected known version becomes this video's version, the source names both videos, and no MV or audio category is claimed.
    @param: none
    @return: None.
    """
    service = version_service(known(5, MV_ID, "rv-a"), known(9, OTHER_ID, "rv-b"))
    shown = press(cards_for(service), "same", 1, 3, extra=9)
    evidence = service.evidence[0]
    assert evidence.version.value == "rv-b" and evidence.version_kind is None
    assert f"watch?v={OTHER_ID}" in evidence.source_reference and "watch?v=dQw4w9WgXcQ" in evidence.source_reference
    assert "MV" not in evidence.source_reference and "已確認" in shown.shown()


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


def test_two_simultaneous_new_version_presses_create_one_version():
    """@description: A double click on the new-version button previews and confirms once.
    @param: none
    @return: None.
    """
    service = version_service()
    cards = cards_for(service)
    first, second = Press(), Press()

    async def both():
        """@description: Press the same button twice at the same moment.
        @param: none
        @return: None.
        """
        button = cards.button_class("new", 1, 3)
        await asyncio.gather(button.callback(first), button.callback(second))

    asyncio.run(both())
    assert len(service.called("preview_version_confirmation")) == 1 and len(service.called("confirm")) == 1
    assert sorted("已確認" in p.shown() for p in (first, second)) == [False, True]


def test_an_unknown_version_outcome_is_retried_with_the_same_token_and_version():
    """@description: After a lost acknowledgement the retry confirms the same token, so the same version id is used and no second version appears.
    @param: none
    @return: None.
    """
    service = version_service()
    service.confirm_failures = [ReviewOutcomeUnknown("lost")]
    cards = cards_for(service)
    assert "未知" in press(cards, "new", 1, 3).shown()
    assert "已確認" in press(cards, "new", 1, 3).shown()
    assert len(service.evidence) == 1 and len(service.called("confirm")) == 2


def test_other_cases_open_a_category_menu_and_write_nothing():
    """@description: The other-cases button lists the existing categories and the known versions for a remaster, states the evidence promise, and writes nothing.
    @param: none
    @return: None.
    """
    service = version_service(known(5, MV_ID), known(9, OTHER_ID))
    shown = press(cards_for(service), "other", 1, 3)
    assert labels(shown.edits[-1]["view"]) == {"Live 現場": True, "Cover 翻唱": True, "Remix": True, "重新錄製": True,
                                              "重製：同一錄音 1": True, "重製：同一錄音 2": True, "返回": True}
    assert "證據不足時不會建立任何版本" in shown.shown() and "可追溯" in shown.shown()
    assert not (service.called("preview_version_confirmation") or service.called("confirm"))


def test_more_known_versions_are_paged_without_any_write():
    """@description: Seven known versions show five at a time; the next page shows the rest and changes nothing.
    @param: none
    @return: None.
    """
    service = version_service(*[known(10 + number, f"V{number:010d}", f"rv-{number}") for number in range(7)])
    cards = cards_for(service)
    first = asyncio.run(cards.first_replies(OWNER_ID))[0]
    assert sum(1 for name in labels(first.view) if name.startswith("確認為同一錄音")) == 5 and labels(first.view)["更多已知版本"]
    cursor = int(next(c.custom_id for c in first.view.children if c.custom_id.startswith("ocard:vpage")).rsplit(":", 1)[1])
    second = press(cards, "vpage", 1, 3, extra=cursor)
    assert sum(1 for name in labels(second.edits[-1]["view"]) if name.startswith("確認為同一錄音")) == 2
    assert not (service.called("preview_version_confirmation") or service.called("confirm"))


def test_a_foreign_press_on_a_version_button_reads_and_writes_nothing():
    """@description: The gate runs before any version read or write.
    @param: none
    @return: None.
    """
    service = version_service(known(5, MV_ID))
    foreign = press(cards_for(service), "new", 1, 3, Press(OTHER_USER_ID))
    assert service.calls == [] and foreign.edits == [] and foreign.response.messages


CATEGORY_CASES = [("Live 現場", 1, VersionClaimKind.LIVE), ("Cover 翻唱", 2, VersionClaimKind.COVER),
                  ("Remix", 3, VersionClaimKind.REMIX), ("重新錄製", 4, VersionClaimKind.RE_RECORDING)]
SOURCE_TEXT = "https://example.test/live-performance-page-2026"


def submit_category(cards: OwnerCards, service: CardService, action: str, extra: int, source: str, revision: int = 3) -> Press:
    """@description: Press a category button, fill its form with the given source and submit it.
    @param cards: Card handler.
    @param service: Fake service, for the assertions after the call.
    @param action: kind or remaster.
    @param extra: Category ordinal, or the first acceptance order of the version to remaster.
    @param source: Judgement basis the Owner types.
    @param revision: Displayed revision.
    @return: The submission interaction.
    """
    modal = press(cards, action, 1, revision, extra=extra).response.modals[0]
    modal.source._value = source
    submitted = Press()
    asyncio.run(modal.on_submit(submitted))
    return submitted


@pytest.mark.parametrize("name,ordinal,kind", CATEGORY_CASES)
def test_a_distinct_category_creates_a_new_version_with_that_category_and_the_typed_source(name, ordinal, kind):
    """@description: Live, Cover, Remix and re-recording each create a new opaque version carrying the chosen category, with the Owner's own source and the displayed revision.
    @param name: Category button label.
    @param ordinal: Category ordinal in the button id.
    @param kind: Existing version category the evidence must carry.
    @return: None.
    """
    service = version_service()
    submitted = submit_category(cards_for(service), service, "kind", ordinal, SOURCE_TEXT)
    evidence = service.evidence[0]
    assert evidence.version_kind is kind and evidence.source_reference == SOURCE_TEXT
    assert evidence.version.value.startswith("rv-") and "dQw4w9WgXcQ" not in evidence.version.value
    assert service.called("preview_version_confirmation")[0][4] == 3 and len(service.called("confirm")) == 1
    assert "已確認" in submitted.shown() and name.split()[0] in submitted.shown()


def test_a_remaster_maps_to_the_selected_known_version_with_its_category_and_the_typed_source():
    """@description: A remaster is the same performance as the selected version, so it reuses that version with the remaster category and the Owner's source.
    @param: none
    @return: None.
    """
    service = version_service(known(5, MV_ID, "rv-a"), known(9, OTHER_ID, "rv-b"))
    submitted = submit_category(cards_for(service), service, "remaster", 9, SOURCE_TEXT)
    evidence = service.evidence[0]
    assert evidence.version.value == "rv-b" and evidence.version_kind is VersionClaimKind.REMASTER and evidence.source_reference == SOURCE_TEXT
    assert "已確認" in submitted.shown()


@pytest.mark.parametrize("weak_source", ["", "1234", "12345678", "discord:1234567", "interaction-98765"])
def test_a_source_the_existing_rule_rejects_never_reaches_a_preview_and_keeps_the_case_pending(weak_source):
    """@description: The form applies the same traceable-source rule as /owner confirm_version before any service call, so a weak source writes nothing and the card stays.
    @param weak_source: Text the existing rule refuses.
    @return: None.
    """
    service = version_service()
    submitted = submit_category(cards_for(service), service, "kind", 1, weak_source)
    assert not (service.called("preview_version_confirmation") or service.called("confirm"))
    assert "維持待處理" in submitted.shown() and labels(submitted.edits[-1]["view"])["確認為新的錄音版本"]


def test_evidence_the_rules_find_insufficient_keeps_the_case_pending_without_a_token():
    """@description: When the existing rules say the evidence establishes no version, nothing is confirmed, the card returns with the reason, and a corrected try works.
    @param: none
    @return: None.
    """
    service = version_service()
    cards = cards_for(service)
    service.version_preview_error = ValueError("Version confirmation has nothing to confirm: the evidence establishes no recording version")
    refused = submit_category(cards, service, "kind", 1, SOURCE_TEXT)
    assert not service.called("confirm") and "證據不足" in refused.shown() and "維持待處理" in refused.shown()
    service.version_preview_error = None
    assert "已確認" in submit_category(cards, service, "kind", 1, SOURCE_TEXT).shown()


def test_category_forms_are_gated_and_bound_to_the_displayed_revision():
    """@description: A foreign press opens no form, a foreign submission writes nothing, and a form opened on an old revision is refused on submit.
    @param: none
    @return: None.
    """
    service = version_service()
    cards = cards_for(service)
    assert press(cards, "kind", 1, 3, Press(OTHER_USER_ID), extra=1).response.modals == []
    modal = press(cards, "kind", 1, 3, extra=1).response.modals[0]
    modal.source._value = SOURCE_TEXT
    foreign = Press(OTHER_USER_ID)
    asyncio.run(modal.on_submit(foreign))
    assert service.calls == [] and foreign.edits == []
    service.items[1].revision = 4
    stale = Press()
    asyncio.run(modal.on_submit(stale))
    assert not service.called("preview_version_confirmation") and "重新載入" in stale.shown()


def test_two_simultaneous_category_submissions_create_one_version():
    """@description: A double submission of the same form previews and confirms once.
    @param: none
    @return: None.
    """
    service = version_service()
    cards = cards_for(service)
    modal = press(cards, "kind", 1, 3, extra=2).response.modals[0]
    modal.source._value = SOURCE_TEXT
    first, second = Press(), Press()

    async def both():
        """@description: Submit the same form twice at the same moment.
        @param: none
        @return: None.
        """
        await asyncio.gather(modal.on_submit(first), modal.on_submit(second))

    asyncio.run(both())
    assert len(service.called("preview_version_confirmation")) == 1 and len(service.called("confirm")) == 1


def test_an_unknown_category_outcome_retries_the_same_token_for_the_same_source():
    """@description: After a lost acknowledgement, submitting the same form again confirms the same token and keeps the same version id.
    @param: none
    @return: None.
    """
    service = version_service()
    service.confirm_failures = [ReviewOutcomeUnknown("lost")]
    cards = cards_for(service)
    assert "未知" in submit_category(cards, service, "kind", 1, SOURCE_TEXT).shown()
    assert "已確認" in submit_category(cards, service, "kind", 1, SOURCE_TEXT).shown()
    assert len(service.evidence) == 1 and len(service.called("confirm")) == 2


def test_back_returns_to_the_card_and_the_menu_pages_without_any_write():
    """@description: Back shows the card again and the menu pages through known versions, changing nothing.
    @param: none
    @return: None.
    """
    service = version_service(*[known(10 + number, f"V{number:010d}", f"rv-{number}") for number in range(7)])
    cards = cards_for(service)
    menu = press(cards, "other", 1, 3)
    assert sum(1 for name in labels(menu.edits[-1]["view"]) if name.startswith("重製")) == 5
    cursor = int(next(c.custom_id for c in menu.edits[-1]["view"].children if c.custom_id.startswith("ocard:opage")).rsplit(":", 1)[1])
    second = press(cards, "opage", 1, 3, extra=cursor)
    assert sum(1 for name in labels(second.edits[-1]["view"]) if name.startswith("重製")) == 2
    assert "確認為新的錄音版本" in labels(press(cards, "back", 1, 3).edits[-1]["view"])
    assert not (service.called("preview_version_confirmation") or service.called("confirm"))


def test_every_card_variant_fits_discord_component_and_message_limits():
    """@description: Each variant respects the documented limits: at most 5 rows, 5 buttons per row, custom_id up to 100 characters, labels up to 80, and 2000 characters of content, even with a very long link.
    @param: none
    @return: None.
    """
    long_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&" + "t=1&" * 500
    service = version_service(*[known(1000000000 + number, f"V{number:010d}", f"rv-{number}") for number in range(8)])
    service.items[1].source_url = long_url
    cards = cards_for(service)
    shots = [asyncio.run(cards.first_replies(OWNER_ID))[0]]
    for action, extra in (("other", 0), ("back", 0)):
        result = press(cards, action, 1, 3, extra=extra)
        shots.append(SimpleNamespace(text=result.shown(), view=result.edits[-1]["view"]))
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
    text = asyncio.run(cards_for(service).first_replies(OWNER_ID))[0].text
    assert "影片：https://www.youtube.com/watch?v=dQw4w9WgXcQ\n" in text
    assert "…" not in text and "t=1" not in text


def test_short_submitted_link_is_shown_unchanged():
    """@description: A link within the budget is displayed exactly as submitted.
    @param: none
    @return: None.
    """
    service = version_service(known(1000000000, "V0000000000", "rv-0"))
    service.items[1].source_url = "https://youtu.be/dQw4w9WgXcQ?t=5"
    text = asyncio.run(cards_for(service).first_replies(OWNER_ID))[0].text
    assert "影片：https://youtu.be/dQw4w9WgXcQ?t=5\n" in text
