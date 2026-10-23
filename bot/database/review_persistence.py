"""Persist version-checked internal review decisions with Owner authorization and immutable evidence.

Existing Hybrid Review transitions enforce duration gates; operation-keyed audit receipts protect repeated decisions.
"""

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any

from bot.database.reward_settlement import current_decisions, persist_decisions
from bot.database.persisted_snapshot import decode_current_identity, decode_current_review, fetch_all
from bot.database.reward_history import audit_reward_history
from bot.database.submission_codec import decode_contract, encode_contract
from bot.database.submission_store import ensure_version, persist_review
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceRecord, EvidenceReliability, assess_evidence
from bot.review import ApprovalSource, ReviewSnapshot, ValidationError, reopen_for_reexamination
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest, _review_request
from bot.version_evidence import resolve_video_version
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus, require_reference


# [optional citations]: Unset citations are left out of the digest so decisions recorded before they existed still replay.
OPTIONAL_CITATION_FIELDS = ("evidence_reference", "content_judgement")


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
    evidence_reference: str | None = None  # [decision citation]: Unset references keep older digests.
    content_judgement: ContentJudgement | None = None  # [content verdict]: Owner's song judgement an approval relied on.

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
        if self.evidence_reference is not None:
            require_reference(self.evidence_reference)


@dataclass(frozen=True)
class ReexaminationCommand:
    """Carry one formal Owner reexamination that reopens a decision on new reliable evidence."""

    decision_id: str  # [idempotency key]: Repeated reexamination must have identical intent.
    operation_id: str  # [review subject]: Accepted operation whose video receives the new evidence.
    actor_id: int  # [audit actor]: Compared to the immutable configured Owner at the boundary.
    reason: str  # [audit explanation]: Required for every reexamination.
    expected_revision: int  # [optimistic concurrency]: The revision the Owner examined.
    evidence: EvidenceRecord  # [new evidence]: Must be reliable and describe the operation's video.

    def __post_init__(self) -> None:
        """@description: Reject malformed reexamination identities before any transaction begins.
        @param self: Trusted internal Owner reexamination action.
        @return: None.
        """
        require_reference(self.decision_id)
        require_reference(self.operation_id)
        if max(len(self.decision_id), len(self.operation_id)) > 128:
            raise ValueError("Decision identifiers exceed storage limits")


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


def content_judgement_digest(content: ContentJudgement) -> str:
    """@description: Fingerprint every fact of the Owner's content verdict so a changed judgement needs a new preview.
    @param content: Owner content judgement the approval relied on.
    @return: Stable digest of the judged video, judge, reason, time, citation and verdict.
    """
    return decision_digest(("content_judgement", content.video, content.judged_by, content.reason,
                            content.retrieved_at, content.evidence_reference, content.is_song))


def review_command_digest(command: ReviewCommand) -> str:
    """@description: Fingerprint every review decision field, omitting unset optional citations so older digests still replay.
    @param command: Authenticated Owner review action.
    @return: Digest stored with the audited decision.
    """
    intent = []
    for field in command.__dataclass_fields__:
        value = getattr(command, field)
        if field in OPTIONAL_CITATION_FIELDS and value is None:
            continue
        intent.append(content_judgement_digest(value) if field == "content_judgement" else value)
    return decision_digest(tuple(intent))


def reexamination_digest(command: ReexaminationCommand) -> str:
    """@description: Keep reexamination intents distinct from review intents, including the cited evidence record.
    @param command: Authenticated formal reexamination action.
    @return: Digest stored with the audited reexamination.
    """
    return decision_digest(("reexamination",) + tuple(getattr(command, field) for field in command.__dataclass_fields__))


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


def audit_reason(command: ReviewCommand) -> str:
    """@description: Write the cited evidence and content judgement in plain text beside the Owner's reason, so audit rows stay readable.
    @param command: Authenticated Owner review action.
    @return: Owner reason, followed by each citation the decision carries.
    """
    reason = command.reason
    if command.evidence_reference is not None:
        reason = f"{reason} [evidence_reference: {command.evidence_reference}]"
    if command.content_judgement is not None:
        content = command.content_judgement
        reason = f"{reason} [content_judgement: evidence_reference={content.evidence_reference}, judged_by={content.judged_by}]"
    return reason


def apply_review(cursor: Any, control: dict, command: ReviewCommand) -> ReviewSnapshot:
    """@description: Atomically update current review, append evidence and freeze affected reward candidates.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param control: Current database read revision.
    @param command: Previously authenticated Owner action.
    @return: Original or newly committed review result, without issuing Nickel.
    """
    # [full decision intent]: Include evidence, actor, revision and explicit freeze resolution.
    digest = review_command_digest(command)
    previous = replay_decision(cursor, command.decision_id, digest)
    if previous is not None:
        return decode_contract(previous, ReviewSnapshot)
    # [authoritative review subject]: Input cannot supply a replacement current revision.
    request = _current_request(cursor, command)
    identity = command.identity or request.identity  # [trusted mapping update]: Never inferred from titles.
    if identity.video != request.identity.video:
        raise ValueError("Identity update targets another video")
    _require_content_subject(command, request.identity.video)
    ensure_version(cursor, identity)
    review = _apply_transitions(cursor, request, command, identity, audit_reason(command))
    _record_identity_resolution(cursor, command, identity)
    _append_content_judgement(cursor, command)
    audit_reward_history(cursor, command.actor_id, command.reason)
    cursor.execute("UPDATE jb2_control SET revision=revision+1 WHERE singleton_id=1")
    control = dict(control, revision=control["revision"] + 1)
    persist_decisions(cursor, current_decisions(cursor, control))
    save_decision(cursor, command.decision_id, digest, command.actor_id, audit_reason(command), review)
    return review


def _require_content_subject(command: ReviewCommand, video: VideoIdentity) -> None:
    """@description: Refuse a content verdict about another video or judged by someone other than the acting Owner, before any write.
    @param command: Authenticated Owner review action, possibly citing a content judgement.
    @param video: Video of the accepted operation the decision changes.
    @return: None.
    """
    content = command.content_judgement
    if content is not None and (content.video != video or content.judged_by != command.actor_id):
        raise ValueError("Content judgement must describe this video and be judged by its Owner")


def _append_content_judgement(cursor: Any, command: ReviewCommand) -> None:
    """@description: Keep the Owner's complete content verdict as an audit row beside the approval, without any version reference.
    @param cursor: Transaction-owned cursor after replay protection has passed, so a retry never reaches this insert.
    @param command: Authenticated Owner review action, possibly citing a content judgement.
    @return: None.
    """
    content = command.content_judgement
    if content is None:
        return
    cursor.execute("INSERT INTO jb2_evidence (video_id,evidence_json,actor_id,decision_reason) VALUES (%s,%s,%s,%s)",
                   (content.video.video_id, encode_contract(content), command.actor_id, command.reason))


def apply_reexamination(cursor: Any, control: dict, command: ReexaminationCommand) -> ReviewSnapshot:
    """@description: Reopen an approved or rejected review on reliable new evidence, keeping its order and the old awards.
    The reopened review blocks approval until the TK-15 ordering event; the decision and audit stay append-only.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param control: Current database read revision.
    @param command: Previously authenticated formal reexamination.
    @return: Original or newly committed pending review, without issuing Nickel.
    """
    digest = reexamination_digest(command)
    previous = replay_decision(cursor, command.decision_id, digest)  # [replay]: A repeated key returns the original revision.
    if previous is not None:
        return decode_contract(previous, ReviewSnapshot)
    request = _current_request(cursor, command)
    _require_reliable_evidence(command, request.identity.video)
    reopened = reopen_for_reexamination(request.review, command.expected_revision, command.evidence.evidence_id)
    _append_evidence_record(cursor, command)
    identity = _reconciled_identity(cursor, command, request.identity)  # [new contradiction]: Owner evidence can freeze the reward.
    ensure_version(cursor, identity)
    persist_review(cursor, reopened, command.actor_id, command.reason, identity)
    cursor.execute("UPDATE jb2_control SET revision=revision+1 WHERE singleton_id=1")
    control = dict(control, revision=control["revision"] + 1)
    persist_decisions(cursor, current_decisions(cursor, control))  # [award block]: Candidate becomes WAITING_VALIDATION.
    save_decision(cursor, command.decision_id, digest, command.actor_id, command.reason, reopened)
    return reopened


def _require_reliable_evidence(command: ReexaminationCommand, video: VideoIdentity) -> None:
    """@description: Refuse reopening on evidence that is unreliable, judged by someone else, or about another video.
    @param command: Reexamination action carrying the evidence record.
    @param video: Video of the accepted operation.
    @return: None.
    """
    if command.evidence.video != video or command.evidence.judged_by != command.actor_id:
        raise ValueError("Reexamination evidence must describe this video and be judged by its Owner")
    if assess_evidence(command.evidence) is not EvidenceReliability.RELIABLE:
        raise ValueError("Reexamination requires reliable Owner-confirmed evidence")


def _reconciled_identity(cursor: Any, command: ReexaminationCommand, current: VersionAssignment) -> VersionAssignment:
    """@description: Recompute the video's version from all stored Owner evidence, freezing any contradiction with a confirmed version.
    @param cursor: Transaction-owned cursor after the new evidence record has been appended.
    @param command: Formal reexamination whose evidence is the newest fact.
    @param current: Persisted identity before the reopen.
    @return: Unchanged conflict, a conflict against the prior confirmed version, or the recomputed assignment.
    """
    if current.status is VersionIdentityStatus.CONFLICT:
        return current  # [explicit resolution]: Only an explicit Owner clear may remove a historical conflict.
    # [canonical roots]: Merged aliases name the same recording, so comparisons use the flattened roots.
    roots = {row["version_id"]: row["canonical_id"] for row in fetch_all(cursor, "SELECT version_id,canonical_id FROM jb2_versions")}
    records = [_canonical_record(record, roots) for record in _stored_evidence(cursor, current.video.video_id)]
    recomputed = resolve_video_version(current.video, records)
    if recomputed.status is VersionIdentityStatus.UNRESOLVED:
        return current  # [missing support]: Evidence that confirms nothing never erases or freezes an existing identity.
    current_root = _canonical_version(current.version, roots)
    contradicts_confirmed = current.status is VersionIdentityStatus.CONFIRMED and (
        recomputed.status is VersionIdentityStatus.CONFLICT or recomputed.version != current_root)
    if contradicts_confirmed:
        return VersionAssignment(current.video, VersionIdentityStatus.CONFLICT, None, command.evidence.evidence_id)
    return recomputed


def _canonical_record(record: EvidenceRecord, roots: dict[str, str]) -> EvidenceRecord:
    """@description: Replace an evidence record's asserted version by its canonical root so merged aliases compare equal.
    @param record: Stored evidence fact with its original asserted version.
    @param roots: Flattened canonical identity map from the writer-gated transaction.
    @return: Equivalent record whose version is the canonical root, or the record unchanged when it asserts no version.
    """
    if record.version is None:
        return record
    return replace(record, version=RecordingVersionIdentity(roots.get(record.version.value, record.version.value)))


def _canonical_version(version: RecordingVersionIdentity | None, roots: dict[str, str]) -> RecordingVersionIdentity | None:
    """@description: Map one persisted version to its canonical root for comparison with recomputed evidence.
    @param version: Persisted version of the current identity, or None when unresolved.
    @param roots: Flattened canonical identity map from the writer-gated transaction.
    @return: Canonical version, or None unchanged.
    """
    if version is None:
        return None
    return RecordingVersionIdentity(roots.get(version.value, version.value))


def _stored_evidence(cursor: Any, video_id: str) -> list[EvidenceRecord]:
    """@description: Load every Owner evidence fact for one video, skipping review-revision audit tuples in the same table.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param video_id: Video whose evidence decides its recording version.
    @return: Decoded evidence records in stored order.
    """
    cursor.execute("SELECT evidence_json FROM jb2_evidence WHERE video_id=%s ORDER BY evidence_id", (video_id,))
    return [decode_contract(row["evidence_json"], EvidenceRecord) for row in cursor.fetchall() if _is_evidence_payload(row["evidence_json"])]


def load_content_judgements(cursor: Any, video_id: str) -> tuple[ContentJudgement, ...]:
    """@description: Recover the Owner's stored content verdicts for one video so audits can show what an approval relied on.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param video_id: Platform video identifier whose judgements are read.
    @return: Decoded content judgements in stored order; evidence and review audit rows are skipped.
    """
    cursor.execute("SELECT evidence_json FROM jb2_evidence WHERE video_id=%s ORDER BY evidence_id", (video_id,))
    return tuple(decode_contract(row["evidence_json"], ContentJudgement) for row in cursor.fetchall()
                 if _contract_of(row["evidence_json"]) == ContentJudgement.__name__)


def _contract_of(payload: str) -> object:
    """@description: Read the top-level contract name so each reader filters the shared evidence table by type.
    @param payload: Stored JSON contract text.
    @return: Contract name for contract values, or None for tuples and other shapes.
    """
    value = json.loads(payload)["value"]
    return value.get("contract") if isinstance(value, dict) else None


def _is_evidence_payload(payload: str) -> bool:
    """@description: Recognize evidence contracts by their top-level type so review audit tuples are never misread as evidence.
    @param payload: Stored JSON contract text.
    @return: Whether the persisted value is an EvidenceRecord.
    """
    return _contract_of(payload) == EvidenceRecord.__name__


def _append_evidence_record(cursor: Any, command: ReexaminationCommand) -> None:
    """@description: Append the full evidence fact beside the reopened revision so the Owner can audit its source.
    @param cursor: Transaction-owned cursor.
    @param command: Reexamination action with its validated evidence.
    @return: None.
    """
    cursor.execute("INSERT INTO jb2_evidence (video_id,evidence_json,actor_id,decision_reason) VALUES (%s,%s,%s,%s)",
                   (command.evidence.video.video_id, encode_contract(command.evidence), command.actor_id, command.reason))


def _current_request(cursor: Any, command: ReviewCommand | ReexaminationCommand) -> SubmissionRequest:
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


def _apply_transitions(cursor: Any, request: SubmissionRequest, command: ReviewCommand, identity: VersionAssignment,
                       reason: str) -> ReviewSnapshot:
    """@description: Append each intermediate revision while reusing Batch A transition rules.
    @param cursor: Transaction cursor.
    @param request: Authoritative base review and expected revision.
    @param command: Validated internal review intent.
    @param identity: Trusted current identity evidence.
    @param reason: Audit explanation written with each intermediate revision, including any evidence citation.
    @return: Final review with every accepted intermediate revision audited.
    """
    # [validation revision]: Evidence/error transition must pass the original expected version.
    validated = _review_request(replace(request, duration=command.duration, validation_error=command.validation_error))
    if validated.version == request.review.version and (identity != request.identity or command.clear_conflict_freeze):
        validated = replace(validated, version=validated.version + 1)
    persist_review(cursor, validated, command.actor_id, reason, identity)
    # [approval revision]: AUTO and OWNER both use the existing hard gates, never direct field assignment.
    reviewed = _review_request(replace(request, review=validated, expected_review_version=validated.version,
                              approval_source=command.approval_source, song_eligible=command.song_eligible))
    if reviewed != validated:
        persist_review(cursor, reviewed, command.actor_id, reason, identity)
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
