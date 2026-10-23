"""Settle first-version rewards atomically using the existing Nickel ledger and balance.

All decisions are recomputed under the writer gate; unique claims and durable settlement receipts protect retries.
"""

import hashlib
from dataclasses import replace
from typing import Any

from bot.database.persisted_snapshot import load_snapshot, reward_barriers
from bot.database.submission_codec import decode_contract, encode_contract
from bot.reward_candidates import RewardCandidateStatus, RewardDecision, evaluate_reward_candidates
from bot.submission_snapshot import aggregate_snapshot
from bot.submission_workflow import _historical_gate


class LedgerBalanceMismatch(RuntimeError):
    """Require a separate audited reconciliation instead of repairing preexisting balances silently."""


def current_decisions(cursor: Any, control: dict) -> tuple[RewardDecision, ...]:
    """@description: Reuse Batch A business gates against complete locked database facts.
    @param cursor: Cursor holding the global writer gate.
    @param control: Authoritative control row.
    @return: All current scheduling decisions, without changing any balance.
    """
    # [database snapshot]: No caller can supply a completeness boolean or READY result.
    snapshot = load_snapshot(cursor, control)
    context = aggregate_snapshot(snapshot)  # [closed versions]: Merge barriers never reopen first rewards.
    context = replace(context, historical_versions=context.historical_versions | reward_barriers(cursor))
    decisions = evaluate_reward_candidates(context)
    # [decision subjects]: Current projection already validates unique operation identities.
    candidates = {item.operation_id: item for item in snapshot.candidates}
    return tuple(_historical_gate(candidates[item.operation_id], item, snapshot) for item in decisions)


def settle_reward(cursor: Any, control: dict, operation_id: str) -> RewardDecision:
    """@description: Coordinate claim, ledger, balance, candidate state and durable idempotency in one transaction.
    @param cursor: Cursor inside the locked transaction.
    @param control: Database policy and completeness state.
    @param operation_id: Existing accepted operation seeking its first-version reward.
    @return: Existing award receipt or current nonpaying decision when settlement is blocked.
    """
    cursor.execute("SELECT result_json FROM jb2_settlements WHERE operation_id=%s", (operation_id,))
    # [settlement replay]: Successful results survive process restarts and unknown commit outcomes.
    existing = cursor.fetchone()
    if existing is not None:
        return decode_contract(existing["result_json"], RewardDecision)
    # [current decision]: Never trust READY from a previous request or an external caller.
    decisions = current_decisions(cursor, control)
    decision = next((item for item in decisions if item.operation_id == operation_id), None)
    if decision is None:
        raise ValueError("Unknown accepted operation")
    if decision.status is not RewardCandidateStatus.READY:
        persist_decisions(cursor, decisions)
        return decision
    if not control["settlement_enabled"]:
        raise PermissionError("Real reward settlement remains disabled pending separate policy approval")
    return _award(cursor, control, operation_id)


def _award(cursor: Any, control: dict, operation_id: str) -> RewardDecision:
    """@description: Issue exactly one transactional +1 entry for the recomputed eligible version.
    @param cursor: Globally serialized transaction cursor.
    @param control: Locked current scope used to refresh candidate state.
    @param operation_id: READY accepted operation.
    @return: Durable AWARDED result only after every required write has succeeded.
    """
    # [qualified subject]: All current identity and review facts came from the guarded snapshot.
    snapshot = load_snapshot(cursor, control)
    candidate = next(item for item in snapshot.candidates if item.operation_id == operation_id)
    lock_reconciled_account(cursor, candidate.submitter_id)
    cursor.execute("SELECT song_id FROM jb2_videos WHERE video_id=%s", (candidate.identity.video.video_id,))
    song_id = cursor.fetchone()["song_id"]  # [legacy ledger FK]: Preserve the original six-table constraints.
    ledger_key = "jb2:" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()  # [ledger idempotency]: Bounded and deterministic.
    cursor.execute("INSERT INTO credit_ledger (discord_user_id,credit_delta,reason,idempotency_key,awarded_song_id) "
                   "VALUES (%s,%s,%s,%s,%s)", (candidate.submitter_id, 1, "submission_award", ledger_key, song_id))
    ledger_id = cursor.lastrowid  # [audit link]: AUTO_INCREMENT is an identifier, never intake priority.
    cursor.execute("INSERT INTO jb2_claims (version_id,operation_id,ledger_id,discord_user_id) VALUES (%s,%s,%s,%s)",
                   (candidate.identity.version.value, operation_id, ledger_id, candidate.submitter_id))
    cursor.execute("UPDATE users SET credit_balance=credit_balance+1 WHERE discord_user_id=%s", (candidate.submitter_id,))
    if cursor.rowcount != 1:
        raise LedgerBalanceMismatch("Reward recipient disappeared")
    cursor.execute("UPDATE jb2_control SET revision=revision+1 WHERE singleton_id=1")
    control = dict(control, revision=control["revision"] + 1)
    decisions = current_decisions(cursor, control)  # [post-claim projection]: Reuse the same immutable result under the held gate.
    persist_decisions(cursor, decisions)
    decision = next(item for item in decisions if item.operation_id == operation_id)
    cursor.execute("INSERT INTO jb2_settlements (operation_id,result_json) VALUES (%s,%s)", (operation_id, encode_contract(decision)))
    return decision


def lock_reconciled_account(cursor: Any, discord_user_id: int) -> int:
    """@description: Refuse to compound preexisting ledger and balance inconsistencies before any point changes.
    @param cursor: Transaction cursor holding the global gate before the user lock.
    @param discord_user_id: Intended reward or exchange account.
    @return: Locked, reconciled available credits.
    """
    cursor.execute("SELECT credit_balance FROM users WHERE discord_user_id=%s FOR UPDATE", (discord_user_id,))
    account = cursor.fetchone()  # [locked available credits]: Cannot change during this reward transaction.
    cursor.execute("SELECT COALESCE(SUM(credit_delta),0) AS total FROM credit_ledger WHERE discord_user_id=%s", (discord_user_id,))
    ledger = cursor.fetchone()  # [audit balance]: No direct balance repair is authorized.
    if account is None or account["credit_balance"] != ledger["total"]:
        raise LedgerBalanceMismatch("Account requires audited ledger reconciliation")
    return account["credit_balance"]


def persist_decisions(cursor: Any, decisions: tuple[RewardDecision, ...]) -> None:
    """@description: Persist candidate scheduling and conservative conflict freezes without changing claims.
    @param cursor: Cursor in the same globally serialized transaction.
    @param decisions: Recomputed pure business outcomes.
    @return: None.
    """
    for decision in decisions:
        cursor.execute("UPDATE jb2_events SET reward_status=%s, conflict_frozen=IF(%s,TRUE,conflict_frozen) WHERE operation_id=%s",
                       (decision.status.value, decision.status is RewardCandidateStatus.CONFLICT, decision.operation_id))
