"""Verify the Owner-facing reexamination wording matches the current rule and never claims an ordering event before the pass.

Text checks only; no Discord, MySQL or Gateway is involved.
"""

from bot.owner_review import PREVIEW_ORDERING_SEQUENCE, REEXAMINATION_EFFECT, REEXAMINATION_PASS_NOTE


def test_reexamination_preview_states_the_pass_rule_and_keeps_the_no_award_effect():
    """@description: The reexamination preview describes what the later pass will do and keeps the no-payout statement.
    @param: none
    @return: None.
    """
    assert "重審會保留原決策與歷史" in REEXAMINATION_EFFECT
    assert "重審通過核准時，建立新的排序事件並排到當時隊尾" in REEXAMINATION_EFFECT
    assert "不恢復原優先權，也不保證發點" in REEXAMINATION_EFFECT
    assert "不發 Nickel" in REEXAMINATION_EFFECT


def test_reexamination_preview_does_not_claim_an_ordering_event_already_exists():
    """@description: Before the pass the preview must not say the new event has been created; that wording belongs to the approval preview.
    @param: none
    @return: None.
    """
    assert "已建立" not in REEXAMINATION_EFFECT
    assert "不得直接核准" not in REEXAMINATION_EFFECT
    assert "確認時" not in REEXAMINATION_EFFECT
    assert "確認時產生新排序事件" in REEXAMINATION_PASS_NOTE


def test_preview_placeholder_is_documented_as_not_a_real_sequence():
    """@description: The placeholder used only to project status is a preview value, never a stored ordering sequence.
    @param: none
    @return: None.
    """
    assert PREVIEW_ORDERING_SEQUENCE == 1
