"""Verify exchange reservation, release, CHECK, transaction and concurrency invariants on a real isolated MySQL 8.0.16+ instance.

Opt-in only: every test is skipped unless JUKEBOX_RUN_EXCHANGE_MYSQL_ISOLATED=1 and the TEST_ISOLATED_DB_* keys name the isolated target on port 3307.
Each test owns synthetic identifiers in a reserved range and removes only its own rows before and after it runs.
"""

import os
import threading
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pymysql
import pytest

from bot.database.exchange_delivery_store import claim_reservation, record_result, refund_undelivered
from bot.database.exchange_reservation import _reserve
from bot.database.reward_settlement import lock_reconciled_account
from bot.database.submission_transaction import submission_transaction
from tests.financial_invariants import account_violations, footprint, ledger_detail
from tests.isolated_mysql_target import ISOLATION_SWITCH, isolated_settings_from_environment, open_verified_connection

pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(os.environ.get(ISOLATION_SWITCH) != "1", reason="Exchange MySQL integration requires the isolated target and its explicit switch"),
]

USER_A = 900000000000000101  # [synthetic test identity]: Never a real Discord account.
USER_LOW, USER_HIGH = 900000000000000100, 900000000000000199  # [fixture range]: Cleanup touches only this identifier range.
FIXTURE_PROVENANCE = "integration-test-fixture"  # [fixture marker]: Cleanup removes only songs created by these tests.
DUPLICATE_KEY = 1062  # [MySQL error]: Unique key violation.
CHECK_VIOLATED = 3819  # [MySQL error]: CHECK constraint violation, enforced from MySQL 8.0.16.
NOW = datetime(2026, 10, 24, 12, 0, 0)  # [fixed clock]: Naive UTC time, as the connection stores it.
REQUIRED_COLUMNS = {"claim_token", "claimed_at", "released_at", "active_slot"}  # [migration 003 columns]: Must exist before any test runs.
REQUIRED_CHECKS = {"chk_recommendations_sending_token", "chk_recommendations_release"}  # [migration 003 checks]: Must be enforced by the server.
REQUIRED_UNIQUE = "uq_recommendations_user_song_active"  # [migration 003 unique key]: Release-aware uniqueness.


@pytest.fixture(scope="module")
def isolated():
    """@description: Resolve and verify the isolated target once, and refuse to run if migration 003 is not applied there.
    @param: none
    @return: Tuple of settings and identity pin for verified connections.
    """
    settings, pin = isolated_settings_from_environment()
    connection = open_verified_connection(settings, pin)
    try:
        require_migration_003(connection)
    finally:
        connection.close()
    return settings, pin


def require_migration_003(connection: pymysql.Connection) -> None:
    """@description: Fail loudly when the columns, release-aware unique key or CHECK constraints from migration 003 are missing.
    @param connection: Verified connection to the isolated target.
    @return: None; a missing shape fails the run instead of skipping it.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT column_name AS name FROM information_schema.columns "
                       "WHERE table_schema=DATABASE() AND table_name='recommendations'")
        columns = {row["name"] for row in cursor.fetchall()}
        cursor.execute("SELECT constraint_name AS name FROM information_schema.table_constraints "
                       "WHERE table_schema=DATABASE() AND table_name='recommendations'")
        constraints = {row["name"] for row in cursor.fetchall()}
    assert REQUIRED_COLUMNS <= columns, "migration 003 columns are missing on the isolated target"
    assert REQUIRED_CHECKS <= constraints, "migration 003 CHECK constraints are missing on the isolated target"
    assert REQUIRED_UNIQUE in constraints and "uq_recommendations_user_song" not in constraints, "migration 003 unique key is not in place"


@contextmanager
def verified_transaction(target):
    """@description: Run one short unit of work on a connection whose server identity was checked, committing success and rolling back failure.
    @param target: Tuple of settings and identity pin from the module fixture.
    @return: Context yielding the transaction connection.
    """
    settings, pin = target
    connection = open_verified_connection(settings, pin)
    try:
        connection.begin()
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def clear_fixture_rows(cursor) -> None:
    """@description: Remove only this file's synthetic rows, children before parents, so every test starts from an empty fixture.
    @param cursor: Cursor inside a transaction on the isolated target.
    @return: None.
    """
    cursor.execute("DELETE FROM credit_ledger WHERE discord_user_id BETWEEN %s AND %s", (USER_LOW, USER_HIGH))
    cursor.execute("DELETE FROM recommendations WHERE discord_user_id BETWEEN %s AND %s", (USER_LOW, USER_HIGH))
    cursor.execute("DELETE FROM users WHERE discord_user_id BETWEEN %s AND %s", (USER_LOW, USER_HIGH))
    cursor.execute("DELETE FROM songs WHERE verification_provenance = %s", (FIXTURE_PROVENANCE,))


@pytest.fixture
def clean_rows(isolated):
    """@description: Clear this file's synthetic rows before and after each test.
    @param isolated: Verified target from the module fixture.
    @return: None.
    """
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            clear_fixture_rows(cursor)
    yield
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            clear_fixture_rows(cursor)


def seed_song(cursor, index: int) -> int:
    """@description: Create one verified synthetic song whose track identifier is unique within this file.
    @param cursor: Cursor inside a transaction.
    @param index: Small positive number that makes the 11-character track identifier unique.
    @return: Generated song identifier.
    """
    track_id = f"JBX{index:08d}"  # [synthetic track]: Fixed pattern, never a real video.
    link = f"https://www.youtube.com/watch?v={track_id}"
    cursor.execute("INSERT INTO songs (platform, platform_track_id, source_url, youtube_url, resolution_status, verification_provenance) "
                   "VALUES ('youtube', %s, %s, %s, 'verified', %s)", (track_id, link, link, FIXTURE_PROVENANCE))
    return cursor.lastrowid


def seed_user_with_point(cursor, user_id: int, award_song_id: int, award_key: str) -> None:
    """@description: Give a synthetic user one spendable point backed by a matching award ledger row, as the refund guard requires.
    @param cursor: Cursor inside a transaction.
    @param user_id: Synthetic recipient.
    @param award_song_id: Song whose single award supplies the point.
    @param award_key: Ledger idempotency key of the award; reused as a reservation key in the atomicity test.
    @return: None.
    """
    cursor.execute("INSERT INTO users (discord_user_id, credit_balance) VALUES (%s, 1)", (user_id,))
    cursor.execute("INSERT INTO credit_ledger (discord_user_id, credit_delta, reason, idempotency_key, awarded_song_id) "
                   "VALUES (%s, 1, 'submission_award', %s, %s)", (user_id, award_key, award_song_id))


def reserve(cursor, user_id: int, request_key: str, song_id: int) -> int:
    """@description: Reserve one song through the production reservation insert, with its single debit in the same transaction.
    @param cursor: Cursor inside a transaction.
    @param user_id: Requesting synthetic user.
    @param request_key: Ledger idempotency key of this reservation.
    @param song_id: Song to reserve.
    @return: Generated recommendation identifier.
    """
    return _reserve(cursor, user_id, request_key, SimpleNamespace(song_id=song_id))


def fail_reservation(cursor, recommendation_id: int) -> None:
    """@description: Drive one reserved row through the production claim and record transitions to the proven-unsent state.
    @param cursor: Cursor inside a transaction.
    @param recommendation_id: Reserved row to fail.
    @return: None.
    """
    _, _, claim = claim_reservation(cursor, recommendation_id, uuid4().hex, NOW)
    record_result(cursor, claim, "failed", None, None)


def balance_of(target, user_id: int) -> int:
    """@description: Read the stored balance of one synthetic user in a separate transaction.
    @param target: Tuple of settings and identity pin.
    @param user_id: Synthetic user.
    @return: Stored credit balance.
    """
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT credit_balance AS balance FROM users WHERE discord_user_id=%s", (user_id,))
            return cursor.fetchone()["balance"]


def count_rows(target, sql: str, params: tuple) -> int:
    """@description: Count rows matching one read-only query in a separate transaction.
    @param target: Tuple of settings and identity pin.
    @param sql: Counting query with %s placeholders.
    @param params: Bound values.
    @return: Number of matching rows.
    """
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()["total"]


def test_second_active_reservation_for_the_same_user_and_song_is_rejected(isolated, clean_rows) -> None:
    """@description: A user may hold only one active reservation per song, so the second insert fails and nothing new is committed.
    @param isolated: Verified target.
    @param clean_rows: Fixture cleanup.
    @return: None.
    """
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            award_song, song = seed_song(cursor, 1), seed_song(cursor, 2)
            seed_user_with_point(cursor, USER_A, award_song, "award:unique")
            reserve(cursor, USER_A, "exchange:unique-1", song)
            with pytest.raises(pymysql.MySQLError) as error:
                cursor.execute("INSERT INTO recommendations (discord_user_id, song_id, status) VALUES (%s, %s, 'reserved')", (USER_A, song))
            assert error.value.args[0] == DUPLICATE_KEY
    assert count_rows(isolated, "SELECT COUNT(*) AS total FROM recommendations WHERE discord_user_id=%s", (USER_A,)) == 1


def test_released_reservation_frees_the_song_for_a_new_reservation(isolated, clean_rows) -> None:
    """@description: After a proven non-delivery is refunded and released, the same user may reserve the same song again, and the balance is exact.
    @param isolated: Verified target.
    @param clean_rows: Fixture cleanup.
    @return: None.
    """
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            award_song, song = seed_song(cursor, 1), seed_song(cursor, 2)
            seed_user_with_point(cursor, USER_A, award_song, "award:release")
            first = reserve(cursor, USER_A, "exchange:release-1", song)
            fail_reservation(cursor, first)
            assert refund_undelivered(cursor, first, NOW).status == "refunded"
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            second = reserve(cursor, USER_A, "exchange:release-2", song)
    assert second != first
    assert balance_of(isolated, USER_A) == 0


def test_sending_status_without_a_claim_token_is_rejected_by_check(isolated, clean_rows) -> None:
    """@description: A sending row must carry the claim token and claim time; the production claim sets both, and a bare status change is refused.
    @param isolated: Verified target.
    @param clean_rows: Fixture cleanup.
    @return: None.
    """
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            award_song, song = seed_song(cursor, 1), seed_song(cursor, 2)
            seed_user_with_point(cursor, USER_A, award_song, "award:check-sending")
            valid = reserve(cursor, USER_A, "exchange:check-sending-1", song)
            assert claim_reservation(cursor, valid, uuid4().hex, NOW)[0] == "sending"
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            other_song = seed_song(cursor, 3)
            cursor.execute("INSERT INTO recommendations (discord_user_id, song_id, status) VALUES (%s, %s, 'reserved')", (USER_A, other_song))
            bare = cursor.lastrowid
            with pytest.raises(pymysql.MySQLError) as error:
                cursor.execute("UPDATE recommendations SET status='sending' WHERE recommendation_id=%s", (bare,))
            assert error.value.args[0] == CHECK_VIOLATED


def test_release_marker_on_a_reserved_row_is_rejected_by_check(isolated, clean_rows) -> None:
    """@description: The release marker is a refund receipt and may exist only on a failed reservation.
    @param isolated: Verified target.
    @param clean_rows: Fixture cleanup.
    @return: None.
    """
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            award_song, song = seed_song(cursor, 1), seed_song(cursor, 2)
            seed_user_with_point(cursor, USER_A, award_song, "award:check-release")
            reserved = reserve(cursor, USER_A, "exchange:check-release-1", song)
            with pytest.raises(pymysql.MySQLError) as error:
                cursor.execute("UPDATE recommendations SET released_at=%s WHERE recommendation_id=%s", (NOW, reserved))
            assert error.value.args[0] == CHECK_VIOLATED


def test_failed_reservation_rolls_back_both_the_reservation_and_the_debit(isolated, clean_rows) -> None:
    """@description: When the debit fails inside the reservation transaction, the reservation row is rolled back too, leaving the balance unchanged.
    @param isolated: Verified target.
    @param clean_rows: Fixture cleanup.
    @return: None.
    """
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            award_song, song = seed_song(cursor, 1), seed_song(cursor, 2)
            seed_user_with_point(cursor, USER_A, award_song, "exchange:atomic-1")
    with pytest.raises(pymysql.MySQLError) as error:
        with verified_transaction(isolated) as connection:
            with connection.cursor() as cursor:
                reserve(cursor, USER_A, "exchange:atomic-1", song)
    assert error.value.args[0] == DUPLICATE_KEY
    assert count_rows(isolated, "SELECT COUNT(*) AS total FROM recommendations WHERE discord_user_id=%s", (USER_A,)) == 0
    assert balance_of(isolated, USER_A) == 1


def test_refund_restores_exactly_one_point_and_a_repeat_changes_nothing(isolated, clean_rows) -> None:
    """@description: One proven non-delivery refunds exactly one point once; the second refund request writes nothing.
    @param isolated: Verified target.
    @param clean_rows: Fixture cleanup.
    @return: None.
    """
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            award_song, song = seed_song(cursor, 1), seed_song(cursor, 2)
            seed_user_with_point(cursor, USER_A, award_song, "award:refund")
            recommendation = reserve(cursor, USER_A, "exchange:refund-1", song)
            fail_reservation(cursor, recommendation)
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            assert refund_undelivered(cursor, recommendation, NOW).status == "refunded"
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            assert refund_undelivered(cursor, recommendation, NOW).status == "already_released"
    assert balance_of(isolated, USER_A) == 1
    assert count_rows(isolated, "SELECT COUNT(*) AS total FROM credit_ledger WHERE recommendation_id=%s AND reason='delivery_refund'",
                      (recommendation,)) == 1


def concurrent_outcomes(target, song_id: int, guarded: bool) -> list:
    """@description: Race two independent connections and preserve every worker outcome.
    @param target: Verified isolated destination.
    @param song_id: Shared synthetic song.
    @param guarded: Whether to use the production transaction gate and account lock.
    @return: Both committed or rejected outcomes; unexpected worker failures are re-raised.
    """
    outcomes, errors = [], []
    start_together = threading.Barrier(2)

    def attempt(number: int) -> None:
        """@description: Record a worker result only after its transaction completes.
        @param number: Unique request number.
        @return: None.
        """
        try:
            start_together.wait(timeout=10)
            concurrent_reserve(target, song_id, f"exchange:concurrent-{number}", guarded)
            outcomes.append("reserved")
        except pymysql.MySQLError as error:
            outcomes.append(error.args[0])
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=attempt, args=(number,)) for number in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    assert not any(thread.is_alive() for thread in threads), "reservation worker did not finish"
    assert not errors, f"unexpected worker errors: {errors!r}"
    return outcomes


def concurrent_reserve(target, song_id: int, request_key: str, guarded: bool) -> None:
    """@description: Compare raw engine contention with the locking contract required by _reserve.
    @param target: Verified isolated destination.
    @param song_id: Shared synthetic song.
    @param request_key: Unique debit key.
    @param guarded: Apply production locks when true.
    @return: None; database rejection propagates after rollback.
    """
    if guarded:
        settings, pin = target
        with submission_transaction(lambda: open_verified_connection(settings, pin)) as (cursor, _):
            lock_reconciled_account(cursor, USER_A)
            reserve(cursor, USER_A, request_key, song_id)
        return
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            reserve(cursor, USER_A, request_key, song_id)


@contextmanager
def concurrency_fixture(target, guarded: bool):
    """@description: Seed a funded recipient and an optional temporary production control row.
    @param target: Verified isolated destination.
    @param guarded: Require the production control row when true.
    @return: Context yielding the shared song; removes only a control row created here.
    """
    created_control = False
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            award_song, song = seed_song(cursor, 1), seed_song(cursor, 2)
            seed_user_with_point(cursor, USER_A, award_song, "award:concurrent")
            if guarded:
                cursor.execute("SELECT schema_version FROM jb2_control WHERE singleton_id=1")
                control = cursor.fetchone()
                if control is None:
                    cursor.execute("INSERT INTO jb2_control (singleton_id,schema_version) VALUES (1,'2b2-b1')")
                    created_control = True
                else:
                    assert control["schema_version"] == "2b2-b1"
    try:
        yield song
    finally:
        if created_control:
            with verified_transaction(target) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM jb2_control WHERE singleton_id=1")


def assert_single_committed_reservation(target) -> None:
    """@description: Verify safety independently of which contention error MySQL chooses.
    @param target: Verified isolated destination.
    @return: None; asserts one reservation, one debit, and zero remaining credits.
    """
    assert count_rows(target, "SELECT COUNT(*) AS total FROM recommendations WHERE discord_user_id=%s AND released_at IS NULL", (USER_A,)) == 1
    assert balance_of(target, USER_A) == 0
    assert_account_reconciled(target, USER_A)
    assert count_rows(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE discord_user_id=%s AND reason='exchange_debit'", (USER_A,)) == 1


def test_characterization_unguarded_concurrent_reservations_preserve_atomicity(isolated, clean_rows) -> None:
    """@description: Raw engine contention may choose a duplicate or deadlock victim but must commit only once.
    @param isolated: Verified target.
    @param clean_rows: Synthetic fixture cleanup.
    @return: None; this deliberately bypasses the production gate, not a public exchange test.
    """
    with concurrency_fixture(isolated, guarded=False) as song:
        outcomes = concurrent_outcomes(isolated, song, guarded=False)
    assert outcomes.count("reserved") == 1
    rejected = [outcome for outcome in outcomes if outcome != "reserved"]
    assert len(rejected) == 1 and rejected[0] in (DUPLICATE_KEY, 1213)
    assert_single_committed_reservation(isolated)


def test_characterization_locked_low_level_insert_hits_unique_constraint(isolated, clean_rows) -> None:
    """@description: Characterize locked low-level inserts, which intentionally bypass the public balance decision.
    @param isolated: Verified target.
    @param clean_rows: Synthetic fixture cleanup.
    @return: None; 1062 here is not the contract of SubmissionRepository.exchange.
    """
    with concurrency_fixture(isolated, guarded=True) as song:
        outcomes = concurrent_outcomes(isolated, song, guarded=True)
    assert sorted(str(outcome) for outcome in outcomes) == sorted(["reserved", str(DUPLICATE_KEY)])
    assert_single_committed_reservation(isolated)


def fixture_footprint(target) -> tuple:
    """@description: Capture every reservation and ledger identifier of this file's synthetic user range before a request.
    @param target: Verified isolated destination.
    @return: Footprint pair from financial_invariants.footprint.
    """
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            return footprint(cursor, USER_LOW, USER_HIGH)


def fixture_snapshot(target) -> dict:
    """@description: Capture the reservation set and the full ledger mapping of this file's user range, so every new row can be attributed.
    @param target: Verified isolated destination.
    @return: Mapping with the reservation identifiers and the ledger rows keyed by identifier.
    """
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            reservations, _ = footprint(cursor, USER_LOW, USER_HIGH)
            return {"reservations": reservations, "ledger": ledger_detail(cursor, USER_LOW, USER_HIGH)}


def assert_account_reconciled(target, user_id: int) -> None:
    """@description: Reconcile committed credits against the ledger and every debit or refund against its owned reservation, naming each violation.
    @param target: Verified isolated destination.
    @param user_id: Synthetic account under examination.
    @return: None; fails with the list of violated invariant codes.
    """
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            violations = account_violations(cursor, user_id)
    assert violations == (), f"accounting invariants violated for the synthetic account: {violations}"
