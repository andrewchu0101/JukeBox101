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
    ReexaminationCommand, ReviewCommand, content_judgement_digest, decision_digest, require_owner,
)
from bot.database.review_projection import ReviewProjection
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.evidence_record import EvidenceRecord, EvidenceReliability, assess_evidence
from bot.review import (
    ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, approve_review, reject_review,
    reopen_for_reexamination,
)
from bot.review_gate import evaluate_approval_gate
from bot.song_judgement import ContentJudgement
from bot.version_identity import VersionIdentityStatus, require_reference

NO_AWARD_EFFECT = "不發 Nickel，不寫入 credit_ledger，點數餘額不變。"  # [financial preview]: Shown before every confirmation.
REEXAMINATION_EFFECT = "重審會保留原決策與受理順序，且重審期間不得直接核准。" + NO_AWARD_EFFECT  # [reexamination preview]: Explains the block.
UNRESOLVED_IDENTITY_NOTE = "版本未確認：此次決定不會確認錄音版本。"  # [identity preview]: Approval never proves the recording.
CONFLICT_IDENTITY_NOTE = "版本衝突：維持凍結，此次決定不會清除衝突。"  # [identity preview]: Conflicts stay frozen.


class ReviewAction(Enum):
    """Name the two Owner decisions that can be previewed and confirmed."""

    APPROVE = "approve"  # [song decision]: Explicit positive song eligibility, still award-free.
    REJECT = "reject"  # [song decision]: Explicit negative song eligibility after duration validation.
    REEXAMINE = "reexamine"  # [formal reopen]: Confirmed only with a reliable evidence record bound into the token.


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
        if content is not None and action is not ReviewAction.APPROVE:
            raise ValueError("Only an approval may cite a content judgement")
        projection = self._repository.review_view(actor_id, operation_id)
        song_eligible = _approval_song_eligibility(actor_id, projection, content) if action is ReviewAction.APPROVE else False
        projected = _project(projection.review, action, expected_revision, song_eligible)  # [hard gates]: Refuse before any token exists.
        token_id = secrets.token_urlsafe(24)  # [unguessable handle]: Not derived from operation or revision.
        expires_at = self._clock() + self._token_ttl
        issued = _IssuedToken(actor_id, operation_id, action, reason, expected_revision, evidence_reference,
                              uuid.uuid4().hex, expires_at, content=content, song_eligible=song_eligible)
        with self._token_lock:
            self._tokens[token_id] = issued
        return ReviewPreview(token_id, operation_id, action, expected_revision, evidence_reference,
                             projection.review.status, projected.status, _financial_effect(projection.identity.status),
                             expires_at, content_digest=content_judgement_digest(content) if content is not None else None)

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

    def preview_reexamination(self, actor_id: int, operation_id: str, evidence: EvidenceRecord, reason: str,
                              expected_revision: int) -> ReviewPreview:
        """@description: Formal reexamination entry: preview the reopen for the Owner, binding the exact evidence and revision.
        Approval stays blocked afterwards until the TK-15 ordering event; only a confirmed token writes the reopen.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner.
        @param operation_id: Accepted operation whose video the evidence describes.
        @param evidence: Evidence record; the Owner's preview confirms it, so owner_confirmed is set here.
        @param reason: Audit explanation.
        @param expected_revision: Review revision the Owner examined.
        @return: Preview with the evidence fingerprint, expiry and the no-award effect.
        """
        require_owner(actor_id, self._owner_id, reason)
        confirmed = replace(evidence, owner_confirmed=True)  # [trusted confirmation]: The Owner's formal preview is the confirmation.
        if assess_evidence(confirmed) is not EvidenceReliability.RELIABLE:
            raise ValueError("Reexamination requires reliable Owner-confirmed evidence")
        projection = self._repository.review_view(actor_id, operation_id)
        if confirmed.video != projection.review.video:
            raise ValueError("Reexamination evidence must describe this video")
        # [hard gates]: Refuse before any token; the projection is the same pure reopen that confirmation writes.
        projected = reopen_for_reexamination(projection.review, expected_revision, confirmed.evidence_id)
        token_id = secrets.token_urlsafe(24)
        expires_at = self._clock() + self._token_ttl
        issued = _IssuedToken(actor_id, operation_id, ReviewAction.REEXAMINE, reason, expected_revision,
                              confirmed.evidence_id, uuid.uuid4().hex, expires_at, evidence=confirmed)
        with self._token_lock:
            self._tokens[token_id] = issued
        return ReviewPreview(token_id, operation_id, ReviewAction.REEXAMINE, expected_revision, confirmed.evidence_id,
                             projection.review.status, projected.status, REEXAMINATION_EFFECT, expires_at,
                             evidence_digest=decision_digest((confirmed,)))

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
        return approve_review(review, ApprovalSource.OWNER, expected_revision, song_eligible=song_eligible)
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
                             evidence_reference=issued.evidence_reference, content_judgement=issued.content)
    return ReviewCommand(issued.decision_id, issued.operation_id, issued.actor_id, issued.reason,
                         issued.expected_revision, song_eligible=issued.song_eligible, evidence_reference=issued.evidence_reference)


def _reexamination_for(issued: _IssuedToken) -> ReexaminationCommand:
    """@description: Translate a previewed reexamination into the repository's audited command with its original key.
    @param issued: Token holding the fixed reopen intent and its evidence record.
    @return: Command that reopens the review and never issues Nickel.
    """
    return ReexaminationCommand(issued.decision_id, issued.operation_id, issued.actor_id, issued.reason,
                                issued.expected_revision, issued.evidence)


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
