"""Verify one identity write rule for ordinary review commands and formal version confirmations.

Real repository and service over the offline SQL-protocol fake; synthetic data only.
Rules: (a) CONFIRMED needs a cited stored Owner record that resolves over all stored evidence to the same root;
(b) a CONFIRMED identity is never overwritten by a different version, the write becomes CONFLICT citing the new record;
(c) CONFLICT and UNRESOLVED proposals are freezes or downgrades and never payable; (d) historical replay returns the original result.
"""

from datetime import datetime, timedelta, timezone

import pytest

import bot.database.review_persistence as review_persistence
from bot.database.review_persistence import ReviewCommand
from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, record_evidence
from bot.owner_review import OwnerReviewService, ReviewAction
from bot.review import ReviewSnapshot, ReviewStatus
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.intake_fixture import seed_intake
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
VIDEO = VideoIdentity("Unify0001X1")


def submission() -> SubmissionRequest:
    """@description: Create the synthetic accepted intake whose duration passes and whose identity is unresolved.
    @param: none
    @return: Repository intake request for one video.
    """
    return SubmissionRequest(f"https://youtu.be/{VIDEO.video_id}", "op-1", 101, 999, VersionAssignment(VIDEO),
                             ReviewSnapshot(VIDEO), 0, DurationEvidence(VIDEO, 180, "synthetic-duration", True, True))


def stored_record(evidence_id: str, version: str, hours: int = 0):
    """@description: Create one Owner-judged evidence fact naming a basic recording version.
    @param evidence_id: Stable synthetic evidence identifier.
    @param version: Asserted version name.
    @param hours: Retrieval offset that orders records.
    @return: Evidence record for the synthetic video, not yet owner-confirmed by the boundary.
    """
    return record_evidence(evidence_id, VIDEO, EvidenceSourceType.OWNER_REVIEW, f"synthetic-{evidence_id}",
                           START + timedelta(hours=hours), "synthetic inspection", OWNER_ID,
                           RecordingVersionIdentity(version), owner_confirmed=True)


@pytest.fixture
def world():
    """@description: Build the fake database, real repository and service with one accepted, unresolved operation.
    @param: none
    @return: Tuple of database, repository and service.
    """
    database = FakeDatabase()
    database.now = START.replace(tzinfo=None)
    repository = SubmissionRepository(database.connect, OWNER_ID)
    service = OwnerReviewService(repository, OWNER_ID, lambda: START, token_ttl=timedelta(minutes=5))
    seed_intake(repository, submission())
    return database, repository, service


def revision_of(repository: SubmissionRepository) -> int:
    """@description: Read the operation's current review revision as an Owner would before deciding.
    @param repository: Real repository over the fake.
    @return: Current review version.
    """
    return repository.review_view(OWNER_ID, "op-1").review.version


def ordinary_write(repository: SubmissionRepository, decision_id: str, identity: VersionAssignment):
    """@description: Submit an ordinary identity-changing review command at the current revision.
    @param repository: Real repository over the fake.
    @param decision_id: Idempotency key of the command.
    @param identity: Identity the command proposes to store.
    @return: Review snapshot returned by the repository.
    """
    command = ReviewCommand(decision_id, "op-1", OWNER_ID, "synthetic identity decision", revision_of(repository), identity=identity)
    return repository.review(command)


def confirmed(version: str, evidence_id: str) -> VersionAssignment:
    """@description: Build a CONFIRMED proposal to one version that cites one evidence record.
    @param version: Canonical version name to confirm.
    @param evidence_id: Cited evidence identifier.
    @return: Confirmed identity assertion.
    """
    return VersionAssignment(VIDEO, VersionIdentityStatus.CONFIRMED, RecordingVersionIdentity(version), evidence_id)


def test_rule_a_confirmed_identity_is_refused_when_stored_evidence_contradicts_the_cited_version(world):
    """@description: Rule (a): an ordinary CONFIRMED write must resolve over ALL stored owner evidence to the same root, so a stored contradiction refuses it.
    @param world: Offline repository fixture.
    @return: None.
    """
    _, repository, _ = world
    repository.record_reexamination_evidence("evidence:op-1:ev-a", "op-1", OWNER_ID, "synthetic owner evidence", stored_record("ev-a", "orig-A"))
    repository.record_reexamination_evidence("evidence:op-1:ev-b", "op-1", OWNER_ID, "synthetic owner evidence",
                                             stored_record("ev-b", "orig-B", hours=1))
    with pytest.raises(ValueError):
        ordinary_write(repository, "rule-a-conflicting-store", confirmed("orig-A", "ev-a"))
    assert repository.review_view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.UNRESOLVED


def test_rule_a_confirmed_identity_is_refused_when_the_cited_record_is_not_stored(world):
    """@description: Rule (a): a CONFIRMED write that cites an evidence id never stored for this video is refused.
    @param world: Offline repository fixture.
    @return: None.
    """
    _, repository, _ = world
    with pytest.raises(ValueError):
        ordinary_write(repository, "rule-a-missing-record", confirmed("orig-A", "ev-missing"))


def test_rule_b_ordinary_write_of_a_different_confirmed_version_over_a_confirmed_identity_becomes_conflict_citing_the_new_record(world):
    """@description: Rule (b): an ordinary CONFIRMED write to version B over a confirmed A is stored as CONFLICT citing ev-b; A is never overwritten.
    @param world: Offline repository fixture.
    @return: None.
    """
    _, repository, _ = world
    repository.record_reexamination_evidence("evidence:op-1:ev-a", "op-1", OWNER_ID, "synthetic owner evidence", stored_record("ev-a", "orig-A"))
    ordinary_write(repository, "rule-b-store-a", confirmed("orig-A", "ev-a"))
    repository.record_reexamination_evidence("evidence:op-1:ev-b", "op-1", OWNER_ID, "synthetic owner evidence",
                                             stored_record("ev-b", "orig-B", hours=1))
    ordinary_write(repository, "rule-b-store-b", confirmed("orig-B", "ev-b"))
    identity = repository.review_view(OWNER_ID, "op-1").identity
    assert identity.status is VersionIdentityStatus.CONFLICT
    assert identity.version is None
    assert identity.evidence_reference == "ev-b"


def test_rule_b_formal_version_confirmation_of_a_different_version_over_a_confirmed_identity_becomes_conflict(world):
    """@description: Rule (b) through the formal path: confirming version B over a confirmed A freezes the identity as CONFLICT citing the new record.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    repository.record_reexamination_evidence("evidence:op-1:ev-a", "op-1", OWNER_ID, "synthetic owner evidence", stored_record("ev-a", "orig-A"))
    ordinary_write(repository, "rule-b-formal-store-a", confirmed("orig-A", "ev-a"))
    new_evidence = record_evidence("ev-b", VIDEO, EvidenceSourceType.OWNER_REVIEW, "synthetic-ev-b", START + timedelta(hours=1),
                                   "synthetic inspection", OWNER_ID, RecordingVersionIdentity("orig-B"))
    token = service.preview_version_confirmation(OWNER_ID, "op-1", new_evidence, "synthetic version confirmation",
                                                 revision_of(repository)).token_id
    service.confirm(OWNER_ID, token)
    identity = service.view(OWNER_ID, "op-1").identity
    assert identity.status is VersionIdentityStatus.CONFLICT
    assert identity.evidence_reference == "ev-b"


def test_rule_c_conflict_freeze_is_allowed_over_a_confirmed_identity_and_is_not_payable(world):
    """@description: Rule (c): an ordinary CONFLICT proposal freezes the identity; the stored result is not CONFIRMED.
    @param world: Offline repository fixture.
    @return: None.
    """
    _, repository, _ = world
    repository.record_reexamination_evidence("evidence:op-1:ev-a", "op-1", OWNER_ID, "synthetic owner evidence", stored_record("ev-a", "orig-A"))
    ordinary_write(repository, "rule-c-store-a", confirmed("orig-A", "ev-a"))
    frozen = VersionAssignment(VIDEO, VersionIdentityStatus.CONFLICT, None, "ev-a")
    ordinary_write(repository, "rule-c-freeze", frozen)
    assert repository.review_view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFLICT


def test_rule_c_unresolved_downgrade_is_allowed_and_never_creates_a_payable_identity(world):
    """@description: Rule (c): an ordinary UNRESOLVED proposal stores the downgrade and leaves no CONFIRMED identity.
    @param world: Offline repository fixture.
    @return: None.
    """
    _, repository, _ = world
    ordinary_write(repository, "rule-c-unresolved", VersionAssignment(VIDEO))
    identity = repository.review_view(OWNER_ID, "op-1").identity
    assert identity.status is VersionIdentityStatus.UNRESOLVED
    assert identity.version is None


def test_rule_d_historical_replay_returns_the_original_result_without_rerunning_the_identity_rules(world, monkeypatch):
    """@description: Rule (d): an identical replay by decision key returns the stored result and never reaches the evidence resolver again.
    @param world: Offline repository fixture.
    @param monkeypatch: Pytest fixture that makes the resolver fail if the replay reaches it.
    @return: None.
    """
    _, repository, _ = world
    repository.record_reexamination_evidence("evidence:op-1:ev-a", "op-1", OWNER_ID, "synthetic owner evidence", stored_record("ev-a", "orig-A"))
    command = ReviewCommand("rule-d-key", "op-1", OWNER_ID, "synthetic identity decision", revision_of(repository),
                            identity=confirmed("orig-A", "ev-a"))
    first = repository.review(command)
    state_after_first = repr(world[0].state)

    def refuse_rerun(*_arguments, **_keyword_arguments):
        raise AssertionError("replay must not re-run the identity check")

    monkeypatch.setattr(review_persistence, "resolve_video_version", refuse_rerun)
    assert repository.review(command) == first
    assert repr(world[0].state) == state_after_first
