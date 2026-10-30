"""Verify /claim grant persistence on the disposable container database through real transactions.

Offline cases run without a database. Database cases run only when the container test mode is explicitly enabled
and the disposable application credential is supplied through the process environment; they never touch the
formal database or any formal secret.
"""

import os
import threading
from datetime import datetime, timezone

import pymysql
import pytest

from bot.database.nickel_grant_store import ClaimStatus, claim_nickel_grants, read_claimable_nickel

UTC = timezone.utc
CONTAINER_MODE = os.environ.get("JUKEBOX_RUN_CONTAINER_MYSQL") == "1"
CONTAINER_PASSWORD = os.environ.get("TEST_CONTAINER_DB_PASSWORD", "")
requires_container = pytest.mark.skipif(not (CONTAINER_MODE and CONTAINER_PASSWORD), reason="container test mode not enabled")


class _FailingConnection:
    """Fail loudly if any statement is attempted, proving the disabled path never reaches the database."""

    def cursor(self):
        raise AssertionError("disabled feature must not touch the database")


def test_disabled_feature_returns_disabled_without_touching_the_database():
    result = claim_nickel_grants(_FailingConnection(), 1, "req-disabled", datetime(2026, 10, 25, 16, 0, tzinfo=UTC), enabled=False)
    assert result.status is ClaimStatus.DISABLED
    assert result.lines == []


def _connect():
    """@description: Open one connection to the disposable container schema on loopback, with autocommit off.
    @param: none
    @return: PyMySQL connection to the isolated test database.
    """
    return pymysql.connect(
        host="127.0.0.1",
        port=3307,
        user="jukebox101_app",
        password=CONTAINER_PASSWORD,
        database="jukebox101_test",
        autocommit=False,
    )


def _unique_user() -> int:
    """@description: Return a fresh Discord-style account id so each test owns its own rows without deleting anything.
    @param: none
    @return: Unique unsigned integer account id.
    """
    return int.from_bytes(os.urandom(6), "big") + 10**15


def _balance(connection, user: int) -> int:
    """@description: Read the spendable balance cache for one account without writing.
    @param connection: Open connection.
    @param user: Discord account id.
    @return: Stored credit_balance, or 0 when the account row does not exist yet.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT credit_balance FROM users WHERE discord_user_id=%s", (user,))
        row = cursor.fetchone()
    return int(row[0]) if row else 0


def _count(connection, table: str, user: int) -> int:
    """@description: Count rows for one account in a Nickel table, used to prove a write did or did not happen.
    @param connection: Open connection.
    @param table: Table name from this module only.
    @param user: Discord account id.
    @return: Row count.
    """
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE discord_user_id=%s", (user,))
        return int(cursor.fetchone()[0])


WEEK_ONE = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)  # Monday 2026-10-26 00:00 Taipei
WEEK_TWO = datetime(2026, 11, 1, 16, 0, tzinfo=UTC)  # Monday 2026-11-02 00:00 Taipei


@requires_container
def test_first_claim_grants_entry_and_this_week_together_and_commits_them_atomically():
    user = _unique_user()
    connection = _connect()
    result = claim_nickel_grants(connection, user, f"req-{user}-a", WEEK_ONE, enabled=True)
    connection.commit()
    assert result.status is ClaimStatus.GRANTED
    assert sum(line.amount for line in result.lines) == 3
    assert _balance(connection, user) == 3
    assert _count(connection, "credit_ledger", user) == 2
    assert _count(connection, "nickel_grants", user) == 2


@requires_container
def test_a_second_claim_in_the_same_week_writes_nothing():
    user = _unique_user()
    first = _connect()
    claim_nickel_grants(first, user, f"req-{user}-a", WEEK_ONE, enabled=True)
    first.commit()
    second = _connect()
    result = claim_nickel_grants(second, user, f"req-{user}-b", WEEK_ONE, enabled=True)
    second.commit()
    assert result.status is ClaimStatus.ALREADY_CLAIMED
    assert _balance(second, user) == 3
    assert _count(second, "nickel_grants", user) == 2


@requires_container
def test_the_entry_grant_is_never_repeated_in_a_later_week_but_the_weekly_grant_is():
    user = _unique_user()
    first = _connect()
    claim_nickel_grants(first, user, f"req-{user}-a", WEEK_ONE, enabled=True)
    first.commit()
    later = _connect()
    result = claim_nickel_grants(later, user, f"req-{user}-b", WEEK_TWO, enabled=True)
    later.commit()
    assert [line.amount for line in result.lines] == [1]
    assert _balance(later, user) == 4


@requires_container
def test_a_retry_of_the_same_request_across_the_week_boundary_replays_the_original_week():
    user = _unique_user()
    original = _connect()
    claim_nickel_grants(original, user, f"req-{user}-cross", WEEK_ONE, enabled=True)
    original.commit()
    retry = _connect()
    result = claim_nickel_grants(retry, user, f"req-{user}-cross", WEEK_TWO, enabled=True)
    retry.commit()
    assert result.status is ClaimStatus.REPLAYED
    assert result.period == "2026-10-26"
    assert _count(retry, "nickel_grants", user) == 2
    assert _balance(retry, user) == 3


@requires_container
def test_concurrent_claims_from_separate_connections_produce_one_set_of_grants():
    user = _unique_user()
    outcomes = []

    def claim(request_suffix):
        connection = _connect()
        try:
            outcomes.append(claim_nickel_grants(connection, user, f"req-{user}-{request_suffix}", WEEK_ONE, enabled=True).status)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    threads = [threading.Thread(target=claim, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    check = _connect()
    assert _count(check, "nickel_grants", user) == 2
    assert _balance(check, user) == 3
    assert outcomes.count(ClaimStatus.GRANTED) == 1


@requires_container
def test_a_fresh_connection_after_a_restart_sees_the_claim_and_does_not_duplicate_it():
    user = _unique_user()
    before_restart = _connect()
    claim_nickel_grants(before_restart, user, f"req-{user}-a", WEEK_ONE, enabled=True)
    before_restart.commit()
    before_restart.close()
    after_restart = _connect()
    result = claim_nickel_grants(after_restart, user, f"req-{user}-b", WEEK_ONE, enabled=True)
    after_restart.commit()
    assert result.status is ClaimStatus.ALREADY_CLAIMED
    assert _count(after_restart, "credit_ledger", user) == 2


@requires_container
def test_a_failure_inside_the_transaction_rolls_back_ledger_grants_and_balance_together(monkeypatch):
    import bot.database.nickel_grant_store as store

    user = _unique_user()
    connection = _connect()
    original_insert = store._insert_grant

    def failing_insert(*args, **kwargs):
        original_insert(*args, **kwargs)
        raise RuntimeError("simulated failure after the first grant was written")

    monkeypatch.setattr(store, "_insert_grant", failing_insert)
    with pytest.raises(RuntimeError):
        claim_nickel_grants(connection, user, f"req-{user}-fail", WEEK_ONE, enabled=True)
    connection.rollback()
    check = _connect()
    assert _count(check, "credit_ledger", user) == 0
    assert _count(check, "nickel_grants", user) == 0
    assert _balance(check, user) == 0


@requires_container
def test_the_database_refuses_a_second_entry_grant_for_the_same_account():
    user = _unique_user()
    connection = _connect()
    claim_nickel_grants(connection, user, f"req-{user}-a", WEEK_ONE, enabled=True)
    connection.commit()
    with connection.cursor() as cursor:
        with pytest.raises(pymysql.err.IntegrityError):
            cursor.execute(
                "INSERT INTO nickel_grants (discord_user_id, grant_kind, grant_period, amount, ledger_id) "
                "VALUES (%s, 'entry', 'LIFETIME', 2, 1)",
                (user,),
            )
    connection.rollback()


@requires_container
def test_a_null_or_repeated_period_cannot_bypass_the_entry_uniqueness():
    # [NULL behaviour]: The entry grant uses the literal LIFETIME, not NULL, so the unique key still applies.
    user = _unique_user()
    connection = _connect()
    claim_nickel_grants(connection, user, f"req-{user}-a", WEEK_ONE, enabled=True)
    connection.commit()
    with connection.cursor() as cursor:
        cursor.execute("SELECT grant_period FROM nickel_grants WHERE discord_user_id=%s AND grant_kind='entry'", (user,))
        assert cursor.fetchone()[0] == "LIFETIME"
    connection.rollback()


@requires_container
def test_reading_claimable_nickel_writes_nothing():
    user = _unique_user()
    connection = _connect()
    before = (_count(connection, "credit_ledger", user), _count(connection, "nickel_grants", user), _balance(connection, user))
    amount = read_claimable_nickel(connection, user, WEEK_ONE)
    after = (_count(connection, "credit_ledger", user), _count(connection, "nickel_grants", user), _balance(connection, user))
    connection.rollback()
    assert amount == 3
    assert before == after == (0, 0, 0)


@requires_container
def test_the_isolation_level_is_the_one_the_lock_order_was_reasoned_about():
    # [isolation check]: REPEATABLE READ is the reasoning basis for the plain read after the user-row lock.
    connection = _connect()
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@transaction_isolation")
        level = cursor.fetchone()[0]
    connection.rollback()
    assert level == "REPEATABLE-READ"


@requires_container
def test_commit_succeeded_but_the_reply_was_lost_then_the_same_request_is_replayed_not_reissued():
    # [unknown commit A]: The database committed, the caller never saw success. A retry must not grant again.
    user = _unique_user()
    first = _connect()
    original = claim_nickel_grants(first, user, f"req-{user}-unknown-a", WEEK_ONE, enabled=True)
    first.commit()
    first.close()  # the response to the caller is lost here
    assert original.status is ClaimStatus.GRANTED
    retry = _connect()
    replay = claim_nickel_grants(retry, user, f"req-{user}-unknown-a", WEEK_ONE, enabled=True)
    retry.commit()
    assert replay.status is ClaimStatus.REPLAYED
    assert replay.period == "2026-10-26"
    assert _balance(retry, user) == 3
    assert _count(retry, "credit_ledger", user) == 2


@requires_container
def test_nothing_was_committed_then_the_same_request_grants_exactly_once():
    # [unknown commit B]: The connection dropped before COMMIT. Nothing is durable, so the retry grants once.
    user = _unique_user()
    lost = _connect()
    claim_nickel_grants(lost, user, f"req-{user}-unknown-b", WEEK_ONE, enabled=True)
    lost.close()  # no commit: the server rolls the transaction back
    check = _connect()
    assert _count(check, "credit_ledger", user) == 0
    assert _balance(check, user) == 0
    retry = _connect()
    result = claim_nickel_grants(retry, user, f"req-{user}-unknown-b", WEEK_ONE, enabled=True)
    retry.commit()
    assert result.status is ClaimStatus.GRANTED
    assert _balance(retry, user) == 3


@requires_container
def test_an_earlier_consistent_read_in_the_same_transaction_cannot_hide_a_committed_claim():
    # [snapshot]: The caller reads the balance first (a consistent read that fixes a REPEATABLE READ snapshot),
    # another connection commits the first claim, then this claim must still see it and must not grant again.
    user = _unique_user()
    caller = _connect()
    assert _balance(caller, user) == 0
    other = _connect()
    claim_nickel_grants(other, user, f"req-{user}-snap-a", WEEK_ONE, enabled=True)
    other.commit()
    other.close()
    result = claim_nickel_grants(caller, user, f"req-{user}-snap-b", WEEK_ONE, enabled=True)
    caller.commit()
    assert result.status is ClaimStatus.ALREADY_CLAIMED
    assert _count(caller, "nickel_grants", user) == 2


_CHILD_CLAIM_SCRIPT = """
import sys
from datetime import datetime, timezone
import pymysql
from bot.database.nickel_grant_store import claim_nickel_grants
connection = pymysql.connect(host="127.0.0.1", port=3307, user="jukebox101_app",
                             password=sys.argv[1], database="jukebox101_test", autocommit=False)
result = claim_nickel_grants(connection, int(sys.argv[2]), sys.argv[3],
                             datetime(2026, 10, 25, 16, 0, tzinfo=timezone.utc), enabled=True)
connection.commit()
connection.close()
print(result.status.value)
"""


@requires_container
def test_a_separate_process_that_committed_and_exited_leaves_a_claim_a_new_process_will_not_repeat():
    # [real restart]: The claim runs in an OS process that exits after COMMIT; a second OS process retries the same request.
    import subprocess
    import sys

    user = _unique_user()
    first = subprocess.run([sys.executable, "-c", _CHILD_CLAIM_SCRIPT, CONTAINER_PASSWORD, str(user), f"req-{user}-proc"],
                           capture_output=True, text=True, timeout=120)
    assert first.returncode == 0, first.stderr[-300:]
    assert first.stdout.strip() == "granted"
    second = subprocess.run([sys.executable, "-c", _CHILD_CLAIM_SCRIPT, CONTAINER_PASSWORD, str(user), f"req-{user}-proc"],
                            capture_output=True, text=True, timeout=120)
    assert second.returncode == 0, second.stderr[-300:]
    assert second.stdout.strip() == "replayed"
    check = _connect()
    assert _balance(check, user) == 3
    assert _count(check, "credit_ledger", user) == 2
