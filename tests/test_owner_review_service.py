"""Exercise the Owner review service over the real repository and the offline SQL-protocol fake.

Every scenario uses synthetic submissions and an in-memory token store; no Discord, MySQL or Gateway is involved.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, record_evidence
from bot.owner_review import (
    ExpiredReviewToken, OwnerReviewService, ReviewAction, ReviewOutcomeUnknown, UnknownReviewToken,
)
from bot.review import InvalidReviewTransition, ReviewStatus, StaleReviewVersion
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionRequest
from bot.version_evidence import resolve_video_version
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777
STRANGER_ID = 999
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
TOKEN_TTL = timedelta(minutes=5)


class Clock:
    """@description: Provide a controllable synthetic clock for token expiry scenarios.
    @param: none
    @return: Callable clock with an advance method.
    """

    def __init__(self) -> None:
        """@description: Start at a fixed synthetic instant.
        @param self: Mutable synthetic clock.
        @return: None.
        """
        self.current = START  # [synthetic now]: Advanced explicitly by scenarios.

    def __call__(self) -> datetime:
        """@description: Return the synthetic current instant.
        @param self: Synthetic clock.
        @return: Current timezone-aware instant.
        """
        return self.current

    def advance(self, seconds: int) -> None:
        """@description: Move synthetic time forward for expiry checks.
        @param self: Synthetic clock.
        @param seconds: Elapsed seconds.
        @return: None.
        """
        self.current += timedelta(seconds=seconds)


def submission(number: int, seconds: int | None = 180) -> SubmissionRequest:
    """@description: Create one synthetic submission with optional full-video duration evidence.
    @param number: Stable synthetic number used for the video and operation identifiers.
    @param seconds: Reliable duration, or None for a submission still missing duration.
    @return: Repository intake request without any review approval.
    """
    video = VideoIdentity(f"Synth{number:04d}X{number}")
    duration = DurationEvidence(video, seconds, "synthetic-duration", True, True) if seconds is not None else None
    return SubmissionRequest(f"https://youtu.be/{video.video_id}", f"op-{number}", 100 + number, 999,
                             VersionAssignment(video), _initial_review(video), 0, duration)


def _initial_review(video: VideoIdentity):
    """@description: Use the pure initial review snapshot required by the intake contract.
    @param video: Synthetic video subject.
    @return: Initial review snapshot.
    """
    from bot.review import ReviewSnapshot

    return ReviewSnapshot(video)


def evidence(video: VideoIdentity, evidence_id: str, version: str | None,
             source: EvidenceSourceType = EvidenceSourceType.OWNER_REVIEW, hours: int = 0):
    """@description: Create one synthetic audited evidence record about a specific video.
    @param video: Video the evidence describes.
    @param evidence_id: Stable synthetic evidence identifier.
    @param version: Asserted version name, or None for a non-version note.
    @param source: Source category.
    @param hours: Retrieval offset for ordering.
    @return: Immutable evidence record.
    """
    asserted = RecordingVersionIdentity(version) if version is not None else None
    return record_evidence(evidence_id, video, source, f"synthetic-{evidence_id}", START + timedelta(hours=hours),
                           "synthetic inspection", OWNER_ID, asserted)


@pytest.fixture
def world():
    """@description: Build a fake database, real repository, service and controllable clock.
    @param: none
    @return: Tuple of database, repository, service and clock.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)  # [fake UTC clock]: Matches the fake's naive database clock.
    repository = SubmissionRepository(database.connect, OWNER_ID)
    clock = Clock()
    service = OwnerReviewService(repository, OWNER_ID, clock, token_ttl=TOKEN_TTL)
    return database, repository, service, clock


def seed_pending(repository: SubmissionRepository, number: int = 1) -> None:
    """@description: Accept one submission whose duration passes and whose identity is still unresolved.
    @param repository: Real repository over the fake.
    @param number: Synthetic submission number.
    @return: None.
    """
    repository.accept(submission(number))


def test_non_owner_cannot_preview_or_confirm_and_no_review_is_written(world):
    """@description: Refuse a non-Owner before any token, review revision or audit row is created.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    before = dict(database.state["jb2_review_history"])
    with pytest.raises(PermissionError):
        service.preview(STRANGER_ID, "op-1", ReviewAction.APPROVE, "stranger attempt", 1)
    assert dict(database.state["jb2_review_history"]) == before


def test_preview_describes_state_and_financial_effect_without_writing(world):
    """@description: Show the projected status and the explicit no-award effect before any confirmation.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    before = dict(database.state["jb2_review_history"])
    preview = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1)
    assert preview.current_status is ReviewStatus.PENDING_REVIEW
    assert preview.projected_status is ReviewStatus.APPROVED
    assert "不發 Nickel" in preview.financial_effect
    assert dict(database.state["jb2_review_history"]) == before


def test_confirm_applies_once_and_a_repeated_confirmation_returns_the_original_result(world):
    """@description: Execute a confirmed approval exactly once and replay the original result on repeat.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1).token_id
    history_before = len(database.state["jb2_review_history"])
    first = service.confirm(OWNER_ID, token)
    second = service.confirm(OWNER_ID, token)
    assert first == second
    assert first.status is ReviewStatus.APPROVED
    assert len(database.state["jb2_review_history"]) == history_before + 1


def test_expired_token_is_refused_without_writing(world):
    """@description: Refuse a confirmation after its time limit and leave the review untouched.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, clock = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 1).token_id
    clock.advance(int(TOKEN_TTL.total_seconds()) + 1)
    before = dict(database.state["jb2_review_history"])
    with pytest.raises(ExpiredReviewToken):
        service.confirm(OWNER_ID, token)
    assert dict(database.state["jb2_review_history"]) == before


def test_token_for_an_earlier_revision_is_refused_after_the_review_changed(world):
    """@description: Refuse a stale confirmation when another decision already advanced the revision.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    stale_token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1).token_id
    fresh_token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "not an eligible song", 1).token_id
    service.confirm(OWNER_ID, fresh_token)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, stale_token)
    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.REJECTED


def test_unknown_commit_outcome_retries_with_the_same_decision_and_applies_once(world):
    """@description: Keep an unknown commit result retryable with the same decision key, never a second write.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1).token_id
    history_before = len(database.state["jb2_review_history"])
    database.commit_fault = "after"  # [injected ambiguity]: Commit may have happened without acknowledgement.
    with pytest.raises(ReviewOutcomeUnknown):
        service.confirm(OWNER_ID, token)
    database.commit_fault = None
    result = service.confirm(OWNER_ID, token)
    assert result.status is ReviewStatus.APPROVED
    assert len(database.state["jb2_review_history"]) == history_before + 1


def test_approving_a_conflicted_video_keeps_the_conflict_and_awards_nothing(world):
    """@description: Refuse to clear a persisted identity conflict through approval.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    conflict = resolve_video_version(video, [evidence(video, "ev-a", "orig-A"), evidence(video, "ev-b", "cover-B")])
    assert conflict.status is VersionIdentityStatus.CONFLICT
    repository.review(ReviewCommand("identity-conflict", "op-1", OWNER_ID, "two contradicting inspections", 1,
                                    identity=conflict))
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 2).token_id
    service.confirm(OWNER_ID, token)
    view = service.view(OWNER_ID, "op-1")
    assert view.identity.status is VersionIdentityStatus.CONFLICT
    assert view.review.status is ReviewStatus.APPROVED
    assert not database.state["credit_ledger"]


def test_reexamination_evidence_keeps_the_original_decision_and_adds_audit_without_state_change(world):
    """@description: Record new evidence as an audit fact while the decision, identity and order stay unchanged.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    token = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1).token_id
    service.confirm(OWNER_ID, token)
    before = service.view(OWNER_ID, "op-1")
    video = before.review.video
    service.add_reexamination_evidence(OWNER_ID, "op-1", evidence(video, "ev-new", "orig-A", hours=3),
                                       "fresh owner inspection", "reexam-1")
    after = service.view(OWNER_ID, "op-1")
    assert after.review == before.review
    assert after.identity == before.identity
    assert after.accepted_sequence == before.accepted_sequence
    assert [item.evidence_id for item in after.evidence] == ["ev-new"]
    assert not database.state["credit_ledger"]


def test_reexamination_is_idempotent_for_the_same_decision_key(world):
    """@description: Replay the same evidence entry without duplicating its audit fact.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    entry = evidence(submission(1).identity.video, "ev-new", "orig-A")
    service.add_reexamination_evidence(OWNER_ID, "op-1", entry, "fresh owner inspection", "reexam-1")
    service.add_reexamination_evidence(OWNER_ID, "op-1", entry, "fresh owner inspection", "reexam-1")
    assert len(service.view(OWNER_ID, "op-1").evidence) == 1


def test_pending_evidence_leaves_identity_unresolved_and_approval_still_awards_nothing(world):
    """@description: Keep title-only evidence pending and show that approval does not confirm the recording.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    seed_pending(repository)
    video = submission(1).identity.video
    pending = resolve_video_version(video, [evidence(video, "ev-title", "orig-A", EvidenceSourceType.PLATFORM_METADATA)])
    assert pending.status is VersionIdentityStatus.UNRESOLVED
    preview = service.preview(OWNER_ID, "op-1", ReviewAction.APPROVE, "synthetic verified song", 1)
    assert "版本未確認" in preview.financial_effect
    service.confirm(OWNER_ID, preview.token_id)
    assert not database.state["credit_ledger"]


def test_unknown_token_is_refused(world):
    """@description: Refuse a confirmation token that was never issued by this service.
    @param world: Offline service fixture.
    @return: None.
    """
    _, _, service, _ = world
    with pytest.raises(UnknownReviewToken):
        service.confirm(OWNER_ID, "not-issued")


def test_preview_refuses_an_approval_the_hard_gates_would_reject(world):
    """@description: Refuse at preview time when a 30-second video cannot pass the duration gate.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service, _ = world
    repository.accept(submission(2, seconds=30))
    with pytest.raises(InvalidReviewTransition):
        service.preview(OWNER_ID, "op-2", ReviewAction.APPROVE, "attempted bypass", 1)
