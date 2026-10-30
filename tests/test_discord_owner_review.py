"""Verify the Owner-only Discord review commands gate, preview, confirm and reply safely with fake interactions.

Inputs are fake Discord interactions and a fake OwnerReviewService that records calls and worker threads; no Discord connection is made.
"""

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from bot.discord_owner_review import build_owner_group
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, VersionClaimKind
from bot.owner_review import ExpiredReviewToken, ReviewAction, ReviewOutcomeUnknown, ReviewPreview, UnknownReviewToken
from bot.review import ApprovalSource, ReviewSnapshot, ReviewStatus
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.version_identity import VersionAssignment, VersionIdentityStatus

OWNER_ID = 1001
GUILD_ID = 2002
OTHER_GUILD_ID = 3003
OTHER_USER_ID = 4004
VIDEO = VideoIdentity("dQw4w9WgXcQ")
PASSING_DURATION = DurationEvidence(VIDEO, 180, "synthetic-fixture", reliable=True, full_video=True)
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
EXPIRES = NOW + timedelta(minutes=5)
REFUSAL_PREFIX = "此指令僅限指定私人伺服器中的 Owner 使用"


@dataclass(frozen=True)
class FakePendingItem:
    """Mirror the PendingItem fields the contract publishes, so rendering is tested without that module."""

    operation_id: str
    review_status: ReviewStatus
    identity_status: VersionIdentityStatus
    revision: int
    evidence_count: int
    conflict_reason: str | None = None


class FakeOwnerReviewService:
    """Record every service call with the thread it ran on and return configured results."""

    def __init__(self) -> None:
        """@description: Start with a pending list, one projection and a confirmable preview.
        @param: none
        @return: None.
        """
        self.calls: list[tuple[str, tuple, dict, threading.Thread]] = []  # [service trace]: Method, arguments and worker thread.
        self.pending_items = (FakePendingItem("op-1", ReviewStatus.PENDING_REVIEW, VersionIdentityStatus.UNRESOLVED, 3, 2),)
        self.projection = SimpleNamespace(
            operation_id="op-1", accepted_sequence=1, identity=VersionAssignment(VIDEO),
            review=ReviewSnapshot(VIDEO, version=3, status=ReviewStatus.PENDING_REVIEW, duration=PASSING_DURATION),
            evidence=(), history=(),
            content_judgements=(), source_url="https://youtu.be/dQw4w9WgXcQ", history_entries=(), conflict_reason=None,
        )
        self.confirm_result = ReviewSnapshot(VIDEO, version=4, status=ReviewStatus.APPROVED, duration=PASSING_DURATION,
                                          approval_source=ApprovalSource.OWNER)
        self.confirm_error: Exception | None = None  # [confirm failure]: Raised instead of the result when set.
        self.view_delay_seconds = 0.0  # [slow call]: Simulates database latency inside the worker thread.
        self.progress: list[int] = []  # [loop progress]: Filled by a coroutine that must keep running during the slow call.
        self.progress_during_call = -1  # [loop progress]: How many progress ticks happened while view was running.

    def _record(self, name: str, args: tuple, kwargs: dict) -> None:
        """@description: Remember the call and the thread so tests can prove off-loop execution.
        @param self: Fake service.
        @param name: Service method invoked.
        @param args: Positional arguments received.
        @param kwargs: Keyword arguments received.
        @return: None.
        """
        self.calls.append((name, args, kwargs, threading.current_thread()))

    def list_pending(self, actor_id: int, *, after_sequence: int | None = None, limit: int = 20):
        """@description: Return fake pending rows for the contract method as one keyset page.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param after_sequence: Accepted sequence the page starts after, or None for the first page.
        @param limit: Maximum rows in the page.
        @return: Page with items and next_after_sequence.
        """
        self._record("list_pending", (actor_id,), {"after_sequence": after_sequence, "limit": limit})
        return SimpleNamespace(items=self.pending_items, next_after_sequence=None)

    def view(self, actor_id: int, operation_id: str):
        """@description: Return the fake projection after an optional simulated delay.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param operation_id: Operation inspected.
        @return: Fake projection.
        """
        self._record("view", (actor_id, operation_id), {})
        if self.view_delay_seconds:
            time.sleep(self.view_delay_seconds)
            self.progress_during_call = len(self.progress)
        return self.projection

    def preview(self, actor_id: int, operation_id: str, action: ReviewAction, reason: str,
                expected_revision: int, evidence_reference: str, content=None) -> ReviewPreview:
        """@description: Issue a fake token bound to the arguments it received.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param operation_id: Operation decided.
        @param action: Decision kind.
        @param reason: Audit explanation.
        @param expected_revision: Revision examined.
        @param evidence_reference: Citation.
        @param content: Content judgement, when given.
        @return: Preview with a fixed token id.
        """
        self._record("preview", (actor_id, operation_id, action, reason, expected_revision, evidence_reference),
                     {"content": content})
        return ReviewPreview("token-1", operation_id, action, expected_revision, evidence_reference,
                             ReviewStatus.PENDING_REVIEW, ReviewStatus.APPROVED, "不發 Nickel", EXPIRES)

    def preview_reexamination(self, actor_id: int, operation_id: str, evidence, reason: str, expected_revision: int):
        """@description: Issue a fake reexamination token bound to the evidence it received.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param operation_id: Operation reopened.
        @param evidence: Evidence record built by the Discord layer.
        @param reason: Audit explanation.
        @param expected_revision: Revision examined.
        @return: Preview with a fixed token id.
        """
        self._record("preview_reexamination", (actor_id, operation_id, evidence, reason, expected_revision), {})
        return ReviewPreview("token-2", operation_id, ReviewAction.REEXAMINE, expected_revision, evidence.evidence_id,
                             ReviewStatus.PENDING_REVIEW, ReviewStatus.PENDING_REVIEW, "不發 Nickel", EXPIRES)

    def preview_version_confirmation(self, actor_id: int, operation_id: str, evidence, reason: str, expected_revision: int):
        """@description: Issue a fake version-confirmation token bound to the evidence it received.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param operation_id: Operation whose version is confirmed.
        @param evidence: Evidence record built by the Discord layer.
        @param reason: Audit explanation.
        @param expected_revision: Revision examined.
        @return: Preview with a fixed token id.
        """
        self._record("preview_version_confirmation", (actor_id, operation_id, evidence, reason, expected_revision), {})
        return ReviewPreview("token-3", operation_id, ReviewAction.REEXAMINE, expected_revision, evidence.evidence_id,
                             ReviewStatus.PENDING_REVIEW, ReviewStatus.PENDING_REVIEW, "不發 Nickel", EXPIRES)

    def confirm(self, actor_id: int, token_id: str) -> ReviewSnapshot:
        """@description: Return the configured confirmation result or raise the configured failure.
        @param self: Fake service.
        @param actor_id: Caller identity.
        @param token_id: Preview token confirmed.
        @return: Confirmed review snapshot.
        """
        self._record("confirm", (actor_id, token_id), {})
        if self.confirm_error is not None:
            raise self.confirm_error
        return self.confirm_result

    def called(self, name: str) -> list[tuple]:
        """@description: Select recorded calls by method name.
        @param self: Fake service.
        @param name: Service method name.
        @return: Matching call records.
        """
        return [call for call in self.calls if call[0] == name]


class FakeResponse:
    """Record every response kwarg so tests can assert privacy and mention flags."""

    def __init__(self) -> None:
        """@description: Start with no recorded replies.
        @param: none
        @return: None.
        """
        self.calls: list[tuple[str, str | None, dict]] = []
        self.deferrals: list[dict] = []  # [acknowledgements]: Defer options, kept apart because defer takes no mention controls.

    async def defer(self, **kwargs) -> None:
        """@description: Record the acknowledgement that keeps slow service work inside Discord's reply window.
        @param self: Fake response.
        @param kwargs: Defer options.
        @return: None.
        """
        self.deferrals.append(kwargs)

    async def send_message(self, content=None, **kwargs) -> None:
        """@description: Record a direct reply.
        @param self: Fake response.
        @param content: Reply text.
        @param kwargs: Discord reply options.
        @return: None.
        """
        self.calls.append(("send_message", content, kwargs))


class FakeFollowup:
    """Record every follow-up kwarg so tests can assert privacy and mention flags."""

    def __init__(self) -> None:
        """@description: Start with no recorded follow-ups.
        @param: none
        @return: None.
        """
        self.calls: list[tuple[str, str | None, dict]] = []

    async def send(self, content=None, **kwargs) -> None:
        """@description: Record a follow-up message.
        @param self: Fake followup.
        @param content: Message text.
        @param kwargs: Discord message options.
        @return: None.
        """
        self.calls.append(("send", content, kwargs))


class FakeInteraction:
    """Carry the guild, user and response objects that the gate and replies read."""

    def __init__(self, user_id: int, guild_id: int | None) -> None:
        """@description: Build an interaction from one caller and one guild.
        @param self: Fake interaction.
        @param user_id: Discord user snowflake of the caller.
        @param guild_id: Guild snowflake where the interaction happened.
        @return: None.
        """
        self.user = SimpleNamespace(id=user_id)
        self.guild_id = guild_id
        self.id = 9001
        self.response = FakeResponse()
        self.followup = FakeFollowup()

    def replies(self) -> list[tuple[str, str | None, dict]]:
        """@description: Collect every reply made through response or followup, in order.
        @param self: Fake interaction.
        @return: Recorded replies.
        """
        return [*self.response.calls, *self.followup.calls]


def _group(service: FakeOwnerReviewService, now=lambda: NOW) -> app_commands.Group:
    """@description: Build the Owner group wired to the fake service and the private guild.
    @param service: Fake service returned by the provider.
    @param now: Injected clock.
    @return: Owner command group.
    """
    return build_owner_group(lambda: service, OWNER_ID, GUILD_ID, now)


def _assert_private_reply(kwargs: dict) -> None:
    """@description: Assert the Discord reply options keep the reply private and mention-free.
    @param kwargs: Options recorded for one reply.
    @return: None.
    """
    assert kwargs["ephemeral"] is True
    mentions = kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.users, mentions.roles, mentions.replied_user) == (False, False, False, False)


def _refusal_texts(interaction: FakeInteraction) -> list[str]:
    """@description: Return the content of every reply that is a refusal.
    @param interaction: Fake interaction whose replies are inspected.
    @return: Refusal reply texts.
    """
    return [content for _, content, _ in interaction.replies() if content and content.startswith(REFUSAL_PREFIX)]


def _confirm_view(interaction: FakeInteraction):
    """@description: Extract the Confirm/Cancel view attached to the last preview reply.
    @param interaction: Fake interaction that received the preview.
    @return: The attached view.
    """
    return interaction.replies()[-1][2]["view"]


def _button_interaction(user_id: int, guild_id: int | None) -> FakeInteraction:
    """@description: Build the interaction a button press would produce.
    @param user_id: Caller snowflake.
    @param guild_id: Guild snowflake.
    @return: Fake button interaction.
    """
    return FakeInteraction(user_id, guild_id)


def _command(group: app_commands.Group, name: str):
    """@description: Fetch one subcommand callback bound to its group, as discord.py dispatches it.
    @param group: Owner group under test.
    @param name: Subcommand name.
    @return: Object whose callback takes the interaction and subcommand arguments.
    """
    command = group.get_command(name)
    return SimpleNamespace(callback=partial(command.callback, command.binding))


def _run(coroutine):
    """@description: Execute one scenario on a fresh event loop.
    @param coroutine: Awaitable scenario.
    @return: Scenario result.
    """
    return asyncio.run(coroutine)


# --- Group shape -------------------------------------------------------------

def test_group_is_named_owner_and_scoped_to_one_private_guild():
    group = _group(FakeOwnerReviewService())
    assert group.name == "owner"
    assert group._guild_ids == [GUILD_ID]  # [guild scope]: discord.py keeps this private; the group must not be global.


def test_group_exposes_all_six_subcommands():
    group = _group(FakeOwnerReviewService())
    names = {command.name for command in group.commands}
    assert names == {"list", "show", "duration", "approve", "reject", "confirm_version", "reexamine"}


# --- Gate: guild and Owner before any private read ---------------------------

@pytest.mark.parametrize("command_name", ["list", "show", "approve", "reject", "confirm_version", "reexamine"])
def test_wrong_guild_is_refused_before_any_service_call(command_name):
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, OTHER_GUILD_ID)
    kwargs = _kwargs_for(command_name)
    _run(_command(_group(service), command_name).callback(interaction, **kwargs))
    assert service.calls == []
    assert len(_refusal_texts(interaction)) == 1
    _assert_private_reply(interaction.replies()[0][2])


@pytest.mark.parametrize("command_name", ["list", "show", "approve", "reject", "confirm_version", "reexamine"])
def test_non_owner_is_refused_before_any_service_call(command_name):
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OTHER_USER_ID, GUILD_ID)
    _run(_command(_group(service), command_name).callback(interaction, **_kwargs_for(command_name)))
    assert service.calls == []
    assert len(_refusal_texts(interaction)) == 1


def test_refusal_reveals_nothing_about_whether_the_operation_exists():
    service = FakeOwnerReviewService()
    wrong_guild = FakeInteraction(OWNER_ID, OTHER_GUILD_ID)
    non_owner = FakeInteraction(OTHER_USER_ID, GUILD_ID)
    _run(_command(_group(service), "show").callback(wrong_guild, operation="op-exists"))
    _run(_command(_group(service), "show").callback(non_owner, operation="op-missing"))
    assert service.calls == []
    assert _refusal_texts(wrong_guild) == _refusal_texts(non_owner)


def test_confirm_button_re_verifies_guild_and_owner_on_its_own_interaction():
    service = FakeOwnerReviewService()
    owner_interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(owner_interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    view = _confirm_view(owner_interaction)
    _run(view.confirm_button.callback(_button_interaction(OWNER_ID, OTHER_GUILD_ID)))
    _run(view.confirm_button.callback(_button_interaction(OTHER_USER_ID, GUILD_ID)))
    assert service.called("confirm") == []


# --- Preview then confirm ----------------------------------------------------

def test_preview_then_confirm_executes_exactly_once():
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    assert [call[0] for call in service.calls] == ["view", "preview"]
    view = _confirm_view(interaction)
    _run(view.confirm_button.callback(_button_interaction(OWNER_ID, GUILD_ID)))
    confirms = service.called("confirm")
    assert len(confirms) == 1
    assert confirms[0][1] == (OWNER_ID, "token-1")


def test_double_press_returns_stored_result_without_second_service_call():
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    view = _confirm_view(interaction)
    first = FakeInteraction(OWNER_ID, GUILD_ID)
    second = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(view.confirm_button.callback(first))
    _run(view.confirm_button.callback(second))
    assert len(service.called("confirm")) == 1
    assert first.replies()[0][1] == second.replies()[0][1]


def test_expired_token_shows_an_ephemeral_expiry_message():
    service = FakeOwnerReviewService()
    service.confirm_error = ExpiredReviewToken("expired")
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    button_interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_confirm_view(interaction).confirm_button.callback(button_interaction))
    content = button_interaction.replies()[0][1]
    assert "過期" in content
    _assert_private_reply(button_interaction.replies()[0][2])


def test_unknown_token_is_refused_with_a_plain_message():
    service = FakeOwnerReviewService()
    service.confirm_error = UnknownReviewToken("unknown")
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    button_interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_confirm_view(interaction).confirm_button.callback(button_interaction))
    assert "無效" in button_interaction.replies()[0][1]


def test_unknown_outcome_asks_for_the_same_confirmation_to_be_retried():
    service = FakeOwnerReviewService()
    service.confirm_error = ReviewOutcomeUnknown("unknown")
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    button_interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_confirm_view(interaction).confirm_button.callback(button_interaction))
    assert "重新確認" in button_interaction.replies()[0][1]


def test_cancel_button_never_calls_confirm():
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    cancel_interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_confirm_view(interaction).cancel_button.callback(cancel_interaction))
    assert service.called("confirm") == []
    _assert_private_reply(cancel_interaction.replies()[0][2])


def test_confirmation_view_has_a_timeout():
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    assert _confirm_view(interaction).timeout is not None


# --- Decision inputs passed to the service -----------------------------------

def test_approve_builds_an_owner_content_judgement_for_the_projected_video():
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service, now=lambda: NOW), "approve").callback(interaction, operation="op-1", citation="ref-1", reason="確認為歌曲"))
    preview_call = service.called("preview")[0]
    content = preview_call[2]["content"]
    assert preview_call[1] == (OWNER_ID, "op-1", ReviewAction.APPROVE, "確認為歌曲", 3, "ref-1")
    assert (content.video, content.judged_by, content.is_song, content.evidence_reference) == (VIDEO, OWNER_ID, True, "ref-1")
    assert content.retrieved_at == NOW


def test_reject_uses_the_revision_the_owner_just_viewed():
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reject").callback(interaction, operation="op-1", citation="ref-1", reason="不是歌曲"))
    assert service.called("preview")[0][1] == (OWNER_ID, "op-1", ReviewAction.REJECT, "不是歌曲", 3, "ref-1")


def test_unknown_version_kind_is_refused_before_any_service_call():
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reexamine").callback(interaction, operation="op-1", version_name="rec-ref-1", version_kind="NOPE", reason="理由"))
    assert service.calls == []
    assert len(interaction.replies()) == 1
    assert "未知" in interaction.replies()[0][1]
    _assert_private_reply(interaction.replies()[0][2])


def test_basic_version_kind_maps_to_no_kind_on_the_evidence_record():
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "reexamine").callback(interaction, operation="op-1", version_name="rec-ref-1",
                                                         version_kind="basic", reason="理由", source="owner-note:brief-A"))
    evidence = service.called("preview_reexamination")[0][1][2]
    assert evidence.version_kind is None
    assert evidence.source_type is EvidenceSourceType.OWNER_REVIEW
    assert evidence.video == VIDEO


def test_named_version_kind_maps_to_the_claim_kind_member():
    service = FakeOwnerReviewService()
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "confirm_version").callback(interaction, operation="op-1", version_name="rec-ref-1",
                                                               version_kind="REMIX", reason="理由", source="owner-note:brief-A"))
    evidence = service.called("preview_version_confirmation")[0][1][2]
    assert evidence.version_kind is VersionClaimKind.REMIX
    assert evidence.version.value == "rec-ref-1"


# --- Replies: privacy and mention controls -----------------------------------

def test_every_reply_across_the_flows_is_ephemeral_and_mention_free():
    service = FakeOwnerReviewService()
    service.confirm_error = ExpiredReviewToken("expired")
    flows = [
        ("list", {}),
        ("show", {"operation": "op-1"}),
        ("reexamine", {"operation": "op-1", "version_name": "rec-ref-1", "version_kind": "basic", "reason": "理由",
                       "source": "owner-note:brief-A"}),
        ("reexamine", {"operation": "op-1", "version_name": "rec-ref-1", "version_kind": "NOPE", "reason": "理由"}),
        ("show", {"operation": "op-1"}),
    ]
    interactions = []
    for name, kwargs in flows:
        interaction = FakeInteraction(OWNER_ID, GUILD_ID)
        interactions.append(interaction)
        _run(_command(_group(service), name).callback(interaction, **kwargs))
    refused = FakeInteraction(OTHER_USER_ID, GUILD_ID)
    interactions.append(refused)
    _run(_command(_group(service), "list").callback(refused))
    button = FakeInteraction(OWNER_ID, GUILD_ID)
    interactions.append(button)
    _run(_confirm_view(interactions[2]).confirm_button.callback(button))
    replies = [reply for interaction in interactions for reply in interaction.replies()]
    assert replies
    for _, _, kwargs in replies:
        _assert_private_reply(kwargs)
    for interaction in interactions:
        for deferral in interaction.response.deferrals:
            assert deferral["ephemeral"] is True


# --- Worker threads and loop progress ----------------------------------------

def test_service_calls_run_on_a_worker_thread():
    service = FakeOwnerReviewService()
    _run(_command(_group(service), "list").callback(FakeInteraction(OWNER_ID, GUILD_ID)))
    assert service.calls[0][3] is not threading.main_thread()


def test_slow_service_call_does_not_block_the_event_loop():
    service = FakeOwnerReviewService()
    service.view_delay_seconds = 0.2
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)

    async def scenario():
        async def ticker():
            for _ in range(10):
                service.progress.append(1)
                await asyncio.sleep(0.01)

        ticker_task = asyncio.create_task(ticker())
        await _command(_group(service), "show").callback(interaction, operation="op-1")
        await ticker_task

    _run(scenario())
    assert service.progress_during_call >= 3
    assert service.calls[0][3] is not threading.main_thread()


# --- Show and list rendering -------------------------------------------------

def test_list_renders_each_pending_row_with_conflict_reason_when_present():
    service = FakeOwnerReviewService()
    service.pending_items = (
        FakePendingItem("op-1", ReviewStatus.PENDING_REVIEW, VersionIdentityStatus.UNRESOLVED, 3, 2),
        FakePendingItem("op-2", ReviewStatus.PENDING_REVIEW, VersionIdentityStatus.CONFLICT, 5, 0, "版本互相矛盾"),
    )
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "list").callback(interaction))
    content = interaction.replies()[0][1]
    assert "op-1" in content and "op-2" in content
    assert "版本互相矛盾" in content


def test_show_renders_identity_revision_evidence_and_content_summary():
    service = FakeOwnerReviewService()
    service.projection.content_judgements = (ContentJudgement(VIDEO, OWNER_ID, "理由", NOW, "ref-1", True),)
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "show").callback(interaction, operation="op-1"))
    content = interaction.replies()[0][1]
    assert "op-1" in content
    assert "修訂 3" in content
    assert "證據 0" in content
    assert "內容判定 1" in content


def test_show_refuses_an_operation_the_service_cannot_find():
    service = FakeOwnerReviewService()

    def missing_view(actor_id, operation_id):
        raise LookupError("missing")

    service.view = missing_view
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    _run(_command(_group(service), "show").callback(interaction, operation="op-missing"))
    assert "找不到" in interaction.replies()[0][1]
    _assert_private_reply(interaction.replies()[0][2])


# --- Real service (enabled once identity-service publishes the contract) ------

def test_real_service_round_trip_over_fake_database():
    """@description: The Owner group drives the real service over the fake database; list and show change no money state.
    @param: none
    @return: None.
    """
    from datetime import timedelta
    from bot.database.submission_repository import SubmissionRepository
    from bot.eligibility import DurationEvidence
    from bot.owner_review import OwnerReviewService
    from bot.review import ReviewSnapshot
    from bot.song_identity import VideoIdentity
    from bot.submission_workflow import SubmissionRequest
    from bot.version_identity import VersionAssignment
    from tests.intake_fixture import seed_intake
    from tests.submission_persistence_fake import FakeDatabase
    from bot.discord_owner_review import build_owner_group

    database = FakeDatabase()
    database.now = NOW.replace(tzinfo=None)
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, lambda: NOW, token_ttl=timedelta(minutes=5))
    video = VideoIdentity("Synth0001X1")
    seed_intake(repository, SubmissionRequest(f"https://youtu.be/{video.video_id}", "op-1", 101, 999,
                                              VersionAssignment(video), ReviewSnapshot(video), 0,
                                              DurationEvidence(video, 180, "synthetic-duration", True, True)))
    money_before = (list(database.state["credit_ledger"]), dict(database.state["users"]), dict(database.state["jb2_settlements"]))
    group = build_owner_group(lambda: service, OWNER_ID, GUILD_ID, lambda: NOW)

    listing = FakeInteraction(user_id=OWNER_ID, guild_id=GUILD_ID)
    _run(_command(group, "list").callback(listing))
    detail = FakeInteraction(user_id=OWNER_ID, guild_id=GUILD_ID)
    _run(_command(group, "show").callback(detail, operation="op-1"))

    assert any("op-1" in str(call[1]) for call in listing.followup.calls)
    assert any("op-1" in str(call[1]) for call in detail.followup.calls)
    assert (list(database.state["credit_ledger"]), dict(database.state["users"]), dict(database.state["jb2_settlements"])) == money_before


def _kwargs_for(command_name: str) -> dict:
    """@description: Provide valid arguments for each subcommand so gate tests exercise only the gate.
    @param command_name: Subcommand under test.
    @return: Keyword arguments accepted by that subcommand.
    """
    arguments = {
        "list": {},
        "show": {"operation": "op-1"},
        "approve": {"operation": "op-1", "citation": "ref-1", "reason": "理由"},
        "reject": {"operation": "op-1", "citation": "ref-1", "reason": "理由"},
        "confirm_version": {"operation": "op-1", "version_name": "rec-ref-1", "version_kind": "basic", "reason": "理由"},
        "reexamine": {"operation": "op-1", "version_name": "rec-ref-1", "version_kind": "basic", "reason": "理由"},
    }
    return arguments[command_name]
