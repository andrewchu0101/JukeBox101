"""Let the configured Owner preview and confirm review decisions through short-lived, single-use tokens.

Previews pre-check the pure hard gates; confirmations delegate every write to the audited repository and never award Nickel.
"""

import secrets
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from bot.database.review_persistence import (
    MAX_PENDING_PAGE, PendingPage, ReexaminationCommand, ReviewCommand, VersionConfirmationCommand, content_judgement_digest,
    decision_digest, require_owner,
)
from bot.database.review_projection import ReviewProjection
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.decision_snapshot import DecisionSnapshot
from bot.evidence_record import EvidenceRecord, EvidenceReliability, assess_evidence
from bot.review import (
    ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, approve_review, reject_review,
)
from bot.review_gate import evaluate_approval_gate
from bot.song_judgement import ContentJudgement
from bot.version_identity import VersionAssignment, VersionIdentityStatus, require_reference

PREVIEW_ORDERING_SEQUENCE = 1  # [preview placeholder]: Only computes the projected status; the sequence is allocated at confirmation.
REEXAMINATION_PASS_NOTE = "重審通過：確認時產生新排序事件，排到當時隊尾；原受理序號與歷史保留。"  # [reexamination approval]: Explains the tail position.
NO_AWARD_EFFECT = "不發 Nickel，不寫入 credit_ledger，點數餘額不變。"  # [financial preview]: Shown before every confirmation.
REEXAMINATION_EFFECT = "重審會保留原決策與歷史；重審通過核准時，建立新的排序事件並排到當時隊尾，不恢復原優先權，也不保證發點。" + NO_AWARD_EFFECT  # [reexamination preview]: States the pass rule without claiming the event exists yet.
VERSION_CONFIRMATION_EFFECT = "版本確認不重開審核，不改變受理順序。" + NO_AWARD_EFFECT  # [version preview]: Identity only, never a payout.
UNRESOLVED_IDENTITY_NOTE = "版本未確認：此次決定不會確認錄音版本。"  # [identity preview]: Approval never proves the recording.
CONFLICT_IDENTITY_NOTE = "版本衝突：維持凍結，此次決定不會清除衝突。"  # [identity preview]: Conflicts stay frozen.
TRACEABLE_SOURCE_MIN_LENGTH = 8  # [source reference floor]: Shorter text cannot name a URL, document or manual check.
TRACEABLE_SOURCE_MAX_LENGTH = 512  # [source reference ceiling]: Keeps provenance text to a bounded, reviewable size.
NON_TRACEABLE_SOURCE_PREFIXES = ("discord:", "interaction:", "interaction-")  # [interaction ids]: Bot-internal identifiers are not evidence.


class ReviewAction(Enum):
    """Name the two Owner decisions that can be previewed and confirmed."""

    APPROVE = "approve"  # [song decision]: Explicit positive song eligibility, still award-free.
    REJECT = "reject"  # [song decision]: Explicit negative song eligibility after duration validation.
    REEXAMINE = "reexamine"  # [formal reopen]: Confirmed only with a reliable evidence record bound into the token.
    CONFIRM_VERSION = "confirm_version"  # [version identity]: Confirmed only with reliable evidence; never reopens review or pays.


class UnknownReviewToken(LookupError):
    """Refuse confirmations for tokens this process never issued."""


class ExpiredReviewToken(RuntimeError):
    """Refuse confirmations after the preview's time limit has passed."""


class ReviewOutcomeUnknown(RuntimeError):
    """Require retrying the same confirmation so its original decision key replays safely."""


@dataclass(frozen=True)
class ReviewPreview:
    """Describe the exact decision the Owner is about to confirm and its financial effect."""

    token_id: str  # [confirmation handle]: Single-use reference bound to this preview only.
    operation_id: str  # [review subject]: Accepted operation the decision targets.
    action: ReviewAction  # [decision kind]: Approve or reject, fixed at preview time.
    expected_revision: int  # [concurrency token]: Confirmation fails if the review moved on.
    evidence_reference: str  # [decision citation]: Evidence the Owner cited; a different citation needs a new preview.
    current_status: ReviewStatus  # [current state]: Status before the decision.
    projected_status: ReviewStatus  # [projected state]: Status the decision would produce.
    financial_effect: str  # [owner-facing effect]: Always states that no Nickel is issued.
    expires_at: datetime  # [token deadline]: After this instant the token is refused.
    evidence_digest: str | None = None  # [evidence fingerprint]: Set for reexamination, which cites a full evidence record.
    content_digest: str | None = None  # [content fingerprint]: Set for approval, which cites the Owner's content judgement.
    source_reference: str | None = None  # [traceable source]: Set for evidence-citing previews, so the Owner sees where the claim came from.
    reason: str = ""  # [audit explanation]: The Owner's stated reason, bound into the confirmation intent.
    current_identity: VersionAssignment | None = None  # [identity before]: Persisted version state the decision starts from.
    projected_identity: VersionAssignment | None = None  # [identity after]: Version state the decision would store, or None when it is recomputed at confirmation.


def require_traceable_source(source_text: str) -> str:
    """@description: Accept only a URL, document name or manual check reference as evidence provenance, never a bare or interaction-style id.
    This is a source reference rule, not a reliability proof; reliability stays with assess_evidence and Owner confirmation.
    @param source_text: Source reference the Owner typed or the evidence record carries.
    @return: The same text, unchanged, when it is traceable.
    """
    if not isinstance(source_text, str) or source_text.strip() != source_text:
        raise ValueError("A source reference must be unpadded text")
    if not TRACEABLE_SOURCE_MIN_LENGTH <= len(source_text) <= TRACEABLE_SOURCE_MAX_LENGTH:
        raise ValueError("A source reference must be 8 to 512 characters")
    if source_text.isdigit():
        raise ValueError("A bare numeric id is not a traceable source")  # [snowflake pattern]: Discord ids are numeric.
    if source_text.lower().startswith(NON_TRACEABLE_SOURCE_PREFIXES):
        raise ValueError("An interaction-style id is not a traceable source")
    return source_text


@dataclass
class _IssuedToken:
    """Hold the immutable intent of one preview plus its completed result, once confirmed."""

    actor_id: int  # [bound owner]: Only this Owner may spend the token.
    operation_id: str  # [review subject]: Fixed at preview time.
    action: ReviewAction  # [decision kind]: Fixed at preview time.
    reason: str  # [audit explanation]: Reused verbatim on retry.
    expected_revision: int  # [concurrency token]: Reused verbatim on retry.
    evidence_reference: str | None  # [decision citation]: Part of the previewed intent, never edited at confirmation.
    decision_id: str  # [idempotency key]: Retries after an unknown commit replay this same key.
    expires_at: datetime  # [token deadline]: Checked before any new write.
    result: ReviewSnapshot | None = None  # [spent marker]: Set only after a confirmed commit.
    evidence: EvidenceRecord | None = None  # [reexamination intent]: Owner-stamped record the token was previewed with.
    content: ContentJudgement | None = None  # [approval intent]: Owner content verdict the approval gate accepted at preview.
    song_eligible: bool = False  # [song decision]: Gate result at preview; never a constant, and never changed at confirmation.
    expected_snapshot: DecisionSnapshot | None = None  # [previewed facts]: Identity, canonical root and evidence set the preview read.


class OwnerReviewService:
    """Coordinate Owner previews, tokens and confirmations over the audited repository boundary."""

    def __init__(self, repository: SubmissionRepository, owner_id: int, clock: Callable[[], datetime],
                 token_ttl: timedelta = timedelta(minutes=5)) -> None:
        """@description: Bind the Owner, clock and token lifetime before any decision can be previewed.
        @param self: New Owner review coordinator.
        @param repository: Audited repository boundary that performs every database write.
        @param owner_id: Deployment-configured Owner Discord user ID.
        @param clock: Timezone-aware current-instant provider, injectable for expiry checks.
        @param token_ttl: Lifetime of an unconfirmed preview.
        @return: None.
        """
        self._repository = repository  # [write boundary]: Owns every transaction and audit row.
        self._owner_id = owner_id  # [authorization authority]: Never taken from a caller-supplied argument.
        self._clock = clock  # [expiry time source]: Synthetic in tests.
        self._token_ttl = token_ttl  # [confirmation window]: Short by default.
        self._tokens: dict[str, _IssuedToken] = {}  # [issued previews]: In-memory only, lost on restart by design.
        self._token_lock = threading.Lock()  # [single-use guard]: Serializes preview issue and confirmation.

    def view(self, actor_id: int, operation_id: str) -> ReviewProjection:
        """@description: Let the Owner inspect one operation's current review and audit facts.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner.
        @param operation_id: Accepted operation to inspect.
        @return: Read-only projection.
        """
        require_owner(actor_id, self._owner_id, "owner review view")
        return self._repository.review_view(actor_id, operation_id)

    def preview(self, actor_id: int, operation_id: str, action: ReviewAction, reason: str,
                expected_revision: int, evidence_reference: str, content: ContentJudgement | None = None) -> ReviewPreview:
        """@description: Show the Owner the exact projected outcome and issue a short-lived token bound to the cited evidence and content verdict.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner.
        @param operation_id: Accepted operation to decide.
        @param action: Approve or reject.
        @param reason: Audit explanation stored with the decision.
        @param expected_revision: Review revision the Owner examined.
        @param evidence_reference: Evidence the Owner relied on, bound into the confirmation intent.
        @param content: Owner's song judgement; required for approval and refused for any other decision.
        @return: Preview with projected status, financial effect and expiry.
        """
        require_owner(actor_id, self._owner_id, reason)
        require_reference(evidence_reference)
        if action is ReviewAction.REEXAMINE:
            raise ValueError("Reexamination previews must cite a full evidence record")
        if action is ReviewAction.CONFIRM_VERSION:
            raise ValueError("Version confirmations must cite a full evidence record")
        if content is not None and action is not ReviewAction.APPROVE:
            raise ValueError("Only an approval may cite a content judgement")
        projection = self._repository.review_view(actor_id, operation_id)
        song_eligible = _approval_song_eligibility(actor_id, projection, content) if action is ReviewAction.APPROVE else False
        projected = _project(projection.review, action, expected_revision, song_eligible)  # [hard gates]: Refuse before any token exists.
        token_id = secrets.token_urlsafe(24)  # [unguessable handle]: Not derived from operation or revision.
        expires_at = self._clock() + self._token_ttl
        issued = _IssuedToken(actor_id, operation_id, action, reason, expected_revision, evidence_reference,
                              uuid.uuid4().hex, expires_at, content=content, song_eligible=song_eligible,
                              expected_snapshot=projection.snapshot)
        with self._token_lock:
            self._tokens[token_id] = issued
        return ReviewPreview(token_id, operation_id, action, expected_revision, evidence_reference,
                             projection.review.status, projected.status,
                             _financial_effect(projection.identity.status) + _pass_note(action, projection.review),
                             expires_at, content_digest=content_judgement_digest(content) if content is not None else None,
                             reason=reason, current_identity=projection.identity, projected_identity=projection.identity)

    def confirm(self, actor_id: int, token_id: str) -> ReviewSnapshot:
        """@description: Apply a previewed decision exactly once and replay its original result on repeat.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner.
        @param token_id: Handle returned by preview.
        @return: Review revision produced by the confirmed decision.
        """
        require_owner(actor_id, self._owner_id, "owner review confirmation")
        with self._token_lock:
            issued = self._tokens.get(token_id)
            if issued is None:
                raise UnknownReviewToken("Review token was not issued by this service")
            if issued.result is not None:
                return issued.result
            if self._clock() >= issued.expires_at:
                raise ExpiredReviewToken("Review token has expired; preview the decision again")
            issued.result = self._apply(issued)
            return issued.result

    def list_pending(self, actor_id: int, *, after_sequence: int | None = None, limit: int = MAX_PENDING_PAGE) -> PendingPage:
        """@description: Show one keyset page of operations that still need a decision, a version or a conflict review, read-only.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner before anything is read.
        @param after_sequence: Last accepted sequence the Owner already saw, from the previous page's next cursor, or None for the first page.
        @param limit: Requested page size, capped at the maximum page size.
        @return: One page in acceptance order with the cursor for the next page.
        """
        require_owner(actor_id, self._owner_id, "owner pending list")
        if after_sequence is not None and (type(after_sequence) is not int or after_sequence < 0):
            raise ValueError("A paging cursor must be a non-negative accepted sequence")
        if type(limit) is not int or limit < 1:
            raise ValueError("A positive page size is required")
        return self._repository.pending_items(actor_id, after_sequence, min(limit, MAX_PENDING_PAGE))

    def stored_evidence(self, actor_id: int, operation_id: str, evidence_id: str) -> EvidenceRecord:
        """@description: Return a stored Owner evidence record for the operation's video so the Owner can cite it again.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner before anything is read.
        @param operation_id: Accepted operation whose video the citation must describe.
        @param evidence_id: Evidence identifier to cite.
        @return: The stored record; unknown or foreign-video identifiers are refused with ValueError.
        """
        require_owner(actor_id, self._owner_id, "owner stored evidence lookup")
        return self._repository.stored_evidence(actor_id, operation_id, evidence_id)

    def preview_version_confirmation(self, actor_id: int, operation_id: str, evidence: EvidenceRecord, reason: str,
                                     expected_revision: int) -> ReviewPreview:
        """@description: The single version-confirmation entry: refuse before any token when the evidence establishes no version, else preview the identity it would stamp.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner.
        @param operation_id: Accepted operation whose video the evidence describes.
        @param evidence: Evidence record; the Owner's preview confirms it, so owner_confirmed is set here.
        @param reason: Audit explanation.
        @param expected_revision: Review revision the Owner examined.
        @return: Preview with the unchanged review status, the no-award effect and expiry.
        """
        require_owner(actor_id, self._owner_id, reason)
        require_traceable_source(evidence.source_reference)  # [traceable citation]: Refuse before any read or token exists.
        confirmed = replace(evidence, owner_confirmed=True)  # [trusted confirmation]: The Owner's formal preview is the confirmation.
        # [one snapshot]: Current facts and the projected identity come from the same locked read, never from two reads.
        plan = self._repository.preview_version_confirmation(actor_id, operation_id, confirmed, expected_revision)
        token_id = secrets.token_urlsafe(24)
        expires_at = self._clock() + self._token_ttl
        issued = _IssuedToken(actor_id, operation_id, ReviewAction.CONFIRM_VERSION, reason, expected_revision,
                              confirmed.evidence_id, uuid.uuid4().hex, expires_at, evidence=confirmed,
                              expected_snapshot=plan.current)
        with self._token_lock:
            self._tokens[token_id] = issued
        return ReviewPreview(token_id, operation_id, ReviewAction.CONFIRM_VERSION, expected_revision, confirmed.evidence_id,
                             plan.review.status, plan.review.status, _version_effect(plan.projected_identity.status), expires_at,
                             evidence_digest=decision_digest((confirmed,)), source_reference=confirmed.source_reference,
                             reason=reason, current_identity=plan.current.identity, projected_identity=plan.projected_identity)

    def preview_reexamination(self, actor_id: int, operation_id: str, evidence: EvidenceRecord, reason: str,
                              expected_revision: int) -> ReviewPreview:
        """@description: Formal reexamination entry: preview the reopen for the Owner, binding the exact evidence and revision.
        The later approval that passes the reexamination creates the new ordering event at the tail; only a confirmed token writes the reopen.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner.
        @param operation_id: Accepted operation whose video the evidence describes.
        @param evidence: Evidence record; the Owner's preview confirms it, so owner_confirmed is set here.
        @param reason: Audit explanation.
        @param expected_revision: Review revision the Owner examined.
        @return: Preview with the evidence fingerprint, expiry and the no-award effect.
        """
        require_owner(actor_id, self._owner_id, reason)
        require_traceable_source(evidence.source_reference)  # [traceable citation]: Refuse before any read or token exists.
        confirmed = replace(evidence, owner_confirmed=True)  # [trusted confirmation]: The Owner's formal preview is the confirmation.
        if assess_evidence(confirmed) is not EvidenceReliability.RELIABLE:
            raise ValueError("Reexamination requires reliable Owner-confirmed evidence")
        # [hard gates and projection]: One locked read supplies the reopen, the recomputed identity and the snapshot to confirm against.
        plan = self._repository.preview_reexamination(actor_id, operation_id, confirmed, expected_revision)
        token_id = secrets.token_urlsafe(24)
        expires_at = self._clock() + self._token_ttl
        issued = _IssuedToken(actor_id, operation_id, ReviewAction.REEXAMINE, reason, expected_revision,
                              confirmed.evidence_id, uuid.uuid4().hex, expires_at, evidence=confirmed,
                              expected_snapshot=plan.current)
        with self._token_lock:
            self._tokens[token_id] = issued
        return ReviewPreview(token_id, operation_id, ReviewAction.REEXAMINE, expected_revision, confirmed.evidence_id,
                             plan.current_review.status, plan.reopened_review.status, REEXAMINATION_EFFECT, expires_at,
                             evidence_digest=decision_digest((confirmed,)), source_reference=confirmed.source_reference,
                             reason=reason, current_identity=plan.current.identity, projected_identity=plan.projected_identity)

    def add_reexamination_evidence(self, actor_id: int, operation_id: str, evidence: EvidenceRecord, reason: str,
                                   decision_id: str) -> EvidenceRecord:
        """@description: Record new Owner-judged evidence without changing the decision, identity or order.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner.
        @param operation_id: Accepted operation whose video the evidence describes.
        @param evidence: Validated evidence record.
        @param reason: Audit explanation.
        @param decision_id: Idempotent entry key.
        @return: Original or newly stored evidence record.
        """
        require_owner(actor_id, self._owner_id, reason)
        require_traceable_source(evidence.source_reference)  # [traceable citation]: Only a traceable source may enter the audit trail.
        confirmed = replace(evidence, owner_confirmed=True)  # [trusted confirmation]: The Owner's submission here is the confirmation.
        with _unknown_outcome_boundary():
            return self._repository.record_reexamination_evidence(decision_id, operation_id, actor_id, reason, confirmed)

    def _apply(self, issued: _IssuedToken) -> ReviewSnapshot:
        """@description: Send one previewed decision through the audited repository with its original key.
        @param self: Owner review coordinator.
        @param issued: Token whose intent is replayed verbatim.
        @return: Persisted or replayed review revision.
        """
        with _unknown_outcome_boundary():
            if issued.action is ReviewAction.CONFIRM_VERSION:
                return self._repository.confirm_version(_version_confirmation_for(issued))
            if issued.action is ReviewAction.REEXAMINE:
                return self._repository.reexamine(_reexamination_for(issued))
            return self._repository.review(_command_for(issued))


def _approval_song_eligibility(actor_id: int, projection: ReviewProjection, content: ContentJudgement | None) -> bool:
    """@description: Ask the approval gate whether the song may be approved, so refusals happen before any token exists.
    @param actor_id: Authenticated Owner, who must also be the judge of the content verdict.
    @param projection: Current review, duration and identity of the operation's video.
    @param content: Owner content judgement, or None when the Owner gave none.
    @return: The gate's song decision, which is True only when the gate says the song is eligible.
    """
    if content is None:
        raise InvalidReviewTransition("Approval requires the Owner's content judgement")
    if content.judged_by != actor_id:
        raise InvalidReviewTransition("Content judgement must be the Owner's own verdict")
    if projection.identity.status is VersionIdentityStatus.CONFLICT:
        raise InvalidReviewTransition("Version conflict forbids approval; rejection stays allowed and does not clear the conflict")
    gate = evaluate_approval_gate(projection.review.duration, content, projection.identity)  # [cross-video]: May raise ValueError.
    if not gate.song_eligible:
        raise InvalidReviewTransition(f"Approval refused by the song gate: {gate.reason}")
    return gate.song_eligible


def _project(review: ReviewSnapshot, action: ReviewAction, expected_revision: int, song_eligible: bool) -> ReviewSnapshot:
    """@description: Run the pure hard gates shared with confirmation so refusals happen at preview time.
    @param review: Current authoritative review.
    @param action: Requested Owner decision.
    @param expected_revision: Revision the Owner examined.
    @param song_eligible: Song decision produced by the approval gate; ignored for rejection.
    @return: Projected revision that confirmation would produce.
    """
    if action is ReviewAction.APPROVE:
        # [preview placeholder]: A reexamination approval gets its real ordering sequence only at confirmation.
        placeholder = PREVIEW_ORDERING_SEQUENCE if review.reexamination_pending else None
        return approve_review(review, ApprovalSource.OWNER, expected_revision, song_eligible=song_eligible,
                              ordering_sequence=placeholder)
    if action is ReviewAction.REJECT:
        return reject_review(review, expected_revision)
    raise ValueError("Unknown review action")


def _command_for(issued: _IssuedToken) -> ReviewCommand:
    """@description: Translate a previewed Owner decision into the repository's audited command.
    @param issued: Token holding the fixed decision intent and the gate's song decision.
    @return: Command that never clears identity conflicts or touches credits.
    """
    if issued.action is ReviewAction.APPROVE:
        return ReviewCommand(issued.decision_id, issued.operation_id, issued.actor_id, issued.reason,
                             issued.expected_revision, approval_source=ApprovalSource.OWNER, song_eligible=issued.song_eligible,
                             evidence_reference=issued.evidence_reference, content_judgement=issued.content,
                             expected_snapshot=_snapshot_for(issued))
    return ReviewCommand(issued.decision_id, issued.operation_id, issued.actor_id, issued.reason,
                         issued.expected_revision, song_eligible=issued.song_eligible, evidence_reference=issued.evidence_reference,
                         expected_snapshot=_snapshot_for(issued))


def _snapshot_for(issued: _IssuedToken) -> DecisionSnapshot:
    """@description: Hand the repository the snapshot the preview read, failing closed so no confirmation can skip the S21 check.
    @param issued: Token whose confirmation is about to be written.
    @return: The snapshot the preview carried.
    """
    if issued.expected_snapshot is None:
        raise ValueError("Every Owner confirmation must carry the snapshot its preview read")
    return issued.expected_snapshot


def _reexamination_for(issued: _IssuedToken) -> ReexaminationCommand:
    """@description: Translate a previewed reexamination into the repository's audited command with its original key.
    @param issued: Token holding the fixed reopen intent and its evidence record.
    @return: Command that reopens the review and never issues Nickel.
    """
    return ReexaminationCommand(issued.decision_id, issued.operation_id, issued.actor_id, issued.reason,
                                issued.expected_revision, issued.evidence, expected_snapshot=_snapshot_for(issued))


def _version_confirmation_for(issued: _IssuedToken) -> VersionConfirmationCommand:
    """@description: Translate a previewed version confirmation into the repository's audited command with its original key.
    @param issued: Token holding the fixed confirmation intent and its evidence record.
    @return: Command that stamps the identity and never issues Nickel.
    """
    return VersionConfirmationCommand(issued.decision_id, issued.operation_id, issued.actor_id, issued.reason,
                                      issued.expected_revision, issued.evidence, expected_snapshot=_snapshot_for(issued))


def _version_effect(identity_status: VersionIdentityStatus) -> str:
    """@description: State the identity result of a version confirmation together with its no-award effect.
    @param identity_status: Identity status the confirmation would persist.
    @return: Owner-facing summary in Traditional Chinese.
    """
    conflict = CONFLICT_IDENTITY_NOTE if identity_status is VersionIdentityStatus.CONFLICT else ""
    return " ".join(part for part in (VERSION_CONFIRMATION_EFFECT, conflict) if part)


def _pass_note(action: ReviewAction, review: ReviewSnapshot) -> str:
    """@description: Tell the Owner that approving a reexamined submission creates a new tail position, not the old one.
    @param action: Requested Owner decision.
    @param review: Current review before the decision.
    @return: Explanation for approvals of reexamined reviews, otherwise an empty string.
    """
    return REEXAMINATION_PASS_NOTE if action is ReviewAction.APPROVE and review.reexamination_pending else ""


def _financial_effect(identity_status: VersionIdentityStatus) -> str:
    """@description: State the no-award effect and any identity limitation the Owner must see.
    @param identity_status: Current persisted identity status of the video.
    @return: Owner-facing financial and identity summary in Traditional Chinese.
    """
    notes = {VersionIdentityStatus.UNRESOLVED: UNRESOLVED_IDENTITY_NOTE,
             VersionIdentityStatus.CONFLICT: CONFLICT_IDENTITY_NOTE}
    return " ".join([NO_AWARD_EFFECT, *([notes[identity_status]] if identity_status in notes else [])])


@contextmanager
def _unknown_outcome_boundary() -> Iterator[None]:
    """@description: Convert ambiguous commits into a retryable domain outcome with the same key preserved.
    @param: none
    @return: Context whose body may raise an ambiguous commit.
    """
    try:
        yield
    except CommitOutcomeUnknown as error:
        raise ReviewOutcomeUnknown("Retry the same confirmation to replay its original outcome") from error
