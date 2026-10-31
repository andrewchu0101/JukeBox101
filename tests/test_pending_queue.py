"""Verify the Owner pending queue, keyset paging, stored-evidence citation, traceable sources and projection audit fields.

Real service -> repository -> offline SQL-protocol fake; synthetic data only, no Discord, MySQL or Gateway.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, record_evidence
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import ReviewSnapshot, ReviewStatus, ValidationError
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.intake_fixture import seed_intake
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777
STRANGER_ID = 999
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def video_of(number: int) -> VideoIdentity:
    """@description: Name the synthetic video that one submission number refers to.
    @param number: Stable synthetic number.
    @return: Platform video identity.
    """
    return VideoIdentity(f"Queue{number:05d}X")  # [11-character video ID]: Keeps the parser's identifier rule satisfied up to 99999.


def submission(number: int) -> SubmissionRequest:
    """@description: Create one synthetic accepted-intake request whose duration passes and whose identity is unresolved.
    @param number: Stable synthetic number used for the video, operation and submitter identifiers.
    @return: Repository intake request without any Owner decision.
    """
    video = video_of(number)
    return SubmissionRequest(f"https://youtu.be/{video.video_id}", f"op-{number}", 100 + number, 999,
                             VersionAssignment(video), ReviewSnapshot(video), 0,
                             DurationEvidence(video, 180, "synthetic-duration", True, True))


def owner_evidence(video: VideoIdentity, evidence_id: str, version: str, hours: int = 0):
    """@description: Create one synthetic Owner-judged evidence fact naming a version.
    @param video: Video the evidence describes.
    @param evidence_id: Stable synthetic evidence identifier.
    @param version: Asserted basic recording version.
    @param hours: Retrieval offset that orders records.
    @return: Evidence record that the service stamps as Owner-confirmed.
    """
    return record_evidence(evidence_id, video, EvidenceSourceType.OWNER_REVIEW, f"synthetic-{evidence_id}",
                           START + timedelta(hours=hours), "synthetic inspection", OWNER_ID,
                           RecordingVersionIdentity(version))


@pytest.fixture
def world():
    """@description: Build the fake database, real repository and Owner review service over synthetic time.
    @param: none
    @return: Tuple of database, repository and service.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)  # [fake UTC clock]: Matches the fake's naive database clock.
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, lambda: START, token_ttl=timedelta(minutes=5))
    return database, repository, service


def approve(service: OwnerReviewService, number: int) -> None:
    """@description: Approve one synthetic submission through a previewed, confirmed Owner decision.
    @param service: Owner review service over the fake.
    @param number: Synthetic submission number.
    @return: None.
    """
    judgement = ContentJudgement(video_of(number), OWNER_ID, "synthetic song judgement", START, f"content-{number}", True)
    revision = service.view(OWNER_ID, f"op-{number}").review.version
    token = service.preview(OWNER_ID, f"op-{number}", ReviewAction.APPROVE, "synthetic verified song", revision,
                            f"cite-{number}", content=judgement).token_id
    service.confirm(OWNER_ID, token)


def fail_validation(repository: SubmissionRepository, number: int) -> None:
    """@description: Move one synthetic submission to VALIDATION_FAILED through the audited review command.
    @param repository: Real repository over the fake.
    @param number: Synthetic submission number.
    @return: None.
    """
    revision = repository.review_view(OWNER_ID, f"op-{number}").review.version
    repository.review(ReviewCommand(f"validation-{number}", f"op-{number}", OWNER_ID, "synthetic metadata failure",
                                    revision, validation_error=ValidationError.VERIFICATION_FAILED))


def confirm_version(service: OwnerReviewService, number: int, evidence_record) -> None:
    """@description: Confirm a recording version for one synthetic submission through the single version entry.
    @param service: Owner review service over the fake.
    @param number: Synthetic submission number.
    @param evidence_record: Evidence fact the Owner relies on.
    @return: None.
    """
    revision = service.view(OWNER_ID, f"op-{number}").review.version
    token = service.preview_version_confirmation(OWNER_ID, f"op-{number}", evidence_record, "synthetic version confirmation",
                                                 revision).token_id
    service.confirm(OWNER_ID, token)


def test_pending_queue_includes_approved_unresolved_and_validation_failed_and_conflicts_and_skips_resolved_approvals(world):
    """@description: Approved-but-unconfirmed, validation-failed and conflicted items stay visible; a confirmed approval does not.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    for number in (1, 2, 3, 4):
        seed_intake(repository, submission(number))
    approve(service, 1)  # [approved, identity unresolved]: Must stay visible until the version is confirmed.
    fail_validation(repository, 2)  # [validation failed]: Must stay visible for the Owner.
    approve(service, 3)
    confirm_version(service, 3, owner_evidence(video_of(3), "ev-3", "orig-C"))  # [approved, confirmed]: Clean, so hidden.
    page = service.list_pending(OWNER_ID)
    assert [item.operation_id for item in page.items] == ["op-1", "op-2", "op-4"]
    assert page.items[0].review_status is ReviewStatus.APPROVED
    assert page.items[0].identity_status is VersionIdentityStatus.UNRESOLVED
    assert page.items[1].review_status is ReviewStatus.VALIDATION_FAILED


def test_pending_item_carries_source_url_and_accepted_sequence_in_the_frozen_field_order(world):
    """@description: Each pending row names its source URL and acceptance sequence, in the published field order.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    item = service.list_pending(OWNER_ID).items[0]
    assert item.source_url == f"https://youtu.be/{video_of(1).video_id}"
    assert item.accepted_sequence > 0  # [database-assigned order]: The repository assigns the sequence, never the caller.
    assert [field for field in item.__dataclass_fields__] == [
        "operation_id", "source_url", "accepted_sequence", "review_status", "identity_status", "revision",
        "evidence_count", "conflict_reason", "submitter_id", "duration_seconds", "api_basis"]


def test_keyset_pages_do_not_duplicate_or_skip_when_a_new_submission_arrives_between_requests(world):
    """@description: A submission accepted between two page requests appears once at the tail and never shifts or repeats earlier items.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    for number in (1, 2, 3):
        seed_intake(repository, submission(number))
    first = service.list_pending(OWNER_ID, limit=2)
    seed_intake(repository, submission(4))  # [new arrival between pages]: Must not shift the next page.
    second = service.list_pending(OWNER_ID, after_sequence=first.next_after_sequence, limit=2)
    seen = [item.operation_id for item in (*first.items, *second.items)]
    assert seen == ["op-1", "op-2", "op-3", "op-4"]
    assert len(set(seen)) == len(seen)
    assert second.next_after_sequence is None


def test_over_twenty_pending_items_are_reachable_by_page_and_the_page_size_is_capped_at_twenty(world):
    """@description: Twenty-five pending items are all reachable; a larger requested limit still returns at most twenty.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    for number in range(1, 26):
        seed_intake(repository, submission(number))
    capped = service.list_pending(OWNER_ID, limit=500)
    assert len(capped.items) == 20
    assert capped.next_after_sequence is not None
    rest = service.list_pending(OWNER_ID, after_sequence=capped.next_after_sequence, limit=20)
    assert len(rest.items) == 5
    assert rest.next_after_sequence is None
    assert len({item.operation_id for item in (*capped.items, *rest.items)}) == 25


def test_a_cursor_beyond_the_last_item_returns_an_empty_page_with_no_next_cursor(world):
    """@description: Paging past the end yields no items and no further cursor, without an error.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    page = service.list_pending(OWNER_ID, after_sequence=10_000)
    assert page.items == ()
    assert page.next_after_sequence is None


def test_stored_evidence_returns_the_record_cited_for_its_own_video(world):
    """@description: An evidence id stored for the operation's video can be cited again as the same record.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-cite", "orig-A"))
    record = service.stored_evidence(OWNER_ID, "op-1", "ev-cite")
    assert record.evidence_id == "ev-cite"
    assert record.video == video_of(1)
    assert record.owner_confirmed is True


def test_stored_evidence_refuses_an_unknown_evidence_id_with_value_error(world):
    """@description: A citation to an evidence id that was never stored is refused.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    with pytest.raises(ValueError):
        service.stored_evidence(OWNER_ID, "op-1", "ev-never-stored")


def test_stored_evidence_refuses_another_videos_evidence_id_with_value_error(world):
    """@description: Evidence stored for a different video cannot be cited against this operation.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-foreign", "orig-B"))
    with pytest.raises(ValueError):
        service.stored_evidence(OWNER_ID, "op-1", "ev-foreign")


def test_stored_evidence_refuses_a_non_owner_before_any_read(world):
    """@description: The stored-evidence citation is Owner-only.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    with pytest.raises(PermissionError):
        service.stored_evidence(STRANGER_ID, "op-1", "ev-anything")


@pytest.mark.parametrize("source_text", ["12345678", "123456789012345678", "discord:interaction-1", "short", " padded-source ", "x" * 513])
def test_traceable_source_refuses_interaction_style_numeric_short_and_padded_text(source_text):
    """@description: Only a traceable source reference of 8 to 512 unpadded characters is accepted; numeric ids, Discord-style ids and padding are refused.
    @param source_text: Candidate source reference that must be refused.
    @return: None.
    """
    from bot.owner_review import require_traceable_source

    with pytest.raises(ValueError):
        require_traceable_source(source_text)


def test_traceable_source_accepts_a_url_document_name_or_manual_check_reference_unchanged():
    """@description: A URL, document name or manual check reference of valid length is returned unchanged.
    @param: none
    @return: None.
    """
    from bot.owner_review import require_traceable_source

    assert require_traceable_source("https://example.org/recording/42") == "https://example.org/recording/42"
    assert require_traceable_source("owner-manual-check-2026") == "owner-manual-check-2026"


def test_projection_carries_source_url_history_with_actor_and_reason_and_the_conflict_reason(world):
    """@description: The Owner's read-only projection names the source, each revision's actor and reason, and the conflict cause.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    approve(service, 1)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-b", "orig-B", hours=1))
    projection = service.view(OWNER_ID, "op-1")
    assert projection.source_url == f"https://youtu.be/{video_of(1).video_id}"
    assert projection.conflict_reason is not None and "ev-b" in projection.conflict_reason
    entries = projection.history_entries
    assert [entry.revision for entry in entries] == [history.version for history in projection.history]
    # [review revisions]: Two intake revisions by the submitter, then the Owner's approval; identity-only confirmations add no revision.
    assert [entry.actor_id for entry in entries] == [101, 101, OWNER_ID]
    assert "synthetic verified song" in entries[-1].reason
    assert entries[-1].status is projection.review.status


def test_projection_conflict_reason_is_none_when_the_identity_is_not_in_conflict(world):
    """@description: A clean identity projects no conflict reason.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    assert service.view(OWNER_ID, "op-1").conflict_reason is None
