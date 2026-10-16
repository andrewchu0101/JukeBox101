"""Persist version-checked internal review decisions with Owner authorization and immutable evidence.

Existing Hybrid Review transitions enforce duration gates; operation-keyed audit receipts protect repeated decisions.
"""

import hashlib
from dataclasses import dataclass, replace
from typing import Any

from bot.database.reward_settlement import current_decisions, persist_decisions
from bot.database.persisted_snapshot import decode_current_identity, decode_current_review
from bot.database.reward_history import audit_reward_history
from bot.database.submission_codec import decode_contract, encode_contract
from bot.database.submission_store import ensure_version, persist_review
from bot.eligibility import DurationEvidence
from bot.review import ApprovalSource, ReviewSnapshot, ValidationError
from bot.submission_workflow import SubmissionRequest, _review_request
from bot.version_identity import VersionAssignment, require_reference


@dataclass(frozen=True)
class ReviewCommand:
    """Carry one authenticated internal review action, independent of Discord command text."""

    decision_id: str  # [idempotency key]: Repeated decisions must have identical intent.
    operation_id: str  # [review subject]: Existing accepted event, not a fabricated historical order.
    actor_id: int  # [audit actor]: Compared to immutable configured Owner identity at the boundary.
    reason: str  # [audit explanation]: Required for every persisted decision.
    expected_revision: int  # [optimistic concurrency]: Old worker results cannot overwrite current review.
    duration: DurationEvidence | None = None  # [full-video evidence]: Internal provider assertion only.
    approval_source: ApprovalSource | None = None  # [approval path]: AUTO still requires trusted internal invocation.
    song_eligible: bool | None = None  # [explicit decision]: Duration passing alone never approves a song.
    validation_error: ValidationError | None = None  # [retryable error]: Not a rejection policy.
    identity: VersionAssignment | None = None  # [new evidence]: Owner-confirmed mapping or conflict.
    clear_conflict_freeze: bool = False  # [explicit resolution]: Never automatically clears historical conflicts.

    def __post_init__(self) -> None:
        """@description: Reject malformed action identities and mutable-policy switches before persistence.
        @param self: Trusted internal Owner action.
        @return: None.
        """
        require_reference(self.decision_id)
        require_reference(self.operation_id)
        if max(len(self.decision_id), len(self.operation_id)) > 128:
            raise ValueError("Decision identifiers exceed storage limits")
        if type(self.clear_conflict_freeze) is not bool:
            raise ValueError("Conflict resolution must be explicit")


def require_owner(actor_id: int, owner_id: int, reason: str) -> None:
    """@description: Reject unauthorized actors before any decision or reward transaction begins.
    @param actor_id: Authenticated caller identity from the future trusted transport.
    @param owner_id: Application-configured immutable Owner Discord user ID.
    @param reason: Required audit explanation.
    @return: None.
    """
    if type(actor_id) is not int or actor_id != owner_id:
        raise PermissionError("Owner identity is required")
    require_reference(reason)
    if len(reason) > 1024:
        raise ValueError("Audit reason is too long")


def decision_digest(intent: tuple) -> str:
    """@description: Bind idempotency keys to the complete original decision intent.
    @param intent: Immutable audited command fields.
    @return: Stable digest used only for replay comparison, not authentication.
    """
    return hashlib.sha256(encode_contract(intent).encode("utf-8")).hexdigest()


def replay_decision(cursor: Any, decision_id: str, digest: str) -> str | None:
    """@description: Refuse idempotency-key reuse for a different Owner action.
    @param cursor: Globally serialized transaction cursor.
    @param decision_id: Stable audited command key.
    @param digest: Complete intent digest.
    @return: Original encoded result, or None for a new action.
    """
    require_reference(decision_id)
    cursor.execute("SELECT request_digest,result_json FROM jb2_decisions WHERE decision_id=%s", (decision_id,))
    previous = cursor.fetchone()  # [decision replay]: Never replace an earlier audit record.
    if previous is None:
        return None
    if previous["request_digest"] != digest:
        raise ValueError("Decision key was reused for different intent")
    return previous["result_json"]


def save_decision(cursor: Any, decision_id: str, digest: str, actor_id: int, reason: str, result: object) -> None:
    """@description: Save immutable audited intent and its original result in the same transaction.
    @param cursor: Transaction-owned cursor.
    @param decision_id: Unique internal action key.
    @param digest: Intent fingerprint checked on every replay.
    @param actor_id: Authenticated Owner identity.
    @param reason: Human-readable explanation.
    @param result: Original safe domain result.
    @return: None.
    """
    cursor.execute("INSERT INTO jb2_decisions (decision_id,request_digest,actor_id,decision_reason,result_json) VALUES (%s,%s,%s,%s,%s)",
                   (decision_id, digest, actor_id, reason, encode_contract(result)))


def apply_review(cursor: Any, control: dict, command: ReviewCommand) -> ReviewSnapshot:
    """@description: Atomically update current review, append evidence and freeze affected reward candidates.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param control: Current database read revision.
    @param command: Previously authenticated Owner action.
    @return: Original or newly committed review result, without issuing Nickel.
    """
    # [full decision intent]: Include evidence, actor, revision and explicit freeze resolution.
    digest = decision_digest(tuple(getattr(command, field) for field in command.__dataclass_fields__))
    previous = replay_decision(cursor, command.decision_id, digest)
    if previous is not None:
        return decode_contract(previous, ReviewSnapshot)
    # [authoritative review subject]: Input cannot supply a replacement current revision.
    request = _current_request(cursor, command)
    identity = command.identity or request.identity  # [trusted mapping update]: Never inferred from titles.
    if identity.video != request.identity.video:
        raise ValueError("Identity update targets another video")
    ensure_version(cursor, identity)
    review = _apply_transitions(cursor, request, command, identity)
    _record_identity_resolution(cursor, command, identity)
    audit_reward_history(cursor, command.actor_id, command.reason)
    cursor.execute("UPDATE jb2_control SET revision=revision+1 WHERE singleton_id=1")
    control = dict(control, revision=control["revision"] + 1)
    persist_decisions(cursor, current_decisions(cursor, control))
    save_decision(cursor, command.decision_id, digest, command.actor_id, command.reason, review)
    return review


def _current_request(cursor: Any, command: ReviewCommand) -> SubmissionRequest:
    """@description: Load the current video revision shared by all accepted operations for that video.
    @param cursor: Globally serialized cursor.
    @param command: Review command naming an existing operation.
    @return: Request ready for the existing pure version checks.
    """
    cursor.execute("SELECT e.*,v.identity_json,v.review_json,v.review_revision,v.review_status FROM jb2_events e "
                   "JOIN jb2_videos v ON v.video_id=e.video_id WHERE e.operation_id=%s", (command.operation_id,))
    row = cursor.fetchone()  # [current projection]: Never take the current review from command payload.
    if row is None:
        raise ValueError("Unknown review operation")
    return SubmissionRequest(row["source_url"], row["operation_id"], row["discord_user_id"], row["accepted_sequence"],
                             decode_current_identity(row), decode_current_review(row), command.expected_revision)


def _apply_transitions(cursor: Any, request: SubmissionRequest, command: ReviewCommand, identity: VersionAssignment) -> ReviewSnapshot:
    """@description: Append each intermediate revision while reusing Batch A transition rules.
    @param cursor: Transaction cursor.
    @param request: Authoritative base review and expected revision.
    @param command: Validated internal review intent.
    @param identity: Trusted current identity evidence.
    @return: Final review with every accepted intermediate revision audited.
    """
    # [validation revision]: Evidence/error transition must pass the original expected version.
    validated = _review_request(replace(request, duration=command.duration, validation_error=command.validation_error))
    if validated.version == request.review.version and (identity != request.identity or command.clear_conflict_freeze):
        validated = replace(validated, version=validated.version + 1)
    persist_review(cursor, validated, command.actor_id, command.reason, identity)
    # [approval revision]: AUTO and OWNER both use the existing hard gates, never direct field assignment.
    reviewed = _review_request(replace(request, review=validated, expected_review_version=validated.version,
                              approval_source=command.approval_source, song_eligible=command.song_eligible))
    if reviewed != validated:
        persist_review(cursor, reviewed, command.actor_id, command.reason, identity)
    return reviewed


def _record_identity_resolution(cursor: Any, command: ReviewCommand, identity: VersionAssignment) -> None:
    """@description: Preserve legacy membership and require explicit Owner conflict-unfreeze intent.
    @param cursor: Globally serialized transaction cursor.
    @param command: Authenticated audited action.
    @param identity: New evidence assertion stored in the current projection and audit stream.
    @return: None.
    """
    if command.identity is not None:
        cursor.execute("UPDATE jb2_legacy l JOIN jb2_videos v ON v.song_id=l.song_id SET l.identity_json=%s WHERE v.video_id=%s",
                       (encode_contract(identity), identity.video.video_id))
    if command.clear_conflict_freeze is True:
        cursor.execute("UPDATE jb2_events SET conflict_frozen=FALSE WHERE video_id=%s", (identity.video.video_id,))
