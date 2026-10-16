"""Exercise real repository coordination over a bounded in-memory SQL protocol fake.

These tests validate Python transaction intent and recovery only, never actual MySQL commits or isolation.
"""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from bot.database.review_persistence import ReviewCommand
from bot.database.submission_codec import decode_contract, encode_contract
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown, submission_transaction
from bot.database.reward_settlement import LedgerBalanceMismatch
from bot.database.reward_settlement import _award
from bot.eligibility import DurationEvidence
from bot.review import ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, StaleReviewVersion
from bot.reward_candidates import RewardCandidateStatus as Status
from bot.submission_snapshot import snapshot_is_complete
from bot.submission_workflow import SubmissionRequest, SubmissionResult
from bot.version_identity import VersionExclusion
from tests.submission_persistence_fake import FakeDatabase
from tests.version_candidate_support import make_candidate

OWNER_ID = 777


def intake(sequence: int, version: str | None = "original", seconds: int | None = 180) -> SubmissionRequest:
    """@description: Supply synthetic intake evidence without a caller-approved review or reliable sequence claim.
    @param sequence: Fixture identifier only; the repository must overwrite its asserted sequence.
    @param version: Explicit trusted recording identity or unknown.
    @param seconds: Reliable synthetic full-video duration, or missing evidence.
    @return: Pending operation suitable for the real repository boundary.
    """
    # [trusted internal fixture]: No external source is called to obtain these assertions.
    candidate = make_candidate(sequence, version, review_status=ReviewStatus.PENDING_VALIDATION)
    return SubmissionRequest(f"https://youtu.be/{candidate.identity.video.video_id}", candidate.operation_id,
                             candidate.submitter_id, 999, candidate.identity, candidate.review, 0,
                             DurationEvidence(candidate.identity.video, seconds, "synthetic", True, True) if seconds is not None else None)


def approve(repository: SubmissionRepository, operation_id: str, revision: int = 1) -> ReviewSnapshot:
    """@description: Apply a legitimate Owner approval through the production hard gates.
    @param repository: Repository using only the fake connection factory.
    @param operation_id: Previously accepted synthetic operation.
    @param revision: Current expected review revision.
    @return: Persisted approved review.
    """
    return repository.review(ReviewCommand("review:" + operation_id, operation_id, OWNER_ID,
                                          "synthetic verified song decision", revision,
                                          approval_source=ApprovalSource.OWNER, song_eligible=True))


@pytest.fixture
def persistence() -> tuple[FakeDatabase, SubmissionRepository]:
    """@description: Inject a fully offline connection protocol and a configured Owner.
    @param: none
    @return: Fake state and actual new repository.
    """
    # [offline database]: Tests never replace the domain evaluator or invoke a real driver.
    database = FakeDatabase()
    return database, SubmissionRepository(database.connect, OWNER_ID)


def test_accept_assigns_transaction_order_and_retries_original_receipt(persistence: tuple) -> None:
    """@description: Ignore caller sequence and preserve original receipt after review has advanced.
    @param persistence: Offline fake plus actual repository.
    @return: None.
    """
    database, repository = persistence  # [actual boundary]: Only connection creation is fake.
    original = repository.accept(intake(2))
    assert original.candidate.accepted_sequence == 1
    approve(repository, "op-2")
    retry = repository.accept(replace(intake(2), source_url="https://youtube.com/watch?v=video000002&list=PL&t=3"))
    assert not retry.create_share_event and retry.reward == original.reward
    assert retry.candidate == original.candidate
    assert len(database.state["jb2_events"]) == 1
    assert all(connection.closed for connection in database.connections)


def test_technical_retry_cannot_change_user_or_video(persistence: tuple) -> None:
    """@description: Bind the operation key to the accepted user and video identity.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [original request]: Replays cannot change ownership.
    repository.accept(intake(1))
    with pytest.raises(ValueError):
        repository.accept(replace(intake(1), submitter_id=99))
    with pytest.raises(ValueError):
        repository.accept(replace(intake(1), source_url="https://youtu.be/video000002"))
    assert len(database.state["jb2_events"]) == 1


@pytest.mark.parametrize("failure", ["INSERT INTO jb2_events", "INSERT INTO jb2_receipts", "INSERT INTO jb2_evidence"])
def test_accept_failure_rolls_back_counter_and_all_rows(persistence: tuple, failure: str) -> None:
    """@description: Keep acceptance sequence, event and receipt in one atomic application unit.
    @param persistence: Offline repository fixture.
    @param failure: Statement to fail before commit.
    @return: None.
    """
    database, repository = persistence  # [baseline]: No failed intake may consume priority.
    before = deepcopy(database.state)
    database.sql_fault = failure
    with pytest.raises(ValueError, match="synthetic"):
        repository.accept(intake(1))
    assert database.state == before
    assert repository.accept(intake(2)).candidate.accepted_sequence == 1
    assert all(connection.closed for connection in database.connections)


@pytest.mark.parametrize("fault", ["before", "after"])
def test_unknown_accept_commit_is_resolved_by_same_key(persistence: tuple, fault: str) -> None:
    """@description: Safely replay after either possible outcome of a lost commit acknowledgement.
    @param persistence: Offline repository fixture.
    @param fault: Simulated failure before or after commit became durable.
    @return: None.
    """
    database, repository = persistence  # [ambiguous commit]: Caller must not create a new operation key.
    database.commit_fault = fault
    with pytest.raises(CommitOutcomeUnknown):
        repository.accept(intake(1))
    result = repository.accept(intake(1))
    assert result.create_share_event is (fault == "before")
    assert len(database.state["jb2_events"]) == 1 and database.state["control"]["accepted_sequence"] == 1


def test_simulated_concurrent_acceptance_serializes_and_preserves_pending_priority(persistence: tuple) -> None:
    """@description: Exercise shared-state ordering with bounded worker joins, without claiming InnoDB verification.
    @param persistence: Actual repository over the fake transaction protocol.
    @return: None.
    """
    database, repository = persistence  # [cross-worker fake gate]: Each worker owns a separate connection.
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(repository.accept, intake(number)) for number in (1, 2)]
        results = [future.result(timeout=5) for future in futures]
    assert sorted(result.candidate.accepted_sequence for result in results) == [1, 2]
    earlier, later = sorted(results, key=lambda result: result.candidate.accepted_sequence)
    approve(repository, later.candidate.operation_id)
    assert repository.settle(later.candidate.operation_id).status is Status.WAITING_PREDECESSOR
    approve(repository, earlier.candidate.operation_id)
    assert repository.settle(earlier.candidate.operation_id).status is Status.AWARDED
    assert repository.settle(later.candidate.operation_id).status is Status.INELIGIBLE


def test_rejected_earlier_share_releases_next_claim(persistence: tuple) -> None:
    """@description: Release priority only through explicit review rejection, never a reminder timeout.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [ordered pair]: A pending before B is protected until rejection.
    repository.accept(intake(1))
    repository.accept(intake(2))
    approve(repository, "op-2")
    repository.review(ReviewCommand("reject-a", "op-1", OWNER_ID, "not eligible song", 1, song_eligible=False))
    assert repository.settle("op-2").status is Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1


@pytest.mark.parametrize("failure", ["INSERT INTO credit_ledger", "INSERT INTO jb2_claims", "UPDATE users SET credit_balance",
                                     "UPDATE jb2_events SET reward_status", "INSERT INTO jb2_settlements"])
def test_settlement_failures_rollback_every_accounting_write(persistence: tuple, failure: str) -> None:
    """@description: Reject partial claim, ledger, balance or receipt settlement states.
    @param persistence: Offline repository fixture.
    @param failure: Production statement prefix to reject.
    @return: None.
    """
    database, repository = persistence  # [approved claim]: No precomputed READY is supplied by the caller.
    repository.accept(intake(1))
    approve(repository, "op-1")
    before = deepcopy(database.state)
    database.sql_fault = failure
    with pytest.raises(ValueError, match="synthetic"):
        repository.settle("op-1")
    assert database.state == before
    assert repository.settle("op-1").status is Status.AWARDED
    assert database.state["users"][1]["credit_balance"] == 1


@pytest.mark.parametrize("fault", ["before", "after"])
def test_unknown_reward_commit_never_credits_twice(persistence: tuple, fault: str) -> None:
    """@description: Resolve uncertain settlement commits through the durable operation result.
    @param persistence: Offline repository fixture.
    @param fault: Commit failure position.
    @return: None.
    """
    database, repository = persistence  # [qualified intake]: Settlement runs the real evaluator again.
    repository.accept(intake(1))
    approve(repository, "op-1")
    database.commit_fault = fault
    with pytest.raises(CommitOutcomeUnknown):
        repository.settle("op-1")
    assert repository.settle("op-1").status is Status.AWARDED
    assert repository.settle("op-1").status is Status.AWARDED
    assert len(database.state["jb2_claims"]) == len(database.state["credit_ledger"]) == 1
    assert database.state["users"][1]["credit_balance"] == 1


def test_simulated_concurrent_settlement_uses_one_global_version_claim(persistence: tuple) -> None:
    """@description: Exercise competing settlement calls without Python locks in production repository code.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [same recording]: MV and audio fixtures have different video IDs.
    for number in (1, 2):
        repository.accept(intake(number))
        approve(repository, f"op-{number}")
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(repository.settle, ("op-2", "op-1"), timeout=5))
    assert results[0].status in (Status.WAITING_PREDECESSOR, Status.INELIGIBLE)
    assert results[1].status is Status.AWARDED
    assert len(database.state["jb2_claims"]) == len(database.state["credit_ledger"]) == 1


def test_different_trusted_remixes_can_settle_independently(persistence: tuple) -> None:
    """@description: Keep explicitly evidenced arrangements separate rather than relying on video titles.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [trusted different arrangements]: No automatic matching is introduced.
    for number, version in ((1, "original"), (2, "official-edm-remix")):
        repository.accept(intake(number, version))
        approve(repository, f"op-{number}")
        assert repository.settle(f"op-{number}").status is Status.AWARDED
    assert len(database.state["jb2_claims"]) == 2


def test_unknown_legacy_and_unready_snapshot_block_rewards(persistence: tuple) -> None:
    """@description: Keep unknown old songs and incomplete repository certification from reopening first rewards.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [unmapped old catalog]: No historical event order is fabricated.
    database.state["songs"]["video999999"] = {"song_id": 50, "platform_track_id": "video999999"}
    repository.accept(intake(1))
    approve(repository, "op-1")
    assert repository.settle("op-1").status is Status.WAITING_IDENTITY
    assert len(database.state["jb2_events"]) == 1
    database.state["control"]["snapshot_ready"] = False
    assert not snapshot_is_complete(repository.snapshot())
    assert repository.settle("op-1").status is Status.WAITING_PREDECESSOR


def test_known_legacy_mapping_never_receives_retroactive_reward(persistence: tuple) -> None:
    """@description: Retain legacy catalog membership after Owner confirms its recording identity.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [existing catalog song]: New share does not make it a new reward identity.
    database.state["songs"]["video000001"] = {"song_id": 50, "platform_track_id": "video000001"}
    repository.accept(intake(1))
    repository.review(ReviewCommand("legacy-map", "op-1", OWNER_ID, "confirmed historical recording", 1,
                                    identity=intake(1).identity, approval_source=ApprovalSource.OWNER, song_eligible=True))
    assert repository.settle("op-1").status is Status.INELIGIBLE
    assert not database.state["credit_ledger"]


def test_owner_revision_and_hard_gates_are_enforced_before_overwrite(persistence: tuple) -> None:
    """@description: Reject unauthorized actors, stale decisions and attempts to approve unknown duration.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [pending without evidence]: Owner cannot bypass hard qualification.
    repository.accept(intake(1, seconds=None))
    count = len(database.connections)
    with pytest.raises(PermissionError):
        repository.review(ReviewCommand("bad-owner", "op-1", 123, "unauthorized", 0))
    assert len(database.connections) == count
    with pytest.raises(StaleReviewVersion):
        approve(repository, "op-1", revision=99)
    with pytest.raises(InvalidReviewTransition):
        approve(repository, "op-1", revision=0)
    assert not database.state["jb2_decisions"]


def test_review_decision_replay_is_bound_to_original_intent(persistence: tuple) -> None:
    """@description: Reuse a decision result without appending a second audit entry or changing its intent.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [stable review key]: Original revision remains valid only for exact replay.
    repository.accept(intake(1))
    first = approve(repository, "op-1")
    assert approve(repository, "op-1") == first
    with pytest.raises(ValueError, match="different intent"):
        repository.review(ReviewCommand("review:op-1", "op-1", OWNER_ID, "different reason", 1))
    assert len(database.state["jb2_decisions"]) == 1


def test_merge_preserves_both_ledgers_and_records_historical_exception(persistence: tuple) -> None:
    """@description: Preserve already issued Nickel when later evidence merges two rewarded versions.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [two previously distinct claims]: Both were valid under prior trusted identity.
    for number, version in ((1, "recording-a"), (2, "recording-b")):
        repository.accept(intake(number, version))
        approve(repository, f"op-{number}")
        repository.settle(f"op-{number}")
    ledger = deepcopy(database.state["credit_ledger"])
    result = repository.merge("merge-1", OWNER_ID, "confirmed same recording", "recording-a", "recording-b")
    assert repository.merge("merge-1", OWNER_ID, "confirmed same recording", "recording-a", "recording-b") == result
    assert database.state["credit_ledger"] == ledger and len(database.state["jb2_claims"]) == 2
    assert len(database.state["jb2_exceptions"]) == 1
    repository.accept(intake(3, "recording-a"))
    approve(repository, "op-3")
    assert repository.settle("op-3").status in (Status.CONFLICT, Status.INELIGIBLE)
    assert len(database.state["credit_ledger"]) == 2


def test_conflicting_evidence_freezes_and_does_not_revoke_awards(persistence: tuple) -> None:
    """@description: Freeze affected unawarded candidates while preserving durable prior settlement receipts.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [prior award]: Original payment must remain immutable.
    repository.accept(intake(1))
    approve(repository, "op-1")
    repository.settle("op-1")
    repository.accept(intake(2))
    approve(repository, "op-2")
    proof = VersionExclusion(intake(1).identity.video, intake(1).identity.version, "contradictory-proof")
    repository.exclude("proof-action", OWNER_ID, "evidence conflict", proof)
    assert repository.settle("op-2").status is Status.CONFLICT
    assert repository.settle("op-1").status is Status.AWARDED
    assert database.state["users"][1]["credit_balance"] == 1


def test_reminders_begin_after_24_hours_and_repeat_by_utc_day(persistence: tuple) -> None:
    """@description: Persist daily reminder intent without expiring priority or sending a message.
    @param persistence: Offline repository fixture with a synthetic database clock.
    @return: None.
    """
    database, repository = persistence  # [accepted pending operation]: First due time is database intake plus 24h.
    repository.accept(intake(1))
    original_sequence = database.state["jb2_events"]["op-1"]["accepted_sequence"]
    database.now += timedelta(hours=24) - timedelta(microseconds=1)
    assert repository.pending_reminders(OWNER_ID) == ()
    database.now += timedelta(microseconds=1)
    assert repository.pending_reminders(OWNER_ID) == ("op-1",)
    assert repository.pending_reminders(OWNER_ID) == ("op-1",)
    database.now += timedelta(days=1)
    assert repository.pending_reminders(OWNER_ID) == ("op-1",)
    assert len(database.state["jb2_reminder_batches"]) == 2
    assert database.state["jb2_events"]["op-1"]["accepted_sequence"] == original_sequence


def test_balance_mismatch_and_disabled_settlement_fail_closed(persistence: tuple) -> None:
    """@description: Refuse existing accounting drift and unapproved real settlement activation.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [approved candidate]: Neither policy nor accounting safeguards are bypassed.
    repository.accept(intake(1))
    approve(repository, "op-1")
    database.state["users"][1]["credit_balance"] = 10
    with pytest.raises(LedgerBalanceMismatch):
        repository.settle("op-1")
    database.state["control"]["settlement_enabled"] = False
    with pytest.raises(PermissionError):
        repository.settle("op-1")
    assert not database.state["credit_ledger"]


def test_receipt_codec_roundtrip_and_unknown_type_rejection(persistence: tuple) -> None:
    """@description: Revalidate immutable original receipts without unpickling or dynamic imports.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [receipt roundtrip]: Includes enums, exact evidence and tuple blockers.
    result = repository.accept(intake(1))
    assert decode_contract(encode_contract(result), SubmissionResult) == result
    with pytest.raises(ValueError):
        decode_contract('{"codec":1,"value":{"contract":"os.system","fields":{}}}', SubmissionResult)


def test_rollback_and_close_failures_preserve_primary_exception() -> None:
    """@description: Preserve the primary failure while still attempting both cleanup operations.
    @param: none
    @return: None.
    """
    connection = MagicMock()  # [failing cleanup boundary]: No driver or socket exists.
    connection.cursor.return_value.__enter__.return_value.fetchone.return_value = {"schema_version": "2b2-b1"}
    connection.rollback.side_effect = OSError("synthetic rollback failure")
    connection.close.side_effect = OSError("synthetic close failure")
    with pytest.raises(ValueError, match="primary") as captured:
        with submission_transaction(lambda: connection):
            raise ValueError("primary")
    assert len(captured.value.__notes__) == 2
    connection.close.assert_called_once()


def test_active_same_video_repeat_after_award_keeps_new_event(persistence: tuple) -> None:
    """@description: Keep deliberate repeat shares separate from technical retries even after terminal approval.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [already awarded video]: Fresh sharing must not try to reopen its review.
    repository.accept(intake(1))
    approve(repository, "op-1")
    repository.settle("op-1")
    result = repository.accept(replace(intake(1), operation_id="deliberate-repeat"))
    assert result.create_share_event and result.reward.status is Status.INELIGIBLE
    assert len(database.state["jb2_events"]) == 2 and len(database.state["credit_ledger"]) == 1


def test_missing_join_row_or_counter_watermark_never_certifies_complete(persistence: tuple) -> None:
    """@description: Detect incomplete SQL projections independently of their filtered joined rows.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [complete accepted row]: Removing its join target simulates projection corruption.
    repository.accept(intake(1))
    assert snapshot_is_complete(repository.snapshot())
    video = database.state["jb2_videos"].pop("video000001")
    assert not snapshot_is_complete(repository.snapshot())
    database.state["jb2_videos"]["video000001"] = video
    database.state["control"]["accepted_sequence"] = 0
    assert not snapshot_is_complete(repository.snapshot())


def test_duplicate_version_claim_constraint_rolls_back_provisional_ledger(persistence: tuple) -> None:
    """@description: Exercise the unique-claim backstop even if a stale internal award path attempts a second insert.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [existing global claim]: Simulate the constraint winning after an invalid stale attempt.
    for number in (1, 2):
        repository.accept(intake(number))
        approve(repository, f"op-{number}")
    repository.settle("op-1")
    before = deepcopy(database.state)
    with pytest.raises(ValueError, match="duplicate primary"):
        with submission_transaction(database.connect) as (cursor, control):
            _award(cursor, control, "op-2")
    assert database.state == before


def test_identity_only_decision_advances_review_revision(persistence: tuple) -> None:
    """@description: Reject stale evidence decisions even when the earlier action changed identity without review status.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [unknown identity]: Owner confirmation is a versioned decision too.
    repository.accept(intake(1, None))
    confirmed = intake(1).identity
    result = repository.review(ReviewCommand("identity-confirm", "op-1", OWNER_ID, "trusted recording proof", 1, identity=confirmed))
    assert result.version == 2 and result.status is ReviewStatus.PENDING_REVIEW
    with pytest.raises(StaleReviewVersion):
        repository.review(ReviewCommand("stale-identity", "op-1", OWNER_ID, "stale worker result", 1, identity=confirmed))


def test_awarded_mapping_change_preserves_original_claim_and_blocks_both_versions(persistence: tuple) -> None:
    """@description: Preserve paid-version provenance when later evidence contradicts a video's recording mapping.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [immutable original payment]: Identity correction cannot move its ledger.
    repository.accept(intake(1))
    approve(repository, "op-1")
    repository.settle("op-1")
    changed = replace(intake(2, "different").identity, video=intake(1).identity.video)
    repository.review(ReviewCommand("mapping-correction", "op-1", OWNER_ID, "conflicting new recording proof", 2, identity=changed))
    for number, version in ((3, "original"), (4, "different")):
        repository.accept(intake(number, version))
        approve(repository, f"op-{number}")
        assert repository.settle(f"op-{number}").status in (Status.INELIGIBLE, Status.CONFLICT)
    assert repository.settle("op-1").status is Status.AWARDED
    assert len(database.state["credit_ledger"]) == 1


def test_review_audits_each_validation_and_approval_revision(persistence: tuple) -> None:
    """@description: Keep both intermediate validation and final approval evidence revisions durable.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [unvalidated intake]: One Owner command can validate then approve atomically.
    repository.accept(intake(1, seconds=None))
    evidence = DurationEvidence(intake(1).identity.video, 180, "synthetic-owner-proof", True, True)
    result = repository.review(ReviewCommand("validate-approve", "op-1", OWNER_ID, "trusted song evidence", 0,
                                           duration=evidence, approval_source=ApprovalSource.OWNER, song_eligible=True))
    assert result.version == 2
    assert set(database.state["jb2_review_history"]) == {("video000001", 0), ("video000001", 1), ("video000001", 2)}


@pytest.mark.parametrize("source", list(ApprovalSource))
@pytest.mark.parametrize("seconds", [59, 721])
def test_persisted_approvals_cannot_bypass_duration_limits(persistence: tuple, source: ApprovalSource, seconds: int) -> None:
    """@description: Apply inclusive hard qualification to both persisted approval paths.
    @param persistence: Offline repository fixture.
    @param source: AUTO or OWNER trusted approval path.
    @param seconds: Reliable full-video duration outside the valid range.
    @return: None.
    """
    database, repository = persistence  # [rejected hard duration]: No Owner override policy exists.
    repository.accept(intake(1, seconds=seconds))
    with pytest.raises(InvalidReviewTransition):
        repository.review(ReviewCommand("invalid-approve", "op-1", OWNER_ID, "attempted hard-gate bypass", 1,
                                       approval_source=source, song_eligible=True))
    assert not database.state["credit_ledger"]


def test_approved_review_with_unresolved_identity_still_gets_pending_summary(persistence: tuple) -> None:
    """@description: Keep identity-pending and conflict cases visible even when video review itself is approved.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [identity unresolved]: Duration and song approval do not settle recording identity.
    repository.accept(intake(1, None))
    approve(repository, "op-1")
    repository.accept(intake(2, "known-different"))
    approve(repository, "op-2")
    database.now += timedelta(hours=24)
    assert repository.pending_reminders(OWNER_ID) == ("op-1",)
    assert database.state["jb2_reminders"]["video000002"]["last_summary_date"] is None


@pytest.mark.parametrize("fault,expected", [("before", [2, 1]), ("after", [1, 2])])
def test_other_intake_between_uncertain_commit_and_replay_has_fair_order(persistence: tuple, fault: str, expected: list[int]) -> None:
    """@description: Order only durable successful acceptances when another user arrives before an uncertain retry.
    @param persistence: Offline repository fixture.
    @param fault: Whether A actually committed before losing its acknowledgement.
    @param expected: Final acceptance sequences for A and B respectively.
    @return: None.
    """
    database, repository = persistence  # [ambiguous first request]: No order is promised for a rolled-back intake.
    database.commit_fault = fault
    with pytest.raises(CommitOutcomeUnknown):
        repository.accept(intake(1))
    second = repository.accept(intake(2))
    first = repository.accept(intake(1))
    assert [first.candidate.accepted_sequence, second.candidate.accepted_sequence] == expected


def test_old_ledger_duplicates_are_audited_without_fabricating_claims(persistence: tuple) -> None:
    """@description: Preserve and flag old six-table awards once Owner evidence confirms a shared recording.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [preexisting real-model facts]: Synthetic fixtures represent immutable old rows.
    for number in (1, 2):
        database.state["songs"][f"video{number:06d}"] = {"song_id": number, "platform_track_id": f"video{number:06d}"}
        database.state["users"][number] = {"discord_user_id": number, "credit_balance": 1}
        database.state["credit_ledger"][number] = {"ledger_id": number, "discord_user_id": number, "credit_delta": 1,
            "reason": "submission_award", "idempotency_key": f"old-award-{number}", "awarded_song_id": number}
    original = deepcopy(database.state["credit_ledger"])
    for number in (1, 2):
        repository.accept(intake(number))
        repository.review(ReviewCommand(f"old-confirm-{number}", f"op-{number}", OWNER_ID, "confirmed old recording", 1,
                                        identity=intake(number).identity, approval_source=ApprovalSource.OWNER, song_eligible=True))
    assert len(database.state["jb2_exceptions"]) == 1
    assert not database.state["jb2_claims"] and database.state["credit_ledger"] == original
    assert len(database.state["jb2_events"]) == 2
    assert repository.settle("op-2").status is Status.INELIGIBLE


@pytest.mark.parametrize("field,value", [("review_revision", 99), ("review_status", "approved")])
def test_mixed_review_projection_is_rejected(persistence: tuple, field: str, value: object) -> None:
    """@description: Refuse completeness certification when JSON and SQL current-review columns disagree.
    @param persistence: Offline repository fixture.
    @param field: Corrupted denormalized projection field.
    @param value: Conflicting synthetic value.
    @return: None.
    """
    database, repository = persistence  # [inconsistent persisted projection]: Never select the more favorable representation.
    repository.accept(intake(1))
    database.state["jb2_videos"]["video000001"][field] = value
    with pytest.raises(ValueError, match="projection"):
        repository.snapshot()


def test_missing_migration_blocks_and_still_closes_connection(persistence: tuple) -> None:
    """@description: Refuse business writes when the reviewed migration marker is unavailable.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [unprepared schema]: No automatic migration is attempted.
    database.state["control"]["schema_version"] = "unprepared"
    with pytest.raises(RuntimeError, match="migration"):
        repository.accept(intake(1))
    assert not database.state["jb2_events"]
    assert all(connection.closed for connection in database.connections)


def test_reminder_batch_does_not_silently_change_owner_target(persistence: tuple) -> None:
    """@description: Preserve the original reminder target when deployment Owner configuration changes mid-day.
    @param persistence: Offline repository fixture.
    @return: None.
    """
    database, repository = persistence  # [original Owner summary]: Reservation is an auditable intent, not delivery.
    repository.accept(intake(1))
    database.now += timedelta(hours=24)
    assert repository.pending_reminders(OWNER_ID) == ("op-1",)
    replacement = SubmissionRepository(database.connect, OWNER_ID + 1)
    with pytest.raises(PermissionError, match="target changed"):
        replacement.pending_reminders(OWNER_ID + 1)
