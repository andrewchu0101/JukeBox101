"""Audit historical duplicate rewards after trusted identity confirmation or canonical merges.

Both old and new ledger entries remain immutable; deduplicated exceptions close future first-reward opportunities.
"""

import hashlib
import json
from typing import Any

from bot.database.persisted_snapshot import fetch_all
from bot.database.submission_codec import decode_contract
from bot.version_identity import VersionAssignment, VersionIdentityStatus

AWARD_AUDIT_SQL = (
    "SELECT l.ledger_id,l.discord_user_id,l.awarded_song_id,s.platform_track_id AS video_id, "
    "h.song_id AS legacy_song_id,h.identity_json AS legacy_identity_json,v.identity_json AS video_identity_json, "
    "c.version_id AS claimed_version FROM credit_ledger l JOIN songs s ON s.song_id=l.awarded_song_id "
    "LEFT JOIN jb2_legacy h ON h.song_id=s.song_id LEFT JOIN jb2_videos v ON v.song_id=s.song_id "
    "LEFT JOIN jb2_claims c ON c.ledger_id=l.ledger_id WHERE l.reason=%s"
)  # [historical audit scope]: Old ledger entries are not converted into fictional acceptance events.


def audit_reward_history(cursor: Any, actor_id: int, reason: str) -> None:
    """@description: Record evidenced historical duplicate awards without moving or reclaiming any Nickel.
    @param cursor: Globally serialized transaction cursor after trusted evidence updates.
    @param actor_id: Authenticated Owner who supplied the decision.
    @param reason: Audited identity or merge explanation.
    @return: None.
    """
    # [canonical evidence]: Unknown legacy recordings stay unknown until explicitly confirmed.
    roots = {row["version_id"]: row["canonical_id"] for row in fetch_all(cursor, "SELECT version_id,canonical_id FROM jb2_versions")}
    groups: dict[str, list[dict]] = {}  # [original rewards]: Group facts only by confirmed current canonical identity.
    for row in fetch_all(cursor, AWARD_AUDIT_SQL, ("submission_award",)):
        version = _awarded_version(row, roots)  # [audit identity]: Existing claims retain their original paid version.
        if version is not None:
            groups.setdefault(version, []).append(row)
    for version, rewards in groups.items():
        if len(rewards) > 1:
            _record_exception(cursor, version, rewards, actor_id, reason)


def _awarded_version(row: dict, roots: dict[str, str]) -> str | None:
    """@description: Distinguish immutable paid-version facts from unresolved historical metadata.
    @param row: Original ledger reference and optional current/legacy evidence.
    @param roots: Trusted canonical version roots.
    @return: Confirmed canonical paid version, or None when historical identity is unresolved.
    """
    if row["claimed_version"] is not None:
        return roots[row["claimed_version"]]
    # [legacy evidence]: An intake assertion alone cannot resolve an older catalog membership.
    payload = row["legacy_identity_json"] if row["legacy_song_id"] is not None else row["video_identity_json"]
    if payload is None:
        return None
    identity = decode_contract(payload, VersionAssignment)
    if identity.status is not VersionIdentityStatus.CONFIRMED:
        return None
    return roots[identity.version.value]


def _record_exception(cursor: Any, version: str, rewards: list[dict], actor_id: int, reason: str) -> None:
    """@description: Deduplicate audit discoveries while permanently blocking another first-version award.
    @param cursor: Transaction cursor holding the writer gate.
    @param version: Confirmed canonical affected version.
    @param rewards: Original ledger references, retained without modification.
    @param actor_id: Authenticated Owner decision actor.
    @param reason: Evidence-backed discovery explanation.
    @return: None.
    """
    # [exception idempotency]: Repeated audits of the same canonical ledger group do not create duplicate exceptions.
    audit_key = hashlib.sha256(json.dumps((version, sorted(row["ledger_id"] for row in rewards))).encode("utf-8")).hexdigest()
    cursor.execute("INSERT INTO jb2_exceptions (audit_key,version_id,actor_id,reason,original_claims) VALUES (%s,%s,%s,%s,%s) "
                   "ON CONFLICT (audit_key) DO NOTHING", (audit_key, version, actor_id, reason, json.dumps(rewards, sort_keys=True)))
    cursor.execute("INSERT INTO jb2_barriers (version_id,reason) VALUES (%s,%s) ON CONFLICT (version_id) DO NOTHING", (version, reason))
