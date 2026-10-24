"""Prepare full repository exchange tests for the explicitly isolated MySQL target.
Synthetic intake, Owner review and settlement create candidates through production boundaries;
no Discord or metadata service is contacted, and opt-in is required before any connection.
"""

import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest

from bot.database.exchange_reservation import ExchangeStatus
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_codec import decode_contract
from bot.review import ReviewSnapshot
from bot.owner_review import OwnerReviewService
from tests.financial_invariants import attribution_violations
from tests.intake_fixture import seed_intake
from tests.isolated_mysql_target import ISOLATION_SWITCH, open_verified_connection
from tests.test_exchange_mysql_integration import (
    USER_A, USER_LOW, USER_HIGH, assert_account_reconciled, count_rows,
    fixture_footprint, fixture_snapshot, isolated, verified_transaction,
)
from tests.test_identity_service_path import (
    OWNER_ID, START, approve, confirm_version, owner_evidence, submission, video_of,
)

pytestmark = [pytest.mark.mysql, pytest.mark.skipif(
    os.environ.get(ISOLATION_SWITCH) != "1", reason="Requires explicitly authorized isolated MySQL target")]
USER_B = USER_A + 1  # [synthetic peer]: Only reserved test identities receive credits.
OPERATIONS = ("op-1", "op-2")  # [owned intake keys]: Reused helpers expect these exact synthetic keys.
VERSIONS = ("s1-original-a", "s1-original-b")  # [owned recording versions]: Never real metadata.
EMPTY_TABLES = (
    "daily_dispatches", "submissions", "recommendations", "credit_ledger", "users", "songs",
    "jb2_claims", "jb2_settlements", "jb2_receipts", "jb2_events", "jb2_evidence",
    "jb2_videos", "jb2_legacy", "jb2_exclusions", "jb2_reminders",
    "jb2_reminder_batches", "jb2_review_history", "jb2_decisions",
    "jb2_barriers", "jb2_exceptions", "jb2_control",
)  # [baseline guard]: Only the two pinned, unused version roots may persist between tests.


def require_empty_baseline(target) -> None:
    """@description: Refuse pre-existing operational rows and allow only pinned, unused synthetic version roots.
    @param target: Verified isolated destination.
    @return: None; missing schema or existing data is a failure, never silently deleted.
    """
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            for table in EMPTY_TABLES:
                cursor.execute(f"SELECT COUNT(*) AS total FROM {table}")
                assert cursor.fetchone()["total"] == 0, f"S1 requires empty baseline: {table}"
            cursor.execute("SELECT version_id,canonical_id,evidence_reference FROM jb2_versions")
            expected = {(version, version, f"s1-proof-{number}") for number, version in enumerate(VERSIONS, 1)}
            assert {tuple(row[column] for column in ("version_id", "canonical_id", "evidence_reference"))
                    for row in cursor.fetchall()} <= expected, "Unexpected persistent version roots"


def clear_repository_fixture(target) -> None:
    """@description: Delete only known fixture identities in foreign-key order after an owned run.
    @param target: Verified isolated destination.
    @return: None; foreign-key surprises fail cleanup instead of disabling checks.
    """
    video_ids = tuple(video_of(number).video_id for number in (1, 2))
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            for table in ("jb2_claims", "credit_ledger", "recommendations", "submissions"):
                cursor.execute(f"DELETE FROM {table} WHERE discord_user_id BETWEEN %s AND %s", (USER_LOW, USER_HIGH))
            for table in ("jb2_settlements", "jb2_receipts"):
                cursor.execute(f"DELETE FROM {table} WHERE operation_id IN (%s,%s)", OPERATIONS)
            cursor.execute("DELETE FROM jb2_events WHERE operation_id IN (%s,%s)", OPERATIONS)
            for table in ("jb2_reminders", "jb2_review_history", "jb2_evidence", "jb2_videos"):
                cursor.execute(f"DELETE FROM {table} WHERE video_id IN (%s,%s)", video_ids)
            clear_fixture_decisions(cursor, video_ids)
            cursor.execute("DELETE FROM users WHERE discord_user_id BETWEEN %s AND %s", (USER_LOW, USER_HIGH))
            cursor.execute("DELETE FROM songs WHERE platform='youtube' AND platform_track_id IN (%s,%s)", video_ids)
            cursor.execute("DELETE FROM jb2_control WHERE singleton_id=1 AND schema_version='2b2-b1'")


def clear_fixture_decisions(cursor, video_ids: tuple) -> None:
    """@description: Match audited decision subjects rather than assuming citation-free reason text.
    @param cursor: Verified isolated transaction cursor.
    @param video_ids: Exact synthetic videos owned by the fixture.
    @return: None; any unrelated decision blocks cleanup before deletion.
    """
    cursor.execute("SELECT decision_id,actor_id,result_json FROM jb2_decisions")
    for row in cursor.fetchall():
        review = decode_contract(row["result_json"], ReviewSnapshot)
        assert row["actor_id"] == OWNER_ID and review.video.video_id in video_ids
        cursor.execute("DELETE FROM jb2_decisions WHERE decision_id=%s", (row["decision_id"],))


@pytest.fixture
def repository_world(isolated):
    """@description: Own a clean isolated control row and a real repository, without schema changes.
    @param isolated: Verified target with migration 003 already present.
    @return: Target, production repository and Owner service; owned rows are cleaned on failure too.
    """
    require_empty_baseline(isolated)
    settings, pin = isolated
    repository = SubmissionRepository(lambda: open_verified_connection(settings, pin), OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, lambda: START, token_ttl=timedelta(minutes=5))
    try:
        with verified_transaction(isolated) as connection:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO jb2_control (singleton_id,schema_version,snapshot_ready,"
                               "legacy_writers_quiesced,settlement_enabled) VALUES (1,'2b2-b1',1,1,1)")
        yield isolated, repository, service
    finally:
        clear_repository_fixture(isolated)
        require_empty_baseline(isolated)


def seed_repository_candidate(world, number: int) -> None:
    """@description: Earn a synthetic credit through intake, version confirmation, approval and settlement.
    @param world: Verified target, repository and Owner service.
    @param number: One or two, selecting a reserved synthetic user and recording version.
    @return: None; no fake state mutation or direct credit award is used.
    """
    target, repository, service = world
    request = replace(submission(number), submitter_id=USER_A + number - 1)
    seed_intake(repository, request)
    confirm_version(service, number, owner_evidence(video_of(number), f"s1-proof-{number}", VERSIONS[number - 1]))
    approve(service, number)
    assert repository.settle(f"op-{number}").status.value == "awarded"
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            cursor.execute("UPDATE songs SET resolution_status='verified',youtube_url=source_url,"
                           "verification_provenance='s1-synthetic-verification' "
                           "WHERE platform='youtube' AND platform_track_id=%s", (video_of(number).video_id,))
            assert cursor.rowcount == 1
    assert_account_reconciled(target, USER_A + number - 1)


def assert_repository_state(world, recommendations: int, balance: int) -> None:
    """@description: Check committed accounting and absence of residual reservation or debit rows.
    @param world: Verified target, repository and service.
    @param recommendations: Exact expected reservation and debit count for the requester.
    @param balance: Exact expected requester credits.
    @return: None; includes peer reconciliation when that peer was seeded.
    """
    target, _, _ = world
    assert_account_reconciled(target, USER_A)
    assert count_rows(target, "SELECT COUNT(*) AS total FROM recommendations WHERE discord_user_id=%s", (USER_A,)) == recommendations
    assert count_rows(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE discord_user_id=%s AND reason='exchange_debit'", (USER_A,)) == recommendations
    assert count_rows(target, "SELECT COUNT(*) AS total FROM users WHERE discord_user_id=%s AND credit_balance=%s", (USER_A, balance)) == 1
    if count_rows(target, "SELECT COUNT(*) AS total FROM users WHERE discord_user_id=%s", (USER_B,)):
        assert_account_reconciled(target, USER_B)


def assert_footprint_unchanged(target, before: tuple) -> None:
    """@description: Prove that a rejected request wrote no reservation and no ledger row anywhere in the fixture range.
    @param target: Verified isolated destination.
    @param before: Footprint captured immediately before the rejected request.
    @return: None; any new reservation or ledger row fails the assertion.
    """
    assert fixture_footprint(target) == before


@pytest.fixture
def funded_pair(repository_world):
    """@description: Give two distinct submitters one reviewed version and one credit each.
    @param repository_world: Clean real repository fixture.
    @return: World with exactly one other-user candidate for the requester.
    """
    for number in (1, 2):
        seed_repository_candidate(repository_world, number)
    return repository_world


def test_repository_exchange_reserves_other_users_candidate(funded_pair):
    """@description: First full exchange reserves the peer song with an exactly reconciled debit.
    @param funded_pair: Two funded submitters over real MySQL.
    @return: None.
    """
    _, repository, _ = funded_pair
    outcome = repository.exchange(USER_A, "s1-first", random.Random(1))
    assert outcome.status is ExchangeStatus.RESERVED
    assert outcome.youtube_url == submission(2).source_url
    assert_repository_state(funded_pair, 1, 0)


def test_repository_exchange_replays_without_another_debit(funded_pair):
    """@description: A technical retry returns the same recommendation even after credits are spent.
    @param funded_pair: Two funded submitters over real MySQL.
    @return: None.
    """
    _, repository, _ = funded_pair
    first = repository.exchange(USER_A, "s1-replay", random.Random(1))
    replay = repository.exchange(USER_A, "s1-replay", random.Random(1))
    assert first.status is ExchangeStatus.RESERVED
    assert replay.status is ExchangeStatus.REPLAYED
    assert replay.recommendation_id == first.recommendation_id
    assert replay.youtube_url == first.youtube_url
    assert_repository_state(funded_pair, 1, 0)


def test_repository_exchange_zero_balance_leaves_no_request_rows(funded_pair):
    """@description: A fresh request after spending the only credit is a business refusal, not SQL failure.
    @param funded_pair: Two funded submitters over real MySQL.
    @return: None.
    """
    target, repository, _ = funded_pair
    assert repository.exchange(USER_A, "s1-spend", random.Random(1)).status is ExchangeStatus.RESERVED
    before = fixture_footprint(target)
    outcome = repository.exchange(USER_A, "s1-empty", random.Random(1))
    assert outcome.status is ExchangeStatus.INSUFFICIENT_CREDITS
    assert outcome.recommendation_id is None
    assert_footprint_unchanged(target, before)
    assert_repository_state(funded_pair, 1, 0)


def test_repository_exchange_own_only_has_no_candidate(repository_world):
    """@description: Own submissions cannot consume a credit when there is no other candidate.
    @param repository_world: Clean real repository fixture.
    @return: None.
    """
    seed_repository_candidate(repository_world, 1)
    target, repository, _ = repository_world
    before = fixture_footprint(target)
    outcome = repository.exchange(USER_A, "s1-own-only", random.Random(1))
    assert outcome.status is ExchangeStatus.NO_CANDIDATE
    assert outcome.recommendation_id is None
    assert_footprint_unchanged(target, before)
    assert_repository_state(repository_world, 0, 1)


@pytest.mark.parametrize("same_key", [False, True])
def test_repository_concurrent_requests_return_only_business_outcomes(funded_pair, same_key):
    """@description: Full repository concurrency returns one reservation and a refusal or replay, never SQL errors.
    @param funded_pair: Two funded submitters over real MySQL.
    @param same_key: Reuse one technical request key, or race two independent requests.
    @return: None; future.result propagates every worker exception to pytest.
    """
    target, repository, _ = funded_pair
    barrier = threading.Barrier(2)
    keys = ("s1-race-a", "s1-race-a" if same_key else "s1-race-b")

    def attempt(key):
        """@description: Start one full repository request on its own worker connection.
        @param key: Durable request identity.
        @return: Production exchange outcome.
        """
        barrier.wait(timeout=10)
        return repository.exchange(USER_A, key, random.Random(1))

    before = fixture_snapshot(target)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(attempt, key) for key in keys]
        outcomes = [future.result(timeout=15) for future in futures]
    loser_status = ExchangeStatus.REPLAYED if same_key else ExchangeStatus.INSUFFICIENT_CREDITS
    assert sorted(outcome.status.value for outcome in outcomes) == sorted([ExchangeStatus.RESERVED.value, loser_status.value])
    if same_key:
        assert outcomes[0].recommendation_id == outcomes[1].recommendation_id
    winner = next(index for index, outcome in enumerate(outcomes) if outcome.status is ExchangeStatus.RESERVED)
    assert attribution_violations(keys[winner], outcomes[winner].recommendation_id, before, fixture_snapshot(target)) == ()
    assert_repository_state(funded_pair, 1, 0)
