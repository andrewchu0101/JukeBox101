"""Verify the Owner show audit, message splitting, list paging, cancel/confirm state machine and citation rules with fake interactions.

Inputs are fake Discord interactions and a fake OwnerReviewService that records calls, decision keys and worker threads; no Discord connection is made.
"""

import asyncio
import re
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot import discord_owner_review as owner_ui
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.owner_review import ReviewAction, ReviewOutcomeUnknown, ReviewPreview
from bot.review import ApprovalSource, ReviewSnapshot, ReviewStatus
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.test_discord_owner_review import (
    FakeInteraction, GUILD_ID, NOW, OTHER_GUILD_ID, OTHER_USER_ID, OWNER_ID, PASSING_DURATION, VIDEO,
    _assert_private_reply, _command, _confirm_view, _group, _refusal_texts, _run,
)

EXPIRES = NOW + timedelta(minutes=5)
PAGE_SIZE = 20
FIRST_SEQUENCE = 100  # [accepted sequence base]: Sequences are not page offsets, so a cursor of 119 proves the button encodes a position.
TRACEABLE_SOURCE = "owner-note:brief-A"
AUDIT_EVIDENCE = record_evidence("ev-audit-1", VIDEO, EvidenceSourceType.OWNER_REVIEW, "owner-note:brief-A", NOW,
                                 "版本證據理由", OWNER_ID, RecordingVersionIdentity("rec-ref-1"),
                                 version_kind=VersionClaimKind.REMIX)
AUDIT_JUDGEMENT = ContentJudgement(VIDEO, OWNER_ID, "判定理由甲", NOW, "ev-audit-1", True)


def _projection(**overrides):
    """@description: Build one full projection with the contract fields, overriding only what a test cares about.
    @param overrides: Field values replacing the defaults.
    @return: Projection-shaped object.
    """
    fields = dict(
        operation_id="op-1", accepted_sequence=1, identity=VersionAssignment(VIDEO),
        review=ReviewSnapshot(VIDEO, version=3, status=ReviewStatus.PENDING_REVIEW, duration=PASSING_DURATION),
        evidence=(), history=(), content_judgements=(), source_url="https://youtu.be/dQw4w9WgXcQ",
        history_entries=(), conflict_reason=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _pending_row(number: int) -> SimpleNamespace:
    """@description: Build one pending row shaped like the contract's PendingItem.
    @param number: Row ordinal used to make the operation id unique.
    @return: Pending row object.
    """
    return SimpleNamespace(operation_id=f"op-{number:02d}", source_url="https://youtu.be/dQw4w9WgXcQ",
                           accepted_sequence=FIRST_SEQUENCE + number, review_status=ReviewStatus.PENDING_REVIEW,
                           identity_status=VersionIdentityStatus.UNRESOLVED, revision=3, evidence_count=0,
                           conflict_reason=None)


class PagedService:
    """Fake OwnerReviewService that pages pending rows, records decision keys and can lose a confirmation reply."""

    def __init__(self, pending_count: int = 25) -> None:
        """@description: Start with one projection, a row list and no injected failures.
        @param pending_count: Number of pending rows to serve.
        @return: None.
        """
        self.calls: list[tuple[str, tuple, dict]] = []  # [service trace]: Method, positional and keyword arguments.
        self.items = tuple(_pending_row(number) for number in range(pending_count))  # [queue]: Served in acceptance order.
        self.projections = {"op-1": _projection()}  # [operations]: Projection per operation id.
        self.evidence_store: dict[str, object] = {"ev-audit-1": AUDIT_EVIDENCE}  # [stored evidence]: Records citable by id.
        self.decision_log: list[tuple[str, str, bool]] = []  # [decision keys]: (token, decision key, reused) per confirm call.
        self.committed: dict[str, ReviewSnapshot] = {}  # [committed writes]: Snapshot per token once the write happened.
        self.lose_next_reply = False  # [lost reply]: Commit the write, then raise ReviewOutcomeUnknown once.
        self.hold_confirm = False  # [in-flight window]: Block confirm on a worker until the test releases it.
        self.confirm_entered = threading.Event()  # [in-flight marker]: Set when a confirm reaches the worker.
        self.confirm_release = threading.Event()  # [in-flight release]: Lets a held confirm finish.
        self._keys: dict[str, str] = {}  # [decision key per token]: Assigned once, reused on every retry.
        self._token_count = 0  # [token counter]: Gives each preview a distinct token id.

    def _record(self, name: str, args: tuple, kwargs: dict) -> None:
        """@description: Remember one service call for later assertions.
        @param self: Fake service.
        @param name: Service method name.
        @param args: Positional arguments received.
        @param kwargs: Keyword arguments received.
        @return: None.
        """
        self.calls.append((name, args, kwargs))

    def called(self, name: str) -> list[tuple]:
        """@description: Select recorded calls by method name.
        @param self: Fake service.
        @param name: Service method name.
        @return: Matching call records.
        """
        return [call for call in self.calls if call[0] == name]

    def list_pending(self, actor_id: int, *, after_sequence: int | None = None, limit: int = PAGE_SIZE):
        """@description: Return the rows accepted after a keyset position, plus the position that continues the list.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param after_sequence: Accepted sequence the window starts after, or None for the first window.
        @param limit: Maximum rows in the window.
        @return: Page-shaped object with items and next_after_sequence.
        """
        self._record("list_pending", (actor_id,), {"after_sequence": after_sequence, "limit": limit})
        later = [item for item in self.items if after_sequence is None or item.accepted_sequence > after_sequence]
        window = tuple(later[:limit])
        has_more = len(later) > limit
        next_after = window[-1].accepted_sequence if has_more else None
        return SimpleNamespace(items=window, next_after_sequence=next_after)

    def view(self, actor_id: int, operation_id: str):
        """@description: Return the projection for one operation or refuse an unknown one.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param operation_id: Operation inspected.
        @return: Projection-shaped object.
        """
        self._record("view", (actor_id, operation_id), {})
        if operation_id not in self.projections:
            raise LookupError("missing")
        return self.projections[operation_id]

    def stored_evidence(self, actor_id: int, operation_id: str, evidence_id: str):
        """@description: Return one stored evidence record by id or refuse an unknown id.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param operation_id: Operation the evidence should describe.
        @param evidence_id: Citation id.
        @return: Stored evidence record.
        """
        self._record("stored_evidence", (actor_id, operation_id, evidence_id), {})
        if evidence_id not in self.evidence_store:
            raise LookupError("missing evidence")
        return self.evidence_store[evidence_id]

    def preview(self, actor_id: int, operation_id: str, action: ReviewAction, reason: str,
                expected_revision: int, evidence_reference: str, content=None) -> ReviewPreview:
        """@description: Issue a fresh token for a decision preview.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param operation_id: Operation decided.
        @param action: Decision kind.
        @param reason: Audit explanation.
        @param expected_revision: Revision examined.
        @param evidence_reference: Citation.
        @param content: Content judgement, when given.
        @return: Preview bound to a fresh token.
        """
        self._record("preview", (actor_id, operation_id, action, reason, expected_revision, evidence_reference), {"content": content})
        return self._issue(operation_id, action, expected_revision, evidence_reference)

    def preview_reexamination(self, actor_id: int, operation_id: str, evidence, reason: str, expected_revision: int):
        """@description: Issue a fresh token for a reexamination preview bound to the evidence it received.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param operation_id: Operation reopened.
        @param evidence: Evidence record built or read by the Discord layer.
        @param reason: Audit explanation.
        @param expected_revision: Revision examined.
        @return: Preview bound to a fresh token.
        """
        self._record("preview_reexamination", (actor_id, operation_id, evidence, reason, expected_revision), {})
        return self._issue(operation_id, ReviewAction.REEXAMINE, expected_revision, evidence.evidence_id)

    def preview_version_confirmation(self, actor_id: int, operation_id: str, evidence, reason: str, expected_revision: int):
        """@description: Issue a fresh token for a version-confirmation preview bound to the evidence it received.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param operation_id: Operation whose version is confirmed.
        @param evidence: Evidence record built or read by the Discord layer.
        @param reason: Audit explanation.
        @param expected_revision: Revision examined.
        @return: Preview bound to a fresh token.
        """
        self._record("preview_version_confirmation", (actor_id, operation_id, evidence, reason, expected_revision), {})
        return self._issue(operation_id, ReviewAction.CONFIRM_VERSION, expected_revision, evidence.evidence_id)

    def _issue(self, operation_id: str, action: ReviewAction, expected_revision: int, evidence_reference: str) -> ReviewPreview:
        """@description: Mint the next token id so each preview in a test is distinct and predictable.
        @param self: Fake service.
        @param operation_id: Operation previewed.
        @param action: Decision kind.
        @param expected_revision: Revision examined.
        @param evidence_reference: Citation.
        @return: Preview object.
        """
        self._token_count += 1
        return ReviewPreview(f"token-{self._token_count}", operation_id, action, expected_revision, evidence_reference,
                             ReviewStatus.PENDING_REVIEW, ReviewStatus.APPROVED, "不發 Nickel", EXPIRES)

    def confirm(self, actor_id: int, token_id: str) -> ReviewSnapshot:
        """@description: Commit the previewed decision once under a stable decision key, replaying the same result on retry.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param token_id: Preview token confirmed.
        @return: Committed review snapshot.
        """
        self._record("confirm", (actor_id, token_id), {})
        if self.hold_confirm:
            self.confirm_entered.set()
            self.confirm_release.wait(2)
        if token_id in self.committed:
            self.decision_log.append((token_id, self._keys[token_id], True))
            return self.committed[token_id]
        key = self._keys.setdefault(token_id, f"dec-{len(self._keys) + 1}")
        self.decision_log.append((token_id, key, False))
        snapshot = ReviewSnapshot(VIDEO, version=4, status=ReviewStatus.APPROVED, duration=PASSING_DURATION,
                                  approval_source=ApprovalSource.OWNER)
        self.committed[token_id] = snapshot
        if self.lose_next_reply:
            self.lose_next_reply = False
            raise ReviewOutcomeUnknown("reply lost after the write committed")
        return snapshot


def _press(user_id: int, guild_id: int | None, custom_id: str) -> FakeInteraction:
    """@description: Build the interaction a list button press produces, carrying its custom_id.
    @param user_id: Caller snowflake.
    @param guild_id: Guild snowflake.
    @param custom_id: Button custom_id that encodes the cursor.
    @return: Fake component interaction.
    """
    interaction = FakeInteraction(user_id, guild_id)
    interaction.data = {"custom_id": custom_id}
    return interaction


def _texts(interaction: FakeInteraction) -> list[str]:
    """@description: Collect the text of every reply made on one interaction, in order.
    @param interaction: Fake interaction.
    @return: Reply texts.
    """
    return [content for _, content, _ in interaction.replies()]


def _preview_view(service: PagedService):
    """@description: Create one preview through the reject command and return its confirmation view.
    @param service: Fake service.
    @return: ConfirmView attached to the preview reply.
    """
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    return _confirm_view(interaction)


def _ids(text: str) -> list[str]:
    """@description: Extract operation identifiers in the order they appear in a text.
    @param text: Reply text.
    @return: Operation ids.
    """
    return re.findall(r"op-\d{2}", text)


# --- Show: every audit field -------------------------------------------------

def test_show_renders_every_audit_field():
    service = PagedService()
    service.projections["op-1"] = _projection(
        accepted_sequence=7,
        identity=VersionAssignment(VIDEO, status=VersionIdentityStatus.CONFIRMED,
                                   version=RecordingVersionIdentity("rec-ref-1"), evidence_reference="ev-audit-1"),
        evidence=(AUDIT_EVIDENCE,),
        content_judgements=(AUDIT_JUDGEMENT,),
        history_entries=(SimpleNamespace(revision=2, status=ReviewStatus.PENDING_REVIEW, actor_id=OWNER_ID, reason="初審歷程理由"),),
    )
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "show").callback(interaction, operation="op-1"))
    text = "\n".join(_texts(interaction))
    expected = [
        "op-1", "受理順序 7", "https://youtu.be/dQw4w9WgXcQ",
        "ev-audit-1", "owner-note:brief-A", NOW.isoformat(timespec="seconds"), "版本證據理由", str(OWNER_ID),
        "rec-ref-1", "REMIX",
        "判定理由甲", "歌曲",
        "修訂 3", "已確認",
        "修訂 2", "待審核", "初審歷程理由",
    ]
    for fragment in expected:
        assert fragment in text, fragment


def test_show_renders_the_conflict_reason_when_identity_conflicts():
    service = PagedService()
    service.projections["op-1"] = _projection(
        identity=VersionAssignment(VIDEO, status=VersionIdentityStatus.CONFLICT, evidence_reference="ev-audit-1"),
        conflict_reason="版本互相矛盾：ev-a 與 ev-b",
    )
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "show").callback(interaction, operation="op-1"))
    assert "版本互相矛盾：ev-a 與 ev-b" in "\n".join(_texts(interaction))


def test_show_marks_absent_version_and_category_explicitly():
    service = PagedService()
    service.projections["op-1"] = _projection(evidence=(record_evidence("ev-plain", VIDEO, EvidenceSourceType.OWNER_REVIEW,
                                                                        "owner-note:plain", NOW, "無版本", OWNER_ID),))
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "show").callback(interaction, operation="op-1"))
    text = "\n".join(_texts(interaction))
    assert "ev-plain" in text and "聲稱版本：無" in text


# --- Splitter: every message under the limit, no line cut ----------------------

def test_large_projection_splits_into_ephemeral_messages_under_the_limit_without_cutting_lines():
    service = PagedService()
    evidence = tuple(
        record_evidence(f"ev-{number:04d}", VIDEO, EvidenceSourceType.OWNER_REVIEW, f"owner-note:batch-{number}",
                        NOW, "長理由" * 12, OWNER_ID)
        for number in range(300)
    )
    service.projections["op-1"] = _projection(evidence=evidence)
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "show").callback(interaction, operation="op-1"))
    contents = [content for _, content, _ in interaction.followup.calls]
    assert len(contents) > 1
    assert all(len(content) <= 2000 for content in contents)
    rebuilt_lines = [line for content in contents for line in content.split("\n")]
    assert rebuilt_lines == owner_ui.render_projection(service.projections["op-1"]).split("\n")
    for _, _, kwargs in interaction.followup.calls:
        _assert_private_reply(kwargs)


def test_split_messages_packs_whole_lines_under_the_limit():
    text = "\n".join(f"line-{number:03d}-" + "x" * 30 for number in range(100))
    chunks = owner_ui.split_messages(text, limit=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert "\n".join(chunks) == text


def test_split_messages_refuses_a_single_line_longer_than_the_limit():
    with pytest.raises(ValueError):
        owner_ui.split_messages("a" * 11, limit=10)


def test_a_max_length_reason_and_an_overlong_source_never_push_a_line_past_the_message_limit():
    service = PagedService()
    service.projections["op-1"] = _projection(evidence=(record_evidence(
        "ev-long", VIDEO, EvidenceSourceType.OWNER_REVIEW, "owner-note:" + "s" * 3000, NOW, "理" * 1024, OWNER_ID),))
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "show").callback(interaction, operation="op-1"))
    contents = [content for _, content, _ in interaction.followup.calls]
    assert all(len(content) <= 2000 for content in contents)
    assert any("…" in content for content in contents)


# --- List: pagination, gate on every press, stale cursors --------------------

def test_first_page_offers_next_with_a_cursor_and_disables_previous():
    service = PagedService(25)
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "list").callback(interaction))
    text = interaction.followup.calls[0][1]
    view = interaction.followup.calls[0][2]["view"]
    assert _ids(text) == [f"op-{number:02d}" for number in range(PAGE_SIZE)]
    assert view.next_button.custom_id == f"owner-list:{FIRST_SEQUENCE + PAGE_SIZE - 1}|f"
    assert service.called("list_pending")[0][2] == {"after_sequence": None, "limit": PAGE_SIZE}
    assert view.next_button.disabled is False
    assert view.previous_button.disabled is True


def test_next_press_serves_the_second_page_and_the_two_pages_do_not_overlap():
    service = PagedService(25)
    first = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "list").callback(first))
    view = first.followup.calls[0][2]["view"]
    press = _press(OWNER_ID, GUILD_ID, view.next_button.custom_id)
    _run(view.next_button.callback(press))
    second_ids = _ids(press.followup.calls[0][1])
    first_ids = _ids(first.followup.calls[0][1])
    assert second_ids == [f"op-{number:02d}" for number in range(PAGE_SIZE, 25)]
    assert set(first_ids).isdisjoint(second_ids)
    assert service.called("list_pending")[-1][2] == {"after_sequence": FIRST_SEQUENCE + PAGE_SIZE - 1, "limit": PAGE_SIZE}
    assert press.followup.calls[0][2]["view"].next_button.disabled is True
    _assert_private_reply(press.followup.calls[0][2])


def test_previous_press_returns_to_the_first_page():
    service = PagedService(25)
    first = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "list").callback(first))
    second_view = _press_next(service, first)
    back = _press(OWNER_ID, GUILD_ID, second_view.previous_button.custom_id)
    _run(second_view.previous_button.callback(back))
    assert _ids(back.followup.calls[0][1]) == [f"op-{number:02d}" for number in range(PAGE_SIZE)]


def _press_next(service: PagedService, first: FakeInteraction):
    """@description: Press Next once on the first page and return the view attached to the second page.
    @param service: Fake service.
    @param first: Interaction that delivered the first page.
    @return: Second page's view.
    """
    view = first.followup.calls[0][2]["view"]
    press = _press(OWNER_ID, GUILD_ID, view.next_button.custom_id)
    _run(view.next_button.callback(press))
    return press.followup.calls[0][2]["view"]


@pytest.mark.parametrize(("user_id", "guild_id"), [(OWNER_ID, OTHER_GUILD_ID), (OTHER_USER_ID, GUILD_ID)])
def test_next_press_re_verifies_guild_and_owner_before_any_read(user_id, guild_id):
    service = PagedService(25)
    first = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "list").callback(first))
    view = first.followup.calls[0][2]["view"]
    reads_before = len(service.calls)
    press = _press(user_id, guild_id, view.next_button.custom_id)
    _run(view.next_button.callback(press))
    assert len(service.calls) == reads_before
    assert len(_refusal_texts(press)) == 1
    _assert_private_reply(press.replies()[0][2])


@pytest.mark.parametrize("custom_id", ["owner-list:abc", "owner-list:-20", "owner-list:", "owner-list:1.5",
                                       "owner-list:119|x", "owner-list:119|-3", "owner-list:119|f,"])
def test_malformed_or_misaligned_cursor_is_a_plain_message_with_no_read(custom_id):
    service = PagedService(25)
    press = _press(OWNER_ID, GUILD_ID, custom_id)
    _run(_list_button(service).callback(press))
    assert service.called("list_pending") == []
    text = press.followup.calls[0][1]
    assert "已過期" in text and _ids(text) == []


def test_previous_walks_back_through_the_stack_of_earlier_pages():
    service = PagedService(45)
    first = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "list").callback(first))
    second_view = _press_next(service, first)
    third = _press(OWNER_ID, GUILD_ID, second_view.next_button.custom_id)
    _run(second_view.next_button.callback(third))
    third_view = third.followup.calls[0][2]["view"]
    assert third_view.previous_button.custom_id == f"owner-list:{FIRST_SEQUENCE + PAGE_SIZE - 1}|f"
    back = _press(OWNER_ID, GUILD_ID, third_view.previous_button.custom_id)
    _run(third_view.previous_button.callback(back))
    assert _ids(back.followup.calls[0][1]) == [f"op-{number:02d}" for number in range(PAGE_SIZE, 2 * PAGE_SIZE)]
    assert service.called("list_pending")[-1][2] == {"after_sequence": FIRST_SEQUENCE + PAGE_SIZE - 1, "limit": PAGE_SIZE}


def test_a_well_formed_sequence_cursor_is_read_as_a_position_not_a_page_boundary():
    service = PagedService(25)
    press = _press(OWNER_ID, GUILD_ID, "owner-list:7")
    _run(_list_button(service).callback(press))
    assert service.called("list_pending")[-1][2] == {"after_sequence": 7, "limit": PAGE_SIZE}
    assert _ids(press.followup.calls[0][1]) == [f"op-{number:02d}" for number in range(PAGE_SIZE)]


def test_cursor_past_the_end_is_a_plain_stale_message_and_names_no_case():
    service = PagedService(25)
    press = _press(OWNER_ID, GUILD_ID, "owner-list:999999")
    _run(_list_button(service).callback(press))
    text = press.followup.calls[0][1]
    assert "已過期" in text and _ids(text) == []
    assert press.followup.calls[0][2].get("view") is None


def _list_button(service: PagedService):
    """@description: Expose the page-button handler through any page view, as the button dispatches to it.
    @param service: Fake service.
    @return: Object whose callback handles a press.
    """
    first = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "list").callback(first))
    service.calls.clear()  # [press-only trace]: Count reads made by the press under test, not by building the view.
    return first.followup.calls[0][2]["view"].next_button


# --- Confirm / cancel state machine ------------------------------------------

def test_cancel_from_open_blocks_a_later_confirm():
    service = PagedService()
    view = _preview_view(service)
    _run(view.cancel_button.callback(FakeInteraction(OWNER_ID, GUILD_ID)))
    confirm_press = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(view.confirm_button.callback(confirm_press))
    assert service.called("confirm") == []
    assert "已取消" in confirm_press.followup.calls[0][1]
    _assert_private_reply(confirm_press.followup.calls[0][2])


def test_cancel_while_in_flight_says_the_confirmation_started_and_names_the_case():
    service = PagedService()
    service.hold_confirm = True
    view = _preview_view(service)

    async def scenario():
        confirm_task = asyncio.create_task(view.confirm_button.callback(FakeInteraction(OWNER_ID, GUILD_ID)))
        await asyncio.to_thread(service.confirm_entered.wait, 2)
        cancel_press = FakeInteraction(OWNER_ID, GUILD_ID)
        await view.cancel_button.callback(cancel_press)
        service.confirm_release.set()
        await confirm_task
        return cancel_press

    cancel_press = _run(scenario())
    text = _texts(cancel_press)[0]
    assert "已開始" in text and "尚未確定" in text
    assert "op-1" in text and "重新" in text and "同一" in text
    assert "不會執行" not in text and "已取消" not in text
    _assert_private_reply(cancel_press.replies()[0][2])


@pytest.mark.parametrize("confirm_starts_first", [True, False])
def test_confirm_and_cancel_started_together_resolve_in_either_order(confirm_starts_first):
    service = PagedService()
    view = _preview_view(service)
    confirm_press = FakeInteraction(OWNER_ID, GUILD_ID)
    cancel_press = FakeInteraction(OWNER_ID, GUILD_ID)
    confirm = view.confirm_button.callback(confirm_press)
    cancel = view.cancel_button.callback(cancel_press)
    ordered = (confirm, cancel) if confirm_starts_first else (cancel, confirm)

    async def together():
        await asyncio.gather(*ordered)

    _run(together())
    if confirm_starts_first:
        assert len(service.called("confirm")) == 1
        assert "已開始" in _texts(cancel_press)[0]
        assert "已確認" in confirm_press.followup.calls[0][1]
    else:
        assert service.called("confirm") == []
        assert "已取消" in _texts(cancel_press)[0]
        assert "已取消" in confirm_press.followup.calls[0][1]


def test_lost_reply_keeps_the_token_and_retry_replays_the_same_decision_key():
    service = PagedService()
    service.lose_next_reply = True
    view = _preview_view(service)
    first = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(view.confirm_button.callback(first))
    assert "結果未知" in first.followup.calls[0][1] and "重新確認" in first.followup.calls[0][1]
    assert service.decision_log == [("token-1", "dec-1", False)]

    second = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(view.confirm_button.callback(second))
    assert "已確認" in second.followup.calls[0][1] and "修訂 4" in second.followup.calls[0][1]
    assert service.decision_log == [("token-1", "dec-1", False), ("token-1", "dec-1", True)]
    assert [call[1] for call in service.called("confirm")] == [(OWNER_ID, "token-1")] * 2


def test_cancel_after_unknown_keeps_the_verification_and_the_retry_still_replays():
    service = PagedService()
    service.lose_next_reply = True
    view = _preview_view(service)
    _run(view.confirm_button.callback(FakeInteraction(OWNER_ID, GUILD_ID)))
    cancel_press = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(view.cancel_button.callback(cancel_press))
    cancel_text = _texts(cancel_press)[0]
    assert "結果未知" in cancel_text and "已取消" not in cancel_text
    retry = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(view.confirm_button.callback(retry))
    assert "已確認" in retry.followup.calls[0][1]
    assert service.decision_log == [("token-1", "dec-1", False), ("token-1", "dec-1", True)]


def test_successful_result_is_replayed_without_a_second_service_call():
    service = PagedService()
    view = _preview_view(service)
    first = FakeInteraction(OWNER_ID, GUILD_ID)
    second = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(view.confirm_button.callback(first))
    _run(view.confirm_button.callback(second))
    assert len(service.called("confirm")) == 1
    assert first.followup.calls[0][1] == second.followup.calls[0][1]


# --- Citation: evidence_id or a traceable source, never the interaction id ----

@pytest.mark.parametrize("command_name", ["confirm_version", "reexamine"])
def test_missing_source_and_evidence_is_refused_before_any_service_call(command_name):
    service = PagedService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), command_name).callback(interaction, operation="op-1", version_name="rec-ref-1",
                                                          version_kind="basic", reason="理由"))
    assert service.calls == []
    assert len(interaction.replies()) == 1
    _assert_private_reply(interaction.replies()[0][2])


@pytest.mark.parametrize("command_name", ["confirm_version", "reexamine"])
def test_giving_both_source_and_evidence_is_refused_before_any_service_call(command_name):
    service = PagedService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), command_name).callback(interaction, operation="op-1", version_name="rec-ref-1",
                                                          version_kind="basic", reason="理由",
                                                          evidence_id="ev-audit-1", source=TRACEABLE_SOURCE))
    assert service.calls == []
    assert len(interaction.replies()) == 1


def test_an_interaction_id_style_source_is_refused_before_any_service_call():
    service = PagedService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "confirm_version").callback(interaction, operation="op-1", version_name="rec-ref-1",
                                                               version_kind="basic", reason="理由", source="9001"))
    assert service.calls == []
    assert len(interaction.replies()) == 1


def test_source_builds_a_new_record_whose_reference_is_the_source_not_the_interaction_id():
    service = PagedService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "confirm_version").callback(interaction, operation="op-1", version_name="rec-ref-1",
                                                               version_kind="REMIX", reason="理由", source=TRACEABLE_SOURCE))
    evidence = service.called("preview_version_confirmation")[0][1][2]
    assert evidence.source_reference == TRACEABLE_SOURCE
    assert str(interaction.id) not in evidence.source_reference
    assert evidence.version_kind is VersionClaimKind.REMIX
    assert str(interaction.id) in evidence.reason


def test_reexamine_with_a_source_also_avoids_the_interaction_id():
    service = PagedService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reexamine").callback(interaction, operation="op-1", version_name="rec-ref-1",
                                                          version_kind="basic", reason="理由", source=TRACEABLE_SOURCE))
    evidence = service.called("preview_reexamination")[0][1][2]
    assert evidence.source_reference == TRACEABLE_SOURCE


def test_evidence_id_cites_the_stored_record_without_creating_one():
    service = PagedService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reexamine").callback(interaction, operation="op-1", reason="理由", evidence_id="ev-audit-1"))
    assert service.called("stored_evidence")[0][1] == (OWNER_ID, "op-1", "ev-audit-1")
    assert service.called("preview_reexamination")[0][1][2] is AUDIT_EVIDENCE


# --- Preview rendering: every action labelled, render failures never masked --

def test_confirm_version_preview_renders_its_label_instead_of_a_missing_case():
    service = PagedService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "confirm_version").callback(interaction, operation="op-1", reason="理由",
                                                               version_name="rec-ref-1", version_kind="basic",
                                                               source=TRACEABLE_SOURCE))
    text = interaction.followup.calls[0][1]
    assert "確認版本" in text
    assert "找不到" not in text


@pytest.mark.parametrize("action", list(ReviewAction))
def test_every_review_action_has_an_owner_label(action):
    assert action in owner_ui.ACTION_LABELS


def test_a_render_failure_after_the_token_is_issued_offers_no_confirm_button(monkeypatch):
    service = PagedService()

    def broken_render(preview):
        raise KeyError("boom")

    monkeypatch.setattr(owner_ui, "render_preview", broken_render)
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    text, kwargs = interaction.followup.calls[0][1], interaction.followup.calls[0][2]
    assert "view" not in kwargs
    assert "未建立確認按鈕" in text
    assert "找不到" not in text


def test_a_key_error_while_showing_is_an_internal_error_not_a_missing_case(monkeypatch):
    service = PagedService()

    def broken_projection(projection):
        raise KeyError("status")

    monkeypatch.setattr(owner_ui, "render_projection", broken_projection)
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "show").callback(interaction, operation="op-1"))
    text = interaction.followup.calls[0][1]
    assert "內部錯誤" in text
    assert "找不到" not in text
