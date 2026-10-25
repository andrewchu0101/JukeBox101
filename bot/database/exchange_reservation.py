"""Reserve one exchange recommendation and debit one point inside a single locked transaction.

Eligibility is recomputed under the writer gate from settled versions, verified links and the requester's own history; a request key makes technical retries replay the original reservation.
"""

import hashlib
from dataclasses import dataclass
from enum import Enum
from random import Random
from typing import Any

from datetime import datetime

from bot.database.compensation_schema import compensation_tables_present, require_schema
from bot.database.compensation_store import held_grant_song_ids, pending_block
from bot.database.persisted_snapshot import load_snapshot
from bot.database.reward_settlement import LedgerBalanceMismatch, current_decisions, lock_reconciled_account
from bot.exchange_selection import SettledVersion, draw_exchange_candidate
from bot.reward_candidates import RewardCandidateStatus

EXCHANGE_COST = 1  # [spendable point cost]: One Nickel per successful reservation, as fixed by S8.
OPEN_DELIVERY_STATES = ("reserved", "sending", "uncertain")  # [unfinished exchange]: Any of these blocks a new debit; failed and delivered do not.


class ExchangeStatus(Enum):
    """Name every business outcome of one exchange request without exposing internal state."""

    RESERVED = "reserved"  # [new reservation]: One recommendation and one debit were committed together.
    REPLAYED = "replayed"  # [technical retry]: The original reservation was returned without a second debit.
    NO_CANDIDATE = "no_candidate"  # [no eligible version]: Nothing was reserved and nothing was charged.
    INSUFFICIENT_CREDITS = "insufficient_credits"  # [spendable points]: The requester could not pay the cost.
    IN_PROGRESS = "in_progress"  # [open exchange]: An earlier reservation of this requester is reserved, sending or uncertain; nothing is debited.
    PENDING_REVIEW = "pending_review"  # [stranded reservation]: An old, never-claimed reservation blocks new requests; nothing was debited.


@dataclass(frozen=True)
class ExchangeOutcome:
    """Carry the reservation result to the service layer."""

    status: ExchangeStatus  # [business outcome]: Decides the user-facing reply.
    recommendation_id: int | None  # [reservation identity]: Present only when a reservation exists.
    youtube_url: str | None  # [delivery link]: Present only for a verified recommendation.


def reserve_exchange(cursor: Any, control: dict, discord_user_id: int, request_key: str, rng: Random, now: datetime,
                     compensation_enabled: bool = False) -> ExchangeOutcome:
    """@description: Reserve one eligible recording version and debit one point, or report why nothing changed.
    @param cursor: Cursor inside the globally serialized transaction.
    @param control: Authoritative control row used to recompute the requester's own submissions.
    @param discord_user_id: Authenticated requesting user.
    @param request_key: Technical idempotency key that retries must reuse.
    @param rng: Injected random source for the uniform draw.
    @param now: Naive UTC clock for the stranded-reservation check, taken inside the gate.
    @param compensation_enabled: Feature switch; when true, or when the compensation tables exist, the stranded and open-grant block and the grant-held songs are checked, after an explicit schema check.
    @return: Reserved, replayed or unchanged outcome; a failure raises before any commit.
    """
    ledger_key = "exchange:" + hashlib.sha256(request_key.encode("utf-8")).hexdigest()  # [ledger idempotency]: Bounded and deterministic.
    replay = _replayed_outcome(cursor, ledger_key)
    if replay is not None:
        return replay
    guarded = compensation_enabled or compensation_tables_present(cursor)  # [switch or tables]: With the tables present the block and the grant holds apply even while the switch is off (D-2).
    if guarded:
        require_schema(cursor)  # [explicit]: A guarded exchange never runs on a schema missing a table or definition.
    if guarded and pending_block(cursor, discord_user_id, now):  # [block under the gate]: After the replay, so the original order is always identified first.
        return ExchangeOutcome(ExchangeStatus.PENDING_REVIEW, None, None)
    if _has_open_delivery(cursor, discord_user_id):  # [one open exchange]: Checked under the gate, so a new key cannot debit while an earlier delivery is unfinished.
        return ExchangeOutcome(ExchangeStatus.IN_PROGRESS, None, None)
    balance = lock_reconciled_account(cursor, discord_user_id)  # [locked spendable credits]: Checked before any candidate work.
    if balance < EXCHANGE_COST:
        return ExchangeOutcome(ExchangeStatus.INSUFFICIENT_CREDITS, None, None)
    own_versions, own_videos = _own_submissions(cursor, control, discord_user_id)  # [requester facts]: Versions and Video IDs, both checked.
    blocked = own_versions | _conflict_versions(cursor, control)  # [versions not offered]: Own submissions and conflicts.
    candidate = draw_exchange_candidate(_settled_versions(cursor), blocked, _held_song_ids(cursor, discord_user_id, guarded), rng, own_videos)
    if candidate is None:
        return ExchangeOutcome(ExchangeStatus.NO_CANDIDATE, None, None)
    recommendation_id = _reserve(cursor, discord_user_id, ledger_key, candidate)
    return ExchangeOutcome(ExchangeStatus.RESERVED, recommendation_id, candidate.youtube_url)


def _replayed_outcome(cursor: Any, ledger_key: str) -> ExchangeOutcome | None:
    """@description: Return the original reservation for a retried request so a technical retry never debits twice.
    @param cursor: Cursor inside the transaction.
    @param ledger_key: Deterministic ledger idempotency key of the request.
    @return: Replayed outcome, or None when the request has never been reserved.
    """
    cursor.execute("SELECT r.recommendation_id,s.youtube_url FROM credit_ledger l "
                   "JOIN recommendations r ON r.recommendation_id=l.recommendation_id "
                   "JOIN songs s ON s.song_id=r.song_id WHERE l.idempotency_key=%s", (ledger_key,))
    row = cursor.fetchone()  # [original receipt]: The ledger key is unique, so at most one reservation matches.
    if row is None:
        return None
    return ExchangeOutcome(ExchangeStatus.REPLAYED, row["recommendation_id"], row["youtube_url"])


def _has_open_delivery(cursor: Any, discord_user_id: int) -> bool:
    """@description: Tell whether the requester has an exchange still reserved, sending or uncertain, which must finish before another debit.
    @param cursor: Cursor inside the globally serialized transaction.
    @param discord_user_id: Requesting user.
    @return: True while one recommendation of the requester is in an open state.
    """
    cursor.execute("SELECT status FROM recommendations WHERE discord_user_id=%s", (discord_user_id,))
    return any(row["status"] in OPEN_DELIVERY_STATES for row in cursor.fetchall())


def _settled_versions(cursor: Any) -> tuple[SettledVersion, ...]:
    """@description: Expose every recording version whose first lawful reward is already settled, with its delivery facts.
    @param cursor: Cursor inside the transaction.
    @return: Settled versions, one per claimed recording version.
    """
    cursor.execute("SELECT c.version_id,e.video_id FROM jb2_claims c JOIN jb2_events e ON e.operation_id=c.operation_id")
    claims = cursor.fetchall()  # [settled versions]: A claim exists only after a lawful first settlement.
    cursor.execute("SELECT video_id,song_id FROM jb2_videos")
    song_by_video = {row["video_id"]: row["song_id"] for row in cursor.fetchall()}  # [video to song]: Delivery uses the song row.
    cursor.execute("SELECT song_id,youtube_url,resolution_status FROM songs")
    songs = {row["song_id"]: row for row in cursor.fetchall()}  # [delivery facts]: Verification is checked in pure selection.
    return tuple(_settled(claim, song_by_video, songs) for claim in claims)


def _settled(claim: dict, song_by_video: dict, songs: dict) -> SettledVersion:
    """@description: Join one claim with its song's verified delivery facts without guessing a link.
    @param claim: Claimed version and the event video that earned it.
    @param song_by_video: Mapping from video identifier to song row.
    @param songs: Song rows keyed by identifier.
    @return: Settled version with its delivery facts.
    """
    song_id = song_by_video[claim["video_id"]]  # [delivery song]: The claimed video's song.
    song = songs[song_id]
    return SettledVersion(claim["version_id"], claim["video_id"], song_id, song["youtube_url"], song["resolution_status"] == "verified")


def _own_submissions(cursor: Any, control: dict, discord_user_id: int) -> tuple[frozenset[str], frozenset[str]]:
    """@description: Collect the requester's submitted versions and Video IDs; a Video ID blocks even when its version is unresolved.
    @param cursor: Cursor inside the transaction.
    @param control: Authoritative control row for the snapshot.
    @param discord_user_id: Requesting user.
    @return: Confirmed canonical versions and every Video ID the requester submitted.
    """
    snapshot = load_snapshot(cursor, control)  # [submitter facts]: Reuses the guarded projection, not a second parser.
    own = [item for item in snapshot.candidates if item.submitter_id == discord_user_id]  # [own submissions]: Every intake, resolved or not.
    versions = frozenset(version for item in own if (version := _version_of(item)) is not None)  # [confirmed versions]: Never guessed.
    videos = frozenset(item.identity.video.video_id for item in own)  # [requested videos]: Exact identifiers, independent of version.
    return versions, videos


def _version_of(item: Any) -> str | None:
    """@description: Read a candidate's canonical version, which is absent while its identity is unresolved.
    @param item: Snapshot candidate.
    @return: Canonical version value, or None when no version is confirmed.
    """
    version = item.identity.version  # [unresolved identity]: Unconfirmed submissions cannot block by version.
    return None if version is None else version.value


def _conflict_versions(cursor: Any, control: dict) -> frozenset[str]:
    """@description: Withdraw a recording version from new exchanges once any of its candidates is in a conflict freeze.
    @param cursor: Cursor inside the transaction.
    @param control: Authoritative control row for the snapshot.
    @return: Canonical versions with a current conflict decision.
    """
    versions = {item.operation_id: _version_of(item) for item in load_snapshot(cursor, control).candidates}  # [operation versions]: Same snapshot as the decisions.
    return frozenset(versions[decision.operation_id] for decision in current_decisions(cursor, control)
                     if decision.status is RewardCandidateStatus.CONFLICT and versions.get(decision.operation_id) is not None)


def _held_song_ids(cursor: Any, discord_user_id: int, compensation_enabled: bool = False) -> frozenset[int]:
    """@description: Exclude every song the requester holds, meaning any reservation not yet released by refund.
    @param cursor: Cursor inside the transaction.
    @param discord_user_id: Requesting user.
    @param compensation_enabled: Feature switch; when true, songs held through open or completed grants are excluded too.
    @return: Song identifiers still held by the requester.
    """
    cursor.execute("SELECT song_id,released_at FROM recommendations WHERE discord_user_id=%s", (discord_user_id,))
    # [held songs]: Delivered, sending, uncertain and unrefunded failed reservations keep the song; a released one does not.
    held_by_reservation = frozenset(row["song_id"] for row in cursor.fetchall() if row["released_at"] is None)
    if not compensation_enabled:
        return held_by_reservation
    return held_by_reservation | held_grant_song_ids(cursor, discord_user_id)  # [grants hold too]: A redelivered song stays held after the reservation was released.


def _reserve(cursor: Any, discord_user_id: int, ledger_key: str, candidate: SettledVersion) -> int:
    """@description: Insert the reservation and its single debit, failing the whole transaction if the balance row disappears.
    @param cursor: Cursor holding the writer gate.
    @param discord_user_id: Requesting user.
    @param ledger_key: Deterministic idempotency key of the request.
    @param candidate: The drawn settled version and its verified song.
    @return: Generated recommendation identifier.
    """
    cursor.execute("INSERT INTO recommendations (discord_user_id,song_id,status) VALUES (%s,%s,%s)",
                   (discord_user_id, candidate.song_id, "reserved"))
    recommendation_id = cursor.lastrowid  # [reservation identity]: Generated by the database, never a sequence.
    cursor.execute("INSERT INTO credit_ledger (discord_user_id,credit_delta,reason,idempotency_key,recommendation_id) "
                   "VALUES (%s,%s,%s,%s,%s)", (discord_user_id, -EXCHANGE_COST, "exchange_debit", ledger_key, recommendation_id))
    cursor.execute("UPDATE users SET credit_balance=credit_balance-1 WHERE discord_user_id=%s", (discord_user_id,))
    if cursor.rowcount != 1:
        raise LedgerBalanceMismatch("Exchange recipient disappeared")  # [atomic rollback]: The reservation is not kept alone.
    return recommendation_id
