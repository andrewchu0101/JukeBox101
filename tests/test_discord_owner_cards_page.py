"""Verify the /owner list page: at most three cases in one flat message, each with its own buttons, plus keyset paging, and unexpanded links.

Inputs are the fake review service and fake interactions of the card tests; no Discord connection is made.
"""

import asyncio
import re
from types import SimpleNamespace

from bot.discord_owner_cards import EMPTY_QUEUE_TEXT, OwnerCards
from bot.owner_review import ReviewOutcomeUnknown
from bot.review import ReviewStatus
from bot.version_identity import VersionIdentityStatus
from tests.test_discord_owner_cards import GUILD_ID, OTHER_USER_ID, OWNER_ID, Press, CardService, case, cards_for

BARE_LINK = re.compile(r"(?<!<)https?://")  # [bare link]: An http link not directly preceded by an angle bracket, which Discord would expand.
PAGE_CAPACITY = 3  # [page size]: Owner-approved number of cases in one list message.


class PagedService(CardService):
    """Behave like the real queue page: the next cursor is the last row's order, set only when a further row exists."""

    def list_pending(self, actor_id: int, *, after_sequence=None, limit: int = 20):
        """@description: Return one keyset page with the real next-cursor rule.
        @param self: Fake service.
        @param actor_id: Caller.
        @param after_sequence: Position already seen.
        @param limit: Maximum rows.
        @return: Page object.
        """
        everything = super().list_pending(actor_id, after_sequence=after_sequence, limit=1000)
        self.calls[-1] = ("list_pending", actor_id, after_sequence, limit)  # [real request]: Record the page size the card asked for, not the one used to read the whole queue.
        page = everything.items[:limit]
        return SimpleNamespace(items=page, next_after_sequence=page[-1].accepted_sequence if len(everything.items) > limit else None)


def open_page(service: CardService):
    """@description: Open the list the way /owner list does.
    @param service: Fake service.
    @return: Cards handler and the first reply.
    """
    cards = cards_for(service)
    return cards, asyncio.run(cards.first_replies(OWNER_ID))[0]


def find(reply_view, label_part: str):
    """@description: Find one button of a view by part of its label.
    @param reply_view: View of a reply or an update.
    @param label_part: Text the label contains.
    @return: The matching dynamic button.
    """
    return next(child for child in reply_view.children if label_part in child.item.label)


def push(button, interaction: Press | None = None) -> Press:
    """@description: Press a rendered button, exactly as Discord would route its custom_id back.
    @param button: Dynamic button taken from a rendered view.
    @param interaction: Press to use, or a default Owner press.
    @return: The press, for inspecting its replies.
    """
    interaction = interaction or Press()
    asyncio.run(button.callback(interaction))
    return interaction


def sequences_shown(text: str) -> list[int]:
    """@description: Read the acceptance orders a page text lists, in order.
    @param text: Page text.
    @return: Orders, each marked as #<order> in the text.
    """
    return [int(number) for number in re.findall(r"#(\d+)", text)]


def queue(count: int, identity: VersionIdentityStatus = VersionIdentityStatus.UNRESOLVED) -> PagedService:
    """@description: Build a queue of pending reviews with distinct revisions.
    @param count: Number of cases, ordered 1..count.
    @param identity: Version state of every case; a confirmed version leaves the queue once the song is decided.
    @return: Fake service.
    """
    return PagedService(*[case(sequence=number, revision=10 + number, identity=identity) for number in range(1, count + 1)])


def test_a_page_shows_at_most_three_cases_in_one_message():
    """@description: Five waiting cases open as a page of the oldest three with a way to the rest.
    @param: none
    @return: None.
    """
    service = queue(5)
    _, reply = open_page(service)
    assert sequences_shown(reply.text) == [1, 2, 3]
    assert service.called("list_pending")[0][3] == PAGE_CAPACITY


def test_each_case_has_its_own_buttons_bound_to_its_own_order_and_revision():
    """@description: The buttons of case 2 carry order 2 and its revision 12 and never another case's.
    @param: none
    @return: None.
    """
    _, reply = open_page(queue(3))
    for sequence in (1, 2, 3):
        mine = [child for child in reply.view.children if f":{sequence}:{10 + sequence}" in child.custom_id]
        assert {child.item.label.split(" ")[0] for child in mine} >= {"確認通過", "拒絕"}
        assert len({child.row for child in mine}) == 1
    assert len({child.row for child in reply.view.children}) == 4


def test_the_page_respects_discord_component_and_message_limits_in_every_state():
    """@description: Three heavy cases still fit: at most 4 rows, 5 buttons per row, ids up to 100 characters, 2000 characters of text.
    @param: none
    @return: None.
    """
    long_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&" + "t=1&" * 500
    service = PagedService(case(1, duration=None, review=ReviewStatus.PENDING_VALIDATION, conflict="x" * 400), case(2, review=ReviewStatus.PENDING_REVIEW),
                           case(3, review=ReviewStatus.VALIDATION_FAILED), case(4))
    for row in service.items.values():
        row.source_url = long_url
    _, reply = open_page(service)
    rows: dict[int, int] = {}
    for child in reply.view.children:
        rows[child.row] = rows.get(child.row, 0) + 1
        assert len(child.custom_id) <= 100 and len(child.item.label) <= 80
    assert len(rows) <= 4 and max(rows.values()) <= 5 and len(reply.text) <= 2000


def test_every_link_is_wrapped_so_discord_does_not_expand_it():
    """@description: No http link on the page is bare, yet each case still shows its link.
    @param: none
    @return: None.
    """
    _, reply = open_page(queue(3))
    assert not BARE_LINK.search(reply.text) and reply.text.count("<https://www.youtube.com/watch?v=") == 3


def test_the_single_case_view_and_the_decision_result_do_not_expand_links_either():
    """@description: The detail card and the result of a decision also wrap the link.
    @param: none
    @return: None.
    """
    service = queue(3)
    cards, reply = open_page(service)
    detail = push(find(reply.view, "詳情"))
    assert not BARE_LINK.search(detail.shown())
    result = push(find(detail.edits[-1]["view"], "確認通過"))
    assert not BARE_LINK.search(result.shown())


def test_an_empty_queue_says_so_in_words():
    """@description: Nothing waiting gives the explicit sentence and no buttons.
    @param: none
    @return: None.
    """
    _, reply = open_page(PagedService())
    assert reply.text == EMPTY_QUEUE_TEXT and reply.view is None


def test_next_and_previous_walk_the_queue_without_a_gap_or_a_repeat():
    """@description: Seven cases make pages 1-3, 4-6 and 7; Next and Previous move between them and each case appears once.
    @param: none
    @return: None.
    """
    _, first = open_page(queue(7))
    second = push(find(first.view, "下一頁")).edits[-1]
    third = push(find(second["view"], "下一頁")).edits[-1]
    assert [sequences_shown(first.text), sequences_shown(second["content"]), sequences_shown(third["content"])] == [[1, 2, 3], [4, 5, 6], [7]]
    assert find(third["view"], "下一頁").item.disabled and find(first.view, "上一頁").item.disabled
    assert sequences_shown(push(find(third["view"], "上一頁")).shown()) == [4, 5, 6]
    assert sequences_shown(push(find(second["view"], "上一頁")).shown()) == [1, 2, 3]


def test_a_decision_on_one_case_leaves_the_others_untouched_and_refreshes_the_page():
    """@description: Approving case 2 decides only case 2; cases 1 and 3 keep their revision, and the page refills with the next case.
    @param: none
    @return: None.
    """
    service = queue(5, VersionIdentityStatus.CONFIRMED)
    _, reply = open_page(service)
    approve = next(child for child in reply.view.children if child.custom_id.startswith("ocard:approve:2:12"))
    shown = push(approve)
    assert len(service.called("confirm")) == 1 and service.called("preview")[0][2] == "discord:internal-2" and service.called("preview")[0][5] == 12
    assert service.items[1].revision == 11 and service.items[3].revision == 13
    assert sequences_shown(shown.shown().split("\n\n", 1)[-1]) == [1, 3, 4] and "已" in shown.shown().split("\n\n", 1)[0]
    assert all(child.custom_id.startswith("ocard:") for child in shown.edits[-1]["view"].children)


def test_a_card_for_a_case_that_changed_or_left_is_stale_and_runs_nothing():
    """@description: Pressing approve for a case at an old revision, or one no longer waiting, writes nothing and says the card is stale.
    @param: none
    @return: None.
    """
    service = queue(3)
    cards, reply = open_page(service)
    approve = next(child for child in reply.view.children if child.custom_id.startswith("ocard:approve:2:12"))
    service.items[2].revision = 99
    assert "已失效" in push(approve).shown() and not service.called("confirm")
    del service.items[3]
    gone = next(child for child in reply.view.children if child.custom_id.startswith("ocard:detail:3:13"))
    assert "已失效" in push(gone).shown() and not service.called("confirm")


def test_pressing_the_same_page_button_twice_at_once_decides_once():
    """@description: Two simultaneous presses of one case's approve serialize; the second finds the case changed and does nothing.
    @param: none
    @return: None.
    """
    service = queue(3)
    cards, reply = open_page(service)
    approve = next(child for child in reply.view.children if child.custom_id.startswith("ocard:approve:2:12"))
    first, second = Press(), Press()

    async def both():
        """@description: Run both callbacks together.
        @param: none
        @return: None.
        """
        await asyncio.gather(approve.callback(first), approve.callback(second))

    asyncio.run(both())
    assert len(service.called("confirm")) == 1


def test_a_page_button_removes_the_buttons_before_any_work_runs():
    """@description: The acknowledgement itself takes the buttons away, so nothing is pressable during the slow work.
    @param: none
    @return: None.
    """
    service = queue(3)
    _, reply = open_page(service)
    interaction = Press()
    push(find(reply.view, "確認通過"), interaction)
    assert interaction.response.locks == [{"view": None}] and interaction.edits[-1]["view"] is not None


def test_a_press_from_a_non_owner_reads_and_writes_nothing():
    """@description: Someone else pressing a page button is refused before any service call.
    @param: none
    @return: None.
    """
    service = queue(3)
    _, reply = open_page(service)
    calls_before = len(service.calls)
    foreign = push(find(reply.view, "確認通過"), Press(user_id=OTHER_USER_ID, guild_id=GUILD_ID))
    assert foreign.response.messages and len(service.calls) == calls_before and not foreign.edits


def test_an_unknown_outcome_is_retried_with_the_original_decision_from_the_page():
    """@description: After a lost connection the buttons come back, and pressing again replays the same token instead of deciding twice.
    @param: none
    @return: None.
    """
    service = queue(3)
    service.confirm_failures.append(ReviewOutcomeUnknown("lost"))
    _, reply = open_page(service)
    unknown = push(next(child for child in reply.view.children if child.custom_id.startswith("ocard:approve:2:12")))
    assert "結果未知" in unknown.shown()
    retry = push(next(child for child in unknown.edits[-1]["view"].children if child.custom_id.startswith("ocard:approve:2:12")))
    assert len(service.called("preview")) == 1 and len(service.called("confirm")) == 2
    assert service.called("confirm")[0][2] == service.called("confirm")[1][2] and "已確認" in retry.shown()


def test_detail_shows_one_case_and_the_back_button_returns_to_the_same_page():
    """@description: Detail opens the full single card for one case with a return to the list, and returning shows the page again.
    @param: none
    @return: None.
    """
    service = queue(5)
    _, reply = open_page(service)
    detail = push(find(reply.view, "詳情"))
    assert "待處理案件" in detail.shown() and "返回清單" in [child.item.label for child in detail.edits[-1]["view"].children]
    assert sequences_shown(push(find(detail.edits[-1]["view"], "返回清單")).shown()) == [1, 2, 3]


def test_a_form_opened_from_the_page_decides_that_case_and_refreshes_the_page():
    """@description: Rejecting from the page opens the reason form for that case only, and submitting it refreshes the page.
    @param: none
    @return: None.
    """
    service = queue(5)
    _, reply = open_page(service)
    opener = Press()
    push(next(child for child in reply.view.children if child.custom_id.startswith("ocard:reject:2:12")), opener)
    modal = opener.response.modals[0]
    modal.reason._value = "不是歌曲"
    submitted = Press()
    asyncio.run(modal.on_submit(submitted))
    assert service.called("preview")[0][2] == "discord:internal-2" and sequences_shown(submitted.shown().split("\n\n", 1)[-1]) == [1, 3, 4]


def test_old_card_buttons_without_a_page_marker_still_work_after_the_upgrade():
    """@description: An id issued before this change still matches the registered pattern and behaves as a single card button.
    @param: none
    @return: None.
    """
    service = CardService(case(revision=3))
    cards = cards_for(service)
    template = cards.button_class.__discord_ui_compiled_template__
    assert template.fullmatch("ocard:approve:1:3") and template.fullmatch("ocard:approve:1:3:p0") and template.fullmatch("ocard:same:1:3:7:p0")
    shown = push(cards.button_class("approve", 1, 3))
    assert "已確認" in shown.shown() and "ocard:next:1:0" in [child.custom_id for child in shown.edits[-1]["view"].children]


def test_three_heavy_cases_plus_a_long_just_handled_line_still_fit_in_one_message():
    """@description: After a decision with a very long reward note, the page text stays within 2000 characters and keeps its buttons.
    @param: none
    @return: None.
    """
    long_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&" + "t=1&" * 500
    service = PagedService(*[case(number, revision=10 + number, conflict="x" * 400) for number in range(1, 6)])
    for row in service.items.values():
        row.source_url, row.identity_status = long_url, VersionIdentityStatus.CONFLICT
    service.settlement_note = lambda actor_id, operation_id: "很長的說明" * 300
    _, reply = open_page(service)
    shown = push(next(child for child in reply.view.children if child.custom_id.startswith("ocard:approve:2:12")))
    assert len(shown.shown()) <= 2000 and "剛處理 #2" in shown.shown() and shown.edits[-1]["view"].children
