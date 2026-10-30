"""Real PostgreSQL tests of the ported /claim Nickel grant store (feature stays off in production).

They replace the MySQL store tests (legacy/mysql/tests/test_nickel_claim_store.py), use the production dict-row
connection (the MySQL tests used tuple rows and missed a row-format defect fixed in v7), and run with the app role.
"""

import threading
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from bot.database.connection import open_connection
from bot.database.nickel_grant_store import ClaimStatus, claim_nickel_grants, read_claimable_nickel
from tests.postgres_support import admin_query

pytestmark = pytest.mark.postgres
USER = 950000000000000001
MONDAY_NOON_TAIPEI = datetime(2026, 10, 26, 4, 0, tzinfo=timezone.utc)  # [fixed week]: Monday 12:00 Asia/Taipei.


def _claim(database, request_key: str, instant: datetime = MONDAY_NOON_TAIPEI):
    """@description: Run one claim transaction exactly as the service does: commit on success, roll back on failure.
    @param database: Disposable database.
    @param request_key: Stable request key.
    @param instant: Claim time.
    @return: ClaimResult.
    """
    connection = open_connection(database.app)
    try:
        result = claim_nickel_grants(connection, USER, request_key, instant, enabled=True)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _money(database) -> dict:
    """@description: Read the account balance and the grant and ledger rows.
    @param database: Disposable database.
    @return: Balance, grant kinds and ledger reasons.
    """
    return {"balance": admin_query(database, "SELECT credit_balance FROM users WHERE discord_user_id = %s", (USER,)),
            "grants": [row["grant_kind"] for row in admin_query(database, "SELECT grant_kind FROM nickel_grants ORDER BY grant_id")],
            "ledger": [row["reason"] for row in admin_query(database, "SELECT reason FROM credit_ledger ORDER BY ledger_id")]}


def test_first_claim_grants_entry_and_week_together_and_the_same_week_grants_nothing_more(pg_database):
    """@description: The first claim writes entry (2) and weekly (1) atomically; a second claim the same week adds nothing.
    @param pg_database: Disposable database.
    @return: None.
    """
    assert _claim(pg_database, "discord:1").status is ClaimStatus.GRANTED
    assert _money(pg_database) == {"balance": [{"credit_balance": 3}], "grants": ["entry", "weekly"],
                                   "ledger": ["nickel_entry_grant", "nickel_weekly_grant"]}
    assert _claim(pg_database, "discord:2").status is ClaimStatus.ALREADY_CLAIMED
    assert _money(pg_database)["balance"] == [{"credit_balance": 3}]
    next_week = _claim(pg_database, "discord:3", MONDAY_NOON_TAIPEI + timedelta(days=7))
    assert next_week.status is ClaimStatus.GRANTED and _money(pg_database)["grants"] == ["entry", "weekly", "weekly"]


def test_replaying_a_request_key_reports_its_original_week_and_writes_nothing(pg_database):
    """@description: A retry across the week boundary replays the original period instead of granting the new week.
    @param pg_database: Disposable database.
    @return: None.
    """
    first = _claim(pg_database, "discord:10")
    replay = _claim(pg_database, "discord:10", MONDAY_NOON_TAIPEI + timedelta(days=7))
    assert replay.status is ClaimStatus.REPLAYED and replay.period == first.period
    assert _money(pg_database)["balance"] == [{"credit_balance": 3}]


def test_concurrent_claims_produce_one_set_of_grants(pg_database):
    """@description: Parallel claims with different keys serialize on the account row: one entry and one weekly grant only.
    @param pg_database: Disposable database.
    @return: None.
    """
    barrier = threading.Barrier(4)

    def claim(index: int):
        """@description: Start all claims together.
        @param index: Request index.
        @return: Claim status.
        """
        barrier.wait()
        return _claim(pg_database, f"discord:2{index}").status

    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(claim, range(4)))
    assert statuses.count(ClaimStatus.GRANTED) == 1
    assert _money(pg_database)["grants"] == ["entry", "weekly"]
    with closing(open_connection(pg_database.app)) as connection:
        assert read_claimable_nickel(connection, USER, MONDAY_NOON_TAIPEI) == 0


def test_a_failure_inside_the_claim_rolls_back_ledger_grants_and_balance(pg_database, monkeypatch):
    """@description: A failure after the first grant row leaves no ledger, grant, request or balance change.
    @param pg_database: Disposable database.
    @param monkeypatch: Fault injection.
    @return: None.
    """
    from bot.database import nickel_grant_store
    original = nickel_grant_store._insert_grant
    calls = []

    def failing_insert(cursor, discord_user_id, line, period):
        """@description: Write the first grant, then fail on the second.
        @param cursor: Claim cursor.
        @param discord_user_id: Account.
        @param line: Grant line.
        @param period: Week.
        @return: None.
        """
        calls.append(line)
        if len(calls) == 2:
            raise RuntimeError("synthetic failure after the first grant")
        original(cursor, discord_user_id, line, period)

    monkeypatch.setattr(nickel_grant_store, "_insert_grant", failing_insert)
    with pytest.raises(RuntimeError):
        _claim(pg_database, "discord:30")
    assert _money(pg_database)["grants"] == [] and _money(pg_database)["ledger"] == []
    assert admin_query(pg_database, "SELECT count(*) AS n FROM nickel_claim_requests") == [{"n": 0}]
