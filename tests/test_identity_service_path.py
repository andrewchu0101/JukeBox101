"""Verify the Owner version-confirmation entry, the pending queue and the no-payout guarantee through the real service and repository.

Synthetic submissions and evidence over the offline SQL-protocol fake only; no Discord, MySQL or Gateway is involved.
Money checks compare committed fake rows before and after each action instead of assuming settlement was never called.
"""

from tests.intake_fixture import seed_intake
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from bot.database.submission_repository import SubmissionRepository
from bot.eligibility import DurationEvidence
from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.owner_review import OwnerReviewService, ReviewAction, ReviewOutcomeUnknown
from bot.review import ReviewSnapshot, ReviewStatus, StaleReviewVersion
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.submission_workflow import SubmissionRequest
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus
from tests.submission_persistence_fake import FakeDatabase

OWNER_ID = 777
STRANGER_ID = 999
START = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def submission(number: int) -> SubmissionRequest:
    """@description: Create one synthetic accepted-intake request whose duration passes and whose identity is unresolved.
    @param number: Stable synthetic number used for the video, operation and submitter identifiers.
    @return: Repository intake request without any Owner decision.
    """
    video = video_of(number)
    return SubmissionRequest(f"https://youtu.be/{video.video_id}", f"op-{number}", 100 + number, 999,
                             VersionAssignment(video), ReviewSnapshot(video), 0,
                             DurationEvidence(video, 180, "synthetic-duration", True, True))


def video_of(number: int) -> VideoIdentity:
    """@description: Name the synthetic video that one submission number refers to.
    @param number: Stable synthetic number.
    @return: Platform video identity.
    """
    return VideoIdentity(f"Synth{number:04d}X{number}")


def owner_evidence(video: VideoIdentity, evidence_id: str, version: str | None, *, kind: VersionClaimKind | None = None,
                   hours: int = 0, source: EvidenceSourceType = EvidenceSourceType.OWNER_REVIEW):
    """@description: Create one synthetic evidence fact; the service stamps Owner confirmation at its own boundary.
    @param video: Video the evidence describes.
    @param evidence_id: Stable synthetic evidence identifier.
    @param version: Asserted version name, or None for a note that names no version.
    @param kind: Declared version category, or None for a basic recording version.
    @param hours: Retrieval offset that orders records.
    @param source: Source category; platform metadata can never confirm a version.
    @return: Evidence record that is not yet owner-confirmed.
    """
    asserted = RecordingVersionIdentity(version) if version is not None else None
    return record_evidence(evidence_id, video, source, f"synthetic-{evidence_id}", START + timedelta(hours=hours),
                           "synthetic inspection", OWNER_ID, asserted, version_kind=kind)


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


def confirm_version(service: OwnerReviewService, number: int, evidence_record) -> ReviewStatus:
    """@description: Confirm a recording version for one synthetic submission through the single version entry.
    @param service: Owner review service over the fake.
    @param number: Synthetic submission number.
    @param evidence_record: Evidence fact the Owner relies on.
    @return: Review status after the confirmation.
    """
    revision = service.view(OWNER_ID, f"op-{number}").review.version
    token = service.preview_version_confirmation(OWNER_ID, f"op-{number}", evidence_record, "synthetic version confirmation",
                                                 revision).token_id
    return service.confirm(OWNER_ID, token).status


def money_state(database: FakeDatabase, submitter_id: int) -> dict:
    """@description: Copy every committed row that a payout or ledger reversal could change for one submitter.
    @param database: Fake whose committed state is inspected.
    @param submitter_id: Submitter whose balance and ledger rows are compared.
    @return: Deep copies of ledger, balance, settlement and claim rows.
    """
    user = database.state["users"].get(submitter_id)
    return {"credit_ledger": deepcopy(database.state["credit_ledger"]),
            "credit_balance": user["credit_balance"] if user is not None else None,
            "jb2_settlements": deepcopy(database.state["jb2_settlements"]),
            "jb2_claims": deepcopy(database.state["jb2_claims"])}


def settle_outcome(repository: SubmissionRepository, operation_id: str):
    """@description: Ask the real reward evaluator for its decision, reporting a READY decision that payout refuses as such.
    @param repository: Repository whose settle path runs the evaluator.
    @param operation_id: Accepted operation to evaluate.
    @return: The evaluator's status, or the string "READY_PAYOUT_DISABLED" when READY was refused by the disabled switch.
    """
    try:
        return repository.settle(operation_id).status
    except PermissionError:
        return "READY_PAYOUT_DISABLED"


def test_non_owner_cannot_preview_a_version_confirmation_and_nothing_is_written(world):
    """@description: Refuse a non-Owner before any token, evidence row or identity write exists.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    before = deepcopy(database.state)
    with pytest.raises(PermissionError):
        service.preview_version_confirmation(STRANGER_ID, "op-1", owner_evidence(video_of(1), "ev-a", "orig-A"), "stolen", 0)
    assert database.state == before


def test_platform_metadata_naming_a_version_never_confirms_it(world):
    """@description: A title-like platform record is pending even after the service stamps Owner confirmation, so no identity is written.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    before = deepcopy(database.state)
    metadata = owner_evidence(video_of(1), "ev-title", "orig-A", source=EvidenceSourceType.PLATFORM_METADATA)
    with pytest.raises(ValueError):
        service.preview_version_confirmation(OWNER_ID, "op-1", metadata, "title match", service.view(OWNER_ID, "op-1").review.version)
    assert database.state == before


def test_evidence_that_establishes_no_version_is_refused_at_preview_with_nothing_to_confirm(world):
    """@description: A categorised claim without traceable proof confirms nothing, so the preview refuses and writes nothing.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    before = deepcopy(database.state)
    clip = owner_evidence(video_of(1), "ev-clip", "orig-A", kind=VersionClaimKind.EDIT)
    with pytest.raises(ValueError, match="nothing to confirm"):
        service.preview_version_confirmation(OWNER_ID, "op-1", clip, "clip note", service.view(OWNER_ID, "op-1").review.version)
    assert database.state == before


def test_version_confirmation_sets_the_identity_without_reopening_or_moving_the_queue(world):
    """@description: Confirming a version stamps it on the identity only; review status, revision and accepted order stay put.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    approve(service, 1)
    before = service.view(OWNER_ID, "op-1")
    status = confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    after = service.view(OWNER_ID, "op-1")
    assert after.identity.status is VersionIdentityStatus.CONFIRMED
    assert after.identity.version == RecordingVersionIdentity("orig-A")
    assert after.identity.evidence_reference == "ev-a"
    assert status is ReviewStatus.APPROVED
    assert after.review.status is before.review.status
    assert after.review.version == before.review.version
    assert after.review.reexamination_pending is False
    assert after.accepted_sequence == before.accepted_sequence


def test_version_confirmation_contradicting_a_confirmed_version_becomes_a_conflict(world):
    """@description: A second Owner-confirmed version that contradicts the first freezes the identity as CONFLICT, citing the new evidence.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-b", "orig-B", hours=1))
    identity = service.view(OWNER_ID, "op-1").identity
    assert identity.status is VersionIdentityStatus.CONFLICT
    assert identity.evidence_reference == "ev-b"


def test_version_confirmation_refuses_a_review_that_changed_after_preview_before_any_write(world):
    """@description: A preview issued for an old revision is refused at confirmation when the Owner's review moved on.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    token = service.preview_version_confirmation(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-a", "orig-A"),
                                                 "synthetic version confirmation", service.view(OWNER_ID, "op-1").review.version).token_id
    rejection = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "synthetic rejection", service.view(OWNER_ID, "op-1").review.version, "cite-reject")
    service.confirm(OWNER_ID, rejection.token_id)
    before = deepcopy(database.state)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, token)
    assert database.state == before


def test_unknown_commit_of_a_version_confirmation_replays_the_original_result_and_writes_once(world):
    """@description: Retrying the same confirmation after an unknown commit returns the original result without a second evidence row.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview_version_confirmation(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-a", "orig-A"),
                                                 "synthetic version confirmation", revision).token_id
    evidence_before = len(database.state["jb2_evidence"])
    database.commit_fault = "after"  # [injected ambiguity]: Commit may have happened without acknowledgement.
    with pytest.raises(ReviewOutcomeUnknown):
        service.confirm(OWNER_ID, token)
    database.commit_fault = None
    first = service.confirm(OWNER_ID, token)
    evidence_after_first = len(database.state["jb2_evidence"])
    assert service.confirm(OWNER_ID, token) == first
    assert evidence_after_first == evidence_before + 2  # [one evidence fact and one audit tuple]: Written once.
    assert len(database.state["jb2_evidence"]) == evidence_after_first
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFIRMED


def test_repeated_version_confirmation_with_the_same_decision_key_writes_once(world):
    """@description: The repository replays an identical version confirmation by its decision key without a second write.
    @param world: Offline service fixture.
    @return: None.
    """
    from bot.database.review_persistence import VersionConfirmationCommand
    from bot.evidence_record import record_evidence

    database, repository, _ = world
    seed_intake(repository, submission(1))
    evidence = record_evidence("ev-a", video_of(1), EvidenceSourceType.OWNER_REVIEW, "synthetic-ev-a", START,
                               "synthetic inspection", OWNER_ID, RecordingVersionIdentity("orig-A"), owner_confirmed=True)
    command = VersionConfirmationCommand("version-key-1", "op-1", OWNER_ID, "synthetic version confirmation", repository.review_view(OWNER_ID, "op-1").review.version, evidence)
    first = repository.confirm_version(command)
    state_after_first = deepcopy(database.state)
    assert repository.confirm_version(command) == first
    assert database.state == state_after_first


def test_list_pending_refuses_a_non_owner_before_any_read(world):
    """@description: The pending queue is Owner-only and opens no database connection for anyone else.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    connections_before = len(database.connections)
    with pytest.raises(PermissionError):
        service.list_pending(STRANGER_ID)
    assert len(database.connections) == connections_before


def test_list_pending_shows_open_and_conflicted_items_with_evidence_counts_and_skips_approved_clean_ones(world):
    """@description: Open reviews and frozen conflicts appear with their revision, evidence count and conflict reason; clean approvals do not.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    seed_intake(repository, submission(2))
    seed_intake(repository, submission(3))
    approve(service, 3)
    confirm_version(service, 3, owner_evidence(video_of(3), "ev-3", "orig-C"))  # [approved, confirmed]: Clean, so hidden.
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-2a", "orig-A"))
    confirm_version(service, 2, owner_evidence(video_of(2), "ev-2b", "orig-B", hours=1))
    items = service.list_pending(OWNER_ID).items
    assert [item.operation_id for item in items] == ["op-1", "op-2"]
    open_item, conflicted = items
    assert open_item.review_status is ReviewStatus.PENDING_REVIEW
    assert open_item.identity_status is VersionIdentityStatus.UNRESOLVED
    assert open_item.evidence_count == 0
    assert open_item.conflict_reason is None
    assert conflicted.identity_status is VersionIdentityStatus.CONFLICT
    assert conflicted.evidence_count == 2
    assert conflicted.revision == service.view(OWNER_ID, "op-2").review.version
    assert conflicted.conflict_reason is not None and "ev-2b" in conflicted.conflict_reason


@pytest.mark.parametrize(("decision", "expected_status"), [
    ("approve", Status.WAITING_IDENTITY),  # [unconfirmed version]: Approval never proves the recording.
    ("reject", Status.INELIGIBLE),  # [negative decision]: A rejected song is never payable.
])
def test_no_payout_before_and_after_a_decision_leaves_committed_money_rows_unchanged(world, decision, expected_status):
    """@description: Approve or reject changes only review facts; ledger, balance, settlement and claim rows stay identical and no Nickel is issued.
    @param world: Offline service fixture.
    @param decision: Which Owner decision to run.
    @param expected_status: Reward status the real evaluator must report afterwards.
    @return: None.
    """
    database, repository, service = world
    database.state["control"]["settlement_enabled"] = False  # [payout switch off]: READY can never award, so only the decision is asserted.
    seed_intake(repository, submission(1))
    before = money_state(database, 101)
    if decision == "reject":
        token = service.preview(OWNER_ID, "op-1", ReviewAction.REJECT, "synthetic rejection", service.view(OWNER_ID, "op-1").review.version, "cite-reject").token_id
        service.confirm(OWNER_ID, token)
    else:
        approve(service, 1)
    assert money_state(database, 101) == before
    assert settle_outcome(repository, "op-1") is expected_status
    assert money_state(database, 101) == before


def test_version_confirmation_making_an_approved_item_ready_performs_no_payout_in_this_slice(world):
    """@description: With payout disabled, a version confirmation that makes an approved item READY changes identity only; the award is refused and no ledger row appears.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    database.state["control"]["settlement_enabled"] = False  # [payout switch off]: This slice performs no payout.
    seed_intake(repository, submission(1))
    approve(service, 1)
    before = money_state(database, 101)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    assert money_state(database, 101) == before
    assert settle_outcome(repository, "op-1") == "READY_PAYOUT_DISABLED"
    assert money_state(database, 101) == before


def test_contradicting_version_confirmation_settling_as_conflict_performs_no_payout_in_this_slice(world):
    """@description: A contradicting version confirmation freezes the item as CONFLICT and leaves every money row unchanged.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    database.state["control"]["settlement_enabled"] = False  # [payout switch off]: CONFLICT must never award anyway.
    seed_intake(repository, submission(1))
    approve(service, 1)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    before = money_state(database, 101)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-b", "orig-B", hours=1))
    assert money_state(database, 101) == before
    assert settle_outcome(repository, "op-1") is Status.CONFLICT
    assert money_state(database, 101) == before


def test_refused_version_confirmation_keeping_identity_waiting_performs_no_payout_in_this_slice(world):
    """@description: A refused confirmation with no version leaves an approved item waiting for identity and every money row unchanged.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    database.state["control"]["settlement_enabled"] = False  # [payout switch off]: The refusal must not change money.
    seed_intake(repository, submission(1))
    approve(service, 1)
    before = money_state(database, 101)
    with pytest.raises(ValueError):
        service.preview_version_confirmation(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-c", "orig-A",
                                                                             kind=VersionClaimKind.EDIT),
                                             "clip note", service.view(OWNER_ID, "op-1").review.version)
    assert money_state(database, 101) == before
    assert settle_outcome(repository, "op-1") is Status.WAITING_IDENTITY
    assert money_state(database, 101) == before


def test_no_payout_when_a_formal_reexamination_settles_as_waiting_validation(world):
    """@description: A formal reopen blocks approval and leaves every money row unchanged; the reward waits for validation.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    database.state["control"]["settlement_enabled"] = False  # [payout switch off]: The reopened item must not award.
    seed_intake(repository, submission(1))
    approve(service, 1)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    before = money_state(database, 101)
    revision = service.view(OWNER_ID, "op-1").review.version
    token = service.preview_reexamination(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-re", "orig-A"),
                                          "synthetic reexamination", revision).token_id
    service.confirm(OWNER_ID, token)
    assert money_state(database, 101) == before
    assert settle_outcome(repository, "op-1") is Status.WAITING_VALIDATION
    assert money_state(database, 101) == before


def test_no_payout_when_supplementary_evidence_is_added_and_the_identity_is_not_changed(world):
    """@description: Recording supplementary evidence neither changes identity nor moves money; the decision is READY but payout stays disabled.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    database.state["control"]["settlement_enabled"] = False  # [payout switch off]: Supplementary evidence never awards.
    seed_intake(repository, submission(1))
    approve(service, 1)
    confirm_version(service, 1, owner_evidence(video_of(1), "ev-a", "orig-A"))
    identity_before = service.view(OWNER_ID, "op-1").identity
    before = money_state(database, 101)
    service.add_reexamination_evidence(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-s", "orig-B"),
                                       "synthetic supplementary note", "supplementary-1")
    assert service.view(OWNER_ID, "op-1").identity == identity_before
    assert money_state(database, 101) == before
    assert settle_outcome(repository, "op-1") == "READY_PAYOUT_DISABLED"
    assert money_state(database, 101) == before


def test_version_preview_carries_the_cited_source_reference_and_evidence_id(world):
    """@description: The version-confirmation preview names the evidence record's source and id so the Owner sees exactly what is cited.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    preview = service.preview_version_confirmation(OWNER_ID, "op-1", owner_evidence(video_of(1), "ev-a", "orig-A"),
                                                   "synthetic version confirmation", service.view(OWNER_ID, "op-1").review.version)
    assert preview.evidence_reference == "ev-a"
    assert preview.source_reference == "synthetic-ev-a"


def test_evidence_digest_changes_when_only_the_source_reference_changes(world):
    """@description: Two previews that differ only in the cited source reference carry different evidence fingerprints.
    @param world: Offline service fixture.
    @return: None.
    """
    _, repository, service = world
    seed_intake(repository, submission(1))
    revision = service.view(OWNER_ID, "op-1").review.version
    original = owner_evidence(video_of(1), "ev-a", "orig-A")
    relinked = replace(original, source_reference="synthetic-relinked-ev-a")
    first = service.preview_version_confirmation(OWNER_ID, "op-1", original, "synthetic version confirmation", revision)
    second = service.preview_version_confirmation(OWNER_ID, "op-1", relinked, "synthetic version confirmation", revision)
    assert first.evidence_digest != second.evidence_digest


def test_version_preview_refuses_an_interaction_style_source_reference_before_any_token_or_write(world):
    """@description: A numeric interaction-style source cannot be cited as the traceable origin of a version confirmation.
    @param world: Offline service fixture.
    @return: None.
    """
    database, repository, service = world
    seed_intake(repository, submission(1))
    before = deepcopy(database.state)
    numeric_source = replace(owner_evidence(video_of(1), "ev-a", "orig-A"), source_reference="123456789012345678")
    with pytest.raises(ValueError):
        service.preview_version_confirmation(OWNER_ID, "op-1", numeric_source, "synthetic version confirmation",
                                             service.view(OWNER_ID, "op-1").review.version)
    assert database.state == before
