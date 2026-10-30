"""Persist /claim Nickel grants in one transaction per request, guarded by the account row lock and unique keys.

The caller owns the connection and its commit or rollback. A claim locks the account's users row, records the
request key, reads the approved grant state with locking reads, then writes ledger, grant and balance rows together.
Retries with the same request key replay the period recorded for that request, so a request that crosses a Taipei
week boundary is never treated as a new week's claim.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from bot.nickel_grant_policy import GrantKind, GrantLine, plan_claim, weekly_period_key

LIFETIME_PERIOD = "LIFETIME"  # [entry period]: The entry grant is keyed by this literal, never NULL, so the unique key applies.
REASON_BY_KIND = {GrantKind.ENTRY: "nickel_entry_grant", GrantKind.WEEKLY: "nickel_weekly_grant"}  # [ledger reasons]: Must match migration 004.


class ClaimStatus(Enum):
    """Name every outcome of one /claim so the reply never states a grant that was not recorded."""

    DISABLED = "disabled"  # [feature off]: No database access at all.
    GRANTED = "granted"  # [new grants]: Each returned line was written in this transaction.
    ALREADY_CLAIMED = "already_claimed"  # [nothing missing]: The account holds every grant for this period.
    REPLAYED = "replayed"  # [same request]: The request key was recorded earlier; nothing new was written.


@dataclass(frozen=True)
class ClaimResult:
    """Carry the outcome of one claim so the caller can reply with sources and amounts."""

    status: ClaimStatus  # [outcome]: Decides which reply the Discord layer shows.
    lines: list  # [grant lines]: Grants written by this request, or recorded for a replay.
    period: str  # [weekly period]: Taipei Monday that the request was evaluated in; None when disabled.


def _claimed_state(cursor, discord_user_id: int, period: str, current_read: bool) -> tuple:
    """@description: Report whether the entry grant and this period's weekly grant already exist for the account.
    @param cursor: Open cursor inside the caller's transaction.
    @param discord_user_id: Discord account id.
    @param period: Weekly period key.
    @param current_read: True inside a claim (after the account row lock), False for the read-only balance view.
    @return: Tuple (entry_claimed, weekly_claimed_for_period).
    """
    # [current read]: Under READ COMMITTED every PostgreSQL statement sees the latest committed rows, and a claim holds
    # the account row lock first, so no locking clause is needed (FOR SHARE would require UPDATE privilege).
    del current_read  # [same statement]: Both callers issue the identical read; the lock order provides currency.
    cursor.execute(
        "SELECT grant_kind, grant_period FROM nickel_grants WHERE discord_user_id=%s AND (grant_kind='entry' OR grant_period=%s)",
        (discord_user_id, period),
    )
    rows = cursor.fetchall()
    entry_claimed = any(row["grant_kind"] == "entry" for row in rows)
    weekly_claimed = any(row["grant_kind"] == "weekly" and row["grant_period"] == period for row in rows)
    return entry_claimed, weekly_claimed


def _insert_grant(cursor, discord_user_id: int, line: GrantLine, period: str) -> None:
    """@description: Write one grant as a ledger row, a grant row and the balance update, all inside the caller's transaction.
    @param cursor: Open cursor inside the caller's transaction.
    @param discord_user_id: Discord account id receiving the grant.
    @param line: Approved grant line to write.
    @param period: Weekly period key, or LIFETIME for the entry grant.
    @return: None; any failure propagates so the whole transaction rolls back.
    """
    grant_period = LIFETIME_PERIOD if line.kind is GrantKind.ENTRY else period
    idempotency_key = f"nickel_{line.kind.value}:{discord_user_id}:{grant_period}"
    cursor.execute(
        "INSERT INTO credit_ledger (discord_user_id, credit_delta, reason, idempotency_key) VALUES (%s, %s, %s, %s) RETURNING ledger_id",
        (discord_user_id, line.amount, REASON_BY_KIND[line.kind], idempotency_key),
    )
    ledger_id = cursor.fetchone()["ledger_id"]
    cursor.execute(
        "INSERT INTO nickel_grants (discord_user_id, grant_kind, grant_period, amount, ledger_id) VALUES (%s, %s, %s, %s, %s)",
        (discord_user_id, line.kind.value, grant_period, line.amount, ledger_id),
    )
    cursor.execute("UPDATE users SET credit_balance = credit_balance + %s WHERE discord_user_id = %s", (line.amount, discord_user_id))


def _record_request(cursor, discord_user_id: int, request_key: str, period: str) -> bool:
    """@description: Record the request key first, so a duplicate request is detected before any grant is written.
    @param cursor: Open cursor inside the caller's transaction.
    @param discord_user_id: Discord account id.
    @param request_key: Stable key of this /claim attempt, reused by every retry of it.
    @param period: Weekly period the request is evaluated in.
    @return: True when the request is new; False when the key was already recorded.
    """
    # [no error path]: A duplicate key must not raise, because a PostgreSQL error would abort the whole claim transaction.
    cursor.execute(
        "INSERT INTO nickel_claim_requests (request_key, discord_user_id, grant_period) VALUES (%s, %s, %s) "
        "ON CONFLICT (request_key) DO NOTHING RETURNING request_key",
        (request_key, discord_user_id, period),
    )
    return cursor.fetchone() is not None


def _stored_period(cursor, request_key: str) -> str:
    """@description: Read the period a recorded request was evaluated in, so a replay reports that week and not the current one.
    @param cursor: Open cursor inside the caller's transaction.
    @param request_key: Key of the earlier request.
    @return: Stored weekly period key.
    """
    cursor.execute("SELECT grant_period FROM nickel_claim_requests WHERE request_key=%s", (request_key,))
    return cursor.fetchone()["grant_period"]


def claim_nickel_grants(connection, discord_user_id: int, request_key: str, instant: datetime, enabled: bool) -> ClaimResult:
    """@description: Give every approved grant the account is missing, in one transaction, and never twice.
    @param connection: Open connection; the caller commits on success and rolls back on any exception.
    @param discord_user_id: Discord account id making the claim.
    @param request_key: Stable key of this attempt; a retry must reuse it.
    @param instant: Timezone-aware moment of the first attempt, used to choose the weekly period.
    @param enabled: Feature switch; when False nothing is read or written.
    @return: ClaimResult describing what this request granted, replayed or found already claimed.
    """
    if not enabled:
        return ClaimResult(ClaimStatus.DISABLED, [], None)
    period = weekly_period_key(instant)
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")  # [lock-order reasoning]: Stated, not assumed from server defaults.
        cursor.execute("INSERT INTO users (discord_user_id) VALUES (%s) ON CONFLICT (discord_user_id) DO NOTHING", (discord_user_id,))
        cursor.execute("SELECT discord_user_id FROM users WHERE discord_user_id=%s FOR UPDATE", (discord_user_id,))
        if not _record_request(cursor, discord_user_id, request_key, period):
            replay_period = _stored_period(cursor, request_key)
            return ClaimResult(ClaimStatus.REPLAYED, [], replay_period)
        entry_claimed, weekly_claimed = _claimed_state(cursor, discord_user_id, period, current_read=True)
        lines = plan_claim(entry_claimed, weekly_claimed)
        for line in lines:
            _insert_grant(cursor, discord_user_id, line, period)
    status = ClaimStatus.GRANTED if lines else ClaimStatus.ALREADY_CLAIMED
    return ClaimResult(status, lines, period)


def read_claimable_nickel(connection, discord_user_id: int, instant: datetime) -> int:
    """@description: Report how much Nickel a claim would grant right now, without writing anything, for the read-only balance view.
    @param connection: Open connection; the caller ends the read transaction.
    @param discord_user_id: Discord account id.
    @param instant: Timezone-aware moment to evaluate.
    @return: Sum of the grants a /claim at this moment would return.
    """
    period = weekly_period_key(instant)
    with connection.cursor() as cursor:
        entry_claimed, weekly_claimed = _claimed_state(cursor, discord_user_id, period, current_read=False)
    return sum(line.amount for line in plan_claim(entry_claimed, weekly_claimed))
