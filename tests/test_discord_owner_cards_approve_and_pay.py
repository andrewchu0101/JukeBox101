"""Verify the card's approve button: with no version doubt it approves the song and confirms the version in one press so the reward can be paid.

Inputs are the fake review service and fake interactions of the card tests; no Discord connection is made.
"""

import asyncio
import re

from bot.owner_review import ReviewOutcomeUnknown
from bot.review import ReviewStatus
from bot.version_concerns import ConcernCode, VersionConcern
from bot.version_identity import VersionIdentityStatus
from tests.test_discord_owner_cards import CardService, OTHER_USER_ID, Press, cards_for, case, labels, press
from tests.test_discord_owner_cards_page import BARE_LINK, PagedService, find, open_page, push

PAID_TEXT = "已發放 1 Nickel"  # [reward sentence]: What the fake settlement says only after the version is confirmed.
UNPAID_TEXT = "待 Owner 確認錄音版本；目前未發放 Nickel。"  # [no reward sentence]: What it says while the version is not confirmed.
PAY_LABEL = "核准並發獎"  # [approve label]: Case without doubts: one press approves and confirms the version.
PENDING_VERSION_LABEL = "核准（版本待定）"  # [approve label]: Case with doubts: the press approves only.
COMBINED_REASON = "核准時一併確認為新的錄音版本"  # [audit reason part]: The reason text of the version confirmation made together with an approval.
DOUBT = (VersionConcern(ConcernCode.SIMILAR_TITLE, "abcdefghijk"),)  # [version doubt]: Any automatic-comparison concern.


class PayService(CardService):
    """Behave like the card service, but pay only when the version of the case is confirmed, as the real settlement does."""

    def settlement_note(self, actor_id: int, operation_id: str) -> str:
        """@description: Report a reward only for a case whose version is confirmed.
        @param self: Fake service.
        @param actor_id: Caller.
        @param operation_id: Case.
        @return: Reward sentence, or the no-reward sentence.
        """
        self.calls.append(("settlement_note", actor_id, operation_id))
        item = next(row for row in self.items.values() if row.operation_id == operation_id)
        return PAID_TEXT if item.identity_status is VersionIdentityStatus.CONFIRMED else UNPAID_TEXT


class PagedPayService(PagedService, PayService):
    """Page the queue like the real service and pay like PayService."""


def pay_case(sequence: int = 1, revision: int = 3, concerns=(), identity: VersionIdentityStatus = VersionIdentityStatus.UNRESOLVED):
    """@description: Build a reviewable pending row with the given doubts.
    @param sequence: Acceptance order.
    @param revision: Displayed revision.
    @param concerns: Stored automatic-comparison doubts.
    @param identity: Version state.
    @return: Pending row.
    """
    row = case(sequence=sequence, revision=revision, identity=identity)
    row.concerns = concerns
    return row


def calls_named(service: CardService) -> list[str]:
    """@description: List the names of the service calls that change or decide something.
    @param service: Fake service.
    @return: Names in call order.
    """
    return [call[0] for call in service.calls if call[0] in ("preview", "preview_version_confirmation", "confirm")]


def test_a_case_without_doubts_is_approved_and_its_version_confirmed_in_one_press():
    """@description: One press previews and confirms the approval, then previews and confirms the version once, and shows the reward result.
    @param: none
    @return: None.
    """
    service = PayService(pay_case())
    shown = press(cards_for(service), "approve", 1, 3)
    assert calls_named(service) == ["preview", "confirm", "preview_version_confirmation", "confirm"]
    assert len(service.called("preview")) == 1 and len(service.called("preview_version_confirmation")) == 1
    assert PAID_TEXT in shown.shown() and "internal" not in shown.shown()
    assert labels(shown.edits[-1]["view"]) == {"下一筆": True}


def test_the_version_is_confirmed_with_the_owner_path_at_the_new_revision_and_says_why():
    """@description: The version confirmation uses the approval's committed revision and the audit reason names the combined press.
    @param: none
    @return: None.
    """
    service = PayService(pay_case(revision=3))
    press(cards_for(service), "approve", 1, 3)
    version_call = service.called("preview_version_confirmation")[0]
    assert version_call[4] == 4 and COMBINED_REASON in version_call[3]
    assert len(service.evidence) == 1 and service.evidence[0].source_reference


def test_a_case_with_doubts_is_only_approved_and_the_owner_judges_the_version_later():
    """@description: Any stored doubt keeps the two steps: the press approves, never confirms a version, and the card still offers the version buttons.
    @param: none
    @return: None.
    """
    service = PayService(pay_case(concerns=DOUBT))
    shown = press(cards_for(service), "approve", 1, 3)
    assert calls_named(service) == ["preview", "confirm"] and PAID_TEXT not in shown.shown()
    assert UNPAID_TEXT in shown.shown()


def test_a_case_whose_version_is_already_confirmed_is_only_approved():
    """@description: The Owner's earlier version confirmation is not repeated; approval alone is done.
    @param: none
    @return: None.
    """
    service = PayService(pay_case(identity=VersionIdentityStatus.CONFIRMED))
    shown = press(cards_for(service), "approve", 1, 3)
    assert calls_named(service) == ["preview", "confirm"] and PAID_TEXT in shown.shown()


def test_the_approve_label_tells_whether_the_press_pays_or_leaves_the_version_open():
    """@description: Without doubts the button says it pays; with doubts it says the version stays undecided; the card text explains each.
    @param: none
    @return: None.
    """
    plain = open_card(PayService(pay_case()))
    doubtful = open_card(PayService(pay_case(concerns=DOUBT)))
    assert PAY_LABEL in labels(plain.view) and PENDING_VERSION_LABEL not in labels(plain.view)
    assert PENDING_VERSION_LABEL in labels(doubtful.view) and PAY_LABEL not in labels(doubtful.view)
    assert "一併" in plain.text and "只核准" in doubtful.text


def open_card(service: CardService):
    """@description: Open the single-case detail card of the oldest case.
    @param service: Fake service.
    @return: Object with the card's text and view.
    """
    from tests.test_discord_owner_cards import first_card
    return first_card(cards_for(service))


def test_the_list_and_the_detail_card_agree_on_the_label_and_the_result():
    """@description: The list page labels each case's button with the same wording plus its order, and pressing it from the list gives the same decision.
    @param: none
    @return: None.
    """
    service = PagedPayService(pay_case(1, 11), pay_case(2, 12, concerns=DOUBT))
    _, reply = open_page(service)
    assert find(reply.view, PAY_LABEL).item.label == f"{PAY_LABEL} #1"
    assert find(reply.view, PENDING_VERSION_LABEL).item.label == f"{PENDING_VERSION_LABEL} #2"
    shown = push(find(reply.view, PAY_LABEL))
    assert calls_named(service) == ["preview", "confirm", "preview_version_confirmation", "confirm"] and PAID_TEXT in shown.shown()


def test_a_failed_version_step_says_the_song_is_approved_but_the_version_is_not_and_pays_nothing():
    """@description: When the version preview fails after the approval, the card says so plainly and offers the version button for the new revision.
    @param: none
    @return: None.
    """
    service = PayService(pay_case(revision=3))
    service.version_preview_error = RuntimeError("boom")
    shown = press(cards_for(service), "approve", 1, 3)
    assert "已核准" in shown.shown() and "版本未確認" in shown.shown() and PAID_TEXT not in shown.shown() and "已發放" not in shown.shown()
    assert "ocard:new:1:4" in [child.custom_id for child in shown.edits[-1]["view"].children]


def test_pressing_again_after_a_failed_version_step_completes_only_the_missing_step():
    """@description: The old approve button is stale after the approval committed; the version button of the new card finishes the case without approving twice.
    @param: none
    @return: None.
    """
    service = PayService(pay_case(revision=3))
    service.version_preview_error = RuntimeError("boom")
    cards = cards_for(service)
    failed = press(cards, "approve", 1, 3)
    service.version_preview_error = None
    stale = press(cards, "approve", 1, 3)
    assert "已失效" in stale.shown() and len(service.called("preview")) == 1
    retry = push_by_id(cards, failed, "ocard:new:1:4")
    assert PAID_TEXT in retry.shown() and len(service.called("preview")) == 1 and len(service.called("preview_version_confirmation")) == 2


def push_by_id(cards, previous: Press, custom_id: str) -> Press:
    """@description: Press the button with the given id on the card a previous press produced.
    @param cards: Card handler.
    @param previous: Press whose last update holds the button.
    @param custom_id: Id of the button to press.
    @return: The new press.
    """
    button = next(child for child in previous.edits[-1]["view"].children if child.custom_id == custom_id)
    return push(button)


def test_an_unknown_outcome_of_the_version_step_is_retried_under_the_same_decision():
    """@description: A lost acknowledgement in the second step never claims a reward and the version button replays the same token.
    @param: none
    @return: None.
    """
    service = PayService(pay_case(revision=3))
    plain_confirm = service.confirm
    attempts = []

    def confirm_then_lose(actor_id, token_id):
        """@description: Let the approval through, then lose the acknowledgement of the first version confirmation.
        @param actor_id: Caller.
        @param token_id: Token.
        @return: Snapshot of a confirmation.
        """
        attempts.append(token_id)
        if len(attempts) == 2:
            raise ReviewOutcomeUnknown("lost")
        return plain_confirm(actor_id, token_id)

    service.confirm = confirm_then_lose
    cards = cards_for(service)
    first = press(cards, "approve", 1, 3)
    assert "未知" in first.shown() and PAID_TEXT not in first.shown()
    retry = push_by_id(cards, first, "ocard:new:1:4")
    assert len(service.called("preview_version_confirmation")) == 1 and PAID_TEXT in retry.shown()


def test_an_unknown_outcome_of_the_approval_is_retried_and_then_continues_to_the_version():
    """@description: A lost approval acknowledgement replays the same approval token and the same press goes on to confirm the version.
    @param: none
    @return: None.
    """
    service = PayService(pay_case(revision=3))
    service.confirm_failures = [ReviewOutcomeUnknown("lost")]
    cards = cards_for(service)
    first = press(cards, "approve", 1, 3)
    assert "未知" in first.shown() and not service.called("preview_version_confirmation")
    second = press(cards, "approve", 1, 3)
    assert len(service.called("preview")) == 1 and PAID_TEXT in second.shown()


def test_two_simultaneous_presses_decide_only_once():
    """@description: The case lock serializes the presses, so approval and version are each decided once and the second press is stale.
    @param: none
    @return: None.
    """
    service = PayService(pay_case())
    cards = cards_for(service)
    first, second = Press(), Press()

    async def both():
        """@description: Run two callbacks of the same displayed button at the same time.
        @param: none
        @return: None.
        """
        button = cards.button_class("approve", 1, 3)
        await asyncio.gather(button.callback(first), button.callback(second))

    asyncio.run(both())
    assert len(service.called("preview")) == 1 and len(service.called("preview_version_confirmation")) == 1
    assert sorted(PAID_TEXT in p.shown() for p in (first, second)) == [False, True]


def test_a_press_from_a_non_owner_reads_and_writes_nothing():
    """@description: The gate runs before any service call.
    @param: none
    @return: None.
    """
    service = PayService(pay_case())
    foreign = press(cards_for(service), "approve", 1, 3, Press(OTHER_USER_ID))
    assert service.calls == [] and foreign.edits == []


def test_a_stale_button_changes_nothing():
    """@description: A card older than the case, or for a case already decided, writes nothing.
    @param: none
    @return: None.
    """
    moved = PayService(pay_case(revision=4))
    assert "已失效" in press(cards_for(moved), "approve", 1, 3).shown() and calls_named(moved) == []
    done = PayService(case(review=ReviewStatus.REJECTED))
    assert "已失效" in press(cards_for(done), "approve", 1, 3).shown() and calls_named(done) == []


def test_the_combined_result_from_the_list_returns_to_the_list_within_the_message_limit():
    """@description: A long link and the combined result still fit in 2000 characters and stay unexpanded.
    @param: none
    @return: None.
    """
    service = PagedPayService(pay_case(1, 11), pay_case(2, 12))
    for row in service.items.values():
        row.source_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&" + "t=1&" * 500
    _, reply = open_page(service)
    shown = push(find(reply.view, PAY_LABEL))
    assert len(shown.shown()) <= 2000 and not BARE_LINK.search(shown.shown()) and re.search(r"剛處理 #1", shown.shown())
    assert PAID_TEXT in shown.shown()
