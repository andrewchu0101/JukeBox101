"""Drive the Owner review path end to end: Discord command tree, real OwnerReviewService, real SubmissionRepository and the offline fake database.

Every scenario uses fake Discord interactions, synthetic submissions and the SQL-protocol fake; no MySQL, Discord connection, Gateway or secret is involved.
"""

import asyncio
import copy
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import discord
import pytest
from discord import app_commands

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.discord_owner_review import build_owner_group
from bot.eligibility import DurationEvidence
from bot.owner_review import OwnerReviewService
from bot.review import InvalidReviewTransition, ReviewSnapshot, ReviewStatus, StaleReviewVersion, ValidationError
from bot.reward_candidates import RewardCandidateStatus
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.intake_fixture import seed_intake
from tests.submission_persistence_fake import FakeDatabase
from tests.test_discord_owner_review import FakeInteraction

OWNER_ID = 1001
GUILD_ID = 2002
OTHER_GUILD_ID = 3003
STRANGER_ID = 4004
START = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)  # [synthetic now]: Fixed clock for previews, tokens and judgements.
TOKEN_TTL = timedelta(minutes=5)
SOURCE_INTERACTION_ID = 9001  # [provenance]: Discord interaction id that the version evidence records as its source.
CITATION_A = "人工核對紀錄 ABC-0001"  # [traceable source]: Manual-check reference for the first version claim.
CITATION_B = "人工核對紀錄 ABC-0002"  # [traceable source]: Different manual-check reference for a contradicting claim.
REFUSAL_PREFIX = "此指令僅限指定私人伺服器中的 Owner 使用"


def submission(number: int, seconds: int = 180) -> SubmissionRequest:
    """@description: Build one synthetic intake whose full-video duration is reliable and whose version is unresolved.
    @param number: Stable synthetic number used for the video, operation and submitter identifiers.
    @param seconds: Reliable full-video duration in seconds.
    @return: Repository intake request carrying no review approval.
    """
    video = VideoIdentity(f"TEST{number:07d}")  # [synthetic video]: Exactly eleven characters, as a YouTube identifier must be.
    duration = DurationEvidence(video, seconds, "synthetic-duration", True, True)
    return SubmissionRequest(f"https://youtu.be/{video.video_id}", f"op-{number}", 100 + number, 999,
                             VersionAssignment(video), ReviewSnapshot(video), 0, duration)


def seed(repository: SubmissionRepository, number: int, seconds: int = 180) -> None:
    """@description: Persist one fixture submission through the same transaction path as intake.
    @param repository: Real repository over the fake database.
    @param number: Synthetic submission number.
    @param seconds: Reliable full-video duration in seconds.
    @return: None.
    """
    seed_intake(repository, submission(number, seconds))


def seed_metadata_unavailable(repository: SubmissionRepository, number: int) -> None:
    """@description: Persist one intake whose metadata validation failed, which is the retryable VALIDATION_FAILED path.
    @param repository: Real repository over the fake database.
    @param number: Synthetic submission number.
    @return: None.
    """
    request = replace(submission(number), duration=None, validation_error=ValidationError.METADATA_UNAVAILABLE)
    seed_intake(repository, request)


def submitter_of(number: int) -> int:
    """@description: Name the Discord user who submitted the synthetic operation, which owns its balance row.
    @param number: Synthetic submission number.
    @return: Submitter Discord user identifier.
    """
    return 100 + number


def money_state(database: FakeDatabase, submitter_id: int) -> dict:
    """@description: Snapshot every table a reward or a refund could change, so an Owner action can be proven money-neutral.
    @param database: Fake database whose committed state is read.
    @param submitter_id: Submitter whose users row carries the credit balance.
    @return: Deep copy of ledger rows, the submitter's balance row, settlements and claims.
    """
    return copy.deepcopy({
        "credit_ledger": list(database.state["credit_ledger"].values()),  # [audit money]: Every award and refund row.
        "users": database.state["users"].get(submitter_id),  # [balance]: Holds credit_balance for the submitter.
        "jb2_settlements": dict(database.state["jb2_settlements"]),  # [settled rewards]: Durable award receipts.
        "jb2_claims": dict(database.state["jb2_claims"]),  # [version claims]: Links each reward to a version and ledger row.
    })


class Clock:
    """@description: Hold one fixed synthetic instant so token expiry and judgement stamps stay deterministic.
    @param: none
    @return: Callable clock returning the synthetic instant.
    """

    def __call__(self) -> datetime:
        """@description: Return the synthetic current instant.
        @param self: Fixed clock.
        @return: Timezone-aware instant.
        """
        return START


class CountingService:
    """@description: Count every service method invoked through the Discord layer while delegating to the real service.
    @param: none
    @return: Wrapper exposing the calls list and delegating every other attribute.
    """

    def __init__(self, inner: OwnerReviewService) -> None:
        """@description: Wrap the real service without changing its behavior.
        @param self: Counting wrapper.
        @param inner: Real OwnerReviewService.
        @return: None.
        """
        self._inner = inner  # [delegate]: The real service performs every operation.
        self.calls: list[str] = []  # [service trace]: Names of the methods the Discord layer invoked.

    def __getattr__(self, name: str):
        """@description: Return a delegating callable that records its own name before forwarding.
        @param self: Counting wrapper.
        @param name: Service attribute requested by the Discord layer.
        @return: Recording delegate for methods, or the raw attribute otherwise.
        """
        attribute = getattr(self._inner, name)
        if not callable(attribute):
            return attribute

        def recorded(*arguments, **keywords):
            self.calls.append(name)
            return attribute(*arguments, **keywords)

        return recorded


@pytest.fixture
def world():
    """@description: Build the fake database, real repository and real service under one fixed clock.
    @param: none
    @return: Tuple of database, repository and service.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)  # [fake UTC clock]: Matches the naive database clock.
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, Clock(), token_ttl=TOKEN_TTL)
    return database, repository, service


def command_group(service_provider):
    """@description: Build the Owner group and attach it to a local command tree, then fetch it back from that tree.
    @param service_provider: Returns the review service used by every command.
    @return: The Owner group as the command tree exposes it for the private guild.
    """
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))  # [local tree]: Never connects.
    guild = discord.Object(id=GUILD_ID)
    tree.add_command(build_owner_group(service_provider, OWNER_ID, GUILD_ID, Clock()), guild=guild)
    return tree.get_command("owner", guild=guild)


def invoke(group, name: str, interaction: FakeInteraction, **keywords) -> None:
    """@description: Run one subcommand callback the way discord.py dispatches it, to completion.
    @param group: Owner group under test.
    @param name: Subcommand name.
    @param interaction: Fake interaction supplied to the callback.
    @param keywords: Subcommand options.
    @return: None.
    """
    command = group.get_command(name)
    asyncio.run(command.callback(command.binding, interaction, **keywords))


def owner_command(group, name: str, *, source_id: int = SOURCE_INTERACTION_ID, **keywords) -> FakeInteraction:
    """@description: Issue one Owner command from the Owner's guild, tagging the interaction with its provenance id.
    @param group: Owner group under test.
    @param name: Subcommand name.
    @param source_id: Discord interaction id recorded as the evidence source.
    @param keywords: Subcommand options.
    @return: The interaction, holding every reply it received.
    """
    interaction = FakeInteraction(OWNER_ID, GUILD_ID)
    interaction.id = source_id
    invoke(group, name, interaction, **keywords)
    return interaction


def press_confirm(preview: FakeInteraction, *, user_id: int = OWNER_ID, guild_id: int = GUILD_ID) -> FakeInteraction:
    """@description: Press the Confirm button attached to a preview reply, from the given caller and guild.
    @param preview: Interaction whose last reply carries the confirmation view.
    @param user_id: Caller pressing the button.
    @param guild_id: Guild where the button is pressed.
    @return: The button press interaction, holding its replies.
    """
    last_reply_options = preview.replies()[-1][2]
    assert "view" in last_reply_options, f"preview issued no confirmation view; reply was: {texts(preview)}"
    view = last_reply_options["view"]
    pressed = FakeInteraction(user_id, guild_id)
    asyncio.run(view.confirm_button.callback(pressed))
    return pressed


def texts(interaction: FakeInteraction) -> list[str]:
    """@description: Collect the text content of every reply an interaction received, in order.
    @param interaction: Fake interaction that received replies.
    @return: Reply texts.
    """
    return [content for _, content, _ in interaction.replies() if content]


def approve_through_discord(group, number: int = 1) -> None:
    """@description: Approve one operation through the Owner command and its confirmation button.
    @param group: Owner group under test.
    @param number: Synthetic submission number.
    @return: None.
    """
    preview = owner_command(group, "approve", operation=f"op-{number}", citation=CITATION_A, reason="確認為歌曲")
    press_confirm(preview)


def confirm_version_through_discord(group, version_name: str, citation: str, source_id: int, number: int = 1) -> FakeInteraction:
    """@description: Confirm a recording version through the Owner command and its confirmation button.
    @param group: Owner group under test.
    @param version_name: Recording version reference the Owner names.
    @param citation: Manual-check reference recorded as the audit reason.
    @param source_id: Discord interaction id recorded as the evidence source.
    @param number: Synthetic submission number.
    @return: The confirmation button press.
    """
    preview = owner_command(group, "confirm_version", source_id=source_id, operation=f"op-{number}",
                            version_name=version_name, version_kind="basic", reason=citation, source=citation)
    return press_confirm(preview)


def reexamine_through_discord(group, version_name: str, citation: str, source_id: int, number: int = 1) -> FakeInteraction:
    """@description: Reopen an operation through the Owner reexamination command and its confirmation button.
    @param group: Owner group under test.
    @param version_name: Recording version reference cited by the reexamination evidence.
    @param citation: Manual-check reference recorded as the audit reason.
    @param source_id: Discord interaction id recorded as the evidence source.
    @param number: Synthetic submission number.
    @return: The confirmation button press.
    """
    preview = owner_command(group, "reexamine", source_id=source_id, operation=f"op-{number}",
                            version_name=version_name, version_kind="basic", reason=citation, source=citation)
    return press_confirm(preview)


def test_approve_through_discord_leaves_money_unchanged_and_settle_waits_for_identity(world):
    """@description: An approval through the Owner commands changes review status only; settlement waits for an unresolved identity.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    seed(repository, 1)
    group = command_group(lambda: service)
    money_before = money_state(database, submitter_of(1))

    approve_through_discord(group, 1)

    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.APPROVED
    assert money_state(database, submitter_of(1)) == money_before
    assert repository.settle("op-1").status is RewardCandidateStatus.WAITING_IDENTITY
    assert money_state(database, submitter_of(1)) == money_before
    assert not database.state["credit_ledger"]


def test_reject_through_discord_leaves_money_unchanged_and_settle_finds_it_ineligible(world):
    """@description: A rejection through the Owner commands changes review status only and can never become a reward.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    seed(repository, 1)
    group = command_group(lambda: service)
    money_before = money_state(database, submitter_of(1))
    preview = owner_command(group, "reject", operation="op-1", citation=CITATION_A, reason="不是歌曲")

    press_confirm(preview)

    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.REJECTED
    assert money_state(database, submitter_of(1)) == money_before
    assert repository.settle("op-1").status is RewardCandidateStatus.INELIGIBLE
    assert money_state(database, submitter_of(1)) == money_before


def test_confirm_version_through_discord_stamps_identity_and_the_disabled_switch_blocks_payout(world):
    """@description: Confirming a version through Discord stamps the identity, moves no money, and settlement stays blocked while payouts are off.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    seed(repository, 1)
    database.state["control"]["settlement_enabled"] = False  # [payout switch]: Off for this test, so settle cannot write a ledger row.
    group = command_group(lambda: service)
    approve_through_discord(group, 1)
    money_before = money_state(database, submitter_of(1))

    confirm_version_through_discord(group, "orig-A", CITATION_A, SOURCE_INTERACTION_ID)

    view = service.view(OWNER_ID, "op-1")
    assert view.identity.status is VersionIdentityStatus.CONFIRMED
    assert view.identity.version.value == "orig-A"
    assert view.review.status is ReviewStatus.APPROVED
    assert money_state(database, submitter_of(1)) == money_before
    # [decision READY]: Settle raises only for READY with the switch off, so the raise proves the decision was READY.
    with pytest.raises(PermissionError):
        repository.settle("op-1")
    assert money_state(database, submitter_of(1)) == money_before
    assert not database.state["credit_ledger"]


def test_conflicting_second_version_freezes_identity_and_the_ordinary_review_path_cannot_pay(world):
    """@description: A second, contradicting version confirmation freezes the identity as CONFLICT, and the ordinary review path cannot stamp it as payable.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    seed(repository, 1)
    group = command_group(lambda: service)
    approve_through_discord(group, 1)
    confirm_version_through_discord(group, "orig-A", CITATION_A, SOURCE_INTERACTION_ID)
    money_before = money_state(database, submitter_of(1))

    confirm_version_through_discord(group, "cover-B", CITATION_B, SOURCE_INTERACTION_ID + 1)

    view = service.view(OWNER_ID, "op-1")
    assert view.identity.status is VersionIdentityStatus.CONFLICT
    assert money_state(database, submitter_of(1)) == money_before
    assert repository.settle("op-1").status is RewardCandidateStatus.CONFLICT
    assert money_state(database, submitter_of(1)) == money_before

    video = submission(1).identity.video
    ordinary = ReviewCommand("ordinary-claim-b", "op-1", OWNER_ID, "ordinary review path", view.review.version,
                             identity=VersionAssignment(video, VersionIdentityStatus.CONFIRMED,
                                                        RecordingVersionIdentity("cover-B"), "ev-ordinary"))
    refused = False
    try:
        repository.review(ordinary)
    except (ValueError, PermissionError, InvalidReviewTransition, StaleReviewVersion):
        refused = True
    assert refused or service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFLICT
    assert money_state(database, submitter_of(1)) == money_before
    assert repository.settle("op-1").status is RewardCandidateStatus.CONFLICT
    assert not database.state["credit_ledger"]


def test_reexamination_contradicting_the_confirmed_version_freezes_without_money(world):
    """@description: A formal reexamination whose evidence names a different version marks the review and freezes the reward with no money written.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    seed(repository, 1)
    group = command_group(lambda: service)
    approve_through_discord(group, 1)
    confirm_version_through_discord(group, "orig-A", CITATION_A, SOURCE_INTERACTION_ID)
    money_before = money_state(database, submitter_of(1))

    reexamine_through_discord(group, "cover-B", CITATION_B, SOURCE_INTERACTION_ID + 1)

    view = service.view(OWNER_ID, "op-1")
    assert view.review.reexamination_pending is True
    assert view.identity.status is VersionIdentityStatus.CONFLICT
    assert money_state(database, submitter_of(1)) == money_before
    assert repository.settle("op-1").status in {RewardCandidateStatus.CONFLICT, RewardCandidateStatus.WAITING_VALIDATION}
    assert money_state(database, submitter_of(1)) == money_before
    assert not database.state["credit_ledger"]


def test_reexamination_agreeing_with_the_confirmed_version_reopens_for_validation_without_money(world):
    """@description: A reexamination that agrees with the confirmed version reopens the review and holds the reward for validation with no money written.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    seed(repository, 1)
    group = command_group(lambda: service)
    approve_through_discord(group, 1)
    confirm_version_through_discord(group, "orig-A", CITATION_A, SOURCE_INTERACTION_ID)
    money_before = money_state(database, submitter_of(1))

    reexamine_through_discord(group, "orig-A", CITATION_B, SOURCE_INTERACTION_ID + 1)

    view = service.view(OWNER_ID, "op-1")
    assert view.review.reexamination_pending is True
    assert view.review.status is not ReviewStatus.APPROVED
    assert money_state(database, submitter_of(1)) == money_before
    assert repository.settle("op-1").status is RewardCandidateStatus.WAITING_VALIDATION
    assert money_state(database, submitter_of(1)) == money_before
    assert not database.state["credit_ledger"]


def test_lost_confirmation_reply_replays_the_original_result_with_one_write_and_no_money(world):
    """@description: A confirmation whose reply is lost reports an unknown outcome, and the retry replays the original result with exactly one write.
    @param world: Offline database, repository and service.
    @return: None.
    """
    database, repository, service = world
    seed(repository, 1)
    group = command_group(lambda: service)
    approve_through_discord(group, 1)
    preview = owner_command(group, "confirm_version", operation="op-1", version_name="orig-A", version_kind="basic",
                            reason=CITATION_A, source=CITATION_A)
    money_before = money_state(database, submitter_of(1))
    decisions_before = len(database.state["jb2_decisions"])
    revision_before = database.state["control"]["revision"]

    database.commit_fault = "after"  # [injected ambiguity]: The commit lands but its acknowledgement is lost.
    lost = press_confirm(preview)
    database.commit_fault = None
    retried = press_confirm(preview)

    assert any("結果未知" in text for text in texts(lost))
    assert any("已確認" in text for text in texts(retried))
    assert len(database.state["jb2_decisions"]) == decisions_before + 1
    assert database.state["control"]["revision"] == revision_before + 1
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFIRMED
    assert money_state(database, submitter_of(1)) == money_before


def test_keyset_pages_see_every_operation_exactly_once_when_a_submission_arrives_between_requests(world):
    """@description: Paging the Owner queue by keyset cursor shows every original operation exactly once, even when a newer submission lands between two page requests.
    @param world: Offline database, repository and service.
    @return: None.
    """
    _, repository, service = world
    for number in range(1, 23):
        seed(repository, number)
    first_page = service.list_pending(OWNER_ID, after_sequence=None, limit=20)

    seed(repository, 23)
    second_page = service.list_pending(OWNER_ID, after_sequence=first_page.next_after_sequence, limit=20)

    seen = [item.operation_id for item in (*first_page.items, *second_page.items)]
    assert seen == [f"op-{number}" for number in range(1, 24)]
    assert second_page.next_after_sequence is None


def test_discord_list_first_page_matches_the_keyset_first_page(world):
    """@description: The Discord list reply shows the same first page of pending operations that the keyset cursor returns.
    @param world: Offline database, repository and service.
    @return: None.
    """
    _, repository, service = world
    for number in range(1, 23):
        seed(repository, number)
    first_page = service.list_pending(OWNER_ID, after_sequence=None, limit=20)
    listing = owner_command(command_group(lambda: service), "list")

    shown = re.findall(r"・(op-\d+)｜", "\n".join(texts(listing)))

    assert shown == [item.operation_id for item in first_page.items]


def test_validation_failed_submission_is_listed_for_the_owner(world):
    """@description: A submission that failed duration validation stays visible in the Owner's pending list.
    @param world: Offline database, repository and service.
    @return: None.
    """
    _, repository, service = world
    seed_metadata_unavailable(repository, 1)
    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.VALIDATION_FAILED

    assert "op-1" in [item.operation_id for item in service.list_pending(OWNER_ID).items]


def test_approved_submission_with_unresolved_identity_is_listed_for_the_owner(world):
    """@description: An approved submission whose recording identity is still unresolved stays visible to the Owner.
    @param world: Offline database, repository and service.
    @return: None.
    """
    _, repository, service = world
    seed(repository, 1)
    approve_through_discord(command_group(lambda: service), 1)
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.UNRESOLVED

    assert "op-1" in [item.operation_id for item in service.list_pending(OWNER_ID).items]


@pytest.mark.parametrize(("caller_id", "guild_id"), [(OWNER_ID, OTHER_GUILD_ID), (STRANGER_ID, GUILD_ID)],
                         ids=["wrong-guild", "non-owner"])
def test_show_is_refused_before_any_service_call_or_database_read(world, caller_id, guild_id):
    """@description: A wrong-guild or non-Owner show command is refused before the service or the database is touched.
    @param world: Offline database, repository and service.
    @param caller_id: Discord user who issues the command.
    @param guild_id: Guild where the command is issued.
    @return: None.
    """
    database, repository, service = world
    seed(repository, 1)
    counting = CountingService(service)
    group = command_group(lambda: counting)
    statements_before = len(database.statements)
    interaction = FakeInteraction(caller_id, guild_id)

    invoke(group, "show", interaction, operation="op-1")

    assert counting.calls == []
    assert len(database.statements) == statements_before
    assert any(text.startswith(REFUSAL_PREFIX) for text in texts(interaction))
