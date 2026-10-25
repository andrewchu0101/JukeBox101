"""Load complete submission projections while holding the shared database writer gate.

Independent scope queries and unpaged row reads feed Batch A manifests; legacy videos remain visible even without new mappings.
"""

from dataclasses import replace
from datetime import timezone
from typing import Any

from bot.database.submission_codec import decode_contract
from bot.review import ReviewSnapshot, effective_priority_released
from bot.reward_candidates import RewardCandidate
from bot.song_identity import VideoIdentity
from bot.submission_snapshot import SnapshotManifest, SubmissionSnapshot
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionExclusion, VersionIdentityStatus

# [global projection]: No per-user, Guild, approval-state or pagination filter may remove predecessors.
EVENT_ROWS_SQL = (
    "SELECT e.*, v.identity_json, v.review_json, v.review_revision, v.review_status, "
    "c.operation_id AS claim_operation, c.version_id AS claimed_version "
    "FROM jb2_events e JOIN jb2_videos v ON v.video_id = e.video_id "
    "LEFT JOIN jb2_claims c ON c.operation_id = e.operation_id ORDER BY e.accepted_sequence"
)
# [legacy coverage]: Old songs not yet seen by the new repository are unknown history, never new rewards.
LEGACY_ROWS_SQL = (
    "SELECT s.platform_track_id AS video_id, l.identity_json FROM songs s "
    "LEFT JOIN jb2_legacy l ON l.song_id = s.song_id "
    "LEFT JOIN jb2_videos v ON v.song_id = s.song_id "
    "WHERE l.song_id IS NOT NULL OR v.song_id IS NULL"
)


def fetch_all(cursor: Any, statement: str, parameters: tuple = ()) -> tuple[dict, ...]:
    """@description: Materialize the complete transaction-owned result without hidden pagination.
    @param cursor: Cursor holding the global writer gate.
    @param statement: Static repository SQL.
    @param parameters: Bound values, never interpolated SQL identifiers.
    @return: Immutable complete row tuple.
    """
    cursor.execute(statement, parameters)
    return tuple(cursor.fetchall())


def load_snapshot(cursor: Any, control: dict) -> SubmissionSnapshot:
    """@description: Certify coverage from independent database scope queries rather than caller booleans.
    @param cursor: Exclusive transaction cursor after the global gate has been acquired.
    @param control: Locked database control state, not a public application argument.
    @return: Batch A snapshot including current reviews, claims, unknown history and exclusions.
    """
    # [expected operations]: Separate unfiltered query detects missing join projection rows.
    operations = fetch_all(cursor, "SELECT operation_id FROM jb2_events")
    # [expected legacy scope]: Independent count and identifiers guard missing history rows.
    legacy_scope = fetch_all(cursor, LEGACY_ROWS_SQL)
    # [canonical projection]: A merge never rewrites original evidence or original claims.
    roots = {row["version_id"]: row["canonical_id"] for row in fetch_all(cursor, "SELECT version_id, canonical_id FROM jb2_versions")}
    candidates = tuple(_candidate(row, roots) for row in fetch_all(cursor, EVENT_ROWS_SQL))
    history = tuple(_legacy(row, roots) for row in fetch_all(cursor, LEGACY_ROWS_SQL))
    exclusions = tuple(_exclusion(row, roots) for row in fetch_all(cursor, "SELECT evidence_json FROM jb2_exclusions"))
    exclusion_scope = fetch_all(cursor, "SELECT evidence_reference FROM jb2_exclusions")
    # [certification gate]: Maintenance must certify legacy writers quiesced before readiness is possible.
    certified = bool(control["snapshot_ready"] and control["legacy_writers_quiesced"])
    manifest = SnapshotManifest(str(control["revision"]), frozenset(row["operation_id"] for row in operations),
                                frozenset(row["video_id"] for row in legacy_scope), control["accepted_sequence"],
                                certified, len(legacy_scope), frozenset(row["evidence_reference"] for row in exclusion_scope))
    return SubmissionSnapshot(manifest, str(control["revision"]), candidates, history, exclusions)


def _canonical(identity: VersionAssignment, roots: dict[str, str]) -> VersionAssignment:
    """@description: Apply only persisted Owner-confirmed canonical merges without deriving musical identity.
    @param identity: Original trusted assignment retained in append-only evidence.
    @param roots: Database-maintained flattened canonical identities.
    @return: Current comparison identity or unchanged unknown assertion.
    """
    if identity.version is None:
        return identity
    if identity.version.value not in roots:
        raise ValueError("Version projection references a missing canonical identity")
    if roots.get(roots[identity.version.value]) != roots[identity.version.value]:
        raise ValueError("Canonical roots must exist and be flattened")
    return replace(identity, version=RecordingVersionIdentity(roots[identity.version.value]))


def _candidate(row: dict, roots: dict[str, str]) -> RewardCandidate:
    """@description: Reuse validated domain contracts for current per-video review and event order.
    @param row: Joined event, current mapping/review and existing claim fact.
    @param roots: Trusted canonical version map.
    @return: Current immutable reward candidate.
    """
    # [current identity]: Frozen conflicts remain explicit rather than becoming optimistic new versions.
    identity = _canonical(decode_current_identity(row), roots)
    if row["conflict_frozen"] or _claim_mapping_conflicts(row, identity, roots):
        identity = VersionAssignment(identity.video, VersionIdentityStatus.CONFLICT, identity.version, "persisted-conflict-freeze")
    review = decode_current_review(row)
    awarded = row["claim_operation"] is not None
    # [ordering event]: A reexamination approval replaces the intake order with its new tail position.
    priority_sequence = review.ordering_sequence if review.ordering_sequence is not None else row["accepted_sequence"]
    # [legacy yield]: Stored reviews from before the ruling are read with the derived release, without rewriting history.
    review = replace(review, priority_released=effective_priority_released(review, awarded=awarded))
    return RewardCandidate(row["operation_id"], row["discord_user_id"], priority_sequence, identity,
                           review, awarded,
                           row["accepted_at"].replace(tzinfo=timezone.utc))


def _claim_mapping_conflicts(row: dict, identity: VersionAssignment, roots: dict[str, str]) -> bool:
    """@description: Preserve original awarded-version facts when later mapping evidence contradicts them.
    @param row: Current event projection with its immutable original claim reference.
    @param identity: Current canonical video mapping.
    @param roots: Confirmed canonical roots.
    @return: Whether existing payment and current version evidence disagree.
    """
    if row["claim_operation"] is None:
        return False
    return identity.version is None or roots[row["claimed_version"]] != identity.version.value


def _legacy(row: dict, roots: dict[str, str]) -> VersionAssignment:
    """@description: Preserve legacy catalog membership without inventing intake order or historical rewards.
    @param row: Existing song with an optional trusted legacy identity assertion.
    @param roots: Persisted canonical identity map.
    @return: Unknown or explicitly evidenced historical assignment.
    """
    if row["identity_json"] is None:
        return VersionAssignment(VideoIdentity(row["video_id"]))
    return _canonical(decode_current_identity(row), roots)


def decode_current_identity(row: dict) -> VersionAssignment:
    """@description: Reject JSON evidence that is bound to a different SQL video row.
    @param row: Current identity projection including its SQL video identifier.
    @return: Subject-validated immutable identity assertion.
    """
    identity = decode_contract(row["identity_json"], VersionAssignment)  # [projection subject]: JSON cannot silently change the row's video.
    if identity.video.video_id != row["video_id"]:
        raise ValueError("Identity projection subject mismatch")
    return identity


def decode_current_review(row: dict) -> ReviewSnapshot:
    """@description: Keep JSON review, revision token and scheduler status in one consistent projection.
    @param row: Current review JSON plus SQL subject, revision and status columns.
    @return: Consistent immutable review or an explicit corruption error.
    """
    review = decode_contract(row["review_json"], ReviewSnapshot)  # [projection revision]: Do not certify stale or mixed current facts.
    if (review.video.video_id, review.version, review.status.value) != (row["video_id"], row["review_revision"], row["review_status"]):
        raise ValueError("Review projection revision or subject mismatch")
    return review


def _exclusion(row: dict, roots: dict[str, str]) -> VersionExclusion:
    """@description: Preserve scoped exclusion evidence through canonical identity merges.
    @param row: Append-only persisted exclusion proof.
    @param roots: Confirmed canonical identity map.
    @return: Exclusion scoped to the current canonical root.
    """
    # [negative evidence]: A missing root is corruption, never evidence of novelty.
    exclusion = decode_contract(row["evidence_json"], VersionExclusion)
    return replace(exclusion, version=RecordingVersionIdentity(roots[exclusion.version.value]))


def reward_barriers(cursor: Any) -> frozenset[RecordingVersionIdentity]:
    """@description: Keep merged rewarded versions permanently closed even after catalog identity changes.
    @param cursor: Cursor holding the global serialization gate.
    @return: Versions whose first-reward opportunity must not reopen.
    """
    return frozenset(RecordingVersionIdentity(row["version_id"]) for row in fetch_all(cursor,
                     "SELECT version_id FROM jb2_barriers UNION SELECT v.canonical_id AS version_id "
                     "FROM jb2_claims c JOIN jb2_versions v ON v.version_id=c.version_id"))
