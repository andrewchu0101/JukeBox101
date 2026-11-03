"""Verify a card that lacks a duration tells the Owner the system still looks it up by itself, and only offers the manual entry as an extra.

Inputs are the fake review service and fake decision core of the card tests; no Discord connection is made. The automatic lookup itself lives in the auto review pipeline and is not touched here.
"""

import asyncio

from bot.review import ReviewStatus
from tests.test_discord_owner_cards import OWNER_ID, case
from tests.test_discord_owner_cards_core_fake import CaseChoices, FakeCore, cards_with_core
from tests.test_discord_owner_cards_page import PagedService


def first_text(item) -> str:
    """@description: Render the first list page for one case that needs a duration.
    @param item: Pending row without a usable duration.
    @return: The message text.
    """
    core = FakeCore()
    core.choices[1] = CaseChoices(can_yes=False, needs_duration=True)
    cards = cards_with_core(PagedService(item), core)
    return asyncio.run(cards.first_replies(OWNER_ID))[0].text


def test_a_case_waiting_for_its_duration_says_the_system_is_fetching_it() -> None:
    """@description: A missing duration is not described as the Owner's job; the automatic lookup is named first.
    @return: None.
    """
    text = first_text(case(1, review=ReviewStatus.PENDING_VALIDATION, duration=None))
    assert "自動" in text and "補時長" in text and "請先補時長" not in text


def test_a_case_the_system_could_not_look_up_says_why_and_offers_the_entry() -> None:
    """@description: A failed lookup keeps its own wording instead of the generic request.
    @return: None.
    """
    text = first_text(case(1, review=ReviewStatus.VALIDATION_FAILED, duration=None))
    assert "無法取得影片資料" in text and "補時長" in text


def test_an_expired_official_basis_says_the_system_will_verify_again() -> None:
    """@description: An expired conclusion is re-verified automatically; the Owner may still enter a duration.
    @return: None.
    """
    item = case(1, duration=None)
    item.api_basis = "expired"
    text = first_text(item)
    assert "自動重新驗證" in text and "補時長" in text
