"""Verify the card's yes button against the real decision core: a yes on a case without doubts is paid, and the card shows the core's sentence within Discord's limits.

Inputs are the fake review service and fake interactions of the card tests; no Discord connection is made. The service fakes here are also reused by the decision-core tests.
"""

import re

from bot.review import ReviewStatus
from bot.version_concerns import ConcernCode, VersionConcern
from bot.version_identity import VersionIdentityStatus
from tests.test_discord_owner_cards import CardService, OTHER_USER_ID, Press, cards_for, case, labels, press
from tests.test_discord_owner_cards_page import BARE_LINK, PagedService, find, open_page, push

PAID_TEXT = "已發放 1 Nickel"  # [reward sentence]: What the fake settlement says only after the version is confirmed.
UNPAID_TEXT = "待 Owner 確認錄音版本；目前未發放 Nickel。"  # [no reward sentence]: What it says while the version is not confirmed.
YES_LABEL = "是，發獎"  # [yes label]: The button that approves and settles the reward.
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


def test_a_yes_on_a_case_without_doubts_is_paid_through_the_real_core():
    """@description: One press of yes goes through the real core: the song is approved, the new song confirmed once, and the reward sentence is shown.
    @param: none
    @return: None.
    """
    service = PayService(pay_case())
    shown = press(cards_for(service), "yes", 1, 3)
    assert calls_named(service) == ["preview", "confirm", "preview_version_confirmation", "confirm"]
    assert PAID_TEXT in shown.shown() and "internal" not in shown.shown()
    assert labels(shown.edits[-1]["view"]) == {"下一筆": True}


def test_the_list_button_gives_the_same_decision_and_stays_within_the_message_limit():
    """@description: Pressing yes from the list decides the same way, returns to the list, and a long link plus the result still fit in 2000 characters unexpanded.
    @param: none
    @return: None.
    """
    service = PagedPayService(pay_case(1, 11), pay_case(2, 12))
    for row in service.items.values():
        row.source_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&" + "t=1&" * 500
    _, reply = open_page(service)
    assert find(reply.view, YES_LABEL).item.label == f"{YES_LABEL} #1"
    shown = push(find(reply.view, YES_LABEL))
    assert calls_named(service) == ["preview", "confirm", "preview_version_confirmation", "confirm"] and PAID_TEXT in shown.shown()
    assert len(shown.shown()) <= 2000 and not BARE_LINK.search(shown.shown()) and re.search(r"剛處理 #1", shown.shown())


def test_a_press_from_a_non_owner_reads_and_writes_nothing():
    """@description: The gate runs before any service call.
    @param: none
    @return: None.
    """
    service = PayService(pay_case())
    foreign = press(cards_for(service), "yes", 1, 3, Press(OTHER_USER_ID))
    assert service.calls == [] and foreign.edits == []


def test_a_stale_button_changes_nothing():
    """@description: A card older than the case, or for a case already decided, writes nothing.
    @param: none
    @return: None.
    """
    moved = PayService(pay_case(revision=4))
    assert "已失效" in press(cards_for(moved), "yes", 1, 3).shown() and calls_named(moved) == []
    done = PayService(case(review=ReviewStatus.REJECTED))
    assert "已失效" in press(cards_for(done), "yes", 1, 3).shown() and calls_named(done) == []
