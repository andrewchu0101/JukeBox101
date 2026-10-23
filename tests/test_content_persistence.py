"""Persist the Owner's complete content judgement beside the approve decision and keep old audit rows readable.

Offline fake database only: real SubmissionRepository and review projection, no MySQL, Discord or network access.
"""

from tests.intake_fixture import seed_intake
import json
from datetime import datetime, timezone

import pytest

from bot.database.review_persistence import ReexaminationCommand, ReviewCommand, load_content_judgements
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, record_evidence
from bot.review import ApprovalSource, ReviewStatus
from bot.reward_candidates import RewardCandidateStatus
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus
from tests.submission_persistence_fake import FakeDatabase
from tests.version_candidate_support import make_candidate

OWNER_ID = 777
TIME = datetime(2026, 10, 23, 8, 0, tzinfo=timezone.utc)
CONTENT_FIELDS = {"video", "judged_by", "reason", "retrieved_at", "evidence_reference", "is_song"}


@pytest.fixture
def persistence() -> tuple[FakeDatabase, SubmissionRepository]:
    """@description: Inject a fully offline connection protocol and a configured Owner.
    @param: none
    @return: Fake state and real repository.
    """
    database = FakeDatabase()
    return database, SubmissionRepository(database.connect, OWNER_ID)


def accepted_video(repository: SubmissionRepository, version: str | None):
    """@description: Accept one synthetic pending submission whose duration passes, with the given recording identity.
    @param repository: Real repository over the fake.
    @param version: Trusted recording identity, or None when the identity is still unresolved.
    @return: Video identity of the accepted operation op-1.
    """
    candidate = make_candidate(1, version, review_status=ReviewStatus.PENDING_VALIDATION)
    duration = DurationEvidence(candidate.identity.video, 180, "synthetic", True, True)
    seed_intake(repository, SubmissionRequest(f"https://youtu.be/{candidate.identity.video.video_id}", candidate.operation_id,
                                        candidate.submitter_id, 999, candidate.identity, candidate.review, 0, duration))
    return candidate.identity.video


def content_for(video, **overrides) -> ContentJudgement:
    """@description: Build a valid synthetic Owner content judgement for one video, optionally overriding fields.
    @param video: Video the judgement describes.
    @param overrides: Constructor fields to change.
    @return: Immutable content judgement.
    """
    fields = {"video": video, "judged_by": OWNER_ID, "reason": "synthetic song judgement", "retrieved_at": TIME,
              "evidence_reference": "ev-content-1", "is_song": True}
    fields.update(overrides)
    return ContentJudgement(**fields)


def approve(repository: SubmissionRepository, revision: int = 1, content: ContentJudgement | None = None,
            decision_id: str = "review:op-1"):
    """@description: Apply an Owner approval through the production review path, optionally citing a content judgement.
    @param repository: Real repository over the fake.
    @param revision: Expected review revision.
    @param content: Owner content judgement the approval relied on, or None for the legacy shape.
    @param decision_id: Idempotency key of the decision.
    @return: Persisted review snapshot.
    """
    return repository.review(ReviewCommand(decision_id, "op-1", OWNER_ID, "synthetic verified song decision", revision,
                                           approval_source=ApprovalSource.OWNER, song_eligible=True,
                                           content_judgement=content))


def content_rows(database: FakeDatabase, video) -> list[dict]:
    """@description: Select the committed jb2_evidence rows whose stored payload is a ContentJudgement for one video.
    @param database: Fake database holding committed state.
    @param video: Video whose rows are inspected.
    @return: Matching rows in insertion order.
    """
    return [row for row in database.state["jb2_evidence"].values()
            if row["video_id"] == video.video_id and json.loads(row["evidence_json"])["value"].get("contract") == "ContentJudgement"]


def test_approve_stores_the_complete_content_judgement_in_the_decision_transaction(persistence: tuple) -> None:
    """@description: Persist every content fact with the approval so the Owner's verdict remains auditable after the decision.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    content = content_for(video)
    approve(repository, content=content)
    rows = content_rows(database, video)
    assert len(rows) == 1
    assert rows[0]["actor_id"] == OWNER_ID
    assert rows[0]["decision_reason"] == "synthetic verified song decision"
    assert set(json.loads(rows[0]["evidence_json"])["value"]["fields"]) == CONTENT_FIELDS


def test_retried_approve_with_the_same_decision_key_never_inserts_a_second_content_row(persistence: tuple) -> None:
    """@description: Replay the original approval after a technical retry without duplicating the audit row.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    content = content_for(video)
    first = approve(repository, content=content)
    rows_before = len(database.state["jb2_evidence"])
    assert approve(repository, content=content) == first
    assert len(content_rows(database, video)) == 1
    assert len(database.state["jb2_evidence"]) == rows_before


def test_unresolved_identity_judgement_stores_no_version_reference(persistence: tuple) -> None:
    """@description: Store the verdict against its video only; an unresolved recording stays unresolved and gains no version.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, None)
    approve(repository, content=content_for(video))
    [row] = content_rows(database, video)
    assert "version" not in json.loads(row["evidence_json"])["value"]["fields"]
    identity = repository.review_view(OWNER_ID, "op-1").identity
    assert identity.version is None
    assert identity.status is VersionIdentityStatus.UNRESOLVED


def test_confirmed_identity_judgement_never_writes_the_version_into_the_content_row(persistence: tuple) -> None:
    """@description: A confirmed recording may be shown beside the verdict, but the content judgement itself never carries a version.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    identity_before = repository.review_view(OWNER_ID, "op-1").identity
    approve(repository, content=content_for(video))
    [row] = content_rows(database, video)
    assert set(json.loads(row["evidence_json"])["value"]["fields"]) == CONTENT_FIELDS
    assert repository.review_view(OWNER_ID, "op-1").identity == identity_before


def test_content_judgement_for_another_video_is_refused_before_any_write(persistence: tuple) -> None:
    """@description: Refuse an approval whose content verdict describes a different video, leaving review and audit untouched.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    other = content_for(VideoIdentity("Zzz_999-Qqq"))
    evidence_before = dict(database.state["jb2_evidence"])
    with pytest.raises(ValueError):
        approve(repository, content=other)
    assert database.state["jb2_evidence"] == evidence_before
    assert repository.review_view(OWNER_ID, "op-1").review.status is ReviewStatus.PENDING_REVIEW


def test_content_judgement_judged_by_another_person_is_refused(persistence: tuple) -> None:
    """@description: The audit row's actor must be the Owner who made the verdict, so a judge naming someone else is refused.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    with pytest.raises(ValueError):
        approve(repository, content=content_for(video, judged_by=OWNER_ID + 1))
    assert content_rows(database, video) == []


def test_approve_without_content_judgement_writes_no_content_row(persistence: tuple) -> None:
    """@description: Keep the legacy approval shape working, with no content row invented for it.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    approve(repository)
    assert content_rows(database, video) == []
    assert repository.review_view(OWNER_ID, "op-1").review.status is ReviewStatus.APPROVED


def test_reexamination_after_content_approval_reads_both_row_types(persistence: tuple) -> None:
    """@description: A reexamination over old evidence and a content row must still reopen the review and project only evidence records.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    approve(repository, content=content_for(video))
    entry = record_evidence("ev-reexam", video, EvidenceSourceType.OWNER_REVIEW, "synthetic-reexam", TIME,
                            "fresh owner inspection", OWNER_ID, RecordingVersionIdentity("original"), owner_confirmed=True)
    repository.reexamine(ReexaminationCommand("reexam-key", "op-1", OWNER_ID, "fresh owner inspection", 2, entry))
    projection = repository.review_view(OWNER_ID, "op-1")
    assert projection.evidence == (entry,)
    assert projection.review.reexamination_pending is True


def test_unresolved_approval_with_content_still_waits_for_identity_at_settlement(persistence: tuple) -> None:
    """@description: End to end, a content-backed approval of an unresolved recording settles as waiting for identity with no credit.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, None)
    approve(repository, content=content_for(video))
    assert repository.settle("op-1").status is RewardCandidateStatus.WAITING_IDENTITY
    assert not database.state["credit_ledger"]


def read_back(database: FakeDatabase, video) -> tuple[ContentJudgement, ...]:
    """@description: Load stored content judgements through the production helper under the writer gate, as a reader would.
    @param database: Fake database holding committed state.
    @param video: Video whose judgements are read.
    @return: Stored content judgements in stored order.
    """
    connection = database.connect()
    connection.acquire()
    try:
        return load_content_judgements(connection.cursor(), video.video_id)
    finally:
        connection.rollback()
        connection.close()


def test_read_back_returns_every_fact_of_the_persisted_content_judgement(persistence: tuple) -> None:
    """@description: A reader after a persisted approval must recover video, judge, reason, time, citation and verdict unchanged.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    content = content_for(video)
    approve(repository, content=content)
    assert read_back(database, video) == (content,)


def test_read_back_skips_legacy_review_audit_and_reexamination_rows(persistence: tuple) -> None:
    """@description: Rows written before the content type existed, and reexamination evidence, must be tolerated and ignored by the content reader.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    content = content_for(video)
    approve(repository, content=content)
    entry = record_evidence("ev-reexam", video, EvidenceSourceType.OWNER_REVIEW, "synthetic-reexam", TIME,
                            "fresh owner inspection", OWNER_ID, RecordingVersionIdentity("original"), owner_confirmed=True)
    repository.reexamine(ReexaminationCommand("reexam-key", "op-1", OWNER_ID, "fresh owner inspection", 2, entry))
    assert len(database.state["jb2_evidence"]) >= 3  # [mixed table]: review audit tuples, content and reexamination evidence
    assert read_back(database, video) == (content,)


def test_read_back_is_empty_for_a_legacy_approval_without_content(persistence: tuple) -> None:
    """@description: Approvals recorded before the content row existed still read back as having no content judgement.
    @param persistence: Offline fake plus real repository.
    @return: None.
    """
    database, repository = persistence
    video = accepted_video(repository, "original")
    approve(repository)
    assert read_back(database, video) == ()
