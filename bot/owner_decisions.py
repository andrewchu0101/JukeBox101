"""Answer the Owner's single question "is this a new song?" for both the Discord card and the web console.

Inputs are a pending case row and the Owner review service; outputs are the choices offered and the decision outcome.
Every rule lives here so the two shells only render buttons and call decide, never judging a case themselves.
"""

import logging
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from bot.owner_card_versions import manual_check_source, new_version_identity, version_evidence
from bot.owner_review import ExpiredReviewToken, ReviewAction, ReviewOutcomeUnknown, UnknownReviewToken
from bot.review import ReviewStatus, StaleReviewVersion
from bot.song_judgement import ContentJudgement
from bot.version_concerns import ConcernCode
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus

_LOGGER = logging.getLogger(__name__)
QUESTION_TEXT = "這是一首新的歌嗎？"  # [Owner question]: The only question the Owner answers.
YES_LABEL = "是，發獎"  # [yes button]: Approves, confirms not a duplicate, and settles the reward.
NO_LABEL = "不是"  # [no button]: Rejects, or records the case as a duplicate of a chosen song.
YES_REASON = "Owner 回答這是新歌"  # [audit reason]: Stored with both steps of a yes.
NO_REASON = "Owner 判定不是新歌"  # [audit reason]: Stored with a rejection made by a no.
DUPLICATE_REASON = "Owner 回答不是新歌，與已收錄的歌是同一首"  # [audit reason]: Stored with a duplicate record made by a no.
CITATION_PREFIX = "owner-answer:"  # [audit citation]: Followed by the video id the Owner just answered about.
DURATION_STATES = {ReviewStatus.PENDING_VALIDATION, ReviewStatus.VALIDATION_FAILED}  # [missing duration]: Only a duration entry makes sense.
UNKNOWN_SONG_TEXT = "某首已收錄的歌"  # [no number known]: Used when the shell gave no sequence lookup.
CONCERN_TEXT = {
    ConcernCode.SIMILAR_TITLE: "可能和 {song} 重複（歌名相似）",
    ConcernCode.SAME_CHANNEL_CLOSE_DURATION: "可能和 {song} 重複（同頻道、長度只差幾秒）",
    ConcernCode.VERSION_KEYWORD: "歌名有現場／翻唱／混音等字樣",
    ConcernCode.TITLE_UNJUDGEABLE: "歌名無法判斷",
    ConcernCode.COMPARISON_INCOMPLETE: "和已收錄的歌比對沒有完成",
}  # [plain doubts]: One everyday sentence per concern code; no jargon and no official data.
NEAR_DUPLICATE_ADVICE = "高度相同，建議回答「不是」"  # [Owner advice, not a state]: Added only when one single known song matches on channel, duration and title together.
INSUFFICIENT_TEXT = "證據不足，沒有記錄是否重複，案件維持待處理，也沒有發放 Nickel。"  # [rule refusal]: The existing rules found the answer insufficient.
JARGON_REPLACEMENTS = (("錄音版本", "是否重複"), ("版本", "是否重複"))  # [plain wording]: Settlement notes are rewritten before the Owner reads them.
PARTIAL_TEXT = "已通過、尚未確認是否重複、尚未發放 Nickel：第二步沒有完成，請再按一次「是，發獎」補完。"  # [honest partial]: Approval is saved, nothing is paid.
UNKNOWN_TEXT = "結果未知（連線中斷，決定可能已儲存）。請再按一次同一個按鈕，系統會以原決定查回結果，不會重複執行。"
STALE_TEXT = "此案件已變更或已處理，沒有執行任何操作。請重新載入。"
REFUSED_TEXT = "這個案件現在不能這樣回答（需要先補時長、已被凍結，或已處理）。沒有執行任何操作。"
PICK_REQUIRED_TEXT = "這首已通過，回答「不是」時必須指定它和哪一首已收錄的歌是同一首。沒有執行任何操作。"
REJECTED_TEXT = "已記錄：不是新歌，已拒絕，不發 Nickel。"
DUPLICATE_TEXT = "已記錄：和你指定的那一首是同一首，不發 Nickel。"
SETTLEMENT_UNCONFIRMED_TEXT = "發獎結果尚未確認，系統會自動補算；目前請當作尚未發放。"
PAID_MARKERS = ("已發放", "已為投稿者發放")  # [paid sentences]: The settlement sentences that mean Nickel was really issued.
UNPAID_MARKER = "未發放"  # [unpaid sentence]: Overrides any paid marker found in the same note.


class Answer(Enum):
    """Name the two answers the Owner can give."""

    YES = "yes"
    NO = "no"


class DecisionKind(Enum):
    """Name every outcome a decision can end in."""

    PAID = "paid"
    REJECTED = "rejected"
    APPROVED_UNPAID = "approved_unpaid"
    DUPLICATE_RECORDED = "duplicate_recorded"
    STALE = "stale"
    UNKNOWN_OUTCOME = "unknown_outcome"
    REFUSED = "refused"


class _OutcomeUnknown(Exception):
    """Stop a decision whose commit result is unknown, keeping its token for the retry."""


@dataclass(frozen=True)
class CaseChoices:
    """Describe what the Owner may answer for one case right now."""

    can_yes: bool  # [yes offered]: Whether the yes button is available.
    can_no: bool  # [no offered]: Whether the no button is available.
    needs_duration: bool  # [duration first]: The shell must offer duration entry instead of an answer.
    frozen_conflict: bool  # [frozen]: A version conflict blocks every answer here.
    no_requires_duplicate_pick: bool  # [approved case]: Answering no must name which accepted song it duplicates.
    doubt_text: str  # [doubts]: One-line plain-language doubts; empty when none.
    question: str = QUESTION_TEXT
    yes_label: str = YES_LABEL
    no_label: str = NO_LABEL


@dataclass(frozen=True)
class DecisionResult:
    """Carry the outcome sentence and whether the case is still waiting."""

    kind: DecisionKind
    text: str  # [owner-facing text]: Never contains official title, channel or duration.
    paid: bool  # [reward state]: True only when Nickel was actually settled.
    still_pending: bool  # [queue state]: True when the case still needs the Owner.


def _doubt_text(concerns, sequence_by_video_id: Mapping[str, int] | None) -> str:
    """@description: Turn stored comparison doubts into one plain line so the Owner can judge without reading codes.
    @param concerns: Stored concerns of the case.
    @param sequence_by_video_id: Optional map from an existing video id to its accepted sequence.
    @return: Doubt line, or an empty string when there are none.
    """
    lookup = sequence_by_video_id or {}
    lines = []
    for concern in concerns:
        sequence = lookup.get(concern.other_video_id)
        song = UNKNOWN_SONG_TEXT if sequence is None else f"#{sequence}"
        line = CONCERN_TEXT[concern.code].format(song=song)
        if line not in lines:
            lines.append(line)
    if _has_near_duplicate(concerns):
        lines.append(NEAR_DUPLICATE_ADVICE)
    return "；".join(lines)


def _has_near_duplicate(concerns) -> bool:
    """@description: Tell whether one single known song is behind both the close-duration doubt and the similar-title doubt.
    @param concerns: Stored concerns of the case.
    @return: True only when both doubts point at the same existing video.
    """
    close = {c.other_video_id for c in concerns if c.code is ConcernCode.SAME_CHANNEL_CLOSE_DURATION}
    similar = {c.other_video_id for c in concerns if c.code is ConcernCode.SIMILAR_TITLE}
    return bool(close & similar - {None})


def case_choices(item, sequence_by_video_id: Mapping[str, int] | None = None) -> CaseChoices:
    """@description: Tell a shell which answers to offer for a case.
    @param item: PendingItem (attribute access only).
    @param sequence_by_video_id: Optional map from an existing video id to its accepted sequence, used to write "#12" in doubts.
    @return: Choices for the case.
    """
    status, identity = item.review_status, item.identity_status
    doubts = _doubt_text(item.concerns, sequence_by_video_id)
    if identity is VersionIdentityStatus.CONFLICT:
        return CaseChoices(False, False, False, True, False, doubts)
    expired = status is ReviewStatus.PENDING_REVIEW and item.api_basis == "expired"
    if status in DURATION_STATES or expired:
        return CaseChoices(False, False, True, False, False, doubts)
    if status is ReviewStatus.PENDING_REVIEW:
        return CaseChoices(True, True, False, False, False, doubts)
    if status is ReviewStatus.APPROVED and identity is VersionIdentityStatus.UNRESOLVED:
        return CaseChoices(True, True, False, False, True, doubts)
    return CaseChoices(False, False, False, False, False, doubts)


def decide(service, actor_id: int, item, answer: Answer, *, duplicate_of_sequence: int | None = None,
           tokens: MutableMapping | None = None) -> DecisionResult:
    """@description: Apply the Owner's answer; blocking, so shells run it in a worker thread.
    @param service: OwnerReviewService.
    @param actor_id: Owner id.
    @param item: PendingItem at the displayed revision.
    @param answer: Yes or no.
    @param duplicate_of_sequence: Accepted sequence of the known song a no on an approved case duplicates.
    @param tokens: Caller-kept dict reused so a retry after an unknown outcome replays the same token.
    @return: Outcome of the decision.
    """
    choices = case_choices(item)
    if not (choices.can_yes if answer is Answer.YES else choices.can_no):
        return _result(DecisionKind.REFUSED, REFUSED_TEXT, still_pending=True)
    kept = {} if tokens is None else tokens  # [token store]: Without a caller dict a retry cannot replay, which only matters for unknown outcomes.
    try:
        if answer is Answer.YES:
            return _answer_yes(service, actor_id, item, kept)
        return _answer_no(service, actor_id, item, choices, duplicate_of_sequence, kept)
    except StaleReviewVersion:
        return _result(DecisionKind.STALE, STALE_TEXT, still_pending=True)
    except _OutcomeUnknown:
        return _result(DecisionKind.UNKNOWN_OUTCOME, UNKNOWN_TEXT, still_pending=True)


def _result(kind: DecisionKind, text: str, *, paid: bool = False, still_pending: bool = False) -> DecisionResult:
    """@description: Build a result with the default of nothing paid and nothing waiting.
    @param kind: Outcome kind.
    @param text: Owner-facing sentence.
    @param paid: Whether Nickel was settled.
    @param still_pending: Whether the case still needs the Owner.
    @return: Result.
    """
    return DecisionResult(kind, text, paid, still_pending)


def _answer_yes(service, actor_id: int, item, tokens: MutableMapping) -> DecisionResult:
    """@description: Run the missing steps of a yes: approve when still waiting for review, then confirm the song is not a duplicate, then settle.
    @param service: Review service.
    @param actor_id: Owner id.
    @param item: Case at the displayed revision.
    @param tokens: Caller-kept token store.
    @return: Paid, partial or unknown result.
    """
    operation_id = item.operation_id  # [case identity]: Kept because the case may leave the queue once it is settled.
    if item.review_status is ReviewStatus.PENDING_REVIEW:
        _commit(service, actor_id, tokens, item, "approve", _approval_preview(service, actor_id, item))
        item = _reload(service, actor_id, item.accepted_sequence)
    if item is not None and item.identity_status is VersionIdentityStatus.UNRESOLVED:
        failure = _confirm_new_song(service, actor_id, item, tokens)
        if failure is not None:
            return failure
    return _settled_result(service, actor_id, operation_id)


def _confirm_new_song(service, actor_id: int, item, tokens: MutableMapping) -> DecisionResult | None:
    """@description: Confirm the case is not a duplicate; when only this second step fails after a fresh approval, say so honestly.
    @param service: Review service.
    @param actor_id: Owner id.
    @param item: Approved case at its current revision.
    @param tokens: Caller-kept token store.
    @return: A partial result when the step failed, otherwise None.
    """
    try:
        _commit(service, actor_id, tokens, item, "new", _version_preview(service, actor_id, item, new_version_identity(), None, YES_REASON))
    except (_OutcomeUnknown, StaleReviewVersion):
        raise
    except ValueError:  # [rule refusal]: The existing rules found the evidence insufficient; nothing was written, so a retry cannot help.
        return _result(DecisionKind.APPROVED_UNPAID, INSUFFICIENT_TEXT, still_pending=True)
    except Exception as error:  # [nothing written by this step]: Approval, if any, is already saved; the case needs the missing step.
        _LOGGER.error("Duplicate check after an Owner yes failed: %s", type(error).__name__)
        return _result(DecisionKind.APPROVED_UNPAID, PARTIAL_TEXT, still_pending=True)
    return None


def _answer_no(service, actor_id: int, item, choices: CaseChoices, duplicate_of_sequence: int | None, tokens: MutableMapping) -> DecisionResult:
    """@description: Reject a case still waiting for review, or record an approved case as a duplicate of the song the Owner picked.
    @param service: Review service.
    @param actor_id: Owner id.
    @param item: Case at the displayed revision.
    @param choices: Choices computed for the case.
    @param duplicate_of_sequence: Accepted sequence of the picked known song.
    @param tokens: Caller-kept token store.
    @return: Rejected, duplicate-recorded, refused or stale result.
    """
    if not choices.no_requires_duplicate_pick:
        _commit(service, actor_id, tokens, item, "reject", _reject_preview(service, actor_id, item))
        return _result(DecisionKind.REJECTED, REJECTED_TEXT)
    if duplicate_of_sequence is None:
        return _result(DecisionKind.REFUSED, PICK_REQUIRED_TEXT, still_pending=True)
    known = _known_song(service, actor_id, duplicate_of_sequence)
    if known is None:
        return _result(DecisionKind.STALE, STALE_TEXT, still_pending=True)
    identity = RecordingVersionIdentity(known.version_id)
    try:
        _commit(service, actor_id, tokens, item, f"same:{duplicate_of_sequence}", _version_preview(service, actor_id, item, identity, known.first_video_id, DUPLICATE_REASON))
    except ValueError:  # [rule refusal]: The existing rules found the evidence insufficient, so nothing was written.
        return _result(DecisionKind.REFUSED, INSUFFICIENT_TEXT, still_pending=True)
    return _result(DecisionKind.DUPLICATE_RECORDED, DUPLICATE_TEXT)


def _settled_result(service, actor_id: int, operation_id: str) -> DecisionResult:
    """@description: After both steps are committed, run settlement and say truthfully whether Nickel was issued.
    @param service: Review service.
    @param actor_id: Owner id.
    @param operation_id: Decided case.
    @return: Paid result, or approved-and-confirmed-but-unpaid with the settlement's reason.
    """
    try:
        note = service.settlement_note(actor_id, operation_id)
    except Exception as error:  # [committed decisions]: Only the follow-up failed; the periodic sweep retries it.
        _LOGGER.error("Settlement after an Owner yes failed: %s", type(error).__name__)
        note = SETTLEMENT_UNCONFIRMED_TEXT
    paid = any(marker in note for marker in PAID_MARKERS) and UNPAID_MARKER not in note
    for term, plain in JARGON_REPLACEMENTS:
        note = note.replace(term, plain)
    if paid:
        return _result(DecisionKind.PAID, f"已通過，並確認是新歌。{note}", paid=True)
    return _result(DecisionKind.APPROVED_UNPAID, f"已通過，並確認是新歌，但尚未發放 Nickel。{note}")


def _commit(service, actor_id: int, tokens: MutableMapping, item, name: str, preview_call: Callable[[], object]):
    """@description: Preview once and confirm, keeping the token when the outcome is unknown so a retry replays the same decision.
    @param service: Review service.
    @param actor_id: Owner id.
    @param tokens: Caller-kept token store.
    @param item: Case at the displayed revision.
    @param name: Step name, part of the token key.
    @param preview_call: Builds the service preview for this step.
    @return: Committed snapshot.
    """
    key = (item.accepted_sequence, item.revision, name)
    try:
        return _confirm_token(service, actor_id, tokens, key, preview_call)
    except (ExpiredReviewToken, UnknownReviewToken):
        tokens.pop(key, None)  # [dead token]: A fresh preview at the same revision is refused as stale if the first press had committed.
        return _confirm_token(service, actor_id, tokens, key, preview_call)


def _confirm_token(service, actor_id: int, tokens: MutableMapping, key: tuple, preview_call: Callable[[], object]):
    """@description: Confirm the token stored under a key, creating it from a preview first when none exists.
    @param service: Review service.
    @param actor_id: Owner id.
    @param tokens: Caller-kept token store.
    @param key: Sequence, revision and step name.
    @param preview_call: Builds the service preview for this step.
    @return: Committed snapshot.
    """
    if key not in tokens:
        tokens[key] = preview_call().token_id
    try:
        snapshot = service.confirm(actor_id, tokens[key])
    except ReviewOutcomeUnknown as error:
        raise _OutcomeUnknown() from error
    tokens.pop(key, None)
    return snapshot


def _now(service) -> datetime:
    """@description: Read the time from the service's own clock so evidence is never dated differently from its token.
    @param service: Review service.
    @return: Current time.
    """
    clock = getattr(service, "_clock", None)
    return clock() if clock is not None else datetime.now(timezone.utc)


def _approval_preview(service, actor_id: int, item) -> Callable[[], object]:
    """@description: Build the step that previews a song approval at the displayed revision, citing the answered video.
    @param service: Review service.
    @param actor_id: Owner id.
    @param item: Case at the displayed revision.
    @return: Function returning the approval preview.
    """
    def preview():
        """@description: Preview the approval with the Owner's song judgement.
        @param: none
        @return: Approval preview.
        """
        video = service.view(actor_id, item.operation_id).review.video
        citation = f"{CITATION_PREFIX}{video.video_id}"
        content = ContentJudgement(video=video, judged_by=actor_id, reason=YES_REASON, retrieved_at=_now(service), evidence_reference=citation, is_song=True)
        return service.preview(actor_id, item.operation_id, ReviewAction.APPROVE, YES_REASON, item.revision, citation, content)

    return preview


def _reject_preview(service, actor_id: int, item) -> Callable[[], object]:
    """@description: Build the step that previews a rejection with the automatic reason, citing the answered video.
    @param service: Review service.
    @param actor_id: Owner id.
    @param item: Case at the displayed revision.
    @return: Function returning the rejection preview.
    """
    def preview():
        """@description: Preview the rejection.
        @param: none
        @return: Rejection preview.
        """
        video = service.view(actor_id, item.operation_id).review.video
        return service.preview(actor_id, item.operation_id, ReviewAction.REJECT, NO_REASON, item.revision, f"{CITATION_PREFIX}{video.video_id}")

    return preview


def _version_preview(service, actor_id: int, item, identity: RecordingVersionIdentity, other_video_id: str | None, reason: str) -> Callable[[], object]:
    """@description: Build the step that previews the Owner's duplicate-check answer with Owner-review evidence.
    @param service: Review service.
    @param actor_id: Owner id.
    @param item: Approved case at its current revision.
    @param identity: New identity for "new song", or the picked song's identity for "same song".
    @param other_video_id: First video of the picked song, or None for a new song.
    @param reason: Audit explanation.
    @return: Function returning the version-confirmation preview.
    """
    def preview():
        """@description: Build the evidence on the worker thread and preview the confirmation.
        @param: none
        @return: Version-confirmation preview.
        """
        video = service.view(actor_id, item.operation_id).review.video
        source = manual_check_source(video.video_id, other_video_id)
        evidence = version_evidence(video, identity, source, _now(service), reason, actor_id)
        return service.preview_version_confirmation(actor_id, item.operation_id, evidence, reason, item.revision)

    return preview


def _reload(service, actor_id: int, sequence: int):
    """@description: Re-read a case after its approval, because the second step must use the new revision.
    @param service: Review service.
    @param actor_id: Owner id.
    @param sequence: Accepted sequence of the case.
    @return: Current pending row, or None when the case left the queue.
    """
    page = service.list_pending(actor_id, after_sequence=None if sequence <= 1 else sequence - 1, limit=1)
    row = page.items[0] if page.items else None
    return row if row is not None and row.accepted_sequence == sequence else None


def _known_song(service, actor_id: int, first_sequence: int):
    """@description: Find the already accepted song a button refers to, or nothing when it no longer exists.
    @param service: Review service.
    @param actor_id: Owner id.
    @param first_sequence: First acceptance order of the picked song.
    @return: Known version row, or None.
    """
    page = service.list_known_versions(actor_id, after_sequence=None if first_sequence <= 1 else first_sequence - 1, limit=1)
    return page.items[0] if page.items and page.items[0].first_sequence == first_sequence else None
