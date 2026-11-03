"""Provide a fake decision core with the interface of bot.owner_decisions, so card tests can check what the cards pass to it and show from it.

It has no tests of its own; the card test files import it. It records every decision and can script choices, failures and queue changes.
"""

from dataclasses import dataclass
from enum import Enum

from bot.discord_owner_cards import OwnerCards
from bot.review import ReviewStatus
from bot.version_identity import VersionIdentityStatus

QUESTION = "這是一首新的歌嗎？"
YES_LABEL = "是，發獎"
NO_LABEL = "不是"
FROZEN_LINE = "需要先解除衝突（用命令列工具）"
PICK_QUESTION = "這首和哪一首已收錄的歌是同一首？"
DOUBT_TEXT = "可能和 #12 重複（歌名相似）"
BANNED_TERMS = ("版本", "錄音版本", "確認通過")  # [old jargon]: Words the Owner interface must no longer show anywhere.


class Answer(Enum):
    """Mirror the two answers of the shared decision core."""

    YES = "yes"
    NO = "no"


@dataclass(frozen=True)
class CaseChoices:
    """Mirror what the shared core says a case allows."""

    can_yes: bool = True
    can_no: bool = True
    needs_duration: bool = False
    frozen_conflict: bool = False
    no_requires_duplicate_pick: bool = False
    doubt_text: str = ""
    question: str = QUESTION
    yes_label: str = YES_LABEL
    no_label: str = NO_LABEL


@dataclass(frozen=True)
class DecisionResult:
    """Mirror the core's outcome of one answer."""

    kind: str
    text: str
    paid: bool = False
    still_pending: bool = False


class FakeCore:
    """Stand in for bot.owner_decisions: scripted choices per case, recorded decisions, optional scripted failures."""

    Answer = Answer

    def __init__(self) -> None:
        """@description: Start with default choices, no failures and no recorded decisions.
        @param self: Fake core.
        @return: None.
        """
        self.choices: dict[int, CaseChoices] = {}  # [scripted choices]: Per acceptance order; missing means every default is allowed.
        self.decisions: list[tuple] = []  # [recorded answers]: (order, answer, duplicate_of_sequence, tokens) per decide call.
        self.failures: list[Exception] = []  # [scripted failures]: Raised by successive decide calls, oldest first.
        self.maps: list = []  # [passed maps]: The sequence_by_video_id argument of each case_choices call.
        self.result_text: str | None = None  # [scripted sentence]: Replaces the default result sentence when set.
        self.still_pending = False  # [scripted outcome]: Whether decided cases stay in the queue.

    def case_choices(self, item, sequence_by_video_id=None) -> CaseChoices:
        """@description: Return the scripted choices of a case, recording the video-to-order map the card passed.
        @param self: Fake core.
        @param item: Pending row.
        @param sequence_by_video_id: Map from a known song's video id to its acceptance order, or None.
        @return: Choices.
        """
        self.maps.append(sequence_by_video_id)
        return self.choices.get(item.accepted_sequence, CaseChoices())

    def decide(self, service, actor_id, item, answer, duplicate_of_sequence=None, tokens=None) -> DecisionResult:
        """@description: Record the answer, change the fake queue like a real decision, or raise a scripted failure.
        @param self: Fake core.
        @param service: Review service the card passed.
        @param actor_id: Deciding Owner.
        @param item: Pending row at the displayed revision.
        @param answer: Answer.YES or Answer.NO.
        @param duplicate_of_sequence: Chosen already-collected song, or None.
        @param tokens: Open token dictionary of the card.
        @return: Result with plain words.
        """
        self.decisions.append((item.accepted_sequence, answer, duplicate_of_sequence, tokens))
        if self.failures:
            raise self.failures.pop(0)
        row = service.items[item.accepted_sequence]
        if not self.still_pending:
            row.review_status = ReviewStatus.APPROVED if answer is Answer.YES else ReviewStatus.REJECTED
            row.identity_status = VersionIdentityStatus.CONFIRMED
        row.revision += 1
        return DecisionResult("done", self.result_text or f"#{item.accepted_sequence} 已處理（{answer.value}）", paid=answer is Answer.YES, still_pending=self.still_pending)


def cards_with_core(service, core: FakeCore) -> OwnerCards:
    """@description: Build the card handler for the private Guild over the fake service and the fake core.
    @param service: Fake review service.
    @param core: Fake decision core.
    @return: Card handler.
    """
    from tests.test_discord_owner_cards import GUILD_ID, NOW, OWNER_ID  # [late import]: The card test module imports this one, so the shared constants are read at call time.

    return OwnerCards(lambda: service, OWNER_ID, GUILD_ID, lambda: NOW, decisions=core)
