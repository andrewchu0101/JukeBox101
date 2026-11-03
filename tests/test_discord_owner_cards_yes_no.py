"""Verify the Owner cards ask one plain question per case, answer it through the shared decision core, and never leave a message without buttons.

Inputs are the fake review service and fake interactions of the card tests plus a fake decision core that has the interface of bot.owner_decisions; no Discord connection is made.
"""

import asyncio
import re
from types import SimpleNamespace

from bot.discord_owner_review import INTERNAL_ERROR_TEXT
from bot.owner_review import ReviewOutcomeUnknown
from bot.review import ReviewStatus
from bot.version_identity import VersionIdentityStatus
from tests.test_discord_owner_cards import GUILD_ID, NOW, OTHER_USER_ID, OWNER_ID, Press, case
from tests.test_discord_owner_cards_core_fake import (BANNED_TERMS, FROZEN_LINE, NO_LABEL, PICK_QUESTION, QUESTION, YES_LABEL, Answer, CaseChoices,
                                                       FakeCore, DOUBT_TEXT, cards_with_core)
from tests.test_discord_owner_cards_page import PagedService, find, push, sequences_shown


def setup(count: int = 3, **row_options):
    """@description: Open a list page of fresh cases over the fake service and the fake core.
    @param count: Number of waiting cases.
    @param row_options: Extra options for every case row.
    @return: Service, core, card handler and the first page reply.
    """
    service = PagedService(*[case(number, revision=10 + number, **row_options) for number in range(1, count + 1)])
    core = FakeCore()
    cards = cards_with_core(service, core)
    return service, core, cards, asyncio.run(cards.first_replies(OWNER_ID))[0]


def labels_of(view) -> list[str]:
    """@description: List the button texts of a view.
    @param view: View of a reply or an update.
    @return: Labels in order.
    """
    return [child.item.label for child in view.children]


def row_labels(view, row: int) -> list[str]:
    """@description: List the button texts of one row.
    @param view: View of a reply or an update.
    @param row: Row index.
    @return: Labels in order.
    """
    return [child.item.label for child in view.children if child.row == row]


def button(view, action: str, sequence: int):
    """@description: Find the button of one action and case by its custom id.
    @param view: View of a reply or an update.
    @param action: Card action.
    @param sequence: Acceptance order.
    @return: Dynamic button.
    """
    return next(child for child in view.children if child.custom_id.startswith(f"ocard:{action}:{sequence}:"))


def test_each_case_asks_one_question_with_yes_no_and_detail_buttons():
    """@description: The page lists each case with its sender, unexpanded link and the question, and three buttons in one row.
    @param: none
    @return: None.
    """
    _, _, _, reply = setup()
    assert sequences_shown(reply.text) == [1, 2, 3] and reply.text.count(QUESTION) == 3
    assert "投稿者" in reply.text and reply.text.count("<https://www.youtube.com/watch?v=") == 3
    for row in range(3):
        first, second, third = row_labels(reply.view, row)
        assert first.startswith(YES_LABEL) and second.startswith(NO_LABEL) and third.startswith("詳情")


def test_the_doubt_line_appears_only_when_the_core_has_a_doubt():
    """@description: A case with a doubt shows the core's sentence under the question; a case without one shows no such line.
    @param: none
    @return: None.
    """
    service, core, cards, _ = setup()
    core.choices[2] = CaseChoices(doubt_text=DOUBT_TEXT)
    reply = asyncio.run(cards.first_replies(OWNER_ID))[0]
    assert reply.text.count(DOUBT_TEXT) == 1
    entry_two = reply.text.split("#2", 1)[1].split("#3", 1)[0]
    assert DOUBT_TEXT in entry_two and QUESTION in entry_two


def test_a_case_that_needs_a_duration_offers_no_yes_button():
    """@description: The yes button is replaced by the duration button; the no button stays only when the core allows it.
    @param: none
    @return: None.
    """
    service, core, cards, _ = setup()
    core.choices[1] = CaseChoices(can_yes=False, needs_duration=True)
    core.choices[2] = CaseChoices(can_yes=False, can_no=False, needs_duration=True)
    reply = asyncio.run(cards.first_replies(OWNER_ID))[0]
    first, second = row_labels(reply.view, 0), row_labels(reply.view, 1)
    assert not any(label.startswith(YES_LABEL) for label in first + second)
    assert first[0].startswith("補時長") and any(label.startswith(NO_LABEL) for label in first)
    assert second[0].startswith("補時長") and not any(label.startswith(NO_LABEL) for label in second)


def test_a_frozen_conflict_shows_one_sentence_and_neither_answer():
    """@description: The page and the detail card say the conflict must be cleared with the command-line tool and offer no yes or no.
    @param: none
    @return: None.
    """
    service, core, cards, _ = setup(count=2)
    core.choices[1] = CaseChoices(can_yes=False, can_no=False, frozen_conflict=True)
    reply = asyncio.run(cards.first_replies(OWNER_ID))[0]
    assert FROZEN_LINE in reply.text and row_labels(reply.view, 0) == ["詳情 #1"]
    detail = push(find(reply.view, "詳情 #1"))
    assert FROZEN_LINE in detail.shown()
    assert not any(label.startswith((YES_LABEL, NO_LABEL)) for label in labels_of(detail.edits[-1]["view"]))


def test_yes_calls_the_core_once_and_shows_its_text_on_the_refreshed_page():
    """@description: Yes decides only that case through decide(YES), with the card's own token dictionary, and the page shows the core's sentence.
    @param: none
    @return: None.
    """
    service, core, cards, reply = setup(count=5)
    shown = push(button(reply.view, "yes", 2))
    order, answer, duplicate_of, tokens = core.decisions[0]
    assert (order, answer, duplicate_of, len(core.decisions)) == (2, Answer.YES, None, 1) and tokens is cards._open_tokens
    assert "#2 已處理（yes）" in shown.shown().split("\n\n", 1)[0] and sequences_shown(shown.shown().split("\n\n", 1)[-1]) == [1, 3, 4]
    assert service.items[1].revision == 11 and service.items[3].revision == 13


def test_no_without_a_duplicate_requirement_decides_at_once():
    """@description: No decides through decide(NO) with no duplicate chosen.
    @param: none
    @return: None.
    """
    _, core, _, reply = setup()
    push(button(reply.view, "no", 1))
    assert core.decisions[0][:3] == (1, Answer.NO, None)


def test_no_that_needs_a_duplicate_asks_which_song_before_deciding():
    """@description: The first press only shows the picker with the known songs; choosing one decides NO with that song's order.
    @param: none
    @return: None.
    """
    service, core, cards, reply = setup()
    service.versions = [SimpleNamespace(version_id="rv-a", first_sequence=7, first_video_id="aaaaaaaaaaa", created_at=NOW),
                        SimpleNamespace(version_id="rv-b", first_sequence=9, first_video_id="bbbbbbbbbbb", created_at=NOW)]
    core.choices[1] = CaseChoices(no_requires_duplicate_pick=True)
    picker = push(button(reply.view, "no", 1))
    assert not core.decisions and PICK_QUESTION in picker.shown() and "<https://www.youtube.com/watch?v=aaaaaaaaaaa>" in picker.shown()
    chosen = push(next(child for child in picker.edits[-1]["view"].children
                       if child.custom_id.startswith("ocard:dup:1:") and child.custom_id.split(":")[4] == "9"))
    assert core.decisions[0][:3] == (1, Answer.NO, 9) and chosen.edits


def test_the_picker_can_go_back_to_the_card_without_deciding():
    """@description: Leaving the picker shows the case again and writes nothing.
    @param: none
    @return: None.
    """
    service, core, cards, reply = setup()
    core.choices[1] = CaseChoices(no_requires_duplicate_pick=True)
    picker = push(button(reply.view, "no", 1))
    back = push(button(picker.edits[-1]["view"], "back", 1))
    assert not core.decisions and QUESTION in back.shown()


def test_a_failed_decision_always_leaves_a_reload_button():
    """@description: An unexpected failure and an expected one both end with the message text and a reload button that returns to the list.
    @param: none
    @return: None.
    """
    for failure in (RuntimeError("boom"), ValueError("bad"), TimeoutError("lock")):
        service, core, cards, reply = setup()
        core.failures.append(failure)
        shown = push(button(reply.view, "yes", 1))
        view = shown.edits[-1]["view"]
        assert view is not None and "重新載入" in labels_of(view)
        reloaded = push(find(view, "重新載入"))
        assert sequences_shown(reloaded.shown()) == [1, 2, 3]
    _, core, _, reply = setup()
    core.failures.append(RuntimeError("boom"))
    assert INTERNAL_ERROR_TEXT in push(button(reply.view, "yes", 1)).shown()


def test_a_failure_on_a_single_card_also_leaves_a_reload_button():
    """@description: Opened from its detail card, a failing yes still ends with a reload button.
    @param: none
    @return: None.
    """
    service, core, cards, reply = setup()
    detail = push(find(reply.view, "詳情 #1"))
    core.failures.append(RuntimeError("boom"))
    shown = push(button(detail.edits[-1]["view"], "yes", 1))
    assert "重新載入" in labels_of(shown.edits[-1]["view"])


def test_an_unknown_outcome_keeps_the_buttons_and_retries_with_the_same_tokens():
    """@description: After a lost connection the buttons come back, and pressing again hands the same token dictionary to the core.
    @param: none
    @return: None.
    """
    service, core, cards, reply = setup()
    core.failures.append(ReviewOutcomeUnknown("lost"))
    unknown = push(button(reply.view, "yes", 1))
    assert "結果未知" in unknown.shown()
    push(button(unknown.edits[-1]["view"], "yes", 1))
    assert len(core.decisions) == 2 and core.decisions[0][3] is core.decisions[1][3] is cards._open_tokens


def test_pressing_yes_twice_at_once_decides_once():
    """@description: Two simultaneous presses serialize; the second finds the case changed and reaches the core no more.
    @param: none
    @return: None.
    """
    _, core, _, reply = setup()
    yes = button(reply.view, "yes", 2)

    async def both():
        """@description: Run both callbacks together.
        @param: none
        @return: None.
        """
        await asyncio.gather(yes.callback(Press()), yes.callback(Press()))

    asyncio.run(both())
    assert len(core.decisions) == 1


def test_a_stale_card_or_a_foreign_press_never_reaches_the_core():
    """@description: A changed revision says the card is stale; someone else's press is refused before any read.
    @param: none
    @return: None.
    """
    service, core, _, reply = setup()
    service.items[2].revision = 99
    assert "已失效" in push(button(reply.view, "yes", 2)).shown()
    calls_before = len(service.calls)
    foreign = push(button(reply.view, "no", 1), Press(user_id=OTHER_USER_ID, guild_id=GUILD_ID))
    assert not core.decisions and len(service.calls) == calls_before and not foreign.edits


def test_a_press_removes_the_buttons_first_and_never_pings_anyone():
    """@description: The acknowledgement takes the buttons away, and the update disables mentions.
    @param: none
    @return: None.
    """
    _, _, _, reply = setup()
    press = push(button(reply.view, "yes", 1))
    assert press.response.locks == [{"view": None}] and press.edits[-1]["allowed_mentions"].users is False


def test_old_buttons_still_route_to_the_two_answers():
    """@description: Ids issued before this change, with or without a page tail, match the pattern; approve acts as yes and reject as no.
    @param: none
    @return: None.
    """
    service = PagedService(case(1, revision=3), case(2, revision=4))
    core = FakeCore()
    cards = cards_with_core(service, core)
    template = cards.button_class.__discord_ui_compiled_template__
    assert all(template.fullmatch(text) for text in ("ocard:approve:1:3", "ocard:reject:1:3:p0", "ocard:yes:1:3:p0", "ocard:no:1:3", "ocard:dup:1:3:7:p0"))
    push(cards.button_class("approve", 1, 3))
    push(cards.button_class("reject", 2, 4))
    assert [(order, answer) for order, answer, _, _ in core.decisions] == [(1, Answer.YES), (2, Answer.NO)]


def test_no_screen_uses_the_old_jargon():
    """@description: The list, the detail card, the result, the picker, the stale reply and the failure reply show none of the old terms, in text or buttons.
    @param: none
    @return: None.
    """
    service, core, cards, reply = setup(identity=VersionIdentityStatus.CONFLICT, conflict="x")
    core.choices[1] = CaseChoices(doubt_text=DOUBT_TEXT, no_requires_duplicate_pick=True)
    core.choices[2] = CaseChoices(can_yes=False, can_no=False, frozen_conflict=True)
    core.choices[3] = CaseChoices(can_yes=False, needs_duration=True)
    reply = asyncio.run(cards.first_replies(OWNER_ID))[0]
    screens = [(reply.text, reply.view)]
    for name in ("詳情 #1", "詳情 #2", "詳情 #3"):
        shot = push(find(reply.view, name))
        screens.append((shot.shown(), shot.edits[-1]["view"]))
    core.failures.append(RuntimeError("x"))
    screens.append((push(button(reply.view, "yes", 1)).shown(), None))
    picker = push(button(reply.view, "no", 1))
    screens.append((picker.shown(), picker.edits[-1]["view"]))
    screens.append((push(cards.button_class("approve", 3, 13)).shown(), None))
    screens.append((push(cards.button_class("reject", 2, 12)).shown(), None))
    screens.append((push(cards.button_class("other", 1, 11)).shown(), None))
    for text, view in screens:
        assert not any(term in text for term in BANNED_TERMS), text
        assert view is None or not any(term in label for label in labels_of(view) for term in BANNED_TERMS)


def test_the_list_respects_discord_limits_in_every_state():
    """@description: Heavy cases of every kind still fit in four rows of at most five buttons, ids up to 100 characters and 2000 characters of text.
    @param: none
    @return: None.
    """
    long_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&" + "t=1&" * 500
    service, core, cards, _ = setup()
    for row in service.items.values():
        row.source_url = long_url
    core.choices[1] = CaseChoices(doubt_text="很長的疑慮" * 200, no_requires_duplicate_pick=True)
    core.choices[2] = CaseChoices(can_yes=False, can_no=False, frozen_conflict=True)
    core.choices[3] = CaseChoices(can_yes=False, needs_duration=True)
    reply = asyncio.run(cards.first_replies(OWNER_ID))[0]
    rows: dict[int, int] = {}
    for child in reply.view.children:
        rows[child.row] = rows.get(child.row, 0) + 1
        assert len(child.custom_id) <= 100 and len(child.item.label) <= 80
    assert len(rows) <= 4 and max(rows.values()) <= 5 and len(reply.text) <= 2000
    assert not re.search(r"(?<!<)https?://", reply.text)


def test_the_picker_pages_through_collected_songs_without_deciding():
    """@description: Seven collected songs show five at a time with a more button; the next page shows the rest and nothing is decided.
    @param: none
    @return: None.
    """
    service, core, cards, reply = setup()
    service.versions = [SimpleNamespace(version_id=f"rv-{number}", first_sequence=20 + number, first_video_id=f"V{number:010d}", created_at=NOW) for number in range(7)]
    core.choices[1] = CaseChoices(no_requires_duplicate_pick=True)
    picker = push(button(reply.view, "no", 1))
    view = picker.edits[-1]["view"]
    assert sum(child.custom_id.startswith("ocard:dup:1:") for child in view.children) == 5
    second = push(button(view, "dpage", 1))
    assert sum(child.custom_id.startswith("ocard:dup:1:") for child in second.edits[-1]["view"].children) == 2 and not core.decisions


def test_buttons_of_the_old_card_design_change_nothing_and_offer_a_reload():
    """@description: The retired version buttons still match, write nothing, reach no decision and leave a reload button.
    @param: none
    @return: None.
    """
    service, core, cards, _ = setup()
    for action in ("new", "same", "other", "vpage", "kind", "remaster", "opage"):
        shown = push(cards.button_class(action, 1, 11, extra=5))
        assert "已改版" in shown.shown() and "重新載入" in labels_of(shown.edits[-1]["view"])
    assert not core.decisions and not service.called("preview") and not service.called("confirm")
