"""Verify the shared yes/no decision core: the choices offered per case state and what each answer writes.

Inputs are the fake review service of the card tests; no Discord connection and no database are used.
"""

from types import SimpleNamespace

import pytest

from bot.owner_decisions import Answer, DecisionKind, case_choices, decide
from bot.owner_review import ReviewAction, ReviewOutcomeUnknown
from bot.review import ReviewStatus
from bot.version_concerns import ConcernCode, VersionConcern
from bot.version_identity import VersionIdentityStatus
from tests.test_discord_owner_cards import OWNER_ID, case
from tests.test_discord_owner_cards_approve_and_pay import PAID_TEXT, PayService, calls_named

JARGON = ("版本", "錄音")  # [banned terms]: Owner-facing wording says "是否重複" and "新歌" instead.
OFFICIAL_CANARY = ("TITLE-CANARY", "CHANNEL-CANARY", "189")  # [official data]: Must never appear in any Owner-facing text.
YES_FULL_CALLS = ["preview", "confirm", "preview_version_confirmation", "confirm"]  # [one-press order]: Approve first, then the duplicate check.
APPROVED = dict(review=ReviewStatus.APPROVED)  # [approved case]: Song accepted, duplicate check still open.


def known(sequence: int = 7, version_id: str = "rv-known-7", video_id: str = "knownvideo7"):
    """@description: Build a known-version row shaped like the service's KnownVersion.
    @param sequence: First acceptance order of the version.
    @param version_id: Internal canonical identity.
    @param video_id: First video of the version.
    @return: Row.
    """
    return SimpleNamespace(version_id=version_id, first_sequence=sequence, first_video_id=video_id, created_at=None)


def current(service: PayService, sequence: int = 1):
    """@description: Re-read a case the way a shell reloads its card after a press.
    @param service: Fake service.
    @param sequence: Acceptance order.
    @return: Pending row, or None when the case left the queue.
    """
    page = service.list_pending(OWNER_ID, after_sequence=None if sequence <= 1 else sequence - 1, limit=1)
    return page.items[0] if page.items else None


# ---- choices ----

def test_a_reviewable_case_offers_both_answers():
    """@description: A song waiting for review can be answered yes or no, and a no needs no duplicate pick.
    @param: none
    @return: None.
    """
    choices = case_choices(case())
    assert choices.can_yes and choices.can_no and not choices.no_requires_duplicate_pick
    assert not choices.needs_duration and not choices.frozen_conflict and choices.doubt_text == ""
    assert (choices.question, choices.yes_label, choices.no_label) == ("這是一首新的歌嗎？", "是，發獎", "不是")


def test_a_case_with_doubts_still_offers_yes_and_shows_the_doubts():
    """@description: Doubts are information, not a block: yes stays available and the doubt text is present.
    @param: none
    @return: None.
    """
    choices = case_choices(case(concerns=(VersionConcern(ConcernCode.SIMILAR_TITLE, "abcdefghijk"),)))
    assert choices.can_yes and choices.doubt_text


def test_an_approved_case_waiting_for_the_duplicate_check_requires_a_pick_for_no():
    """@description: Rejection is not allowed once approved, so no must name the song it duplicates.
    @param: none
    @return: None.
    """
    choices = case_choices(case(**APPROVED))
    assert choices.can_yes and choices.can_no and choices.no_requires_duplicate_pick


@pytest.mark.parametrize("review", [ReviewStatus.PENDING_VALIDATION, ReviewStatus.VALIDATION_FAILED])
def test_a_case_without_an_official_duration_only_asks_for_the_duration(review):
    """@description: Neither answer is offered; the shell must offer duration entry.
    @param review: Status without a duration.
    @return: None.
    """
    choices = case_choices(case(review=review, duration=None))
    assert choices.needs_duration and not choices.can_yes and not choices.can_no


def test_a_frozen_conflict_offers_nothing():
    """@description: A conflict is resolved with the command-line tool, never with this answer.
    @param: none
    @return: None.
    """
    choices = case_choices(case(identity=VersionIdentityStatus.CONFLICT, conflict="x"))
    assert choices.frozen_conflict and not choices.can_yes and not choices.can_no and not choices.needs_duration


@pytest.mark.parametrize("code, fragment", [
    (ConcernCode.SIMILAR_TITLE, "歌名相似"), (ConcernCode.SAME_CHANNEL_CLOSE_DURATION, "同頻道"),
    (ConcernCode.VERSION_KEYWORD, "歌名有"), (ConcernCode.TITLE_UNJUDGEABLE, "無法判斷")])
def test_each_doubt_becomes_a_plain_line_without_jargon_or_official_data(code, fragment):
    """@description: The doubt text names the reason in everyday words and carries no official data or jargon.
    @param code: Stored concern code.
    @param fragment: Wording expected.
    @return: None.
    """
    text = case_choices(case(concerns=(VersionConcern(code, "knownvideo7"),)), {"knownvideo7": 12}).doubt_text
    assert fragment in text and not any(word in text for word in JARGON + OFFICIAL_CANARY)


def test_a_doubt_names_the_known_song_by_its_number_when_the_shell_knows_it():
    """@description: With a lookup the doubt says "#12"; without one it still says something readable.
    @param: none
    @return: None.
    """
    doubt = (VersionConcern(ConcernCode.SIMILAR_TITLE, "knownvideo7"),)
    assert "#12" in case_choices(case(concerns=doubt), {"knownvideo7": 12}).doubt_text
    assert "#" not in case_choices(case(concerns=doubt)).doubt_text


# ---- yes ----

def test_yes_on_a_reviewable_case_approves_confirms_not_duplicate_and_pays_in_one_press():
    """@description: One answer makes the four writes in order, the evidence is the Owner path, and the result says paid.
    @param: none
    @return: None.
    """
    service = PayService(case())
    result = decide(service, OWNER_ID, case(), Answer.YES)
    assert calls_named(service) == YES_FULL_CALLS
    version_call = service.called("preview_version_confirmation")[0]
    assert "Owner 回答這是新歌" in version_call[3] and version_call[4] == 4
    assert service.evidence[0].source_type.value == "owner_review"
    assert result.kind is DecisionKind.PAID and result.paid and not result.still_pending and PAID_TEXT in result.text
    assert not any(word in result.text for word in JARGON)


def test_yes_on_a_case_with_doubts_completes_the_same_way():
    """@description: The Owner has seen the doubts, so the answer is honoured.
    @param: none
    @return: None.
    """
    doubtful = case(concerns=(VersionConcern(ConcernCode.SIMILAR_TITLE, "knownvideo7"),))
    service = PayService(doubtful)
    assert decide(service, OWNER_ID, doubtful, Answer.YES).kind is DecisionKind.PAID
    assert calls_named(service) == YES_FULL_CALLS


def test_yes_on_an_approved_case_only_confirms_the_duplicate_check():
    """@description: The approval is not repeated; only the missing second step runs.
    @param: none
    @return: None.
    """
    approved = case(**APPROVED)
    service = PayService(approved)
    result = decide(service, OWNER_ID, approved, Answer.YES)
    assert calls_named(service) == ["preview_version_confirmation", "confirm"] and result.kind is DecisionKind.PAID


def test_when_the_second_step_fails_the_result_says_so_and_a_retry_runs_only_that_step():
    """@description: Approval stays, nothing is claimed paid, and pressing again completes only the missing step.
    @param: none
    @return: None.
    """
    service = PayService(case())
    service.version_preview_error = RuntimeError("boom")
    first = decide(service, OWNER_ID, case(), Answer.YES)
    assert first.kind is DecisionKind.APPROVED_UNPAID and not first.paid and first.still_pending
    assert "已通過、尚未確認是否重複、尚未發放 Nickel" in first.text and PAID_TEXT not in first.text
    service.version_preview_error = None
    service.calls.clear()
    retry = decide(service, OWNER_ID, current(service), Answer.YES)
    assert calls_named(service) == ["preview_version_confirmation", "confirm"] and retry.kind is DecisionKind.PAID


def test_an_unknown_outcome_is_replayed_with_the_same_token_on_the_next_press():
    """@description: The retry confirms the original token and does not preview a second decision.
    @param: none
    @return: None.
    """
    service, tokens = PayService(case()), {}
    service.confirm_failures = [ReviewOutcomeUnknown("lost")]
    first = decide(service, OWNER_ID, case(), Answer.YES, tokens=tokens)
    assert first.kind is DecisionKind.UNKNOWN_OUTCOME and not first.paid and first.still_pending and tokens
    original = service.called("confirm")[0][2]
    second = decide(service, OWNER_ID, case(), Answer.YES, tokens=tokens)
    assert len(service.called("preview")) == 1 and service.called("confirm")[1][2] == original
    assert second.kind is DecisionKind.PAID


def test_a_revision_that_moved_on_is_reported_as_stale_without_writing():
    """@description: A refused preview at the displayed revision means the card is out of date.
    @param: none
    @return: None.
    """
    service = PayService(case(revision=5))
    result = decide(service, OWNER_ID, case(revision=3), Answer.YES)
    assert result.kind is DecisionKind.STALE and not result.paid and not service.called("confirm")


# ---- no ----

def test_no_on_a_reviewable_case_rejects_with_an_automatic_reason():
    """@description: The Owner types nothing; the audit reason is fixed, and no reward is claimed.
    @param: none
    @return: None.
    """
    service = PayService(case())
    result = decide(service, OWNER_ID, case(), Answer.NO)
    preview = service.called("preview")[0]
    assert preview[3] is ReviewAction.REJECT and preview[4] == "Owner 判定不是新歌"
    assert calls_named(service) == ["preview", "confirm"]
    assert result.kind is DecisionKind.REJECTED and not result.paid and not result.still_pending
    assert not any(word in result.text for word in JARGON)


def test_no_on_an_approved_case_without_a_pick_is_refused_and_writes_nothing():
    """@description: Rejecting an approved case is not allowed, so a duplicate pick is required.
    @param: none
    @return: None.
    """
    service = PayService(case(**APPROVED))
    result = decide(service, OWNER_ID, case(**APPROVED), Answer.NO)
    assert result.kind is DecisionKind.REFUSED and calls_named(service) == []


def test_no_on_an_approved_case_records_the_chosen_song_as_the_same_recording_and_pays_nothing():
    """@description: The version confirmation maps to the picked known song; no rejection and no reward.
    @param: none
    @return: None.
    """
    service = PayService(case(**APPROVED))
    service.versions = [known(7, "rv-known-7")]
    result = decide(service, OWNER_ID, case(**APPROVED), Answer.NO, duplicate_of_sequence=7)
    assert calls_named(service) == ["preview_version_confirmation", "confirm"]
    assert service.evidence[0].version.value == "rv-known-7"
    assert result.kind is DecisionKind.DUPLICATE_RECORDED and not result.paid and not result.still_pending
    assert PAID_TEXT not in result.text and not any(word in result.text for word in JARGON)


def test_a_duplicate_pick_that_no_longer_exists_is_stale():
    """@description: An unknown known-song number writes nothing.
    @param: none
    @return: None.
    """
    service = PayService(case(**APPROVED))
    result = decide(service, OWNER_ID, case(**APPROVED), Answer.NO, duplicate_of_sequence=99)
    assert result.kind is DecisionKind.STALE and calls_named(service) == []


# ---- not decidable ----

@pytest.mark.parametrize("answer", [Answer.YES, Answer.NO])
@pytest.mark.parametrize("row", [case(review=ReviewStatus.PENDING_VALIDATION, duration=None),
                                 case(identity=VersionIdentityStatus.CONFLICT, conflict="x")])
def test_a_case_that_needs_a_duration_or_is_frozen_cannot_be_decided(row, answer):
    """@description: Neither answer writes anything for these states.
    @param row: Undecidable case.
    @param answer: Either answer.
    @return: None.
    """
    service = PayService(row)
    result = decide(service, OWNER_ID, row, answer)
    assert result.kind is DecisionKind.REFUSED and not result.paid and calls_named(service) == []


class UnpaidService(PayService):
    """Behave like PayService but always report that settlement paid nothing."""

    def settlement_note(self, actor_id: int, operation_id: str) -> str:
        """@description: Say that nothing was paid even though both steps committed.
        @param self: Fake service.
        @param actor_id: Caller.
        @param operation_id: Case.
        @return: Unpaid sentence containing the banned term.
        """
        return "正在等待較早的投稿處理完成；目前未發放 Nickel。版本證據待處理。"


def test_both_steps_done_but_settlement_unpaid_is_never_reported_as_paid():
    """@description: The unpaid sentence wins: not paid, not pending, and the Owner-facing text has no jargon.
    @param: none
    @return: None.
    """
    service = UnpaidService(case())
    result = decide(service, OWNER_ID, case(), Answer.YES)
    assert result.kind is DecisionKind.APPROVED_UNPAID and not result.paid and not result.still_pending
    assert "未發放" in result.text and not any(word in result.text for word in JARGON)


# ---- rules carried over from the retired version-flow tests ----

NO_VERSION_ERROR = ValueError("evidence establishes no recording version")  # [existing rule refusal]: What the service raises for insufficient evidence.


def test_insufficient_evidence_on_an_approved_case_pays_nothing_and_leaves_no_token():
    """@description: The refused preview issues no token and no confirm; the case stays pending and the text says nothing was recorded.
    @param: none
    @return: None.
    """
    service = PayService(case(**APPROVED))
    service.version_preview_error = NO_VERSION_ERROR
    result = decide(service, OWNER_ID, case(**APPROVED), Answer.YES)
    assert result.kind is DecisionKind.APPROVED_UNPAID and result.still_pending and not result.paid
    assert not service.tokens and not service.called("confirm") and "證據不足" in result.text


def test_insufficient_evidence_on_a_fresh_yes_keeps_the_approval_and_pays_nothing():
    """@description: The approval stays saved, the duplicate check is not recorded, and no reward is claimed.
    @param: none
    @return: None.
    """
    service = PayService(case())
    service.version_preview_error = NO_VERSION_ERROR
    result = decide(service, OWNER_ID, case(), Answer.YES)
    assert calls_named(service) == ["preview", "confirm", "preview_version_confirmation"]
    assert not result.paid and result.still_pending and len(service.tokens) == 1


def test_insufficient_evidence_on_a_duplicate_pick_records_nothing():
    """@description: A refused duplicate record leaves the case pending with no token.
    @param: none
    @return: None.
    """
    service = PayService(case(**APPROVED))
    service.versions = [known(7)]
    service.version_preview_error = NO_VERSION_ERROR
    result = decide(service, OWNER_ID, case(**APPROVED), Answer.NO, duplicate_of_sequence=7)
    assert result.kind is DecisionKind.REFUSED and result.still_pending and not service.tokens


def test_the_duplicate_check_evidence_uses_a_traceable_source_and_the_owner_path():
    """@description: The fixed source names the answered link, so the existing source rule accepts it.
    @param: none
    @return: None.
    """
    service = PayService(case(**APPROVED))
    decide(service, OWNER_ID, case(**APPROVED), Answer.YES)
    assert service.evidence[0].source_reference.startswith("人工核對：http")


def test_an_unknown_outcome_on_the_duplicate_record_is_replayed_with_the_same_token():
    """@description: A retry of a duplicate pick confirms the original token and previews nothing new.
    @param: none
    @return: None.
    """
    service, tokens = PayService(case(**APPROVED)), {}
    service.versions = [known(7)]
    service.confirm_failures = [ReviewOutcomeUnknown("lost")]
    first = decide(service, OWNER_ID, case(**APPROVED), Answer.NO, duplicate_of_sequence=7, tokens=tokens)
    assert first.kind is DecisionKind.UNKNOWN_OUTCOME
    second = decide(service, OWNER_ID, case(**APPROVED), Answer.NO, duplicate_of_sequence=7, tokens=tokens)
    assert len(service.called("preview_version_confirmation")) == 1 and second.kind is DecisionKind.DUPLICATE_RECORDED
    assert service.called("confirm")[0][2] == service.called("confirm")[1][2]


# ---- doubt wording carried over from the retired concern-wording tests ----

CLOSE = ConcernCode.SAME_CHANNEL_CLOSE_DURATION  # [duration doubt]: Same channel and a duration within a few seconds.
SIMILAR = ConcernCode.SIMILAR_TITLE  # [title doubt]: Highly similar title.
ADVICE = "高度相同，建議回答「不是」"  # [advice]: Added only when one song matches on both doubts.


def doubts(*pairs, lookup=None) -> str:
    """@description: Build the doubt text of a case with the given (code, existing video) pairs.
    @param pairs: Concern code and existing video id pairs.
    @param lookup: Optional video-to-sequence map.
    @return: Doubt text.
    """
    return case_choices(case(concerns=tuple(VersionConcern(code, video) for code, video in pairs)), lookup).doubt_text


def test_both_doubts_about_one_song_add_the_advice_and_name_the_song():
    """@description: Close duration and similar title against the same song: both lines, the song number and the advice.
    @param: none
    @return: None.
    """
    text = doubts((CLOSE, "knownvideo7"), (SIMILAR, "knownvideo7"), lookup={"knownvideo7": 12})
    assert ADVICE in text and "同頻道" in text and "歌名相似" in text and text.count("#12") == 2


def test_a_single_doubt_never_adds_the_advice():
    """@description: Title alone, duration alone and the keyword alone give no advice.
    @param: none
    @return: None.
    """
    assert all(ADVICE not in doubts(pair) for pair in ((SIMILAR, "a"), (CLOSE, "a"), (ConcernCode.VERSION_KEYWORD, None)))


def test_doubts_spread_over_two_different_songs_do_not_add_the_advice():
    """@description: A similar title to one song and a close duration to another is not highly identical to either.
    @param: none
    @return: None.
    """
    text = doubts((SIMILAR, "aaaa"), (CLOSE, "bbbb"), lookup={"aaaa": 3, "bbbb": 4})
    assert ADVICE not in text and "#3" in text and "#4" in text


def test_the_keyword_doubt_names_no_song_and_the_text_leaks_no_other_data():
    """@description: The keyword line has no song number and no video id appears in any doubt text.
    @param: none
    @return: None.
    """
    assert "#" not in doubts((ConcernCode.VERSION_KEYWORD, None))
    assert "knownvideo7" not in doubts((SIMILAR, "knownvideo7"), (CLOSE, "knownvideo7"), lookup={"knownvideo7": 9})
