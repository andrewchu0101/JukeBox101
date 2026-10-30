"""Verify the N1/N2 Owner path on the explicitly isolated MySQL target: formal duration and approval through the operator CLI, version confirmation through the existing Owner service, publication of the verified link, approval replay, injected publish failure, non-publishing decisions, and a restricted settlement window followed by a repository exchange.

Every video, user and operation identifier is synthetic and carries the N12 prefix. Durations and version names are labelled test values, never platform facts. No Discord client, direct message or delivery sender is used; the exchange stops at the reservation. Nothing here creates or alters schema, grants, or rows outside the N12 identifiers, and no append-only audit row is deleted.

Opt-in only: every test is skipped unless JUKEBOX_RUN_EXCHANGE_MYSQL_ISOLATED=1 and the isolated target is configured.
"""

import os
import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from bot import owner_cli
from bot.database.exchange_reservation import ExchangeStatus
from bot.database.repository import _ensure_user
from bot.database.review_persistence import ReviewCommand, _publish_owner_approved_link
from bot.database.submission_repository import SubmissionRepository
from bot.evidence_record import EvidenceSourceType, record_evidence
from bot.owner_cli import APPROVAL_LABEL, duration_digest
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import ApprovalSource, ReviewStatus
from bot.reward_candidates import RewardCandidateStatus
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.urls import canonical_watch_url
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus
from tests.isolated_mysql_target import ISOLATION_SWITCH, WINDOW_SWITCH, open_verified_connection, window_settings_from_environment
from tests.live_acceptance_guard import assert_control_transition, assert_settlement_scope
from tests.test_exchange_mysql_integration import isolated, require_migration_003  # noqa: F401  (isolated: fixture imported by name)

pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(os.environ.get(ISOLATION_SWITCH) != "1", reason="Requires explicitly authorized isolated MySQL target"),
]

OWNER = 777  # [synthetic Owner]: A test identity only; the real Owner identifier is never used here.
NOW = datetime.now(timezone.utc).replace(microsecond=0)  # [fixed clock]: Timezone-aware for the Owner service and the CLI.
RUN = "n12r2"  # [run prefix]: Second run of this file; the first run keeps its rows as recorded evidence.
RUN_OFFSET = 100  # [identifier offset]: Keeps this run's videos, users and operations apart from the first run.
USER_BASE = 900000002200  # [synthetic users]: Outside every earlier fixture range; each number adds one user.
SOURCE_LABEL = "synthetic-owner-stated-duration:n12"  # [test duration label]: Not platform evidence; recorded as the source.
ENVIRON = {"DISCORD_OWNER_USER_ID": str(OWNER), "DB_HOST": "127.0.0.1", "DB_PORT": "3307", "DB_NAME": "jukebox101_test",
           "DB_USER": "jukebox101_test_exchange", "DB_PASSWORD": "unused-factory-supplies-the-connection"}  # [names only]: Factory replaces the connection.


class Probe:
    """Record every UPDATE songs row count observed on the real connection and hold an optional injected failure for that statement."""

    def __init__(self) -> None:
        """@description: Start with no observations and no injected failure.
        @return: None.
        """
        self.publish_updates = []  # [observed rowcounts]: MySQL affected-row count of each UPDATE songs statement, in order.
        self.fault = None  # [injected failure]: Statement prefix that raises before it runs; None when no fault is injected.


class ObservedCursor:
    """Wrap a real PyMySQL cursor to observe and optionally fail UPDATE songs statements; every other statement runs unchanged."""

    def __init__(self, cursor, probe: Probe) -> None:
        """@description: Keep the real cursor and the probe that observes it.
        @param cursor: Real PyMySQL cursor.
        @param probe: Observation and fault holder.
        @return: None.
        """
        self._cursor = cursor
        self._probe = probe

    def execute(self, statement: str, arguments=None):
        """@description: Run the statement on MySQL, failing first when the probe injects a fault for it, and record the affected-row count of song updates.
        @param statement: SQL text.
        @param arguments: Bound parameters.
        @return: Result of the real execute.
        """
        if self._probe.fault and statement.lstrip().startswith(self._probe.fault):
            raise RuntimeError("injected publish failure")  # [injected]: Raised inside the transaction, so the approval must roll back.
        result = self._cursor.execute(statement, arguments)
        if statement.lstrip().startswith("UPDATE songs"):
            self._probe.publish_updates.append(self._cursor.rowcount)
        return result

    def __enter__(self):
        """@description: Support the with-statement the production code uses.
        @return: This wrapper.
        """
        return self

    def __exit__(self, *details):
        """@description: Close the real cursor when the with-block ends.
        @param details: Exception details, passed through.
        @return: Result of the real cursor's exit.
        """
        return self._cursor.__exit__(*details)

    def __getattr__(self, name: str):
        """@description: Delegate every other cursor attribute to the real cursor.
        @param name: Attribute name.
        @return: The real cursor's attribute.
        """
        return getattr(self._cursor, name)


class ObservedConnection:
    """Wrap a real PyMySQL connection so that every cursor it hands out is observed; the connection itself is not replaced."""

    def __init__(self, connection, probe: Probe) -> None:
        """@description: Keep the real connection and the probe.
        @param connection: Real verified connection.
        @param probe: Observation and fault holder shared by the cursors.
        @return: None.
        """
        self._connection = connection
        self._probe = probe

    def cursor(self, *arguments, **keywords):
        """@description: Return an observed cursor over a real cursor.
        @param arguments: Passed to the real connection.
        @param keywords: Passed to the real connection.
        @return: Observed cursor.
        """
        return ObservedCursor(self._connection.cursor(*arguments, **keywords), self._probe)

    def __getattr__(self, name: str):
        """@description: Delegate transaction and close calls to the real connection.
        @param name: Attribute name.
        @return: The real connection's attribute.
        """
        return getattr(self._connection, name)


def video_of(number: int) -> str:
    """@description: Name the synthetic 11-character video identifier of one number.
    @param number: Synthetic number.
    @return: Video identifier.
    """
    return f"N12Vid{number + RUN_OFFSET:05d}"


def operation_of(number: int) -> str:
    """@description: Name the synthetic accepted operation of one number.
    @param number: Synthetic number.
    @return: Operation identifier.
    """
    return f"{RUN}-op-{number + RUN_OFFSET}"


def user_of(number: int) -> int:
    """@description: Name the synthetic Discord user of one number.
    @param number: Synthetic number.
    @return: Discord user identifier.
    """
    return USER_BASE + number + RUN_OFFSET


@pytest.fixture
def target(isolated):
    """@description: Build the production repository and Owner service over the default isolated target, with observation available.
    @param isolated: Verified isolated settings and identity pin.
    @return: Namespace with the repository, the service, the probe and the settings used for raw checks.
    """
    return make_target(*isolated)


@pytest.fixture
def window_target():
    """@description: Build the same namespace over the dedicated window schema, used only by the settlement window test; the default target never resolves there.
    @return: Namespace bound to the window schema after its migration shape is verified.
    """
    settings, pin = window_settings_from_environment()
    connection = open_verified_connection(settings, pin)
    try:
        require_migration_003(connection)
    finally:
        connection.close()
    return make_target(settings, pin)


def make_target(settings, pin) -> SimpleNamespace:
    """@description: Build the production repository and Owner service over one verified destination, with observation available.
    @param settings: Validated destination.
    @param pin: Expected identity of that destination.
    @return: Namespace with the repository, the service, the probe and the settings used for raw checks.
    """
    probe = Probe()
    repository = SubmissionRepository(lambda: ObservedConnection(open_verified_connection(settings, pin), probe), OWNER)
    service = OwnerReviewService(repository, OWNER, lambda: NOW, token_ttl=timedelta(minutes=5))
    return SimpleNamespace(settings=settings, pin=pin, probe=probe, repository=repository, service=service)


def raw_rows(target, statement: str, arguments: tuple = ()) -> list:
    """@description: Read committed rows through a separate verified connection, for checks outside the production path.
    @param target: Isolated namespace.
    @param statement: Read-only SQL.
    @param arguments: Bound parameters.
    @return: Rows as dictionaries.
    """
    connection = open_verified_connection(target.settings, target.pin)
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, arguments)
            return cursor.fetchall()
    finally:
        connection.close()


def raw_write(target, statement: str, arguments: tuple = ()) -> int:
    """@description: Apply one control-row write through a verified connection and commit it, for the settlement window only.
    @param target: Isolated namespace.
    @param statement: SQL that changes only the named switch.
    @param arguments: Bound parameters.
    @return: Affected-row count.
    """
    connection = open_verified_connection(target.settings, target.pin)
    try:
        with connection.cursor() as cursor:
            cursor.execute(statement, arguments)
            affected = cursor.rowcount
        connection.commit()
        return affected
    finally:
        connection.close()


def song_of(target, number: int) -> dict:
    """@description: Read the committed song row of one synthetic video.
    @param target: Isolated namespace.
    @param number: Synthetic number.
    @return: The song row.
    """
    rows = raw_rows(target, "SELECT resolution_status, youtube_url, verification_provenance FROM songs WHERE platform='youtube' AND platform_track_id=%s",
                    (video_of(number),))
    assert len(rows) == 1
    return rows[0]


def ensure_account(target, discord_user_id: int) -> None:
    """@description: Create the account row a reward needs, through the reviewed helper the award path already uses, and commit it.
    @param target: Isolated namespace.
    @param discord_user_id: Synthetic account identifier.
    @return: None.
    """
    connection = open_verified_connection(target.settings, target.pin)
    try:
        with connection.cursor() as cursor:
            _ensure_user(cursor, discord_user_id)
        connection.commit()
    finally:
        connection.close()


def balance_of(target, discord_user_id: int) -> int:
    """@description: Read one account's committed credit balance.
    @param target: Isolated namespace.
    @param discord_user_id: Synthetic account identifier.
    @return: Credit balance.
    """
    return raw_rows(target, "SELECT credit_balance FROM users WHERE discord_user_id=%s", (discord_user_id,))[0]["credit_balance"]


def cli(target, capsys, *arguments: str) -> str:
    """@description: Run one operator CLI subcommand over the isolated repository and return what it printed.
    @param target: Isolated namespace whose repository the CLI receives through the factory.
    @param capsys: Pytest output capture.
    @param arguments: Subcommand arguments.
    @return: Printed text; the subcommand must exit 0.
    """
    code = owner_cli.main(list(arguments), environ=ENVIRON, repository_factory=lambda settings, owner: target.repository, clock=lambda: NOW)
    output = capsys.readouterr().out
    assert code == 0, output
    return output


def digest_from(output: str) -> str:
    """@description: Extract the digest line printed by a duration preview.
    @param output: Preview text.
    @return: Digest value.
    """
    return next(line.split(": ", 1)[1] for line in output.splitlines() if line.startswith("digest: "))


def intake(target, number: int) -> None:
    """@description: Accept one synthetic pending submission through the production intake boundary.
    @param target: Isolated namespace.
    @param number: Synthetic number.
    @return: None.
    """
    target.repository.accept_pending(user_of(number), f"https://youtu.be/{video_of(number)}", operation_of(number))


def formal_duration(target, capsys, number: int, seconds: int, decision: str) -> str:
    """@description: Record an Owner-stated full-video duration through the operator CLI preview and then its digest-bound write.
    @param target: Isolated namespace.
    @param capsys: Pytest output capture.
    @param number: Synthetic number.
    @param seconds: Test duration.
    @param decision: Decision identifier.
    @return: Output of the write.
    """
    common = ["--operation", operation_of(number), "--seconds", str(seconds), "--source", SOURCE_LABEL, "--decision-id", decision,
              "--reason", "synthetic full-video duration", "--full-video-confirmed", "--reliable-confirmed"]
    digest = digest_from(cli(target, capsys, "duration", "preview", *common))
    revision = target.repository.review_view(OWNER, operation_of(number)).review.version
    return cli(target, capsys, "duration", "write", *common, "--expected-revision", str(revision), "--digest", digest)


def formal_approval(target, capsys, number: int) -> str:
    """@description: Approve one synthetic submission through the operator CLI: preview, then a write bound to the digest of the same arguments.
    @param target: Isolated namespace.
    @param capsys: Pytest output capture.
    @param number: Synthetic number.
    @return: Output of the write.
    """
    operation = operation_of(number)
    revision = target.repository.review_view(OWNER, operation).review.version
    reason, reference = "synthetic owner approval", f"synthetic-approval-{number}"
    common = ["--operation", operation, "--expected-revision", str(revision), "--reason", reason, "--evidence-reference", reference,
              "--is-song-confirmed"]
    cli(target, capsys, "approve", "preview", *common)
    digest = duration_digest(operation, revision, video_of(number), None, reference, APPROVAL_LABEL, reason, OWNER, approve=True)
    return cli(target, capsys, "approve", "write", *common, "--digest", digest)


def confirm_version(target, number: int, version: str) -> VersionIdentityStatus:
    """@description: Stamp a synthetic recording version through the existing Owner service entry; the operator CLI has no version command.
    @param target: Isolated namespace.
    @param number: Synthetic number.
    @param version: Synthetic version name.
    @return: Identity status after the confirmation.
    """
    operation = operation_of(number)
    evidence = record_evidence(f"{RUN}-ev-{number + RUN_OFFSET}", VideoIdentity(video_of(number)), EvidenceSourceType.OWNER_REVIEW,
                               f"synthetic-n12-ev-{number}", NOW, "synthetic inspection", OWNER, RecordingVersionIdentity(version))
    revision = target.service.view(OWNER, operation).review.version
    token = target.service.preview_version_confirmation(OWNER, operation, evidence, "synthetic version confirmation", revision).token_id
    target.service.confirm(OWNER, token)
    return target.repository.review_view(OWNER, operation).identity.status


def test_formal_path_publishes_the_song_after_duration_approval_and_version_confirmation(target, capsys):
    """@description: Pending intake, CLI duration, CLI approval, then version confirmation publish the canonical link and leave the identity confirmed.
    @param target: Isolated namespace.
    @param capsys: Pytest output capture.
    @return: None.
    """
    intake(target, 1)
    assert song_of(target, 1)["resolution_status"] == "pending" and song_of(target, 1)["youtube_url"] is None
    formal_duration(target, capsys, 1, 180, f"{RUN}-dur-1")
    assert song_of(target, 1)["youtube_url"] is None
    formal_approval(target, capsys, 1)
    assert target.repository.review_view(OWNER, operation_of(1)).review.status is ReviewStatus.APPROVED
    song = song_of(target, 1)
    assert song["resolution_status"] == "verified"
    assert song["youtube_url"] == canonical_watch_url(video_of(1))
    assert song["verification_provenance"].startswith("owner_approval:")
    assert confirm_version(target, 1, "n12-version-1") is VersionIdentityStatus.CONFIRMED
    assert song_of(target, 1)["youtube_url"] == canonical_watch_url(video_of(1))


def test_the_approval_update_reports_one_affected_row_and_a_repeat_publish_reports_zero(target, capsys):
    """@description: The first publish changes exactly one row on MySQL; a repeat of the same publish matches no pending row, reports zero, and leaves the link unchanged.
    @param target: Isolated namespace.
    @param capsys: Pytest output capture.
    @return: None.
    """
    intake(target, 2)
    formal_duration(target, capsys, 2, 180, f"{RUN}-dur-2")
    target.probe.publish_updates.clear()
    formal_approval(target, capsys, 2)
    assert target.probe.publish_updates == [1]
    target.probe.publish_updates.clear()
    review = target.repository.review_view(OWNER, operation_of(2)).review
    command = ReviewCommand(f"{RUN}-repeat-2", operation_of(2), OWNER, "synthetic repeat probe", review.version,
                            approval_source=ApprovalSource.OWNER)
    connection = ObservedConnection(open_verified_connection(target.settings, target.pin), target.probe)
    try:
        with connection.cursor() as cursor:
            _publish_owner_approved_link(cursor, command, review.video, review)
        connection.commit()
    finally:
        connection.close()
    assert target.probe.publish_updates == [0]
    assert song_of(target, 2)["youtube_url"] == canonical_watch_url(video_of(2))


def test_replaying_an_approved_token_does_not_publish_again(target, capsys):
    """@description: Confirming an already used approval token neither changes the published song nor records a second publish.
    @param target: Isolated namespace.
    @param capsys: Pytest output capture.
    @return: None.
    """
    intake(target, 3)
    formal_duration(target, capsys, 3, 180, f"{RUN}-dur-3")
    operation = operation_of(3)
    revision = target.service.view(OWNER, operation).review.version
    token = target.service.preview(OWNER, operation, ReviewAction.APPROVE, "synthetic owner approval", revision, "synthetic-approval-3",
                                   content=_judgement(3)).token_id
    target.service.confirm(OWNER, token)
    before = song_of(target, 3)
    target.probe.publish_updates.clear()
    try:
        target.service.confirm(OWNER, token)
        observed = "returned without error"
    except Exception as error:  # [observed behaviour]: The replay outcome is recorded, not assumed.
        observed = type(error).__name__
    print(f"replay of an approved token on MySQL: {observed}")
    assert target.probe.publish_updates == []
    assert song_of(target, 3) == before


def _judgement(number: int) -> ContentJudgement:
    """@description: Build the synthetic content judgement the approval needs for one video.
    @param number: Synthetic number.
    @return: Content judgement about that video.
    """
    return ContentJudgement(VideoIdentity(video_of(number)), OWNER, "synthetic owner content judgement", NOW, f"synthetic-approval-{number}", True)


def test_an_injected_publish_failure_rolls_the_approval_back_on_mysql(target, capsys):
    """@description: A failing song update after the review write leaves the review pending and the song unpublished on the real database.
    @param target: Isolated namespace.
    @param capsys: Pytest output capture.
    @return: None.
    """
    intake(target, 4)
    formal_duration(target, capsys, 4, 180, f"{RUN}-dur-4")
    operation = operation_of(4)
    revision = target.service.view(OWNER, operation).review.version
    token = target.service.preview(OWNER, operation, ReviewAction.APPROVE, "synthetic owner approval", revision, "synthetic-approval-4",
                                   content=_judgement(4)).token_id
    target.probe.fault = "UPDATE songs"
    with pytest.raises(RuntimeError, match="injected publish failure"):
        target.service.confirm(OWNER, token)
    target.probe.fault = None
    assert target.repository.review_view(OWNER, operation).review.status is ReviewStatus.PENDING_REVIEW
    song = song_of(target, 4)
    assert song["resolution_status"] == "pending" and song["youtube_url"] is None


def test_a_duration_only_and_a_rejection_publish_nothing_on_mysql(target, capsys):
    """@description: With only the duration recorded, and after an explicit rejection, the song stays pending and unlinked.
    @param target: Isolated namespace.
    @param capsys: Pytest output capture.
    @return: None.
    """
    intake(target, 5)
    formal_duration(target, capsys, 5, 180, f"{RUN}-dur-5")
    assert song_of(target, 5)["youtube_url"] is None
    operation = operation_of(5)
    revision = target.service.view(OWNER, operation).review.version
    token = target.service.preview(OWNER, operation, ReviewAction.REJECT, "synthetic owner rejection", revision, "synthetic-reject-5").token_id
    assert target.service.confirm(OWNER, token).status is ReviewStatus.REJECTED
    song = song_of(target, 5)
    assert song["resolution_status"] == "pending" and song["youtube_url"] is None


def test_an_out_of_range_duration_is_a_final_rejection_that_publishes_nothing_on_mysql(target, capsys):
    """@description: A 721-second test duration is a final rejection through the CLI, and the song is never published.
    @param target: Isolated namespace.
    @param capsys: Pytest output capture.
    @return: None.
    """
    intake(target, 6)
    formal_duration(target, capsys, 6, 721, f"{RUN}-dur-6")
    assert target.repository.review_view(OWNER, operation_of(6)).review.status is ReviewStatus.REJECTED
    assert song_of(target, 6)["youtube_url"] is None


@pytest.mark.skipif(os.environ.get(WINDOW_SWITCH) != "1", reason="Requires the explicitly authorized window schema switch")
def test_the_settlement_window_settles_only_the_identified_operations_and_the_peer_song_becomes_the_candidate(window_target, capsys):
    """@description: Inside a restricted window the two identified operations, each approved, verified and version-confirmed, settle once each; the requester's exchange debits one point and reserves the peer's song, never the requester's own, with no direct message.
    @param target: Isolated namespace.
    @param capsys: Pytest output capture.
    @return: None.
    """
    target = window_target
    requester, peer = 7, 8
    for number in (requester, peer):
        intake(target, number)
        formal_duration(target, capsys, number, 180, f"{RUN}-dur-{number}")
        formal_approval(target, capsys, number)
        assert confirm_version(target, number, f"{RUN}-version-{number}") is VersionIdentityStatus.CONFIRMED
        ensure_account(target, user_of(number))
        assert target.repository.review_view(OWNER, operation_of(number)).review.status is ReviewStatus.APPROVED
        assert song_of(target, number)["resolution_status"] == "verified"
        assert song_of(target, number)["youtube_url"] == canonical_watch_url(video_of(number))
    settled_scope = {operation_of(requester), operation_of(peer)}
    before = raw_rows(target, "SELECT schema_version, snapshot_ready, legacy_writers_quiesced, settlement_enabled, "
                              "accepted_sequence, revision FROM jb2_control WHERE singleton_id=1")[0]
    assert before["settlement_enabled"] == 0
    assert raw_write(target, "UPDATE jb2_control SET settlement_enabled=1 WHERE singleton_id=1") == 1
    try:
        awarded = {operation: target.repository.settle(operation).status for operation in sorted(settled_scope)}
    finally:
        assert raw_write(target, "UPDATE jb2_control SET settlement_enabled=0 WHERE singleton_id=1") == 1
        after = raw_rows(target, "SELECT schema_version, snapshot_ready, legacy_writers_quiesced, settlement_enabled, "
                                 "accepted_sequence, revision FROM jb2_control WHERE singleton_id=1")[0]
        assert after["settlement_enabled"] == 0
        assert_control_transition(before, after, {"settlement_enabled", "revision", "accepted_sequence"})
    assert awarded == {operation_of(requester): RewardCandidateStatus.AWARDED, operation_of(peer): RewardCandidateStatus.AWARDED}
    claimed = {row["operation_id"] for row in raw_rows(target, "SELECT operation_id FROM jb2_claims")}
    assert_settlement_scope(claimed, settled_scope)  # [business scope]: Exactly the two identified operations hold a first-version claim.
    for number in (requester, peer):
        assert ledger_count(target, user_of(number), "submission_award") == 1  # [one award each]: One +1 entry per settled operation.
        assert balance_of(target, user_of(number)) == 1
    outcome = target.repository.exchange(user_of(requester), f"{RUN}-request-1", random.Random(1), now=NOW)
    assert outcome.status is ExchangeStatus.RESERVED
    assert outcome.youtube_url == canonical_watch_url(video_of(peer))  # [candidate pool]: The peer's settled, verified song is the only eligible one.
    assert outcome.youtube_url != canonical_watch_url(video_of(requester))  # [own song excluded]: A requester never receives their own submission.
    assert ledger_count(target, user_of(requester), "exchange_debit") == 1  # [one debit]: One point for one reservation.
    assert balance_of(target, user_of(requester)) == 0
    assert balance_of(target, user_of(peer)) == 1
    reserved = raw_rows(target, "SELECT song_id FROM recommendations WHERE recommendation_id=%s", (outcome.recommendation_id,))
    assert reserved[0]["song_id"] == raw_rows(target, "SELECT song_id FROM songs WHERE platform='youtube' AND platform_track_id=%s",
                                              (video_of(peer),))[0]["song_id"]


def ledger_count(target, discord_user_id: int, reason: str) -> int:
    """@description: Count one account's committed ledger entries of one reason.
    @param target: Isolated namespace.
    @param discord_user_id: Synthetic account identifier.
    @param reason: Ledger reason to count.
    @return: Number of entries.
    """
    return raw_rows(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE discord_user_id=%s AND reason=%s",
                    (discord_user_id, reason))[0]["total"]
