"""Verify the account invariant checks offline against SQLite counterexamples, so every named violation is reproduced without MySQL.

The SQL under test is the same text that runs on the isolated MySQL target; SQLite only stands in for the engine here.
Each counterexample is one committed state of a synthetic account. The legacy codes are the checks the original assertion made;
the stricter cases must be reported by the full check set while the legacy codes stay silent.
"""

import sqlite3

import pytest

from tests.financial_invariants import (
    LEGACY_CODES, account_violations, attribution_violations, exchange_ledger_key, footprint, legacy_violations,
)

ACCOUNT = 900000000000000101  # [synthetic account]: Stands in for one fixture user.
OTHER = 900000000000000102  # [synthetic account]: Second synthetic user for recipient mismatch cases.


class SqliteCursor:
    """Adapt the MySQL-style %s placeholders and row access used by the invariant checks to SQLite."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """@description: Wrap one in-memory connection so the shared SQL runs unchanged.
        @param connection: In-memory SQLite connection with Row access.
        @return: None.
        """
        self._cursor = connection.cursor()

    def execute(self, sql: str, params: tuple = ()) -> None:
        """@description: Run one statement after translating placeholders.
        @param sql: Statement using %s placeholders.
        @param params: Bound values.
        @return: None.
        """
        self._cursor.execute(sql.replace("%s", "?"), params)

    def fetchone(self):
        """@description: Return the next row with name access.
        @return: One row or None.
        """
        return self._cursor.fetchone()

    def fetchall(self):
        """@description: Return all remaining rows with name access.
        @return: Rows.
        """
        return self._cursor.fetchall()


SCHEMA = """
CREATE TABLE users (discord_user_id INTEGER PRIMARY KEY, credit_balance INTEGER NOT NULL);
CREATE TABLE recommendations (recommendation_id INTEGER PRIMARY KEY, discord_user_id INTEGER NOT NULL,
                              song_id INTEGER NOT NULL, status TEXT NOT NULL, released_at TEXT NULL);
CREATE TABLE credit_ledger (ledger_id INTEGER PRIMARY KEY, discord_user_id INTEGER NOT NULL, credit_delta INTEGER NOT NULL,
                            reason TEXT NOT NULL, recommendation_id INTEGER NULL, idempotency_key TEXT NOT NULL);
"""


def new_world() -> SqliteCursor:
    """@description: Create an empty synthetic database with the three accounting tables.
    @param: none
    @return: Cursor over a fresh in-memory database.
    """
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    return SqliteCursor(connection)


def clean_account(cursor: SqliteCursor) -> None:
    """@description: Build the reference state: one award point, one reserved and debited recommendation, balance zero.
    @param cursor: Empty synthetic database.
    @return: None.
    """
    cursor.execute("INSERT INTO users VALUES (%s, 0)", (ACCOUNT,))
    cursor.execute("INSERT INTO credit_ledger VALUES (1, %s, 1, 'submission_award', NULL, 'award-1')", (ACCOUNT,))
    cursor.execute("INSERT INTO recommendations VALUES (10, %s, 20, 'reserved', NULL)", (ACCOUNT,))
    cursor.execute("INSERT INTO credit_ledger VALUES (2, %s, -1, 'exchange_debit', 10, 'exchange-10')", (ACCOUNT,))


def test_reference_state_has_no_violations() -> None:
    """@description: The consistent reference state is accepted by every check, so the counterexamples below differ only by their defect.
    @param: none
    @return: None.
    """
    cursor = new_world()
    clean_account(cursor)
    assert account_violations(cursor, ACCOUNT) == ()


def test_released_without_refund_is_missed_by_the_legacy_assertion_and_caught_now() -> None:
    """@description: A reservation marked released with no refund row removes the slot without returning the point; the legacy checks miss it.
    @param: none
    @return: None.
    """
    cursor = new_world()
    clean_account(cursor)
    cursor.execute("UPDATE recommendations SET status='failed', released_at='2026-10-24 12:00:00' WHERE recommendation_id=10")
    assert legacy_violations(cursor, ACCOUNT) == ()  # [documented gap]: the original assertion is silent here.
    assert "RELEASE_WITHOUT_REFUND" in account_violations(cursor, ACCOUNT)


def test_refund_without_release_is_caught() -> None:
    """@description: A refund row whose reservation is still unreleased would let the same song be held and refunded again; it must be reported.
    @param: none
    @return: None.
    """
    cursor = new_world()
    clean_account(cursor)
    cursor.execute("UPDATE recommendations SET status='failed' WHERE recommendation_id=10")
    cursor.execute("INSERT INTO credit_ledger VALUES (3, %s, 1, 'delivery_refund', 10, 'refund-10')", (ACCOUNT,))
    cursor.execute("UPDATE users SET credit_balance=1 WHERE discord_user_id=%s", (ACCOUNT,))
    assert legacy_violations(cursor, ACCOUNT) == ()
    assert "REFUND_WITHOUT_RELEASE" in account_violations(cursor, ACCOUNT)


def test_refund_amount_must_equal_the_original_debit() -> None:
    """@description: A refund larger than the original debit creates points out of nothing; it must be reported.
    @param: none
    @return: None.
    """
    cursor = new_world()
    clean_account(cursor)
    cursor.execute("UPDATE recommendations SET status='failed', released_at='2026-10-24 12:00:00' WHERE recommendation_id=10")
    cursor.execute("INSERT INTO credit_ledger VALUES (3, %s, 2, 'delivery_refund', 10, 'refund-10')", (ACCOUNT,))
    cursor.execute("UPDATE users SET credit_balance=2 WHERE discord_user_id=%s", (ACCOUNT,))
    assert legacy_violations(cursor, ACCOUNT) == ()
    assert "REFUND_AMOUNT_MISMATCH" in account_violations(cursor, ACCOUNT)


def test_refund_for_another_recipient_is_an_orphan_for_that_recipient() -> None:
    """@description: A refund credited to a user other than the reservation's recipient must be reported for the credited user.
    @param: none
    @return: None.
    """
    cursor = new_world()
    clean_account(cursor)
    cursor.execute("UPDATE recommendations SET status='failed', released_at='2026-10-24 12:00:00' WHERE recommendation_id=10")
    cursor.execute("INSERT INTO users VALUES (%s, 1)", (OTHER,))
    cursor.execute("INSERT INTO credit_ledger VALUES (3, %s, 1, 'delivery_refund', 10, 'refund-10')", (OTHER,))
    assert "ORPHAN_LEDGER" in account_violations(cursor, OTHER)
    assert "RELEASE_WITHOUT_REFUND" in account_violations(cursor, ACCOUNT)


@pytest.mark.parametrize(("code", "mutate"), [
    ("NEGATIVE_BALANCE", "UPDATE users SET credit_balance=-1 WHERE discord_user_id=%(a)s"),
    ("LEDGER_MISMATCH", "UPDATE users SET credit_balance=5 WHERE discord_user_id=%(a)s"),
    ("RESERVATION_WITHOUT_DEBIT", "INSERT INTO recommendations VALUES (11, %(a)s, 21, 'reserved', NULL)"),
    ("ORPHAN_LEDGER", "INSERT INTO credit_ledger VALUES (4, %(a)s, -1, 'exchange_debit', 99, 'exchange-99')"),
])
def test_legacy_defects_are_still_reported(code, mutate) -> None:
    """@description: Each defect the original assertion already covered stays reported by the full check set.
    @param code: Violation code the mutation must produce.
    @param mutate: Statement that corrupts the reference state for this account.
    @return: None.
    """
    cursor = new_world()
    clean_account(cursor)
    cursor.execute(mutate.replace("%(a)s", "%s"), (ACCOUNT,))
    assert code in account_violations(cursor, ACCOUNT)
    assert code in legacy_violations(cursor, ACCOUNT)


def test_missing_account_is_reported() -> None:
    """@description: An account with ledger history but no balance row is a violation, not a silent pass.
    @param: none
    @return: None.
    """
    cursor = new_world()
    assert "ACCOUNT_MISSING" in account_violations(cursor, ACCOUNT)


def test_footprint_detects_any_new_reservation_or_ledger_row() -> None:
    """@description: The before/after footprint of the fixture range is equal only when no reservation or ledger row was written.
    @param: none
    @return: None.
    """
    cursor = new_world()
    clean_account(cursor)
    before = footprint(cursor, ACCOUNT, OTHER)
    assert footprint(cursor, ACCOUNT, OTHER) == before
    cursor.execute("INSERT INTO recommendations VALUES (12, %s, 22, 'reserved', NULL)", (ACCOUNT,))
    assert footprint(cursor, ACCOUNT, OTHER) != before


def ledger_row(user_id: int, delta: int, reason: str, recommendation_id, request_key: str) -> dict:
    """@description: Build one ledger row as the attribution helper reads it, with the idempotency key derived from the request.
    @param user_id: Recipient of the row.
    @param delta: Credit change.
    @param reason: Ledger reason.
    @param recommendation_id: Reservation the row belongs to, or None.
    @param request_key: Technical request identity that produced the row.
    @return: Row mapping.
    """
    return {"user_id": user_id, "delta": delta, "reason": reason, "recommendation_id": recommendation_id,
            "key": exchange_ledger_key(request_key)}


def test_attribution_accepts_exactly_the_winners_reservation_and_debit() -> None:
    """@description: When only the winning request wrote, every new reservation and ledger row belongs to its request key and reservation.
    @param: none
    @return: None.
    """
    before = {"reservations": frozenset(), "ledger": {}}
    after = {"reservations": frozenset({10}), "ledger": {1: ledger_row(ACCOUNT, -1, "exchange_debit", 10, "s2-winner")}}
    assert attribution_violations("s2-winner", 10, before, after) == ()


def test_loser_rows_are_reported_even_when_the_total_count_looks_right() -> None:
    """@description: A loser that wrote a reservation and debit alongside a winner whose rows were lost would balance the totals; attribution must still fail it.
    @param: none
    @return: None.
    """
    before = {"reservations": frozenset(), "ledger": {}}
    after = {"reservations": frozenset({10, 11}),
             "ledger": {1: ledger_row(ACCOUNT, -1, "exchange_debit", 11, "s2-loser"),
                        2: ledger_row(ACCOUNT, -1, "exchange_debit", 10, "s2-loser")}}
    violations = attribution_violations("s2-winner", 10, before, after)
    assert "UNATTRIBUTED_RESERVATION" in violations and "UNATTRIBUTED_LEDGER" in violations


def test_winner_reservation_without_its_debit_is_reported() -> None:
    """@description: A reservation without the winning request's debit would be a silent free item; attribution must report it.
    @param: none
    @return: None.
    """
    before = {"reservations": frozenset(), "ledger": {}}
    after = {"reservations": frozenset({10}), "ledger": {}}
    assert attribution_violations("s2-winner", 10, before, after) == ("UNATTRIBUTED_LEDGER",)


def test_replayed_request_adds_nothing_beyond_the_original_winner() -> None:
    """@description: Over a same-key run, the replay contributes no new rows, so the complete run is attributed to the original reservation and one debit under that key.
    @param: none
    @return: None.
    """
    before = {"reservations": frozenset(), "ledger": {}}
    after = {"reservations": frozenset({10}), "ledger": {1: ledger_row(ACCOUNT, -1, "exchange_debit", 10, "s2-same")}}
    assert attribution_violations("s2-same", 10, before, after) == ()
