"""Let the configured Owner preview and confirm review decisions through short-lived, single-use tokens.

Previews pre-check the pure hard gates; confirmations delegate every write to the audited repository and never award Nickel.
"""

import secrets
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from bot.database.review_persistence import ReviewCommand, require_owner
from bot.database.review_projection import ReviewProjection
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.evidence_record import EvidenceRecord
from bot.review import ApprovalSource, ReviewSnapshot, ReviewStatus, approve_review, reject_review
from bot.version_identity import VersionIdentityStatus

NO_AWARD_EFFECT = "不發 Nickel，不寫入 credit_ledger，點數餘額不變。"  # [financial preview]: Shown before every confirmation.
UNRESOLVED_IDENTITY_NOTE = "版本未確認：此次決定不會確認錄音版本。"  # [identity preview]: Approval never proves the recording.
CONFLICT_IDENTITY_NOTE = "版本衝突：維持凍結，此次決定不會清除衝突。"  # [identity preview]: Conflicts stay frozen.


class ReviewAction(Enum):
    """Name the two Owner decisions that can be previewed and confirmed."""

    APPROVE = "approve"  # [song decision]: Explicit positive song eligibility, still award-free.
    REJECT = "reject"  # [song decision]: Explicit negative song eligibility after duration validation.


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
    current_status: ReviewStatus  # [current state]: Status before the decision.
    projected_status: ReviewStatus  # [projected state]: Status the decision would produce.
    financial_effect: str  # [owner-facing effect]: Always states that no Nickel is issued.
    expires_at: datetime  # [token deadline]: After this instant the token is refused.


@dataclass
class _IssuedToken:
    """Hold the immutable intent of one preview plus its completed result, once confirmed."""

    actor_id: int  # [bound owner]: Only this Owner may spend the token.
    operation_id: str  # [review subject]: Fixed at preview time.
    action: ReviewAction  # [decision kind]: Fixed at preview time.
    reason: str  # [audit explanation]: Reused verbatim on retry.
    expected_revision: int  # [concurrency token]: Reused verbatim on retry.
    decision_id: str  # [idempotency key]: Retries after an unknown commit replay this same key.
    expires_at: datetime  # [token deadline]: Checked before any new write.
    result: ReviewSnapshot | None = None  # [spent marker]: Set only after a confirmed commit.


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
                expected_revision: int) -> ReviewPreview:
        """@description: Show the Owner the exact projected outcome and issue a short-lived confirmation token.
        @param self: Owner review coordinator.
        @param actor_id: Authenticated caller, which must be the Owner.
        @param operation_id: Accepted operation to decide.
        @param action: Approve or reject.
        @param reason: Audit explanation stored with the decision.
        @param expected_revision: Review revision the Owner examined.
        @return: Preview with projected status, financial effect and expiry.
        """
        require_owner(actor_id, self._owner_id, reason)
        projection = self._repository.review_view(actor_id, operation_id)
        projected = _project(projection.review, action, expected_revision)  # [hard gates]: Refuse before any token exists.
        token_id = secrets.token_urlsafe(24)  # [unguessable handle]: Not derived from operation or revision.
        expires_at = self._clock() + self._token_ttl
        issued = _IssuedToken(actor_id, operation_id, action, reason, expected_revision, uuid.uuid4().hex, expires_at)
        with self._token_lock:
            self._tokens[token_id] = issued
        return ReviewPreview(token_id, operation_id, action, expected_revision, projection.review.status,
                             projected.status, _financial_effect(projection.identity.status), expires_at)

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
        with _unknown_outcome_boundary():
            return self._repository.record_reexamination_evidence(decision_id, operation_id, actor_id, reason, evidence)

    def _apply(self, issued: _IssuedToken) -> ReviewSnapshot:
        """@description: Send one previewed decision through the audited repository with its original key.
        @param self: Owner review coordinator.
        @param issued: Token whose intent is replayed verbatim.
        @return: Persisted or replayed review revision.
        """
        command = _command_for(issued)
        with _unknown_outcome_boundary():
            return self._repository.review(command)


def _project(review: ReviewSnapshot, action: ReviewAction, expected_revision: int) -> ReviewSnapshot:
    """@description: Run the pure hard gates shared with confirmation so refusals happen at preview time.
    @param review: Current authoritative review.
    @param action: Requested Owner decision.
    @param expected_revision: Revision the Owner examined.
    @return: Projected revision that confirmation would produce.
    """
    if action is ReviewAction.APPROVE:
        return approve_review(review, ApprovalSource.OWNER, expected_revision, song_eligible=True)
    if action is ReviewAction.REJECT:
        return reject_review(review, expected_revision)
    raise ValueError("Unknown review action")


def _command_for(issued: _IssuedToken) -> ReviewCommand:
    """@description: Translate a previewed Owner decision into the repository's audited command.
    @param issued: Token holding the fixed decision intent.
    @return: Command that never clears identity conflicts or touches credits.
    """
    if issued.action is ReviewAction.APPROVE:
        return ReviewCommand(issued.decision_id, issued.operation_id, issued.actor_id, issued.reason,
                             issued.expected_revision, approval_source=ApprovalSource.OWNER, song_eligible=True)
    return ReviewCommand(issued.decision_id, issued.operation_id, issued.actor_id, issued.reason,
                         issued.expected_revision, song_eligible=False)


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
