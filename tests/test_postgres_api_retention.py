"""Real PostgreSQL behaviour of the API data lifecycle: official values live in one short-lived table, expired values are refused and then really cleared.

Run against disposable PG17 and PG18 servers. Metadata is a scripted fake at the provider boundary; review, history, receipts, ledger and privileges are the production tables.
"""

import logging
from datetime import datetime, timedelta, timezone
from functools import partial

import psycopg
import pytest

from bot.auto_pipeline import AutoPipeline
from bot.database.connection import open_connection
from bot.database.submission_repository import SubmissionRepository
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import DurationEvidenceExpired, ReviewStatus
from bot.reward_candidates import RewardCandidateStatus
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, NOW, OWNER_ID, TRUSTED, VIDEO_A, VIDEO_B, FakeMetadata, found, url

pytestmark = pytest.mark.postgres

OTHER_CHANNEL = "UC" + "z" * 22  # [untrusted channel]: Leaves the song decision to the Owner, so the case stays pending.
AGE_EXPIRED = ("UPDATE jb2_api_observations SET observed_at = statement_timestamp() - interval '20 days', "
               "expires_at = statement_timestamp() - interval '%s days' WHERE purged_at IS NULL")  # [time travel]: Moves unpurged rows past their expiry, inside the table's 30-day window.


class SpyMetadata(FakeMetadata):
    """Answer like the scripted provider and remember how many purge runs existed when each lookup happened."""

    def __init__(self, database) -> None:
        """@description: Bind the database that is asked for the run count.
        @param self: Spy provider.
        @param database: Disposable database.
        @return: None.
        """
        super().__init__()
        self.database = database
        self.runs_at_lookup: list[int] = []  # [order evidence]: Purge runs recorded before each lookup.

    def lookup(self, video_id: str):
        """@description: Record the purge-run count, then answer from the script.
        @param self: Spy provider.
        @param video_id: Requested video.
        @return: Scripted or default lookup.
        """
        try:
            self.runs_at_lookup.append(admin_query(self.database, "SELECT count(*) AS n FROM jb2_retention_runs")[0]["n"])
        except psycopg.errors.UndefinedTable:
            self.runs_at_lookup.append(-1)  # [migration absent]: A release running before its migration has no run table.
        return super().lookup(video_id)


class RetentionWorld:
    """Hold one disposable database with automatic review and API retention switched on, settlement on for this database only."""

    def __init__(self, database, api_retention: bool = True) -> None:
        """@description: Build repository, Owner service and pipeline against the app role.
        @param self: World.
        @param database: Disposable database.
        @param api_retention: Whether the pipeline keeps official values only in the short-lived table.
        @return: None.
        """
        self.database = database
        self.repository = SubmissionRepository(partial(open_connection, database.app), OWNER_ID, automatic_settlement=True, api_retention=api_retention)
        self.metadata = SpyMetadata(database)
        self.pipeline = self.new_pipeline(api_retention)
        self.owner = OwnerReviewService(self.repository, OWNER_ID, lambda: NOW, settlement_followup=self.pipeline.owner_settlement_note)
        admin_query(database, "UPDATE jb2_control SET settlement_enabled = TRUE")

    def new_pipeline(self, api_retention: bool = True) -> AutoPipeline:
        """@description: Build a pipeline as a fresh process would.
        @param self: World.
        @param api_retention: Retention switch of that process.
        @return: Pipeline.
        """
        return AutoPipeline(self.repository, self.metadata, frozenset({TRUSTED}), settlement_enabled=True, api_retention=api_retention)

    def share(self, user: int, video_id: str, operation_id: str, channel: str = TRUSTED):
        """@description: Accept one share with a scripted channel and run the pipeline, as /share does.
        @param self: World.
        @param user: Submitter.
        @param video_id: Video.
        @param operation_id: Operation key.
        @param channel: Channel the provider reports.
        @return: Pipeline outcome.
        """
        self.metadata.script[video_id] = found(video_id, channel=channel)
        self.repository.accept_pending(user, url(video_id), operation_id)
        return self.pipeline.process(operation_id)

    def age(self, days_past_expiry: int) -> None:
        """@description: Make every unpurged observation expire the given number of days ago.
        @param self: World.
        @param days_past_expiry: Days since expiry.
        @return: None.
        """
        admin_query(self.database, AGE_EXPIRED % days_past_expiry)

    def approve_as_owner(self, operation_id: str):
        """@description: Run the Owner's approval preview and confirmation.
        @param self: World.
        @param operation_id: Operation.
        @return: Review snapshot after the confirmation.
        """
        return self.owner.confirm(OWNER_ID, self.preview_approval(operation_id).token_id)

    def preview_approval(self, operation_id: str):
        """@description: Preview the Owner's approval at the current revision.
        @param self: World.
        @param operation_id: Operation.
        @return: Review preview.
        """
        view = self.owner.view(OWNER_ID, operation_id)
        judgement = ContentJudgement(view.review.video, OWNER_ID, "確認為歌曲", NOW, "https://example.test/manual-check", True)
        return self.owner.preview(OWNER_ID, operation_id, ReviewAction.APPROVE, "確認為歌曲", view.review.version, judgement.evidence_reference, judgement)

    def observation(self, video_id: str) -> dict | None:
        """@description: Read one observation row as the administrator.
        @param self: World.
        @param video_id: Video.
        @return: Row, or None.
        """
        rows = admin_query(self.database, "SELECT * FROM jb2_api_observations WHERE video_id = %s", (video_id,))
        return rows[0] if rows else None

    def tables_containing(self, needle: str) -> set[str]:
        """@description: Find every table of the schema whose rows mention the text, so a copy hidden in a JSON or reason column is found.
        @param self: World.
        @param needle: Text to look for.
        @return: Names of the tables that contain it.
        """
        names = [row["table_name"] for row in admin_query(self.database, "SELECT table_name FROM information_schema.tables "
                                                                         "WHERE table_schema = 'jukebox101' AND table_type = 'BASE TABLE'")]
        found_in = set()
        for name in names:
            if admin_query(self.database, f'SELECT 1 FROM "{name}" t WHERE t::text LIKE %s LIMIT 1', (f"%{needle}%",)):
                found_in.add(name)
        return found_in


@pytest.fixture
def world(pg_database) -> RetentionWorld:
    """@description: Provide one retention-mode world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return RetentionWorld(pg_database)


def test_every_copy_of_official_values_is_in_the_observation_table_only(world) -> None:
    """@description: After an automatic review that keeps the case pending, the channel, the retrieval time, the policy source label and the seconds exist in no table but the short-lived one.
    @param world: Retention-mode database.
    @return: None.
    """
    outcome = world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    assert outcome.review_status is ReviewStatus.PENDING_REVIEW
    assert world.tables_containing(OTHER_CHANNEL) == {"jb2_api_observations"}
    assert world.tables_containing("youtube-data-api") == set()
    assert world.tables_containing("fetched_at") == set()
    assert world.tables_containing('"seconds"') == set()
    row = world.observation(VIDEO_A)
    assert row["duration_seconds"] == 189 and row["channel_id"] == OTHER_CHANNEL and row["purged_at"] is None
    assert world.owner.view(OWNER_ID, "discord:1").review.duration_verdict is not None


def test_a_decided_case_leaves_no_official_value_anywhere(world) -> None:
    """@description: An automatic approval stores the conclusion and clears the values in the same transaction, so even the short-lived table holds nothing.
    @param world: Retention-mode database.
    @return: None.
    """
    assert world.share(ALICE, VIDEO_A, "discord:1").review_status is ReviewStatus.APPROVED
    assert world.tables_containing(TRUSTED) == set()
    row = world.observation(VIDEO_A)
    assert row["duration_seconds"] is None and row["channel_id"] is None and row["purge_reason"] == "completed"


def test_an_out_of_range_rejection_stores_no_value_at_all(world) -> None:
    """@description: A rejected duration is a conclusion; the measured seconds are never stored.
    @param world: Retention-mode database.
    @return: None.
    """
    world.metadata.script[VIDEO_A] = found(VIDEO_A, seconds=721)
    world.repository.accept_pending(ALICE, url(VIDEO_A), "discord:1")
    assert world.pipeline.process("discord:1").review_status is ReviewStatus.REJECTED
    assert world.observation(VIDEO_A) is None and world.tables_containing('"seconds"') == set()


def test_without_the_switch_the_previous_behaviour_is_unchanged(pg_database) -> None:
    """@description: With retention off the review still records the evidence with seconds, exactly as before.
    @param pg_database: Disposable database.
    @return: None.
    """
    old = RetentionWorld(pg_database, api_retention=False)
    old.share(ALICE, VIDEO_A, "discord:1")
    assert old.owner.view(OWNER_ID, "discord:1").review.duration.seconds == 189
    assert old.observation(VIDEO_A) is None


def test_an_expired_conclusion_stops_approval_and_the_case_stays_pending(world) -> None:
    """@description: The only duration basis has expired: the preview refuses, a token issued earlier is refused at confirmation, and nothing is written.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    token = world.preview_approval("discord:1").token_id
    before = world.owner.view(OWNER_ID, "discord:1").review
    world.age(6)
    with pytest.raises(DurationEvidenceExpired):
        world.owner.confirm(OWNER_ID, token)
    with pytest.raises(DurationEvidenceExpired):
        world.preview_approval("discord:1")
    after = world.owner.view(OWNER_ID, "discord:1")
    assert after.review == before and after.duration_basis_expired is True
    item = world.owner.list_pending(OWNER_ID).items[0]
    assert item.api_basis == "expired" and item.duration_seconds is None


def test_owner_entered_duration_is_unaffected_by_the_api_window_and_unblocks_approval(world) -> None:
    """@description: The Owner's own duration evidence does not expire with the API retention window and keeps the existing revision rules.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.age(6)
    entered = world.owner.preview_duration(OWNER_ID, "discord:1", 200, "https://example.test/manual-check", "人工補時長")
    assert world.owner.confirm(OWNER_ID, entered.token_id).status is ReviewStatus.PENDING_REVIEW
    world.pipeline.purge_expired()
    assert world.owner.view(OWNER_ID, "discord:1").duration_basis_expired is False
    assert world.approve_as_owner("discord:1").status is ReviewStatus.APPROVED
    assert all(item.api_basis is None for item in world.owner.list_pending(OWNER_ID).items)


def test_an_owner_duration_outside_the_limits_still_rejects_over_an_api_pass(world) -> None:
    """@description: The existing conflict rule is unchanged: a stated duration outside the limits rejects, whatever the API said.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    entered = world.owner.preview_duration(OWNER_ID, "discord:1", 900, "https://example.test/manual-check", "人工補時長")
    assert world.owner.confirm(OWNER_ID, entered.token_id).status is ReviewStatus.REJECTED


def test_an_approved_case_finishes_its_version_and_settlement_after_its_data_is_gone(world) -> None:
    """@description: Approval is never revoked by expiry: the version is confirmed and the one Nickel is paid after the observation expired and was purged.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.approve_as_owner("discord:1")
    world.age(6)
    assert world.pipeline.purge_expired() == 1 or world.observation(VIDEO_A)["purged_at"] is not None
    from tests.test_postgres_auto_pipeline import World
    helper = World.__new__(World)
    helper.owner, helper.pipeline, helper.database = world.owner, world.pipeline, world.database
    helper.confirm_version("discord:1", "rec-A")
    outcome = world.pipeline.process("discord:1")
    assert outcome.decision.status is RewardCandidateStatus.AWARDED
    assert admin_query(world.database, "SELECT credit_balance FROM users WHERE discord_user_id = %s", (ALICE,))[0]["credit_balance"] == 1
    assert len(admin_query(world.database, "SELECT 1 FROM credit_ledger")) == 1


def test_purge_really_clears_expired_values_and_records_the_run(world) -> None:
    """@description: The purge sets every value to NULL (not just hides it), marks why, is repeatable, and leaves a closed run record.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.share(ALICE, VIDEO_B, "discord:2", channel=OTHER_CHANNEL)
    world.age(2)
    assert world.repository.purge_api_observations() == 2
    for video in (VIDEO_A, VIDEO_B):
        row = world.observation(video)
        assert row["duration_seconds"] is None and row["channel_id"] is None and row["purge_reason"] == "expired"
    assert world.tables_containing(OTHER_CHANNEL) == set()
    assert world.repository.purge_api_observations() == 0
    runs = admin_query(world.database, "SELECT purged_rows, error_class, finished_at FROM jb2_retention_runs ORDER BY run_id")
    assert [run["purged_rows"] for run in runs] == [2, 0] and all(run["error_class"] is None and run["finished_at"] for run in runs)


def test_check_constraints_bound_the_columns_but_never_clear_anything(world) -> None:
    """@description: The database refuses a window over 30 days and values on a purged row, and an expired row simply stays until the job clears it.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    with pytest.raises(psycopg.errors.CheckViolation):
        admin_query(world.database, "UPDATE jb2_api_observations SET expires_at = observed_at + interval '31 days'")
    with pytest.raises(psycopg.errors.CheckViolation):
        admin_query(world.database, "UPDATE jb2_api_observations SET purged_at = statement_timestamp(), purge_reason = 'expired'")
    world.age(6)
    held = world.observation(VIDEO_A)
    assert held["duration_seconds"] == 189 and held["channel_id"] == OTHER_CHANNEL  # [no automatic clearing]: Expired, still holding values.


def test_a_failing_purge_is_reported_blocks_trust_and_recovers_when_fixed(world, caplog) -> None:
    """@description: When the purge cannot run, the debt is visible from the database, approvals still refuse the expired value, and the next successful run clears it.
    @param world: Retention-mode database.
    @param caplog: Log capture.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.age(4)
    admin_query(world.database, "CREATE FUNCTION jukebox101.refuse_purge() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'purge disabled for test'; END $$")
    admin_query(world.database, "CREATE TRIGGER refuse_purge BEFORE UPDATE ON jukebox101.jb2_api_observations FOR EACH ROW EXECUTE FUNCTION jukebox101.refuse_purge()")
    with caplog.at_level(logging.ERROR):
        assert world.pipeline.purge_expired() == 0
    runs = admin_query(world.database, "SELECT error_class, finished_at FROM jb2_retention_runs")
    assert len(runs) == 1 and runs[0]["error_class"] and runs[0]["finished_at"]
    status = world.repository.api_retention_status()
    assert status.overdue_rows == 1 and status.oldest_overdue_days >= 3 and status.last_success_age_days is None
    with pytest.raises(DurationEvidenceExpired):
        world.preview_approval("discord:1")
    admin_query(world.database, "DROP TRIGGER refuse_purge ON jukebox101.jb2_api_observations")
    world.pipeline.sweep()
    assert world.repository.api_retention_status().overdue_rows == 0
    assert [run["purged_rows"] for run in admin_query(world.database, "SELECT purged_rows FROM jb2_retention_runs ORDER BY run_id")] == [0, 1]


def test_far_behind_purge_stops_new_lookups_until_it_catches_up(world) -> None:
    """@description: Past three days of retention debt no new official value is fetched; once the purge succeeds lookups resume.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.age(4)
    admin_query(world.database, "CREATE FUNCTION jukebox101.refuse_purge() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'x'; END $$")
    admin_query(world.database, "CREATE TRIGGER refuse_purge BEFORE UPDATE ON jukebox101.jb2_api_observations FOR EACH ROW EXECUTE FUNCTION jukebox101.refuse_purge()")
    world.repository.accept_pending(ALICE, url(VIDEO_B), "discord:2")
    calls_before = len(world.metadata.calls)
    world.pipeline.sweep()
    assert len(world.metadata.calls) == calls_before
    admin_query(world.database, "DROP TRIGGER refuse_purge ON jukebox101.jb2_api_observations")
    world.pipeline.sweep()
    assert VIDEO_B in world.metadata.calls[calls_before:]


def test_a_restarted_process_purges_before_it_looks_anything_up(world) -> None:
    """@description: Recovery after a long stop: a new process clears the expired values in its first sweep, before any lookup.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.age(10)
    world.repository.accept_pending(ALICE, url(VIDEO_B), "discord:2")
    restarted = world.new_pipeline()
    calls_before = len(world.metadata.calls)
    restarted.sweep()
    assert VIDEO_B in world.metadata.calls[calls_before:]
    assert all(runs >= 1 for runs in world.metadata.runs_at_lookup[-2:])  # [purge first]: A run was already recorded when the first new lookup happened.
    assert world.repository.api_retention_status().overdue_rows == 0


def test_the_sweep_revalidates_only_a_pending_case_resting_on_an_expired_conclusion(world) -> None:
    """@description: Waiting cases are asked again once, with no new review revision; decided cases are never asked.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.share(ALICE, VIDEO_B, "discord:2")
    revision = world.owner.view(OWNER_ID, "discord:1").review.version
    world.age(2)
    calls_before = len(world.metadata.calls)
    world.pipeline.sweep()
    asked = world.metadata.calls[calls_before:]
    assert asked == [VIDEO_A]
    row = world.observation(VIDEO_A)
    assert row["purged_at"] is None and row["duration_seconds"] == 189 and row["expires_at"] > datetime.now(timezone.utc) + timedelta(days=10)
    assert world.owner.view(OWNER_ID, "discord:1").review.version == revision
    assert world.approve_as_owner("discord:1").status is ReviewStatus.APPROVED


def test_a_failed_revalidation_leaves_the_case_pending_for_the_owner(world) -> None:
    """@description: If the provider no longer confirms a pass, nothing is refreshed and the case waits for the Owner's duration.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.age(2)
    world.metadata.script[VIDEO_A] = found(VIDEO_A, channel=OTHER_CHANNEL, seconds=900)
    world.pipeline.sweep()
    assert world.observation(VIDEO_A)["purged_at"] is not None
    assert world.owner.view(OWNER_ID, "discord:1").review.status is ReviewStatus.PENDING_REVIEW


def test_replaying_an_automatic_decision_writes_nothing_twice(world) -> None:
    """@description: The same verdict applied again is a stale no-op: no second revision, no second observation, no duplicate history.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    from bot.auto_review import decide_auto_review
    verdict = decide_auto_review(found(VIDEO_A, channel=OTHER_CHANNEL), frozenset({TRUSTED}))
    history = len(admin_query(world.database, "SELECT 1 FROM jb2_review_history"))
    again = world.repository.apply_auto_review("discord:1", 0, verdict, retain_api_values=True)
    assert again.version == world.owner.view(OWNER_ID, "discord:1").review.version
    assert len(admin_query(world.database, "SELECT 1 FROM jb2_review_history")) == history


def test_legacy_rows_with_seconds_still_decode_and_approve_under_retention_mode(pg_database) -> None:
    """@description: A review written before the switch (evidence with seconds) keeps working, with no expiry applied to it.
    @param pg_database: Disposable database.
    @return: None.
    """
    old = RetentionWorld(pg_database, api_retention=False)
    old.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    admin_query(pg_database, "SELECT 1")
    new = RetentionWorld(pg_database, api_retention=True)
    view = new.owner.view(OWNER_ID, "discord:1")
    assert view.review.duration.seconds == 189 and view.duration_basis_expired is False
    assert new.approve_as_owner("discord:1").status is ReviewStatus.APPROVED


def test_privileges_allow_clearing_but_never_deleting_or_rewriting_history(world) -> None:
    """@description: The application role may update (purge) the observation tables but not delete from them, nor update any history table; the read-only role cannot write.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    def attempt(settings, statement: str) -> None:
        with open_connection(settings) as connection, connection.cursor() as cursor:
            cursor.execute(statement)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        attempt(world.database.app, "DELETE FROM jb2_api_observations")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        attempt(world.database.app, "UPDATE jb2_review_history SET decision_reason = 'x'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        attempt(world.database.readonly, "UPDATE jb2_api_observations SET channel_id = NULL")
    attempt(world.database.readonly, "SELECT count(*) FROM jb2_api_observations")


def test_a_release_running_before_its_migration_behaves_as_before(pg_database) -> None:
    """@description: With the switch off and the tables absent (migration not yet applied) intake, listing, approval, the Owner's view and the warning all work and no retention table is read.
    @param pg_database: Disposable database.
    @return: None.
    """
    admin_query(pg_database, "DROP TABLE jb2_retention_runs, jb2_api_observations")
    old = RetentionWorld(pg_database, api_retention=False)
    old.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    item = old.owner.list_pending(OWNER_ID).items[0]
    assert item.api_basis is None and item.duration_seconds == 189
    assert old.owner.view(OWNER_ID, "discord:1").duration_basis_expired is False
    assert old.owner.retention_status(OWNER_ID) is None
    assert old.approve_as_owner("discord:1").status is ReviewStatus.APPROVED
    assert old.pipeline.sweep() == 0


def test_the_warning_reads_the_tables_only_when_retention_is_switched_on(world) -> None:
    """@description: With the switch on the Owner service reports real retention figures from the database clock.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.age(5)
    status = world.owner.retention_status(OWNER_ID)
    assert status.overdue_rows == 1 and 4.9 < status.oldest_overdue_days < 5.1


def test_an_owner_decided_case_is_cleared_by_the_next_purge_before_it_expires(world) -> None:
    """@description: Values are not kept for a decided case: the purge clears them as completed even though the window has not elapsed.
    @param world: Retention-mode database.
    @return: None.
    """
    world.share(ALICE, VIDEO_A, "discord:1", channel=OTHER_CHANNEL)
    world.approve_as_owner("discord:1")
    assert world.observation(VIDEO_A)["duration_seconds"] == 189
    assert world.repository.api_purge_preview() == (0, 1)
    assert world.repository.purge_api_observations() == 1
    row = world.observation(VIDEO_A)
    assert row["duration_seconds"] is None and row["channel_id"] is None and row["purge_reason"] == "completed"



def test_the_startup_check_accepts_a_migrated_database(pg_database) -> None:
    """@description: With 0004 applied and the shipped grants, the application role passes the check without changing anything.
    @param pg_database: Disposable database.
    @return: None.
    """
    from bot.database.api_retention_job import verify_retention_schema
    verify_retention_schema(pg_database.app)


def test_the_startup_check_names_missing_tables_and_applies_nothing(pg_database) -> None:
    """@description: Without the migration the check refuses with an explicit message, and the database is exactly as it was afterwards.
    @param pg_database: Disposable database.
    @return: None.
    """
    from bot.config import ConfigurationError
    from bot.database.api_retention_job import verify_retention_schema
    admin_query(pg_database, "DROP TABLE jb2_retention_runs, jb2_api_observations")
    with pytest.raises(ConfigurationError) as refusal:
        verify_retention_schema(pg_database.app)
    assert "jb2_api_observations is missing" in str(refusal.value) and "migration 0004" in str(refusal.value) and "不會自動套用" in str(refusal.value)
    assert admin_query(pg_database, "SELECT to_regclass('jb2_api_observations') IS NOT NULL AS present")[0]["present"] is False


@pytest.mark.parametrize("revoke,expected", [
    ("REVOKE UPDATE ON jb2_api_observations FROM jukebox101_app_role", "UPDATE on jb2_api_observations is not granted"),
    ("REVOKE INSERT ON jb2_api_observations FROM jukebox101_app_role", "INSERT on jb2_api_observations is not granted"),
    ("REVOKE INSERT ON jb2_retention_runs FROM jukebox101_app_role", "INSERT on jb2_retention_runs is not granted"),
    ("REVOKE UPDATE (finished_at) ON jb2_retention_runs FROM jukebox101_app_role", "UPDATE on jb2_retention_runs.finished_at is not granted"),
])
def test_the_startup_check_names_each_missing_privilege(pg_database, revoke, expected) -> None:
    """@description: Every privilege the purge needs is checked on its own and named, and nothing is granted back.
    @param pg_database: Disposable database.
    @param revoke: Statement removing one privilege.
    @param expected: Text the refusal must contain.
    @return: None.
    """
    from bot.config import ConfigurationError
    from bot.database.api_retention_job import verify_retention_schema
    admin_query(pg_database, revoke)
    with pytest.raises(ConfigurationError, match="migration 0004") as refusal:
        verify_retention_schema(pg_database.app)
    assert expected in str(refusal.value)


def test_the_startup_check_refuses_when_the_database_cannot_be_checked(pg_database) -> None:
    """@description: An unreachable database is a refusal that names only the error class and never echoes connection details.
    @param pg_database: Disposable database.
    @return: None.
    """
    from dataclasses import replace
    from bot.config import ConfigurationError
    from bot.database.api_retention_job import verify_retention_schema
    unreachable = replace(pg_database.app, port=1, password="SYNTHETIC-PASSWORD-MUST-NOT-APPEAR")
    with pytest.raises(ConfigurationError) as refusal:
        verify_retention_schema(unreachable)
    assert "無法檢查資料庫" in str(refusal.value) and "SYNTHETIC-PASSWORD" not in str(refusal.value) and unreachable.host not in str(refusal.value)
