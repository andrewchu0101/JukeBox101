"""Real PostgreSQL behaviour of automatic review and settlement, run against disposable PG17 and PG18 servers.

Metadata is a scripted fake at the provider boundary; review, identity, ledger, balance, claims and receipts are the production tables.
"""

import threading
from datetime import datetime, timedelta, timezone
from functools import partial

import pytest

from bot.auto_pipeline import AutoPipeline, format_share_outcome
from bot.database.connection import open_connection
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown, PersistenceNotReady
from bot.evidence_record import EvidenceSourceType, record_evidence
from bot.ledger_integrity import STATUS_PASS, evaluate_ledger_integrity
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import ApprovalSource, ReviewStatus
from bot.reward_candidates import RewardCandidateStatus, RewardDecisionReason
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus
from bot.youtube_metadata import LookupStatus, MetadataLookup, VideoMetadata
from tests.postgres_support import admin_query

pytestmark = pytest.mark.postgres

OWNER_ID = 900001
ALICE, BOB = 500001, 500002
TRUSTED = "UC" + "a" * 22
NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
VIDEO_A, VIDEO_B, VIDEO_C = "aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"


def url(video_id: str) -> str:
    """@description: Build a share URL for a synthetic video.
    @param video_id: Eleven-character video identifier.
    @return: Watch URL.
    """
    return f"https://www.youtube.com/watch?v={video_id}"


class FakeMetadata:
    """Answer lookups from a per-video script and count how often the provider was asked."""

    def __init__(self, **overrides) -> None:
        """@description: Store the outcome per video; unscripted videos are trusted music of 189 seconds.
        @param self: Fake provider.
        @param overrides: Video identifier to scripted lookup.
        @return: None.
        """
        self.script = dict(overrides)
        self.calls = []

    def lookup(self, video_id: str) -> MetadataLookup:
        """@description: Answer one lookup.
        @param self: Fake provider.
        @param video_id: Requested video.
        @return: Scripted or default lookup.
        """
        self.calls.append(video_id)
        return self.script.get(video_id) or found(video_id)


def found(video_id: str, channel: str = TRUSTED, seconds: int = 189) -> MetadataLookup:
    """@description: Build one found lookup.
    @param video_id: Video identifier.
    @param channel: Channel identifier.
    @param seconds: Duration.
    @return: Lookup.
    """
    return MetadataLookup(LookupStatus.FOUND, VideoMetadata(video_id, channel, "10", "Song", "none", False, "processed", "public", seconds, NOW))


class World:
    """Hold one disposable database with the automatic mode switched on for it only."""

    def __init__(self, database) -> None:
        """@description: Build repository, Owner service and pipeline against the app role.
        @param self: World.
        @param database: Disposable database.
        @return: None.
        """
        self.database = database
        self.repository = SubmissionRepository(partial(open_connection, database.app), OWNER_ID, automatic_settlement=True)
        self.metadata = FakeMetadata()
        self.pipeline = self.new_pipeline()
        self.owner = OwnerReviewService(self.repository, OWNER_ID, lambda: NOW, settlement_followup=self.pipeline.owner_settlement_note)
        admin_query(database, "UPDATE jb2_control SET settlement_enabled = TRUE")

    def new_pipeline(self, repository=None) -> AutoPipeline:
        """@description: Build a pipeline as a fresh process would.
        @param self: World.
        @param repository: Optional repository override.
        @return: Pipeline.
        """
        return AutoPipeline(repository or self.repository, self.metadata, frozenset({TRUSTED}), settlement_enabled=True)

    def share(self, user: int, video_id: str, operation_id: str):
        """@description: Accept one share and run the pipeline, as /share does.
        @param self: World.
        @param user: Submitter.
        @param video_id: Video.
        @param operation_id: Operation key.
        @return: Pipeline outcome.
        """
        self.repository.accept_pending(user, url(video_id), operation_id)
        return self.pipeline.process(operation_id)

    def confirm_version(self, operation_id: str, version: str):
        """@description: Run the Owner's version preview and confirmation.
        @param self: World.
        @param operation_id: Operation.
        @param version: Version reference.
        @return: Review snapshot.
        """
        projection = self.owner.view(OWNER_ID, operation_id)
        evidence = record_evidence(f"ev-{operation_id}", projection.review.video, EvidenceSourceType.OWNER_REVIEW,
                                   "https://example.test/manual-check", NOW, "人工核對", OWNER_ID, RecordingVersionIdentity(version))
        preview = self.owner.preview_version_confirmation(OWNER_ID, operation_id, evidence, "人工核對", projection.review.version)
        return self.owner.confirm(OWNER_ID, preview.token_id)

    def balances(self) -> dict:
        """@description: Read every balance.
        @param self: World.
        @return: User to balance.
        """
        return {row["discord_user_id"]: row["credit_balance"] for row in admin_query(self.database, "SELECT discord_user_id, credit_balance FROM users")}

    def assert_books_balance(self, expected_awards: int) -> None:
        """@description: Check the ledger, claims, receipts and balances tell the same story.
        @param self: World.
        @param expected_awards: Number of awards that must exist.
        @return: None.
        """
        users = admin_query(self.database, "SELECT discord_user_id, credit_balance FROM users")
        ledger = admin_query(self.database, "SELECT ledger_id, discord_user_id, credit_delta, reason, awarded_song_id FROM credit_ledger")
        claims = admin_query(self.database, "SELECT version_id, ledger_id, discord_user_id FROM jb2_claims")
        receipts = admin_query(self.database, "SELECT operation_id FROM jb2_settlements")
        assert evaluate_ledger_integrity(users, ledger, claims).status == STATUS_PASS
        assert len(ledger) == len(claims) == len(receipts) == expected_awards
        assert sum(row["credit_balance"] for row in users) == expected_awards


@pytest.fixture
def world(pg_database) -> World:
    """@description: Provide one automatic-mode world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return World(pg_database)


def test_trusted_video_is_approved_by_the_automatic_source_and_waits_for_a_version(world) -> None:
    """@description: Official metadata approves without any Owner identity, but cannot prove a recording version, so no Nickel moves.
    @param world: Automatic-mode database.
    @return: None.
    """
    outcome = world.share(ALICE, VIDEO_A, "discord:1")
    assert outcome.review_status is ReviewStatus.APPROVED
    assert outcome.decision.status is RewardCandidateStatus.WAITING_IDENTITY
    view = world.owner.view(OWNER_ID, "discord:1")
    assert view.review.approval_source is ApprovalSource.AUTO and view.review.duration.seconds == 189
    assert view.identity.status is VersionIdentityStatus.UNRESOLVED
    assert OWNER_ID not in {row["actor_id"] for row in admin_query(world.database, "SELECT actor_id FROM jb2_review_history")}
    world.assert_books_balance(0)
    assert "版本" in format_share_outcome("discord:1", outcome) and "+1" not in format_share_outcome("discord:1", outcome)


def test_owner_version_confirmation_settles_once_and_says_so_only_after_commit(world) -> None:
    """@description: Completing the last condition in Discord pays exactly one Nickel; replay and resettling pay nothing more.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "rec-A")
    note = world.owner.settlement_note(OWNER_ID, "discord:1")
    assert "+1" in note
    assert world.balances()[ALICE] == 1
    world.pipeline.settle_ready()
    assert world.owner.settlement_note(OWNER_ID, "discord:1") == note
    world.assert_books_balance(1)


def test_same_version_on_another_video_and_a_resubmission_pay_nothing(world) -> None:
    """@description: The MV and Audio of one recording share one reward; re-sharing a video adds no reward either.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "rec-A")
    world.pipeline.settle_ready()
    world.share(BOB, VIDEO_B, "discord:2")
    world.confirm_version("discord:2", "rec-A")
    outcome = world.pipeline.process("discord:2")
    assert outcome.decision.status is RewardCandidateStatus.INELIGIBLE
    assert outcome.decision.reason in {RewardDecisionReason.VERSION_ALREADY_AWARDED, RewardDecisionReason.HISTORICAL_VERSION}
    resubmitted = world.share(BOB, VIDEO_A, "discord:3")
    assert resubmitted.decision.status is RewardCandidateStatus.INELIGIBLE
    assert world.balances()[BOB] == 0
    world.assert_books_balance(1)
    assert "+1" not in format_share_outcome("discord:3", resubmitted)


def test_replaying_the_same_share_after_an_award_pays_nothing_more(world) -> None:
    """@description: Discord retrying one interaction returns the original receipt and leaves the books alone.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "rec-A")
    world.pipeline.settle_ready()
    assert world.share(ALICE, VIDEO_A, "discord:1").decision.status is RewardCandidateStatus.AWARDED
    assert world.balances()[ALICE] == 1
    world.assert_books_balance(1)


def test_an_earlier_unknown_candidate_blocks_later_awards_until_it_is_resolved(world) -> None:
    """@description: A later confirmed song waits for the earlier unknown-version submission, and resolving it wakes the blocked candidate.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.share(BOB, VIDEO_B, "discord:2")
    world.confirm_version("discord:2", "rec-B")
    blocked = world.pipeline.process("discord:2")
    assert blocked.decision.status is RewardCandidateStatus.WAITING_PREDECESSOR
    assert blocked.decision.blocking_operation_ids == ("discord:1",)
    assert "較早" in format_share_outcome("discord:2", blocked)
    world.confirm_version("discord:1", "rec-A")
    world.owner.settlement_note(OWNER_ID, "discord:1")
    assert world.balances() == {ALICE: 1, BOB: 1}
    world.assert_books_balance(2)


def test_two_users_settling_one_version_concurrently_pay_exactly_once(world) -> None:
    """@description: Separate connections racing to settle the same version serialize on the gate and produce one award.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.share(BOB, VIDEO_B, "discord:2")
    world.confirm_version("discord:1", "rec-A")
    world.confirm_version("discord:2", "rec-A")
    barrier = threading.Barrier(4)

    def settle_concurrently() -> None:
        """@description: Start every worker together.
        @param: none
        @return: None.
        """
        barrier.wait()
        world.new_pipeline().settle_ready()

    workers = [threading.Thread(target=settle_concurrently) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert world.balances() == {ALICE: 1, BOB: 0}
    world.assert_books_balance(1)


def test_concurrent_shares_of_one_new_video_write_one_automatic_review(world) -> None:
    """@description: Both pipelines may fetch, but the stale one applies nothing.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.repository.accept_pending(ALICE, url(VIDEO_A), "discord:1")
    world.repository.accept_pending(BOB, url(VIDEO_A), "discord:2")
    barrier = threading.Barrier(2)

    def process(operation_id: str) -> None:
        """@description: Run one pipeline after the barrier.
        @param operation_id: Operation.
        @return: None.
        """
        barrier.wait()
        world.new_pipeline().process(operation_id)

    workers = [threading.Thread(target=process, args=(op,)) for op in ("discord:1", "discord:2")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert world.owner.view(OWNER_ID, "discord:1").review.status is ReviewStatus.APPROVED
    revisions = admin_query(world.database, "SELECT revision FROM jb2_review_history ORDER BY revision")
    assert len(revisions) == len({row["revision"] for row in revisions})


class LostCommitConnection:
    """Commit for real, then report failure for the armed number of commits, like a lost acknowledgement."""

    def __init__(self, connection, faults: list) -> None:
        """@description: Wrap one real connection.
        @param self: Proxy.
        @param connection: Real connection.
        @param faults: Shared remaining-fault list.
        @return: None.
        """
        self._connection = connection
        self._faults = faults

    def __getattr__(self, name: str):
        """@description: Delegate everything else.
        @param self: Proxy.
        @param name: Attribute.
        @return: Real attribute.
        """
        return getattr(self._connection, name)

    def commit(self) -> None:
        """@description: Commit, then lose the acknowledgement while faults remain.
        @param self: Proxy.
        @return: None.
        """
        self._connection.commit()
        if self._faults:
            self._faults.pop()
            raise OSError("acknowledgement lost")


def test_a_lost_commit_acknowledgement_replays_the_receipt_and_never_pays_twice(world) -> None:
    """@description: The award committed but its reply was lost; the same key finds the durable receipt.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "rec-A")
    faults = []
    factory = lambda: LostCommitConnection(open_connection(world.database.app), faults)  # noqa: E731
    repository = SubmissionRepository(factory, OWNER_ID, automatic_settlement=True)
    faults.append(True)
    with pytest.raises(CommitOutcomeUnknown):
        repository.settle("discord:1")
    assert world.balances()[ALICE] == 1
    assert repository.settle("discord:1").status is RewardCandidateStatus.AWARDED
    world.assert_books_balance(1)


def test_the_pipeline_recovers_a_lost_settlement_acknowledgement_by_itself(world) -> None:
    """@description: One lost acknowledgement inside the pipeline is replayed under the same operation, and the outcome is the award.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "rec-A")
    faults = [True]
    factory = lambda: LostCommitConnection(open_connection(world.database.app), faults)  # noqa: E731
    pipeline = world.new_pipeline(SubmissionRepository(factory, OWNER_ID, automatic_settlement=True))
    assert pipeline.process("discord:1").decision.status is RewardCandidateStatus.AWARDED
    world.assert_books_balance(1)


def test_a_restarted_process_settles_what_the_previous_one_left_ready(world) -> None:
    """@description: A crash between the decision commit and settlement is repaired by the startup sweep, once.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.confirm_version("discord:1", "rec-A")  # [crash point]: No settlement ran after this commit.
    assert world.balances()[ALICE] == 0
    world.new_pipeline().sweep()
    world.new_pipeline().sweep()
    assert world.balances()[ALICE] == 1
    world.assert_books_balance(1)


def test_a_sweep_retries_a_failed_lookup_and_then_approves(world) -> None:
    """@description: An API failure leaves the case pending with no write, and a later sweep finishes it.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.metadata.script[VIDEO_A] = MetadataLookup(LookupStatus.TEMPORARY_FAILURE, None)
    outcome = world.share(ALICE, VIDEO_A, "discord:1")
    assert outcome.review_status is ReviewStatus.PENDING_VALIDATION and outcome.retry_later
    assert world.owner.view(OWNER_ID, "discord:1").review.version == 0
    world.metadata.script.clear()
    world.new_pipeline().sweep()
    assert world.owner.view(OWNER_ID, "discord:1").review.status is ReviewStatus.APPROVED


def test_an_untrusted_channel_records_duration_and_waits_for_owner_approval(world) -> None:
    """@description: Duration needs no VPS step; the song decision stays with the Owner, and approval then flows to settlement.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.metadata.script[VIDEO_A] = found(VIDEO_A, channel="UC" + "z" * 22)
    outcome = world.share(ALICE, VIDEO_A, "discord:1")
    assert outcome.review_status is ReviewStatus.PENDING_REVIEW
    view = world.owner.view(OWNER_ID, "discord:1")
    judgement = ContentJudgement(VideoIdentity(VIDEO_A), OWNER_ID, "確認為歌曲", NOW, "https://example.test/manual-check", True)
    preview = world.owner.preview(OWNER_ID, "discord:1", ReviewAction.APPROVE, "確認為歌曲", view.review.version, judgement.evidence_reference, judgement)
    assert "自動" in preview.financial_effect
    assert world.owner.confirm(OWNER_ID, preview.token_id).approval_source is ApprovalSource.OWNER


def test_official_out_of_range_duration_rejects_and_releases_later_candidates(world) -> None:
    """@description: A hard-limit rejection stops blocking, so the next candidate can proceed.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.metadata.script[VIDEO_A] = found(VIDEO_A, seconds=721)
    assert world.share(ALICE, VIDEO_A, "discord:1").review_status is ReviewStatus.REJECTED
    world.share(BOB, VIDEO_B, "discord:2")
    world.confirm_version("discord:2", "rec-B")
    assert world.pipeline.process("discord:2").decision.status is RewardCandidateStatus.AWARDED
    world.assert_books_balance(1)


def test_owner_duration_preview_confirm_then_stale_and_repeat_are_safe(world) -> None:
    """@description: The Discord duration entry binds revision and token; repeat replays, a moved revision refuses.
    @param world: Automatic-mode database.
    @return: None.
    """
    world.metadata.script[VIDEO_A] = MetadataLookup(LookupStatus.TEMPORARY_FAILURE, None)
    world.share(ALICE, VIDEO_A, "discord:1")
    stale = world.owner.preview_duration(OWNER_ID, "discord:1", 189, "https://example.test/manual-check", "人工補時長")
    fresh = world.owner.preview_duration(OWNER_ID, "discord:1", 189, "https://example.test/manual-check", "人工補時長")
    first = world.owner.confirm(OWNER_ID, fresh.token_id)
    assert first.status is ReviewStatus.PENDING_REVIEW
    assert world.owner.confirm(OWNER_ID, fresh.token_id) == first
    with pytest.raises(Exception, match="(?i)version|snapshot|revision|changed"):
        world.owner.confirm(OWNER_ID, stale.token_id)


def test_intake_refuses_settlement_on_unless_the_repository_is_in_automatic_mode(world) -> None:
    """@description: The earlier fail-closed rule still stands for every repository not explicitly in automatic mode.
    @param world: Automatic-mode database.
    @return: None.
    """
    closed = SubmissionRepository(partial(open_connection, world.database.app), OWNER_ID)
    with pytest.raises(PersistenceNotReady):
        closed.accept_pending(ALICE, url(VIDEO_A), "discord:1")


def test_settlement_switch_off_never_pays_and_says_it_is_pending(world) -> None:
    """@description: With the mode off the pipeline keeps READY candidates unpaid and the reply states the wait.
    @param world: Automatic-mode database.
    @return: None.
    """
    pipeline = AutoPipeline(world.repository, world.metadata, frozenset({TRUSTED}), settlement_enabled=False)
    world.repository.accept_pending(ALICE, url(VIDEO_A), "discord:1")
    pipeline.process("discord:1")
    world.confirm_version("discord:1", "rec-A")
    outcome = pipeline.process("discord:1")
    assert outcome.decision.status is RewardCandidateStatus.READY
    assert "+1" not in format_share_outcome("discord:1", outcome)
    world.assert_books_balance(0)
