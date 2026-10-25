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
from bot.decision_snapshot import DecisionSnapshot, require_unchanged_snapshot, snapshot_intent
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceRecord, EvidenceReliability, assess_evidence
from bot.review import (
    ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, StaleReviewVersion, ValidationError,
    reopen_for_reexamination,
)
from bot.song_identity import VideoIdentity
from bot.urls import canonical_watch_url
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest, _review_request
from bot.version_evidence import resolve_video_version
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus, require_reference


# [optional citations]: Unset citations are left out of the digest so decisions recorded before they existed still replay.
OPTIONAL_CITATION_FIELDS = ("evidence_reference", "content_judgement", "expected_snapshot")


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
    identity: VersionAssignment | None = None  # [new evidence]: CONFIRMED must cite stored owner evidence; CONFLICT freezes.
    clear_conflict_freeze: bool = False  # [explicit resolution]: Never automatically clears historical conflicts.
    evidence_reference: str | None = None  # [decision citation]: Unset references keep older digests.
    content_judgement: ContentJudgement | None = None  # [content verdict]: Owner's song judgement an approval relied on.
    expected_snapshot: DecisionSnapshot | None = None  # [previewed facts]: Refuse the write when identity, canonical root or evidence moved.

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
    expected_snapshot: DecisionSnapshot | None = None  # [previewed facts]: Refuse the reopen when identity, canonical root or evidence moved.

    def __post_init__(self) -> None:
        """@description: Reject malformed reexamination identities before any transaction begins.
        @param self: Trusted internal Owner reexamination action.
        @return: None.
        """
        _require_decision_keys(self.decision_id, self.operation_id)


@dataclass(frozen=True)
class VersionConfirmationCommand:
    """Carry one Owner confirmation that a reliable evidence fact establishes a recording version, without reopening the review."""

    decision_id: str  # [idempotency key]: Retries with identical intent replay the original result.
    operation_id: str  # [review subject]: Accepted operation whose video the evidence confirms.
    actor_id: int  # [audit actor]: Compared to the immutable configured Owner at the boundary.
    reason: str  # [audit explanation]: Required for every confirmation.
    expected_revision: int  # [optimistic concurrency]: The review revision the Owner examined.
    evidence: EvidenceRecord  # [version evidence]: Must be reliable and describe the operation's video.
    expected_snapshot: DecisionSnapshot | None = None  # [previewed facts]: Refuse the stamp when identity, canonical root or evidence moved.

    def __post_init__(self) -> None:
        """@description: Reject malformed confirmation identities before any transaction begins.
        @param self: Trusted internal Owner confirmation action.
        @return: None.
        """
        _require_decision_keys(self.decision_id, self.operation_id)


def _require_decision_keys(decision_id: str, operation_id: str) -> None:
    """@description: Keep Owner action keys valid and within storage limits for every decision-type command.
    @param decision_id: Idempotency key of the action.
    @param operation_id: Accepted operation the action targets.
    @return: None.
    """
    require_reference(decision_id)
    require_reference(operation_id)
    if max(len(decision_id), len(operation_id)) > 128:
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
    return decision_digest(_command_intent(command))


def reexamination_digest(command: ReexaminationCommand) -> str:
    """@description: Keep reexamination intents distinct from review intents, including the cited evidence record.
    @param command: Authenticated formal reexamination action.
    @return: Digest stored with the audited reexamination.
    """
    return decision_digest(("reexamination",) + _command_intent(command))


def version_confirmation_digest(command: VersionConfirmationCommand) -> str:
    """@description: Keep version confirmations distinct from other decisions so a retry must repeat the same evidence and revision.
    @param command: Authenticated Owner version confirmation.
    @return: Digest stored with the audited confirmation.
    """
    return decision_digest(("version_confirmation",) + _command_intent(command))


def _command_intent(command: object) -> tuple:
    """@description: List every intent field of one command, omitting unset optional fields so older digests still replay.
    @param command: Authenticated Owner action carrying dataclass intent fields.
    @return: Ordered intent values that the digest covers.
    """
    intent = []
    for field in command.__dataclass_fields__:
        value = getattr(command, field)
        if field in OPTIONAL_CITATION_FIELDS and value is None:
            continue
        intent.append(_intent_value(field, value))
    return tuple(intent)


def _intent_value(field: str, value: object) -> object:
    """@description: Replace nested judgement and snapshot objects with stable fingerprints so digests compare by content.
    @param field: Command field name.
    @param value: Field value as stored on the command.
    @return: Fingerprint for judgement or snapshot fields, otherwise the value itself.
    """
    if field == "content_judgement":
        return content_judgement_digest(value)
    if field == "expected_snapshot":
        return decision_digest(snapshot_intent(value))
    return value


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
    request = _current_request(cursor, command.operation_id, command.expected_revision)
    _require_review_snapshot(cursor, command, request.identity)
    _refuse_approval_in_conflict(command, request.identity)
    request, control = _with_ordering_sequence(cursor, control, command, request)
    proposed = command.identity or request.identity  # [trusted mapping update]: Never inferred from titles.
    if proposed.video != request.identity.video:
        raise ValueError("Identity update targets another video")
    _require_content_subject(command, request.identity.video)
    identity = request.identity if command.identity is None else check_identity_write(cursor, request.identity, proposed, request.identity.video)
    ensure_version(cursor, identity)
    review = _apply_transitions(cursor, request, command, identity, audit_reason(command))
    _publish_owner_approved_link(cursor, command, request.identity.video, review)  # [same transaction]: Approval and its verified link commit or roll back together.
    _record_identity_resolution(cursor, command, identity)
    _append_content_judgement(cursor, command)
    audit_reward_history(cursor, command.actor_id, command.reason)
    cursor.execute("UPDATE jb2_control SET revision=revision+1 WHERE singleton_id=1")
    control = dict(control, revision=control["revision"] + 1)
    persist_decisions(cursor, current_decisions(cursor, control))
    save_decision(cursor, command.decision_id, digest, command.actor_id, audit_reason(command), review)
    return review


class ApprovalLinkRefused(RuntimeError):
    """Refuse an approval whose verified link cannot be published exactly once; the whole approval rolls back with it."""


def _publish_owner_approved_link(cursor: Any, command: ReviewCommand, video: VideoIdentity, review: ReviewSnapshot) -> None:
    """@description: Publish the confirmed video as the verified exchange link only when an Owner approval has just made the review approved; repeats of the same link are no-ops.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param command: Authenticated Owner review command whose decision identifier becomes the provenance.
    @param video: Video of the operation, already bound to the approval by the review gate.
    @param review: Review revision produced by this transaction.
    @return: None; a song that cannot take the link refuses the approval and the transaction rolls back.
    """
    if command.approval_source is not ApprovalSource.OWNER or review.status is not ReviewStatus.APPROVED:
        return  # [no publication]: Rejections, pending validation and duration-only revisions never publish a link.
    link = canonical_watch_url(video.video_id)
    cursor.execute("UPDATE songs SET resolution_status=%s, youtube_url=%s, verification_provenance=%s "
                   "WHERE platform_track_id=%s AND platform=%s AND resolution_status='pending' AND youtube_url IS NULL",
                   ("verified", link, f"owner_approval:{command.decision_id}", video.video_id, "youtube"))
    if cursor.rowcount == 1:
        return  # [single publication]: Exactly one song row changed from pending and unlinked to the approved link.
    cursor.execute("SELECT resolution_status,youtube_url FROM songs WHERE platform_track_id=%s AND platform=%s",
                   (video.video_id, "youtube"))
    rows = cursor.fetchall()
    if len(rows) == 1 and rows[0]["resolution_status"] == "verified" and rows[0]["youtube_url"] == link:
        return  # [replayed publication]: The same link is already published; nothing is written twice.
    raise ApprovalLinkRefused("Approval cannot publish its verified link; the approval is rolled back")


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
    The reopened review is passed only by an approval that creates its new ordering event; the decision and audit stay append-only.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param control: Current database read revision.
    @param command: Previously authenticated formal reexamination.
    @return: Original or newly committed pending review, without issuing Nickel.
    """
    digest = reexamination_digest(command)
    previous = replay_decision(cursor, command.decision_id, digest)  # [replay]: A repeated key returns the original revision.
    if previous is not None:
        return decode_contract(previous, ReviewSnapshot)
    request = _current_request(cursor, command.operation_id, command.expected_revision)
    require_unchanged_snapshot(command.expected_snapshot, current_decision_snapshot(cursor, request.identity, command.evidence))
    _require_reliable_evidence(command.evidence, command.actor_id, request.identity.video)
    reopened = reopen_for_reexamination(request.review, command.expected_revision, command.evidence.evidence_id,
                                        awarded=_is_awarded(cursor, command.operation_id))
    identity = _reconciled_identity(cursor, command.evidence, request.identity)  # [new contradiction]: Owner evidence can freeze the reward.
    _append_evidence_record(cursor, command)
    ensure_version(cursor, identity)
    persist_review(cursor, reopened, command.actor_id, command.reason, identity)
    _mirror_legacy_identity(cursor, identity)  # [legacy mirror]: A reexamination can freeze a confirmed identity, so readers of the mirror must see it.
    cursor.execute("UPDATE jb2_control SET revision=revision+1 WHERE singleton_id=1")
    control = dict(control, revision=control["revision"] + 1)
    persist_decisions(cursor, current_decisions(cursor, control))  # [award block]: Candidate becomes WAITING_VALIDATION.
    save_decision(cursor, command.decision_id, digest, command.actor_id, command.reason, reopened)
    return reopened


def apply_version_confirmation(cursor: Any, control: dict, command: VersionConfirmationCommand) -> ReviewSnapshot:
    """@description: Stamp the recording version a reliable Owner evidence fact establishes, leaving review status, order and money untouched.
    The only write is the identity, its audit evidence and the candidate scheduling it implies; this slice never issues Nickel.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param control: Current database read revision.
    @param command: Previously authenticated Owner version confirmation.
    @return: Original or current unchanged review, so the review itself is never reopened.
    """
    digest = version_confirmation_digest(command)
    previous = replay_decision(cursor, command.decision_id, digest)  # [replay]: A retried key returns the original result and writes nothing.
    if previous is not None:
        return decode_contract(previous, ReviewSnapshot)
    plan = plan_version_confirmation(cursor, command.operation_id, command.evidence, command.actor_id, command.expected_revision)
    require_unchanged_snapshot(command.expected_snapshot, plan.current)
    review, identity = plan.review, plan.projected_identity
    _append_evidence_record(cursor, command)
    ensure_version(cursor, identity)
    persist_review(cursor, review, command.actor_id, command.reason, identity)
    _mirror_legacy_identity(cursor, identity)
    cursor.execute("UPDATE jb2_control SET revision=revision+1 WHERE singleton_id=1")
    control = dict(control, revision=control["revision"] + 1)
    persist_decisions(cursor, current_decisions(cursor, control))  # [candidate state]: Conflicts freeze; confirmations never award.
    save_decision(cursor, command.decision_id, digest, command.actor_id, command.reason, review)
    return review


@dataclass(frozen=True)
class VersionConfirmationPlan:
    """Carry what a version confirmation would read and write, computed without any write."""

    review: ReviewSnapshot  # [review after]: Unchanged, because a confirmation never reopens a decision.
    current: DecisionSnapshot  # [previewed facts]: State the Owner's preview must still match when confirmed.
    projected_identity: VersionAssignment  # [identity after]: Identity the confirmation would persist.


@dataclass(frozen=True)
class ReexaminationPlan:
    """Carry the current and reopened reviews plus the identity a reexamination would persist, computed without any write."""

    current_review: ReviewSnapshot  # [review before]: Status the Owner sees before the reopen.
    current: DecisionSnapshot  # [previewed facts]: State the Owner's preview must still match when confirmed.
    reopened_review: ReviewSnapshot  # [review after]: Pending review or pending validation with the reexamination block set.
    projected_identity: VersionAssignment  # [identity after]: Identity recomputed from all Owner evidence, including the new record.


def plan_version_confirmation(cursor: Any, operation_id: str, evidence: EvidenceRecord, actor_id: int,
                              expected_revision: int) -> VersionConfirmationPlan:
    """@description: Compute, without writing, the review, facts and identity a version confirmation would produce, so previews and confirmations agree.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param operation_id: Accepted operation whose video the evidence confirms.
    @param evidence: Owner-stamped evidence fact the confirmation relies on.
    @param actor_id: Authenticated Owner who judged the evidence.
    @param expected_revision: Review revision the Owner examined.
    @return: Plan with the unchanged review, the current snapshot and the identity that confirming it would persist.
    """
    request = _current_request(cursor, operation_id, expected_revision)
    _require_reliable_evidence(evidence, actor_id, request.identity.video)
    _require_current_revision(request.review, expected_revision)
    identity = _reconciled_identity(cursor, evidence, request.identity)
    if _confirms_nothing(cursor, evidence, request.identity.video):
        raise ValueError("Version confirmation has nothing to confirm: the evidence establishes no recording version")
    return VersionConfirmationPlan(request.review, current_decision_snapshot(cursor, request.identity, evidence), identity)


def plan_reexamination(cursor: Any, operation_id: str, evidence: EvidenceRecord, actor_id: int,
                       expected_revision: int) -> ReexaminationPlan:
    """@description: Compute, without writing, what a formal reexamination would reopen and which identity it would store, so its preview shows the real result.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param operation_id: Accepted operation whose video the evidence describes.
    @param evidence: Owner-stamped reliable evidence the reexamination relies on.
    @param actor_id: Authenticated Owner who judged the evidence.
    @param expected_revision: Review revision the Owner examined.
    @return: Plan with the current review, current snapshot, reopened review and projected identity.
    """
    request = _current_request(cursor, operation_id, expected_revision)
    _require_reliable_evidence(evidence, actor_id, request.identity.video)
    reopened = reopen_for_reexamination(request.review, expected_revision, evidence.evidence_id,
                                        awarded=_is_awarded(cursor, operation_id))
    identity = _reconciled_identity(cursor, evidence, request.identity)
    return ReexaminationPlan(request.review, current_decision_snapshot(cursor, request.identity, evidence), reopened, identity)


def current_decision_snapshot(cursor: Any, identity: VersionAssignment, pending: EvidenceRecord | None = None) -> DecisionSnapshot:
    """@description: Read the identity, canonical roots and evidence set that a decision is based on, including the not-yet-stored evidence it cites.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param identity: Persisted identity of the operation's video.
    @param pending: Evidence the decision cites but has not stored yet, or None.
    @return: Snapshot of the facts a preview or confirmation depends on.
    """
    roots = _canonical_roots(cursor)
    # [content fingerprint]: Identifiers alone would miss a rewritten row, and every record carries its canonical root.
    fingerprints = [decision_digest((record, _canonical_version(record.version, roots)))
                    for record in _stored_evidence(cursor, identity.video.video_id)]
    if pending is not None:  # [cited evidence]: A merge that re-roots the cited version must also refuse the confirmation.
        fingerprints.append(decision_digest(("pending", pending, _canonical_version(pending.version, roots))))
    return DecisionSnapshot(identity, _canonical_version(identity.version, roots), tuple(fingerprints))


def _refuse_approval_in_conflict(command: ReviewCommand, identity: VersionAssignment) -> None:
    """@description: Owner ruling 2026-10-23: a version in CONFLICT never permits approval, while rejection stays allowed.
    @param command: Review decision being applied.
    @param identity: Identity read at the start of the decision.
    @return: None; raises InvalidReviewTransition before any write when an approval meets a conflict.
    """
    if command.approval_source is not None and identity.status is VersionIdentityStatus.CONFLICT:
        raise InvalidReviewTransition("Version conflict forbids approval; rejection stays allowed and does not clear the conflict")


def _with_ordering_sequence(cursor: Any, control: dict, command: ReviewCommand,
                            request: SubmissionRequest) -> tuple[SubmissionRequest, dict]:
    """@description: Allocate the system ordering sequence for a reexamination approval inside the writer gate, and keep the in-transaction control row equal to the database.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param control: Control row read at the start of the transaction.
    @param command: Review decision being applied.
    @param request: Current review request.
    @return: The request carrying the new sequence and the control row that matches the database, or the unchanged pair for ordinary decisions.
    """
    if command.approval_source is None or not request.review.reexamination_pending:
        return request, control
    sequence = int(control["accepted_sequence"]) + 1  # [ordering event]: Same counter as intake, so the tail is strictly after every accepted submission.
    cursor.execute("UPDATE jb2_control SET accepted_sequence = %s, revision = revision + 1 WHERE singleton_id = 1", (sequence,))
    # [in-transaction control]: Projections read below must see the sequence and revision the database now holds.
    return replace(request, ordering_sequence=sequence), dict(control, accepted_sequence=sequence, revision=control["revision"] + 1)


def _is_awarded(cursor: Any, operation_id: str) -> bool:
    """@description: Tell whether a submission already has a first-reward claim, which keeps its priority semantics unchanged on reopen.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param operation_id: Accepted operation identifier.
    @return: True when a claim row exists for the operation.
    """
    cursor.execute("SELECT operation_id FROM jb2_claims WHERE operation_id=%s", (operation_id,))
    return cursor.fetchone() is not None


def _require_review_snapshot(cursor: Any, command: ReviewCommand, identity: VersionAssignment) -> None:
    """@description: Refuse an Owner approval whose previewed facts changed; trusted internal decisions are checked only when they cite a snapshot.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param command: Review decision whose snapshot must match the stored state when one is cited.
    @param identity: Identity read at the start of the decision.
    @return: None; raises StaleReviewVersion before any write when the cited facts changed.
    """
    if command.expected_snapshot is not None:
        require_unchanged_snapshot(command.expected_snapshot, current_decision_snapshot(cursor, identity))


def _confirms_nothing(cursor: Any, evidence: EvidenceRecord, video: VideoIdentity) -> bool:
    """@description: Ask the TK-23 rules whether the stored and new evidence together establish any version at all.
    @param cursor: Transaction-owned cursor.
    @param evidence: Owner-stamped evidence the confirmation relies on.
    @param video: Video whose records are recomputed.
    @return: True when no reliable record in the set confirms a version.
    """
    roots = _canonical_roots(cursor)
    records = [_canonical_record(record, roots) for record in _stored_evidence(cursor, video.video_id)]
    records.append(_canonical_record(evidence, roots))
    return resolve_video_version(video, records).status is VersionIdentityStatus.UNRESOLVED


def _require_current_revision(review: ReviewSnapshot, expected_revision: int) -> None:
    """@description: Refuse a confirmation whose previewed revision no longer matches, so the Owner must preview again.
    @param review: Current authoritative review.
    @param expected_revision: Revision the Owner examined.
    @return: None.
    """
    if review.version != expected_revision:
        raise StaleReviewVersion("Review version no longer matches; preview the confirmation again")


def _require_reliable_evidence(evidence: EvidenceRecord, judge_id: int, video: VideoIdentity) -> None:
    """@description: Refuse Owner evidence that is unreliable, judged by someone else, or about another video.
    @param evidence: Evidence record the Owner cites.
    @param judge_id: Authenticated Owner identity that must have judged the record.
    @param video: Video of the accepted operation.
    @return: None.
    """
    if evidence.video != video or evidence.judged_by != judge_id:
        raise ValueError("Evidence must describe this video and be judged by its Owner")
    if assess_evidence(evidence) is not EvidenceReliability.RELIABLE:
        raise ValueError("Evidence requires reliable Owner-confirmed evidence")


def check_identity_write(cursor: Any, current: VersionAssignment, proposed: VersionAssignment, video: VideoIdentity,
                         *, pending_evidence: EvidenceRecord | None = None) -> VersionAssignment:
    """@description: Decide which identity a write may store, so ordinary review commands and formal confirmations share one rule set.
    CONFIRMED needs a cited Owner record that resolves over all Owner evidence to the same canonical root; a different confirmed
    root than the current one becomes CONFLICT citing the new record, so A is never overwritten by B; CONFLICT and UNRESOLVED pass through as freezes.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param current: Identity persisted before the write.
    @param proposed: Identity the write asks to store.
    @param video: Video of the accepted operation.
    @param pending_evidence: Owner-stamped record that the formal path has not stored yet, or None when every citation is stored.
    @return: The identity that may be stored; never a payable identity without matching Owner evidence.
    """
    if current.status is VersionIdentityStatus.CONFLICT:
        return current  # [frozen conflict]: No ordinary or formal write lifts or downgrades it; resolution is not opened in this stage.
    if proposed.status is not VersionIdentityStatus.CONFIRMED:
        return proposed  # [safe directions]: Conflicts freeze and unresolved states never create a payable identity.
    roots = _canonical_roots(cursor)
    cited = _cited_owner_record(cursor, proposed, video, pending_evidence)
    proposed_root = _canonical_version(proposed.version, roots)
    if _canonical_version(cited.version, roots) != proposed_root:
        raise ValueError("A confirmed identity must cite stored owner-confirmed evidence that asserts that version")
    if current.status is VersionIdentityStatus.CONFIRMED and _canonical_version(current.version, roots) != proposed_root:
        return VersionAssignment(video, VersionIdentityStatus.CONFLICT, None, cited.evidence_id)  # [A is never overwritten by B]: Freeze instead.
    _require_resolution_to(cursor, video, proposed_root, pending_evidence, roots)
    return proposed


def _cited_owner_record(cursor: Any, proposed: VersionAssignment, video: VideoIdentity,
                        pending_evidence: EvidenceRecord | None) -> EvidenceRecord:
    """@description: Find the Owner record a proposed confirmation cites, among stored records and the not-yet-stored formal evidence.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param proposed: Confirmed identity assertion carrying the cited evidence identifier.
    @param video: Video the citation must describe.
    @param pending_evidence: Formal evidence not yet stored, or None.
    @return: The cited record for this video.
    """
    cited = [record for record in _owner_records(cursor, video, pending_evidence) if record.evidence_id == proposed.evidence_reference]
    if not cited:
        raise ValueError("A confirmed identity must cite stored owner-confirmed evidence for this video")
    return cited[-1]  # [most recent citation]: A pending formal record is appended last, so it wins a tie.


def _require_resolution_to(cursor: Any, video: VideoIdentity, expected_root: RecordingVersionIdentity, pending_evidence: EvidenceRecord | None,
                           roots: dict[str, str]) -> None:
    """@description: Refuse a confirmed write unless all Owner evidence for the video, taken together, still resolves to the same canonical version.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param video: Video whose records are recomputed.
    @param expected_root: Canonical version the write confirms.
    @param pending_evidence: Formal evidence not yet stored, or None.
    @param roots: Flattened canonical identity map from the writer-gated transaction.
    @return: None.
    """
    records = [_canonical_record(record, roots) for record in _owner_records(cursor, video, pending_evidence)]
    resolved = resolve_video_version(video, records)
    if resolved.status is not VersionIdentityStatus.CONFIRMED or resolved.version != expected_root:
        raise ValueError("A confirmed identity must resolve from all stored owner-confirmed evidence to the same version")


def _owner_records(cursor: Any, video: VideoIdentity, pending_evidence: EvidenceRecord | None) -> list[EvidenceRecord]:
    """@description: Collect every Owner evidence fact for one video, plus the formal record that is not stored yet.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param video: Video whose records are collected.
    @param pending_evidence: Formal evidence not yet stored, or None.
    @return: Stored records in stored order, followed by the pending record when given.
    """
    records = _stored_evidence(cursor, video.video_id)
    return records if pending_evidence is None else [*records, pending_evidence]


def stored_evidence_record(cursor: Any, operation_id: str, evidence_id: str) -> EvidenceRecord:
    """@description: Return one stored Owner evidence record for the operation's video, so the Owner can cite it again.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param operation_id: Accepted operation whose video the citation must describe.
    @param evidence_id: Evidence identifier the Owner wants to cite.
    @return: The stored evidence record; unknown or foreign-video identifiers are refused.
    """
    request = _current_request(cursor, operation_id, 0)  # [video subject]: The expected revision is not needed for a read.
    matches = [record for record in _stored_evidence(cursor, request.identity.video.video_id) if record.evidence_id == evidence_id]
    if not matches:
        raise ValueError("Evidence id is not stored for this operation's video")
    return matches[-1]


def _reconciled_identity(cursor: Any, evidence: EvidenceRecord, current: VersionAssignment) -> VersionAssignment:
    """@description: Recompute the video's version from all stored Owner evidence plus the new fact, freezing any contradiction with a confirmed version.
    @param cursor: Transaction-owned cursor holding the writer gate; the new fact need not be stored yet.
    @param evidence: Owner-stamped evidence that is the newest fact.
    @param current: Persisted identity before the decision.
    @return: Unchanged conflict, a conflict against the prior confirmed version, or the recomputed assignment.
    """
    if current.status is VersionIdentityStatus.CONFLICT:
        return current  # [explicit resolution]: Only an explicit Owner clear may remove a historical conflict.
    roots = _canonical_roots(cursor)
    records = [_canonical_record(record, roots) for record in _stored_evidence(cursor, current.video.video_id)]
    recomputed = resolve_video_version(current.video, [*records, _canonical_record(evidence, roots)])
    if recomputed.status is VersionIdentityStatus.UNRESOLVED:
        return current  # [missing support]: Evidence that confirms nothing never erases or freezes an existing identity.
    current_root = _canonical_version(current.version, roots)
    contradicts_confirmed = current.status is VersionIdentityStatus.CONFIRMED and (
        recomputed.status is VersionIdentityStatus.CONFLICT or recomputed.version != current_root)
    proposed = (VersionAssignment(current.video, VersionIdentityStatus.CONFLICT, None, evidence.evidence_id)
                if contradicts_confirmed else recomputed)
    return check_identity_write(cursor, current, proposed, current.video, pending_evidence=evidence)


def _canonical_roots(cursor: Any) -> dict[str, str]:
    """@description: Flatten the version alias map so merged aliases compare as the same recording.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @return: Mapping from each version identifier to its canonical root.
    """
    return {row["version_id"]: row["canonical_id"] for row in fetch_all(cursor, "SELECT version_id,canonical_id FROM jb2_versions")}


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


def _append_evidence_record(cursor: Any, command: ReexaminationCommand | VersionConfirmationCommand) -> None:
    """@description: Append the full evidence fact beside the reopened revision so the Owner can audit its source.
    @param cursor: Transaction-owned cursor.
    @param command: Reexamination action with its validated evidence.
    @return: None.
    """
    cursor.execute("INSERT INTO jb2_evidence (video_id,evidence_json,actor_id,decision_reason) VALUES (%s,%s,%s,%s)",
                   (command.evidence.video.video_id, encode_contract(command.evidence), command.actor_id, command.reason))


def _current_request(cursor: Any, operation_id: str, expected_revision: int) -> SubmissionRequest:
    """@description: Load the current video revision shared by all accepted operations for that video.
    @param cursor: Globally serialized cursor.
    @param operation_id: Accepted operation named by the Owner action.
    @param expected_revision: Review revision the Owner examined.
    @return: Request ready for the existing pure version checks.
    """
    cursor.execute("SELECT e.*,v.identity_json,v.review_json,v.review_revision,v.review_status FROM jb2_events e "
                   "JOIN jb2_videos v ON v.video_id=e.video_id WHERE e.operation_id=%s", (operation_id,))
    row = cursor.fetchone()  # [current projection]: Never take the current review from command payload.
    if row is None:
        raise ValueError("Unknown review operation")
    return SubmissionRequest(row["source_url"], row["operation_id"], row["discord_user_id"], row["accepted_sequence"],
                             decode_current_identity(row), decode_current_review(row), expected_revision)


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
        _mirror_legacy_identity(cursor, identity)
    if command.clear_conflict_freeze is True:
        cursor.execute("UPDATE jb2_events SET conflict_frozen=FALSE WHERE video_id=%s", (identity.video.video_id,))


def _mirror_legacy_identity(cursor: Any, identity: VersionAssignment) -> None:
    """@description: Keep a legacy song's identity mirror equal to the video's persisted identity after an Owner identity write.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @param identity: Identity just persisted for the video.
    @return: None.
    """
    cursor.execute("UPDATE jb2_legacy l JOIN jb2_videos v ON v.song_id=l.song_id SET l.identity_json=%s WHERE v.video_id=%s",
                   (encode_contract(identity), identity.video.video_id))


MAX_PENDING_PAGE = 20  # [page size cap]: Keeps each Owner queue reply inside one Discord message.


@dataclass(frozen=True)
class PendingItem:
    """Summarize one accepted operation that still needs an Owner decision or conflict review, without private payloads."""

    operation_id: str  # [queue subject]: Accepted operation waiting for the Owner.
    source_url: str  # [submitted link]: The URL as accepted, shown so the Owner can find the song.
    accepted_sequence: int  # [queue position]: Database-assigned acceptance order, also the paging cursor.
    review_status: ReviewStatus  # [review state]: Current Hybrid Review status of the video.
    identity_status: VersionIdentityStatus  # [identity state]: Current version identity status of the video.
    revision: int  # [expected revision]: The value a later preview and confirmation must present.
    evidence_count: int  # [audit size]: Owner evidence records stored for the video.
    conflict_reason: str | None  # [freeze explanation]: Cited contradiction, set only while the identity is CONFLICT.


@dataclass(frozen=True)
class PendingPage:
    """Carry one bounded slice of the Owner queue plus the cursor for the next slice."""

    items: tuple[PendingItem, ...]  # [page rows]: At most the capped page size, in acceptance order.
    next_after_sequence: int | None  # [keyset cursor]: Pass back as the after-sequence; None when no later pending item exists.


def pending_review_items(cursor: Any) -> tuple[PendingItem, ...]:
    """@description: List operations whose review is still open, whose approval awaits a version, or whose identity is frozen, in acceptance order.
    @param cursor: Transaction-owned cursor holding the writer gate.
    @return: Pending items with counts and conflict reasons; clean terminal reviews are omitted.
    """
    rows = fetch_all(cursor, "SELECT e.operation_id,e.source_url,e.accepted_sequence,e.video_id,v.identity_json,v.review_json,"
                             "v.review_revision,v.review_status FROM jb2_events e JOIN jb2_videos v ON v.video_id=e.video_id "
                             "ORDER BY e.accepted_sequence")
    items = []
    for row in rows:
        review = decode_current_review(row)
        identity = decode_current_identity(row)
        if not _awaits_owner(review.status, identity.status):
            continue
        items.append(PendingItem(row["operation_id"], row["source_url"], row["accepted_sequence"], review.status, identity.status,
                                 review.version, len(_stored_evidence(cursor, row["video_id"])), conflict_reason(identity)))
    return tuple(items)


def page_pending(items: tuple[PendingItem, ...], after_sequence: int | None, limit: int) -> PendingPage:
    """@description: Cut one keyset page from the acceptance-ordered queue, so items never shift when earlier ones leave the queue.
    @param items: Complete pending queue in acceptance order.
    @param after_sequence: Last accepted sequence the Owner already saw, or None for the first page.
    @param limit: Page size already capped by the service.
    @return: Page of items after the cursor, with the next cursor when more items remain.
    """
    remaining = tuple(item for item in items if after_sequence is None or item.accepted_sequence > after_sequence)
    page = remaining[:limit]
    more_follow = len(remaining) > limit
    return PendingPage(page, page[-1].accepted_sequence if more_follow else None)


def _awaits_owner(review_status: ReviewStatus, identity_status: VersionIdentityStatus) -> bool:
    """@description: Decide whether an operation needs the Owner's attention, using only its current statuses.
    @param review_status: Current Hybrid Review status.
    @param identity_status: Current version identity status.
    @return: True for open or failed reviews, approvals still waiting for a confirmed version, and identities frozen by a conflict.
    """
    open_review = review_status in {ReviewStatus.PENDING_VALIDATION, ReviewStatus.PENDING_REVIEW, ReviewStatus.VALIDATION_FAILED}
    approved_unconfirmed = review_status is ReviewStatus.APPROVED and identity_status is VersionIdentityStatus.UNRESOLVED
    return open_review or approved_unconfirmed or identity_status is VersionIdentityStatus.CONFLICT


def conflict_reason(identity: VersionAssignment) -> str | None:
    """@description: Explain a frozen identity by naming the evidence that caused the contradiction.
    @param identity: Current identity of the video.
    @return: Owner-facing reason for a conflict, or None when the identity is not in conflict.
    """
    if identity.status is not VersionIdentityStatus.CONFLICT:
        return None
    return f"版本衝突：引用證據 {identity.evidence_reference}"
