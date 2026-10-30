"""Verify the automatic mode switches, the health policy, the /share and /history replies, and the Discord duration entry, offline.

The Discord layer uses the shared fake interaction helpers and the repository runs over the offline fake database.
"""

import asyncio

import pytest

from bot.auto_pipeline import PipelineOutcome, format_share_outcome, reward_label
from bot.auto_startup import build_auto_pipeline, load_auto_settings
from bot.config import ConfigurationError, load_trusted_channel_ids
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.owner_review import ExpiredReviewToken, OwnerReviewService
from bot.readiness import readiness_problems
from bot.review import ReviewStatus
from bot.reward_candidates import RewardCandidateStatus, RewardDecision, RewardDecisionReason
from bot.services import MusicService
from tests.test_owner_e2e_acceptance import (GUILD_ID, OWNER_ID, command_group, owner_command, press_confirm, seed_metadata_unavailable, texts, world)  # noqa: F401

CHANNEL = "UC" + "a" * 22
IDENTITY = {"is_superuser": False}
CONTROL = {"schema_version": "2b2-b1", "snapshot_ready": True, "legacy_writers_quiesced": True}


def test_every_automatic_mode_is_off_by_default_and_builds_no_pipeline() -> None:
    """@description: An untouched deployment keeps today's behaviour, with no credential read.
    @return: None.
    """
    settings = load_auto_settings({})
    assert not settings.review_enabled and not settings.settlement_enabled and settings.trusted_channel_ids == frozenset()
    assert build_auto_pipeline(None, settings) is None


def test_automatic_review_without_a_credential_refuses_to_start() -> None:
    """@description: A switched-on mode fails closed, naming the setting only.
    @return: None.
    """
    with pytest.raises(ConfigurationError, match="YOUTUBE_API_KEY"):
        load_auto_settings({"JUKEBOX_AUTO_REVIEW": "on"})


@pytest.mark.parametrize("value", ["maybe", "2"])
def test_an_unrecognised_switch_value_refuses_to_start(value) -> None:
    """@description: Typing errors never open a route.
    @param value: Invalid switch text.
    @return: None.
    """
    with pytest.raises(ConfigurationError):
        load_auto_settings({"JUKEBOX_AUTO_SETTLEMENT": value})


@pytest.mark.parametrize("entry", ["Some Channel", "@handle", "UCshort", "https://youtube.com/channel/" + CHANNEL])
def test_the_allowlist_refuses_anything_but_a_stable_channel_id(entry) -> None:
    """@description: Display names and handles are not identities.
    @param entry: Invalid allowlist entry.
    @return: None.
    """
    with pytest.raises(ConfigurationError):
        load_trusted_channel_ids({"YOUTUBE_TRUSTED_CHANNEL_IDS": entry})


def test_the_credential_never_appears_in_a_representation() -> None:
    """@description: Settings may be logged by accident; the key must not be.
    @return: None.
    """
    settings = load_auto_settings({"JUKEBOX_AUTO_REVIEW": "on", "YOUTUBE_API_KEY": "secret-value"})
    assert "secret-value" not in repr(settings)
    assert "secret-value" not in repr(build_auto_pipeline(None, settings)._metadata_client)


@pytest.mark.parametrize("flag,mode,expected", [
    (False, False, []), (True, False, ["SETTLEMENT_ENABLED"]), (True, True, []), (False, True, ["SETTLEMENT_SWITCH_OFF"])])
def test_health_requires_the_database_flag_and_the_deployment_mode_to_agree(flag, mode, expected) -> None:
    """@description: Neither side alone can open or silently stall settlement.
    @param flag: Database settlement flag.
    @param mode: Deployment automatic settlement mode.
    @param expected: Problem codes.
    @return: None.
    """
    assert readiness_problems(IDENTITY, dict(CONTROL, settlement_enabled=flag), mode) == expected


def outcome(status: RewardCandidateStatus, reason: RewardDecisionReason, review=ReviewStatus.APPROVED, **flags) -> PipelineOutcome:
    """@description: Build one outcome for reply checks.
    @param status: Reward status.
    @param reason: Reward reason.
    @param review: Review status.
    @param flags: Outcome flags.
    @return: Outcome.
    """
    return PipelineOutcome(review, RewardDecision("op", status, reason), **flags)


@pytest.mark.parametrize("status,reason,phrase", [
    (RewardCandidateStatus.WAITING_IDENTITY, RewardDecisionReason.IDENTITY_UNRESOLVED, "錄音版本"),
    (RewardCandidateStatus.WAITING_PREDECESSOR, RewardDecisionReason.EARLIER_SUBMISSION, "較早"),
    (RewardCandidateStatus.CONFLICT, RewardDecisionReason.EVIDENCE_CONFLICT, "衝突"),
    (RewardCandidateStatus.INELIGIBLE, RewardDecisionReason.VERSION_ALREADY_AWARDED, "重複"),
    (RewardCandidateStatus.READY, RewardDecisionReason.FIRST_ELIGIBLE, "尚未開啟")])
def test_a_reply_names_the_waiting_condition_and_never_claims_an_award(status, reason, phrase) -> None:
    """@description: Each blocked state has its own honest sentence.
    @param status: Reward status.
    @param reason: Reward reason.
    @param phrase: Words the reply must contain.
    @return: None.
    """
    reply = format_share_outcome("discord:1", outcome(status, reason))
    assert phrase in reply and "+1" not in reply


def test_only_a_committed_award_reply_says_plus_one() -> None:
    """@description: The award sentence comes only from the AWARDED state.
    @return: None.
    """
    assert "+1" in format_share_outcome("discord:1", outcome(RewardCandidateStatus.AWARDED, RewardDecisionReason.PREVIOUSLY_AWARDED))


def test_an_unknown_settlement_commit_says_unconfirmed_not_paid() -> None:
    """@description: A lost acknowledgement is reported as unknown.
    @return: None.
    """
    reply = format_share_outcome("discord:1", outcome(RewardCandidateStatus.READY, RewardDecisionReason.FIRST_ELIGIBLE, settlement_unknown=True))
    assert "未確認" in reply and "+1" not in reply


def test_unknown_stored_reward_values_are_not_echoed() -> None:
    """@description: /history never prints an unrecognised database value.
    @return: None.
    """
    assert reward_label("<script>") == "獎勵待確認"
    assert reward_label(None) == "獎勵待確認"
    assert reward_label("awarded") == "已發放 +1"


class StubPipeline:
    """Fail or answer the automatic step, so the accepted submission's reply can be observed."""

    def __init__(self, error=None) -> None:
        """@description: Store the failure to raise.
        @param self: Stub.
        @param error: Exception, or None.
        @return: None.
        """
        self.error = error

    def process(self, operation_id: str) -> PipelineOutcome:
        """@description: Raise the failure or return a waiting outcome.
        @param self: Stub.
        @param operation_id: Operation.
        @return: Outcome.
        """
        if self.error is not None:
            raise self.error
        return outcome(RewardCandidateStatus.WAITING_IDENTITY, RewardDecisionReason.IDENTITY_UNRESOLVED)


class StubIntake:
    """Accept every submission."""

    def accept_pending(self, discord_user_id: int, source_url: str, operation_id: str) -> object:
        """@description: Pretend the receipt committed.
        @param self: Stub.
        @param discord_user_id: Submitter.
        @param source_url: URL.
        @param operation_id: Operation.
        @return: Unused receipt.
        """
        return object()


def test_a_failed_automatic_step_keeps_the_receipt_and_asks_not_to_resubmit() -> None:
    """@description: Failure after intake never reads as a failed submission or as an award.
    @return: None.
    """
    service = MusicService(None, StubIntake(), auto_pipeline=StubPipeline(CommitOutcomeUnknown("lost")))
    reply = asyncio.run(service.share(1, "https://youtu.be/aaaaaaaaaaa", "discord:1"))
    assert "已儲存" in reply and "勿重新投稿" in reply and "+1" not in reply


def test_share_uses_the_pipeline_reply_only_when_a_pipeline_exists() -> None:
    """@description: The pipeline changes the reply text; no pipeline means the earlier reply path.
    @return: None.
    """
    service = MusicService(None, StubIntake(), auto_pipeline=StubPipeline())
    assert "錄音版本" in asyncio.run(service.share(1, "https://youtu.be/aaaaaaaaaaa", "discord:1"))


def owner_service(repository, clock_now=None):
    """@description: Build the review service over the shared offline fixture.
    @param repository: Fake-backed repository.
    @param clock_now: Unused placeholder for symmetry.
    @return: Service.
    """
    from tests.test_owner_e2e_acceptance import Clock
    return OwnerReviewService(repository, OWNER_ID, Clock())


def test_owner_duration_previews_then_confirms_in_discord(world) -> None:  # noqa: F811
    """@description: The Owner supplies a duration from Discord; nothing changes until Confirm.
    @param world: Offline fixture.
    @return: None.
    """
    repository = world[1] if isinstance(world, tuple) else world.repository
    seed_metadata_unavailable(repository, 1)
    service = owner_service(repository)
    group = command_group(lambda: service)
    preview = owner_command(group, "duration", operation="op-1", seconds=189, source="https://example.test/manual-check", reason="人工補時長")
    assert "189" in texts(preview)[-1]
    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.VALIDATION_FAILED
    assert "待審核" in texts(press_confirm(preview))[-1]
    assert service.view(OWNER_ID, "op-1").review.duration.seconds == 189


def test_a_non_owner_cannot_preview_or_confirm_a_duration(world) -> None:  # noqa: F811
    """@description: The gate runs first on both the command and the button.
    @param world: Offline fixture.
    @return: None.
    """
    repository = world[1] if isinstance(world, tuple) else world.repository
    seed_metadata_unavailable(repository, 1)
    service = owner_service(repository)
    group = command_group(lambda: service)
    preview = owner_command(group, "duration", operation="op-1", seconds=189, source="https://example.test/manual-check", reason="人工補時長")
    pressed = press_confirm(preview, user_id=OWNER_ID + 1)
    assert "僅限" in texts(pressed)[-1]
    assert "僅限" in texts(press_confirm(preview, guild_id=GUILD_ID + 1))[-1]
    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.VALIDATION_FAILED


def test_an_expired_duration_preview_writes_nothing(world) -> None:  # noqa: F811
    """@description: A token past its deadline is refused.
    @param world: Offline fixture.
    @return: None.
    """
    from datetime import timedelta
    from tests.test_owner_e2e_acceptance import START
    repository = world[1] if isinstance(world, tuple) else world.repository
    seed_metadata_unavailable(repository, 1)
    moments = [START]
    service = OwnerReviewService(repository, OWNER_ID, lambda: moments[0], token_ttl=timedelta(minutes=5))
    preview = service.preview_duration(OWNER_ID, "op-1", 189, "https://example.test/manual-check", "人工補時長")
    moments[0] = START + timedelta(minutes=5)
    with pytest.raises(ExpiredReviewToken):
        service.confirm(OWNER_ID, preview.token_id)
    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.VALIDATION_FAILED


def test_a_failed_settlement_followup_does_not_hide_the_committed_decision(world) -> None:  # noqa: F811
    """@description: The decision committed; the reply says settlement is pending rather than reporting failure or an award.
    @param world: Offline fixture.
    @return: None.
    """
    from tests.test_owner_e2e_acceptance import Clock

    def failing(operation_id: str) -> str:
        """@description: Simulate a settlement failure.
        @param operation_id: Operation.
        @return: Never.
        """
        raise CommitOutcomeUnknown("lost")

    repository = world[1] if isinstance(world, tuple) else world.repository
    seed_metadata_unavailable(repository, 1)
    service = OwnerReviewService(repository, OWNER_ID, Clock(), settlement_followup=failing)
    group = command_group(lambda: service)
    preview = owner_command(group, "duration", operation="op-1", seconds=189, source="https://example.test/manual-check", reason="人工補時長")
    reply = texts(press_confirm(preview))[-1]
    assert "已確認" in reply and "自動結算尚未完成" in reply and "+1" not in reply.replace("不會重複", "")
