"""Resolve a frozen version conflict in favour of one stored Owner record, inside the caller's writer-gated transaction.

Inputs are the conflicted operation, the Owner evidence to keep and the revision the Owner saw; the effect is a confirmed identity on that record's version,
the competing versions merged into it, and every candidate that was frozen only by this conflict released and re-evaluated. Nothing is paid here.
"""

from typing import Any

from bot.database.review_persistence import (ReviewCommand, _canonical_roots, _current_request, _stored_evidence, apply_review,
                                             replay_decision, review_command_digest, stored_evidence_record)
from bot.database.submission_maintenance import merge_versions
from bot.database.submission_store import ensure_version
from bot.evidence_record import EvidenceRecord, EvidenceSourceType
from bot.review import ReviewSnapshot
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus


def resolve_conflict_to_owner_record(cursor: Any, control: dict, actor_id: int, decision_id: str, operation_id: str,
                                     keep_evidence_id: str, reason: str, expected_revision: int) -> ReviewSnapshot:
    """@description: Replace a frozen conflict by the version of one Owner record, so a contradiction caused by a later record no longer blocks rewards.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param control: Locked control row.
    @param actor_id: Authenticated configured Owner.
    @param decision_id: Idempotency key of this resolution.
    @param operation_id: Any accepted operation of the conflicted video.
    @param keep_evidence_id: Stored Owner record whose version wins.
    @param reason: Owner's audit explanation.
    @param expected_revision: Review revision the Owner examined.
    @return: The review after the resolution, or the original result for a repeated decision key.
    """
    request = _current_request(cursor, operation_id, expected_revision)
    kept = _kept_owner_record(cursor, operation_id, keep_evidence_id)
    command = _resolution_command(kept, actor_id, decision_id, operation_id, reason, expected_revision)
    if replay_decision(cursor, decision_id, review_command_digest(command)) is not None:
        return apply_review(cursor, control, command)  # [replay]: apply_review returns the stored result without writing.
    if request.identity.status is not VersionIdentityStatus.CONFLICT:
        raise ValueError("Only a frozen version conflict can be resolved")
    _merge_competing_versions(cursor, control, actor_id, decision_id, reason, kept)
    return apply_review(cursor, control, command)


def _resolution_command(kept: EvidenceRecord, actor_id: int, decision_id: str, operation_id: str, reason: str,
                        expected_revision: int) -> ReviewCommand:
    """@description: Build the review command that carries the explicit conflict-unfreeze intent and the kept record's version.
    @param kept: The stored Owner record that wins.
    @param actor_id: Owner.
    @param decision_id: Idempotency key.
    @param operation_id: Conflicted operation.
    @param reason: Owner's explanation.
    @param expected_revision: Revision the Owner examined.
    @return: Review command with clear_conflict_freeze set.
    """
    identity = VersionAssignment(kept.video, VersionIdentityStatus.CONFIRMED, kept.version, kept.evidence_id)
    return ReviewCommand(decision_id, operation_id, actor_id, reason, expected_revision, identity=identity, clear_conflict_freeze=True,
                         evidence_reference=kept.evidence_id)


def _kept_owner_record(cursor: Any, operation_id: str, keep_evidence_id: str) -> EvidenceRecord:
    """@description: Load the record to keep and refuse anything that is not an Owner record naming a version for this video.
    @param cursor: Transaction-owned cursor.
    @param operation_id: Conflicted operation.
    @param keep_evidence_id: Evidence identifier chosen by the Owner.
    @return: The stored Owner record.
    """
    kept = stored_evidence_record(cursor, operation_id, keep_evidence_id)  # [same video]: Unknown or foreign identifiers are refused here.
    if kept.source_type is not EvidenceSourceType.OWNER_REVIEW or kept.version is None:
        raise ValueError("Only an Owner-confirmed record that names a version can be kept")
    return kept


def _merge_competing_versions(cursor: Any, control: dict, actor_id: int, decision_id: str, reason: str, kept: EvidenceRecord) -> None:
    """@description: Make every other version asserted for the video an alias of the kept one, so the stored records stop contradicting each other.
    @param cursor: Transaction-owned cursor.
    @param control: Locked control row.
    @param actor_id: Owner.
    @param decision_id: Resolution key; each merge derives its own key from it.
    @param reason: Owner's explanation.
    @param kept: The Owner record that wins.
    @return: None.
    """
    asserted = sorted({record.version.value for record in _stored_evidence(cursor, kept.video.video_id) if record.version is not None})
    for version in asserted:  # [register first]: A version asserted only by a contradicting record has no identity row until now.
        ensure_version(cursor, VersionAssignment(kept.video, VersionIdentityStatus.CONFIRMED, RecordingVersionIdentity(version), kept.evidence_id))
    roots = _canonical_roots(cursor)
    target = kept.version.value
    for other in asserted:
        if roots.get(other, other) != roots.get(target, target):
            merge_versions(cursor, control, f"{decision_id}:merge:{other}", actor_id, reason, other, target)
