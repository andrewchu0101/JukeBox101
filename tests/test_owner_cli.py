"""Verify the operator CLI for Owner duration evidence and approval offline: no connection without a subcommand, explicit confirmations, digest binding,
boundary and out-of-range behaviour, stale revisions, replay, the configured Owner only, no cross-video input, and rollback on a failed publish.

The CLI runs over the real repository and owner service on the SQL-protocol fake. Nothing connects to MySQL or Discord.
"""

from datetime import datetime, timezone

import pytest

from bot.database.submission_repository import SubmissionRepository
from bot.owner_cli import duration_digest, main
from bot.review import ReviewStatus
from bot.urls import parse_youtube_url
from tests.submission_persistence_fake import FakeDatabase

OWNER = 777  # [configured Owner]: The identity the fake repository was built with.
URL = "https://youtu.be/Abc_123-Xyz"
VIDEO = parse_youtube_url(URL).video_id
OPERATION = "discord:901"
NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
ENVIRONMENT = {"DISCORD_OWNER_USER_ID": str(OWNER), "DB_HOST": "127.0.0.1", "DB_PORT": "3307", "DB_NAME": "jukebox101_test",
               "DB_USER": "jukebox101_test_exchange", "DB_PASSWORD": "synthetic-not-a-secret"}  # [synthetic environment]: Only for the offline factory.


@pytest.fixture
def world():
    """@description: Fake database and a real repository with one pending intake, plus a factory the CLI can receive.
    @return: Tuple of database, repository and factory.
    """
    database = FakeDatabase()
    database.now = NOW.replace(tzinfo=None)
    database.state["control"].update(snapshot_ready=True, legacy_writers_quiesced=True, settlement_enabled=False)
    repository = SubmissionRepository(database.connect, OWNER)
    repository.accept_pending(123, URL, OPERATION)
    return database, repository, lambda settings, owner_id: repository


def run(argv, environ, factory) -> int:
    """@description: Run the CLI with an injected environment and repository factory, returning its exit code.
    @param argv: Arguments after the program name.
    @param environ: Environment mapping the CLI may read.
    @param factory: Repository factory standing in for the database connection.
    @return: Exit code.
    """
    return main(argv, environ=environ, repository_factory=factory, clock=lambda: NOW)


def song(database) -> dict:
    """@description: The committed song row of the fixture video.
    @param database: Fake database.
    @return: Song row.
    """
    return next(row for row in database.state["songs"].values() if row["platform_track_id"] == VIDEO)


def test_no_subcommand_connects_to_nothing_and_writes_nothing(world) -> None:
    """@description: Running the CLI without a subcommand never builds a repository or touches the database.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    calls = []
    assert run([], ENVIRONMENT, lambda settings, owner_id: calls.append(1)) == 0
    assert calls == []


def test_a_missing_explicit_port_refuses_before_any_connection(world) -> None:
    """@description: The port must be stated explicitly; a missing port refuses before any repository is built, so no default port path exists.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    environment = dict(ENVIRONMENT)
    del environment["DB_PORT"]
    calls = []
    assert run(["duration", "preview", "--operation", OPERATION, "--seconds", "180", "--source", "owner", "--decision-id", "d1", "--reason", "owner duration",
                "--full-video-confirmed", "--reliable-confirmed"], environment, lambda s, o: calls.append(1)) == 1
    assert calls == []


def test_an_actor_argument_is_not_accepted(world) -> None:
    """@description: The acting Owner is never taken from the command line.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    with pytest.raises(SystemExit):
        run(["duration", "preview", "--operation", OPERATION, "--actor", "999", "--seconds", "180", "--source", "owner",
             "--decision-id", "d1", "--full-video-confirmed", "--reliable-confirmed"], ENVIRONMENT, lambda s, o: None)


def test_the_configured_owner_identity_must_be_a_positive_integer(world) -> None:
    """@description: A missing or malformed Owner identity refuses before any connection.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    environment = dict(ENVIRONMENT, DISCORD_OWNER_USER_ID="not-a-number")
    calls = []
    assert run(["duration", "preview", "--operation", OPERATION, "--seconds", "180", "--source", "owner", "--decision-id", "d1", "--reason", "owner duration",
                "--full-video-confirmed", "--reliable-confirmed"], environment, lambda s, o: calls.append(1)) == 1
    assert calls == []


@pytest.mark.parametrize("seconds, verdict", [(60, "PASS"), (720, "PASS"), (59, "REJECT"), (721, "REJECT")])
def test_the_preview_states_the_boundary_verdict_and_writes_nothing(world, capsys, seconds, verdict) -> None:
    """@description: The preview states PASS at 60 and 720 seconds and REJECT just outside, and the out-of-range warning is shown; nothing is written.
    @param world: Fake database, repository and factory.
    @param capsys: Captured output.
    @param seconds: Owner-stated seconds under test.
    @param verdict: Expected verdict text.
    @return: None.
    """
    database, repository, factory = world
    before = dict(song(database))
    assert run(["duration", "preview", "--operation", OPERATION, "--seconds", str(seconds), "--source", "owner-observed:2026-10-24",
                "--decision-id", "dur-preview", "--reason", "owner duration", "--full-video-confirmed", "--reliable-confirmed"], ENVIRONMENT, factory) == 0
    output = capsys.readouterr().out
    assert verdict in output
    if verdict == "REJECT":
        assert "最終拒絕" in output
    assert song(database) == before
    assert repository.review_view(OWNER, OPERATION).review.status is ReviewStatus.PENDING_VALIDATION


def test_a_write_without_the_matching_digest_is_refused_and_writes_nothing(world) -> None:
    """@description: A write whose digest does not match the previewed inputs and revision is refused before any write.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    database, repository, factory = world
    assert run(["duration", "write", "--operation", OPERATION, "--seconds", "180", "--source", "owner-observed:2026-10-24",
                "--decision-id", "dur-bad", "--reason", "owner duration", "--expected-revision", "0", "--digest", "0" * 64,
                "--full-video-confirmed", "--reliable-confirmed"], ENVIRONMENT, factory) == 1
    assert repository.review_view(OWNER, OPERATION).review.status is ReviewStatus.PENDING_VALIDATION


def test_a_matching_digest_writes_the_duration_once_and_a_replay_repeats_nothing(world) -> None:
    """@description: The confirmed duration is recorded for the operation's own video and revision; repeating the same decision identifier changes nothing.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    database, repository, factory = world
    digest = duration_digest(OPERATION, 0, VIDEO, 180, "owner-observed:2026-10-24", "dur-ok", "owner duration", OWNER)
    argv = ["duration", "write", "--operation", OPERATION, "--seconds", "180", "--source", "owner-observed:2026-10-24",
            "--decision-id", "dur-ok", "--reason", "owner duration", "--expected-revision", "0", "--digest", digest,
            "--full-video-confirmed", "--reliable-confirmed"]
    assert run(argv, ENVIRONMENT, factory) == 0
    review = repository.review_view(OWNER, OPERATION).review
    assert review.status is ReviewStatus.PENDING_REVIEW and review.duration.seconds == 180
    version = review.version
    assert run(argv, ENVIRONMENT, factory) == 0
    assert repository.review_view(OWNER, OPERATION).review.version == version


def test_a_stale_expected_revision_is_refused_and_writes_nothing(world) -> None:
    """@description: A duration decision made against an old revision is refused, even with a matching digest.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    database, repository, factory = world
    digest = duration_digest(OPERATION, 5, VIDEO, 180, "owner-observed:2026-10-24", "dur-stale", "owner duration", OWNER)
    assert run(["duration", "write", "--operation", OPERATION, "--seconds", "180", "--source", "owner-observed:2026-10-24",
                "--decision-id", "dur-stale", "--reason", "owner duration", "--expected-revision", "5", "--digest", digest,
                "--full-video-confirmed", "--reliable-confirmed"], ENVIRONMENT, factory) == 1
    assert repository.review_view(OWNER, OPERATION).review.status is ReviewStatus.PENDING_VALIDATION


def test_the_duration_is_bound_to_the_operations_own_video_and_has_no_video_argument(world) -> None:
    """@description: The CLI derives the video from the stored operation; a video argument is not accepted, so a duration cannot name another video.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    with pytest.raises(SystemExit):
        run(["duration", "preview", "--operation", OPERATION, "--video", "OtherVideo01", "--seconds", "180", "--source", "owner",
             "--decision-id", "d2", "--full-video-confirmed", "--reliable-confirmed"], ENVIRONMENT, lambda s, o: None)


def test_a_non_owner_configured_identity_is_refused_by_the_repository_and_writes_nothing(world) -> None:
    """@description: An environment naming an identity other than the repository's Owner is refused by the Owner gate; nothing is written.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    database, repository, factory = world
    digest = duration_digest(OPERATION, 0, VIDEO, 180, "owner-observed:2026-10-24", "dur-x", "owner duration", 999)
    assert run(["duration", "write", "--operation", OPERATION, "--seconds", "180", "--source", "owner-observed:2026-10-24",
                "--decision-id", "dur-x", "--reason", "owner duration", "--expected-revision", "0", "--digest", digest,
                "--full-video-confirmed", "--reliable-confirmed"], dict(ENVIRONMENT, DISCORD_OWNER_USER_ID="999"), factory) == 1
    assert repository.review_view(OWNER, OPERATION).review.status is ReviewStatus.PENDING_VALIDATION


def test_an_approval_preview_writes_nothing_and_a_refused_approval_publishes_nothing(world) -> None:
    """@description: Approval preview writes nothing, and an approval the gate refuses (no duration yet) writes nothing and publishes no link.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    database, repository, factory = world
    assert run(["approve", "preview", "--operation", OPERATION, "--expected-revision", "0", "--reason", "owner approval",
                "--evidence-reference", "owner-observed:2026-10-24", "--is-song-confirmed"], ENVIRONMENT, factory) == 1
    assert song(database)["youtube_url"] is None


def test_a_confirmed_approval_publishes_the_verified_link_and_a_failed_publish_rolls_back(world) -> None:
    """@description: After a passing duration, the confirmed approval publishes the link in its transaction; a failed publish leaves the review pending.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    database, repository, factory = world
    digest = duration_digest(OPERATION, 0, VIDEO, 180, "owner-observed:2026-10-24", "dur-a", "owner duration", OWNER)
    assert run(["duration", "write", "--operation", OPERATION, "--seconds", "180", "--source", "owner-observed:2026-10-24",
                "--decision-id", "dur-a", "--reason", "owner duration", "--expected-revision", "0", "--digest", digest,
                "--full-video-confirmed", "--reliable-confirmed"], ENVIRONMENT, factory) == 0
    revision = repository.review_view(OWNER, OPERATION).review.version
    approval_digest = duration_digest(OPERATION, revision, VIDEO, None, "owner-observed:2026-10-24", "approve", "owner approval", OWNER,
                                      approve=True)
    database.sql_fault = "UPDATE songs"
    assert run(["approve", "write", "--operation", OPERATION, "--expected-revision", str(revision), "--reason", "owner approval",
                "--evidence-reference", "owner-observed:2026-10-24", "--is-song-confirmed", "--digest", approval_digest], ENVIRONMENT,
               factory) == 1
    assert repository.review_view(OWNER, OPERATION).review.status is ReviewStatus.PENDING_REVIEW
    assert song(database)["youtube_url"] is None
    database.sql_fault = None
    assert run(["approve", "write", "--operation", OPERATION, "--expected-revision", str(revision), "--reason", "owner approval",
                "--evidence-reference", "owner-observed:2026-10-24", "--is-song-confirmed", "--digest", approval_digest], ENVIRONMENT,
               factory) == 0
    assert repository.review_view(OWNER, OPERATION).review.status is ReviewStatus.APPROVED
    assert song(database)["youtube_url"] == f"https://www.youtube.com/watch?v={VIDEO}"
    assert song(database)["resolution_status"] == "verified"


def test_an_approval_preview_prints_the_digest_its_write_accepts_and_a_changed_input_is_refused(world, capsys) -> None:
    """@description: The approval preview shows the digest that the write needs, writes nothing, and a write whose reason changed is refused with the old digest.
    @param world: Fake database, repository and factory.
    @param capsys: Pytest output capture.
    @return: None.
    """
    database, repository, factory = world
    digest = duration_digest(OPERATION, 0, VIDEO, 180, "owner-observed:2026-10-24", "dur-p", "owner duration", OWNER)
    assert run(["duration", "write", "--operation", OPERATION, "--seconds", "180", "--source", "owner-observed:2026-10-24",
                "--decision-id", "dur-p", "--reason", "owner duration", "--expected-revision", "0", "--digest", digest,
                "--full-video-confirmed", "--reliable-confirmed"], ENVIRONMENT, factory) == 0
    revision = repository.review_view(OWNER, OPERATION).review.version
    status_before = repository.review_view(OWNER, OPERATION).review.status
    capsys.readouterr()
    preview_arguments = ["approve", "preview", "--operation", OPERATION, "--expected-revision", str(revision), "--reason", "owner approval",
                         "--evidence-reference", "owner-observed:2026-10-24", "--is-song-confirmed"]
    assert run(preview_arguments, ENVIRONMENT, factory) == 0
    digest_lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("digest: ")]
    assert digest_lines, "approval preview prints no digest"  # [red reason]: The operator has no digest to pass to the write.
    preview_digest = digest_lines[0].split(": ", 1)[1]
    assert repository.review_view(OWNER, OPERATION).review.status is status_before and song(database)["youtube_url"] is None
    changed = ["approve", "write", "--operation", OPERATION, "--expected-revision", str(revision), "--reason", "owner approval (changed)",
               "--evidence-reference", "owner-observed:2026-10-24", "--is-song-confirmed", "--digest", preview_digest]
    assert run(changed, ENVIRONMENT, factory) == 1
    assert song(database)["youtube_url"] is None
    accepted = ["approve", "write", "--operation", OPERATION, "--expected-revision", str(revision), "--reason", "owner approval",
                "--evidence-reference", "owner-observed:2026-10-24", "--is-song-confirmed", "--digest", preview_digest]
    assert run(accepted, ENVIRONMENT, factory) == 0
    assert song(database)["youtube_url"] == f"https://www.youtube.com/watch?v={VIDEO}"


def test_a_duration_decision_adds_evidence_to_the_same_operation_without_a_second_submission(world) -> None:
    """@description: Recording the duration never creates another intake event; a repeated public submission of the same link returns the original receipt.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    database, repository, factory = world
    events_before = len(database.state["jb2_events"])
    digest = duration_digest(OPERATION, 0, VIDEO, 180, "owner-observed:2026-10-24", "dur-same", "owner duration", OWNER)
    assert run(["duration", "write", "--operation", OPERATION, "--seconds", "180", "--source", "owner-observed:2026-10-24",
                "--decision-id", "dur-same", "--reason", "owner duration", "--expected-revision", "0", "--digest", digest,
                "--full-video-confirmed", "--reliable-confirmed"], ENVIRONMENT, factory) == 0
    assert len(database.state["jb2_events"]) == events_before
    again = repository.accept_pending(123, URL, OPERATION)
    assert again.create_share_event is False
    assert len(database.state["jb2_events"]) == events_before


def test_a_duration_write_without_the_explicit_full_video_and_reliability_statements_is_refused(world) -> None:
    """@description: The Owner must state full-video observation and reliability explicitly; without both statements nothing is written.
    @param world: Fake database, repository and factory.
    @return: None.
    """
    database, repository, factory = world
    digest = duration_digest(OPERATION, 0, VIDEO, 180, "owner-observed:2026-10-24", "dur-no-flags", "owner duration", OWNER)
    assert run(["duration", "write", "--operation", OPERATION, "--seconds", "180", "--source", "owner-observed:2026-10-24",
                "--decision-id", "dur-no-flags", "--reason", "owner duration", "--expected-revision", "0", "--digest", digest],
               ENVIRONMENT, factory) == 1
    assert repository.review_view(OWNER, OPERATION).review.status is ReviewStatus.PENDING_VALIDATION
