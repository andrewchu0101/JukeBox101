"""Name every committed accounting defect for one synthetic account, and fingerprint a fixture range before and after a rejected request.

The checks are read-only SELECT statements with %s placeholders, so the same text runs on the isolated MySQL target and on the offline
SQLite counterexamples. Each check returns how many rows violate one invariant; a violation is reported by its code, never by a silent pass.
"""

import hashlib

ACCOUNT_CHECKS = (
    ("ACCOUNT_MISSING", "SELECT 1 - COUNT(*) AS total FROM users WHERE discord_user_id=%s"),
    ("NEGATIVE_BALANCE", "SELECT COUNT(*) AS total FROM users WHERE discord_user_id=%s AND credit_balance < 0"),
    ("LEDGER_MISMATCH", "SELECT COUNT(*) AS total FROM users u WHERE u.discord_user_id=%s AND u.credit_balance <> "
                        "(SELECT COALESCE(SUM(l.credit_delta),0) FROM credit_ledger l WHERE l.discord_user_id=u.discord_user_id)"),
    ("ORPHAN_LEDGER", "SELECT COUNT(*) AS total FROM credit_ledger l LEFT JOIN recommendations r "
                      "ON r.recommendation_id=l.recommendation_id AND r.discord_user_id=l.discord_user_id "
                      "WHERE l.discord_user_id=%s AND l.reason IN ('exchange_debit','delivery_refund') AND r.recommendation_id IS NULL"),
    ("RESERVATION_WITHOUT_DEBIT", "SELECT COUNT(*) AS total FROM recommendations r LEFT JOIN credit_ledger l "
                                  "ON l.recommendation_id=r.recommendation_id AND l.discord_user_id=r.discord_user_id "
                                  "AND l.reason='exchange_debit' WHERE r.discord_user_id=%s AND l.ledger_id IS NULL"),
    ("RELEASE_WITHOUT_REFUND", "SELECT COUNT(*) AS total FROM recommendations r LEFT JOIN credit_ledger l "
                               "ON l.recommendation_id=r.recommendation_id AND l.discord_user_id=r.discord_user_id "
                               "AND l.reason='delivery_refund' WHERE r.discord_user_id=%s AND r.released_at IS NOT NULL "
                               "AND l.ledger_id IS NULL"),
    ("REFUND_WITHOUT_RELEASE", "SELECT COUNT(*) AS total FROM credit_ledger l JOIN recommendations r "
                               "ON r.recommendation_id=l.recommendation_id AND r.discord_user_id=l.discord_user_id "
                               "WHERE l.discord_user_id=%s AND l.reason='delivery_refund' AND r.released_at IS NULL"),
    ("REFUND_AMOUNT_MISMATCH", "SELECT COUNT(*) AS total FROM credit_ledger refund LEFT JOIN credit_ledger debit "
                               "ON debit.recommendation_id=refund.recommendation_id AND debit.reason='exchange_debit' "
                               "AND debit.discord_user_id=refund.discord_user_id "
                               "WHERE refund.discord_user_id=%s AND refund.reason='delivery_refund' "
                               "AND (debit.ledger_id IS NULL OR refund.credit_delta + debit.credit_delta <> 0)"),
)  # [invariant checks]: Each row is one violated invariant; the code names what the operator must investigate.

LEGACY_CODES = ("ACCOUNT_MISSING", "NEGATIVE_BALANCE", "LEDGER_MISMATCH", "ORPHAN_LEDGER", "RESERVATION_WITHOUT_DEBIT")  # [original assertion]: Exactly what the earlier helper checked.


def violations_for(cursor, user_id: int, codes: tuple) -> tuple:
    """@description: Run the selected checks for one account and return the codes whose violating-row count is nonzero.
    @param cursor: Read-only cursor on the target database.
    @param user_id: Synthetic account under examination.
    @param codes: Check codes to evaluate, in reporting order.
    @return: Tuple of violated codes; an empty tuple means the selected invariants hold.
    """
    selected = {code: sql for code, sql in ACCOUNT_CHECKS}
    found = []
    for code in codes:
        cursor.execute(selected[code], (user_id,))
        if cursor.fetchone()["total"]:
            found.append(code)
    return tuple(found)


def account_violations(cursor, user_id: int) -> tuple:
    """@description: Evaluate every accounting invariant for one account, including release-refund pairing and refund amount.
    @param cursor: Read-only cursor on the target database.
    @param user_id: Synthetic account under examination.
    @return: Tuple of violated codes; empty when every invariant holds.
    """
    return violations_for(cursor, user_id, tuple(code for code, _ in ACCOUNT_CHECKS))


def legacy_violations(cursor, user_id: int) -> tuple:
    """@description: Evaluate only the checks the original assertion made, to show what it could not see.
    @param cursor: Read-only cursor on the target database.
    @param user_id: Synthetic account under examination.
    @return: Tuple of violated codes from the legacy subset.
    """
    return violations_for(cursor, user_id, LEGACY_CODES)


def footprint(cursor, low: int, high: int) -> tuple:
    """@description: Capture every reservation and ledger identifier written for the fixture user range, to prove a rejected request wrote nothing.
    @param cursor: Read-only cursor on the target database.
    @param low: Lowest synthetic user identifier in the fixture range.
    @param high: Highest synthetic user identifier in the fixture range.
    @return: Pair of frozensets: reservation identifiers and ledger identifiers.
    """
    cursor.execute("SELECT recommendation_id AS identifier FROM recommendations WHERE discord_user_id BETWEEN %s AND %s", (low, high))
    reservations = frozenset(row["identifier"] for row in cursor.fetchall())
    cursor.execute("SELECT ledger_id AS identifier FROM credit_ledger WHERE discord_user_id BETWEEN %s AND %s", (low, high))
    ledger_rows = frozenset(row["identifier"] for row in cursor.fetchall())
    return reservations, ledger_rows


def exchange_ledger_key(request_key: str) -> str:
    """@description: Derive the ledger idempotency key the production reservation uses for one technical request.
    @param request_key: Durable technical request identity.
    @return: The idempotency key stored on that request's exchange debit.
    """
    return "exchange:" + hashlib.sha256(request_key.encode("utf-8")).hexdigest()


def ledger_detail(cursor, low: int, high: int) -> dict:
    """@description: Read every ledger row of the fixture range keyed by identifier, so each new row can be attributed to a request.
    @param cursor: Read-only cursor on the target database.
    @param low: Lowest synthetic user identifier in the fixture range.
    @param high: Highest synthetic user identifier in the fixture range.
    @return: Mapping from ledger identifier to its user, delta, reason, reservation and idempotency key.
    """
    cursor.execute("SELECT ledger_id AS identifier, discord_user_id AS user_id, credit_delta AS delta, reason AS reason, "
                   "recommendation_id AS recommendation_id, idempotency_key AS idempotency_key "
                   "FROM credit_ledger WHERE discord_user_id BETWEEN %s AND %s", (low, high))
    return {row["identifier"]: {"user_id": row["user_id"], "delta": row["delta"], "reason": row["reason"],
                                "recommendation_id": row["recommendation_id"], "key": row["idempotency_key"]}
            for row in cursor.fetchall()}


def attribution_violations(winner_request_key: str, winner_recommendation_id: int, before: dict, after: dict) -> tuple:
    """@description: Attribute every reservation and ledger row written during a run to the winning request, so a loser's write cannot hide in the totals.
    @param winner_request_key: Request identity of the request that reported RESERVED.
    @param winner_recommendation_id: Reservation identifier reported by that request.
    @param before: Snapshot with the reservation set and ledger mapping before the run.
    @param after: Snapshot with the same shape after the run.
    @return: Tuple of attribution failures; empty when the new rows are exactly the winner's reservation and its one debit.
    """
    new_reservations = frozenset(after["reservations"]) - frozenset(before["reservations"])
    new_ledger_ids = frozenset(after["ledger"]) - frozenset(before["ledger"])
    expected_key = exchange_ledger_key(winner_request_key)
    winner_debits = frozenset(identifier for identifier in new_ledger_ids
                              if after["ledger"][identifier]["key"] == expected_key
                              and after["ledger"][identifier]["reason"] == "exchange_debit"
                              and after["ledger"][identifier]["recommendation_id"] == winner_recommendation_id)
    violations = []
    if new_reservations != frozenset({winner_recommendation_id}):
        violations.append("UNATTRIBUTED_RESERVATION")
    if len(winner_debits) != 1 or new_ledger_ids != winner_debits:
        violations.append("UNATTRIBUTED_LEDGER")
    return tuple(violations)
