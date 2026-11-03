"""Real PostgreSQL behaviour of automatic version confirmation, run against disposable PostgreSQL 17 and 18 servers.

The official provider is a scripted fake; the comparison read, the staleness check, the approval, the system-judged version evidence,
settlement and the books are the production tables. Old songs' titles must never reach any table.
"""

import re
import threading

import pytest

from bot.auto_pipeline import AutoPipeline
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import ApprovalSource, ReviewStatus
from bot.reward_candidates import RewardCandidateStatus
from bot.song_judgement import ContentJudgement
from bot.version_identity import VersionIdentityStatus
from bot.youtube_metadata import BatchLookup, BatchStatus, LookupStatus, MetadataLookup, VideoMetadata
from bot.database.auto_version_store import STALE_TOLERANCE_SUBMISSIONS
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import (ALICE, BOB, NOW, OWNER_ID, TRUSTED, VIDEO_A, VIDEO_B, VIDEO_C, FakeMetadata, World, url)

pytestmark = pytest.mark.postgres

TITLE_CANARY = "TitleCanaryQ"
SYSTEM_EVIDENCE = "strpos(evidence_json::text, 'EvidenceRecord') > 0 AND strpos(evidence_json::text, 'system_judgement') > 0"  # [system rows only]: Every review write also stores identity-assertion rows.
CANARY_CHANNEL = "UC" + "q" * 22  # [leak marker]: A trusted channel only this test uses, so any stored copy is attributable.
CANARY_SECONDS = 613  # [leak marker]: An official duration no other fixture uses.
OLD_TITLE = f"{TITLE_CANARY} Alpha Beta Gamma"
UNRELATED_TITLE = "Delta Epsilon Zeta"
TABLES = ("users", "songs", "submissions", "credit_ledger", "jb2_versions", "jb2_videos", "jb2_events", "jb2_receipts", "jb2_claims",
          "jb2_settlements", "jb2_review_history", "jb2_evidence", "jb2_decisions", "jb2_api_observations")  # [leak scan]: Every table that could keep a title.


def official(video_id: str, title: str, seconds: int, channel: str = TRUSTED) -> VideoMetadata:
    """@description: Build official metadata for a scripted video.
    @param video_id: Video identifier.
    @param title: Official title.
    @param seconds: Official duration.
    @param channel: Channel identifier.
    @return: Metadata.
    """
    return VideoMetadata(video_id, channel, "10", title, "none", False, "processed", "public", seconds, NOW)


class ComparingMetadata(FakeMetadata):
    """Answer single lookups from a per-video script and comparison batches from the same script."""

    def __init__(self, scripted: dict[str, VideoMetadata]) -> None:
        """@description: Store the scripted metadata and the optional hook run during every comparison.
        @param self: Fake provider.
        @param scripted: Video identifier to official metadata.
        @return: None.
        """
        super().__init__()
        self.scripted, self.batches, self.during_comparison = scripted, [], None

    def lookup(self, video_id: str) -> MetadataLookup:
        """@description: Answer one candidate lookup.
        @param self: Fake provider.
        @param video_id: Candidate.
        @return: Found lookup.
        """
        self.calls.append(video_id)
        return MetadataLookup(LookupStatus.FOUND, self.scripted[video_id])

    def lookup_many(self, video_ids) -> BatchLookup:
        """@description: Answer one comparison batch, optionally causing a concurrent event first.
        @param self: Fake provider.
        @param video_ids: Existing songs.
        @return: Complete batch.
        """
        self.batches.append(tuple(video_ids))
        if self.during_comparison is not None:
            self.during_comparison()
        return BatchLookup(BatchStatus.COMPLETE, tuple(self.scripted[video] for video in video_ids))


class ConfirmWorld(World):
    """A world whose pipeline compares new submissions and confirms versions by itself."""

    def __init__(self, database) -> None:
        """@description: Replace the provider and pipeline with the comparing ones.
        @param self: World.
        @param database: Disposable database.
        @return: None.
        """
        super().__init__(database)
        self.metadata = ComparingMetadata({VIDEO_A: official(VIDEO_A, OLD_TITLE, 189),
                                           VIDEO_B: official(VIDEO_B, UNRELATED_TITLE, 250),
                                           VIDEO_C: official(VIDEO_C, "Omega Sigma Tau", 300)})
        self.pipeline = self.new_pipeline()
        self.owner = OwnerReviewService(self.repository, OWNER_ID, lambda: NOW, settlement_followup=self.pipeline.owner_settlement_note)

    def new_pipeline(self, repository=None) -> AutoPipeline:
        """@description: Build a pipeline with review, retention, settlement and version confirmation all on.
        @param self: World.
        @param repository: Optional repository override.
        @return: Pipeline.
        """
        return AutoPipeline(repository or self.repository, self.metadata, frozenset({TRUSTED, CANARY_CHANNEL}),
                            settlement_enabled=True, api_retention=True, version_confirm=True)

    def everything_stored(self) -> str:
        """@description: Dump every table that could keep a title as text.
        @param self: World.
        @return: One string with all rows.
        """
        rows = [row["payload"] for table in TABLES for row in admin_query(self.database, f"SELECT row_to_json(t)::text AS payload FROM {table} t")]
        return "\n".join(rows)


def approve_as_owner(world: "ConfirmWorld", operation_id: str) -> None:
    """@description: Approve the song as the Owner does for a case that waited in review.
    @param world: Comparing world.
    @param operation_id: Operation.
    @return: None.
    """
    review = world.owner.view(OWNER_ID, operation_id).review
    content = ContentJudgement(review.video, OWNER_ID, "人工確認是歌曲", NOW, f"content-{operation_id}", True)
    preview = world.owner.preview(OWNER_ID, operation_id, ReviewAction.APPROVE, "人工判斷", review.version, f"cite-{operation_id}", content=content)
    world.owner.confirm(OWNER_ID, preview.token_id)


@pytest.fixture
def world(pg_database) -> ConfirmWorld:
    """@description: Provide one comparing world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return ConfirmWorld(pg_database)


def test_the_first_clean_submission_is_confirmed_by_the_system_and_paid_once(world) -> None:
    """@description: Approval, version confirmation and the single award happen with no Owner action and no Owner identity in the history.
    @param world: Comparing world.
    @return: None.
    """
    outcome = world.share(ALICE, VIDEO_A, "discord:1")
    view = world.owner.view(OWNER_ID, "discord:1")
    assert outcome.review_status is ReviewStatus.APPROVED and outcome.decision.status is RewardCandidateStatus.AWARDED
    assert view.review.approval_source is ApprovalSource.AUTO and view.identity.status is VersionIdentityStatus.CONFIRMED
    assert OWNER_ID not in {row["actor_id"] for row in admin_query(world.database, "SELECT actor_id FROM jb2_review_history")}
    assert world.balances()[ALICE] == 1
    world.assert_books_balance(1)


def test_the_version_evidence_is_system_judged_not_owner_confirmed_and_holds_no_title(world) -> None:
    """@description: The audit row must say the system judged it, must not claim the Owner did and must not contain a title or channel.
    @param world: Comparing world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    rows = admin_query(world.database, f"SELECT evidence_json::text AS payload FROM jb2_evidence WHERE {SYSTEM_EVIDENCE}")
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert "system_judgement" in payload and re.search(r'owner_confirmed"?\s*:\s*true', payload) is None
    assert OLD_TITLE not in payload and TRUSTED not in payload


def test_an_unrelated_second_song_is_compared_with_the_first_and_also_paid(world) -> None:
    """@description: A clean comparison against an existing song still confirms and pays; each video becomes its own version.
    @param world: Comparing world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    outcome = world.share(BOB, VIDEO_B, "discord:2")
    assert world.metadata.batches == [(VIDEO_A,)]
    assert outcome.decision.status is RewardCandidateStatus.AWARDED and world.balances() == {ALICE: 1, BOB: 1}
    world.assert_books_balance(2)


def test_a_similar_song_waits_for_the_owner_with_its_reason_and_pays_nothing_until_confirmed(world) -> None:
    """@description: Same channel, a duration one second apart and a near-identical title: the case goes to the Owner and nothing is paid.
    @param world: Comparing world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.metadata.scripted[VIDEO_B] = official(VIDEO_B, "alpha beta gamma (Official Video)", 190)
    outcome = world.share(BOB, VIDEO_B, "discord:2")
    view = world.owner.view(OWNER_ID, "discord:2")
    assert outcome.review_status is ReviewStatus.PENDING_REVIEW and view.identity.status is VersionIdentityStatus.UNRESOLVED
    history = admin_query(world.database, "SELECT decision_reason AS reason FROM jb2_review_history ORDER BY revision DESC")
    assert any("same_channel_close_duration" in row["reason"] and "similar_title" in row["reason"] for row in history)
    assert world.balances() == {ALICE: 1, BOB: 0}
    world.assert_books_balance(1)


def test_the_owner_can_still_confirm_a_flagged_case_the_old_way_and_it_then_pays_once(world) -> None:
    """@description: Doubt never blocks the Owner path; confirming a version settles exactly one Nickel.
    @param world: Comparing world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.metadata.scripted[VIDEO_B] = official(VIDEO_B, "alpha beta gamma (Official Video)", 190)
    world.share(BOB, VIDEO_B, "discord:2")
    approve_as_owner(world, "discord:2")
    world.confirm_version("discord:2", "rec-B")
    assert "+1" in world.owner.settlement_note(OWNER_ID, "discord:2")
    assert world.balances() == {ALICE: 1, BOB: 1}
    world.assert_books_balance(2)


def test_many_submissions_accepted_during_the_comparison_make_the_clearance_stale(world) -> None:
    """@description: More than the tolerated number of shares accepted after the comparison began are not covered by it, so the transaction downgrades to Owner review.
    Owner ruling 2026-11-03: a few concurrent submissions are tolerated (the later one compares itself with this one anyway).
    @param world: Comparing world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")

    def crowd() -> None:
        """@description: Accept one more than the tolerated number of shares while the comparison runs.
        @param: none
        @return: None.
        """
        for index in range(STALE_TOLERANCE_SUBMISSIONS + 1):
            world.repository.accept_pending(ALICE, url(f"x{index:010d}"), f"discord:9{index}")

    world.metadata.during_comparison = crowd
    outcome = world.share(BOB, VIDEO_B, "discord:2")
    world.metadata.during_comparison = None
    assert outcome.review_status is ReviewStatus.PENDING_REVIEW
    assert world.owner.view(OWNER_ID, "discord:2").identity.status is VersionIdentityStatus.UNRESOLVED
    reasons = " ".join(row["reason"] for row in admin_query(world.database, "SELECT decision_reason AS reason FROM jb2_review_history"))
    assert "comparison_stale" in reasons and world.balances()[BOB] == 0
    world.assert_books_balance(1)


def test_replaying_the_same_share_creates_one_confirmation_and_one_award(world) -> None:
    """@description: A retried interaction or lost acknowledgement must not duplicate evidence, versions or Nickel.
    @param world: Comparing world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    world.pipeline.process("discord:1")
    world.share(ALICE, VIDEO_A, "discord:1")
    assert len(admin_query(world.database, f"SELECT 1 FROM jb2_evidence WHERE {SYSTEM_EVIDENCE}")) == 1
    assert len(admin_query(world.database, "SELECT 1 FROM jb2_versions")) == 1
    world.assert_books_balance(1)


def test_two_pipelines_racing_on_one_new_video_confirm_and_pay_once(world) -> None:
    """@description: Both may compare, but only one automatic decision is written and one Nickel exists.
    @param world: Comparing world.
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
    assert sum(world.balances().values()) == 1
    assert len(admin_query(world.database, f"SELECT 1 FROM jb2_evidence WHERE {SYSTEM_EVIDENCE}")) == 1
    world.assert_books_balance(1)


def test_two_look_alike_songs_submitted_together_are_never_both_confirmed_automatically(world) -> None:
    """@description: Each pipeline either sees the other submission or finds its own clearance stale, so neither pays.
    @param world: Comparing world.
    @return: None.
    """
    world.metadata.scripted[VIDEO_A] = official(VIDEO_A, OLD_TITLE, 189)
    world.metadata.scripted[VIDEO_B] = official(VIDEO_B, "alpha beta gamma (Official Video)", 190)
    world.repository.accept_pending(ALICE, url(VIDEO_A), "discord:1")
    world.repository.accept_pending(BOB, url(VIDEO_B), "discord:2")
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
    assert sum(world.balances().values()) == 0 and len(admin_query(world.database, f"SELECT 1 FROM jb2_evidence WHERE {SYSTEM_EVIDENCE}")) == 0
    world.assert_books_balance(0)


def test_the_comparison_leaves_no_trace_of_the_old_songs_official_values(world) -> None:
    """@description: Old songs' titles appear in no table, and their observation rows are untouched by the comparison.
    @param world: Comparing world.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1")
    before = admin_query(world.database, "SELECT * FROM jb2_api_observations WHERE video_id = %s", (VIDEO_A,))
    world.share(BOB, VIDEO_B, "discord:2")
    world.metadata.scripted[VIDEO_C] = official(VIDEO_C, "alpha beta gamma live", 189)
    world.share(BOB, VIDEO_C, "discord:3")
    assert admin_query(world.database, "SELECT * FROM jb2_api_observations WHERE video_id = %s", (VIDEO_A,)) == before
    assert TITLE_CANARY not in world.everything_stored() and UNRELATED_TITLE not in world.everything_stored()


def test_the_recent_comparison_read_is_bounded_newest_first_deduplicated_and_excludes_the_candidate(world) -> None:
    """@description: The repository read feeding the comparison returns existing songs in all states, once each, newest first.
    @param world: Comparing world.
    @return: None.
    """
    world.repository.accept_pending(ALICE, url(VIDEO_A), "discord:1")
    world.repository.accept_pending(ALICE, url(VIDEO_B), "discord:2")
    world.repository.accept_pending(BOB, url(VIDEO_B), "discord:3")
    world.repository.accept_pending(BOB, url(VIDEO_C), "discord:4")
    assert world.repository.recent_comparison(VIDEO_C, 500).video_ids == (VIDEO_B, VIDEO_A)
    assert world.repository.recent_comparison(VIDEO_A, 500).video_ids == (VIDEO_C, VIDEO_B)
    assert world.repository.recent_comparison(VIDEO_C, 1).video_ids == (VIDEO_B,)


def test_the_recent_comparison_read_marks_the_newest_accepted_submission_and_writes_nothing(world) -> None:
    """@description: The staleness mark advances with each accepted share, and reading changes no row.
    @param world: Comparing world.
    @return: None.
    """
    world.repository.accept_pending(ALICE, url(VIDEO_A), "discord:1")
    first = world.repository.recent_comparison(VIDEO_C, 500).last_accepted_sequence
    snapshot = world.everything_stored()
    assert world.repository.recent_comparison(VIDEO_C, 500).last_accepted_sequence == first and world.everything_stored() == snapshot
    world.repository.accept_pending(BOB, url(VIDEO_B), "discord:2")
    assert world.repository.recent_comparison(VIDEO_C, 500).last_accepted_sequence > first


def test_new_data_holds_only_ids_and_links_never_a_title_a_channel_or_an_official_duration(world) -> None:
    """@description: Outside the short-lived observation table, nothing the automatic path writes contains a title, a channel ID or an official duration.
    @param world: Comparing world.
    @return: None.
    """
    world.metadata.scripted[VIDEO_C] = official(VIDEO_C, f"{TITLE_CANARY} Omega", CANARY_SECONDS, CANARY_CHANNEL)
    world.share(ALICE, VIDEO_A, "discord:1")
    world.share(BOB, VIDEO_C, "discord:2")
    durable = [row["payload"] for table in TABLES if table != "jb2_api_observations"
               for row in admin_query(world.database, f"SELECT row_to_json(t)::text AS payload FROM {table} t")]
    text = "\n".join(durable)
    assert TITLE_CANARY not in text and CANARY_CHANNEL not in text and TRUSTED not in text
    assert re.search(rf"(?<![0-9]){CANARY_SECONDS}(?![0-9])", text) is None
    assert VIDEO_C in text and "youtube.com" in text  # [what is kept]: The video ID and the original link.
