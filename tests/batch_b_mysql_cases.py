"""Build trusted synthetic music inputs for real application-account repository transactions.

No metadata provider or Discord transport is contacted; read observers use separate validated MySQL connections.
"""

from typing import Any
from bot.database.submission_repository import SubmissionRepository
from bot.database.review_persistence import ReviewCommand
from bot.eligibility import DurationEvidence
from bot.review import ApprovalSource, ReviewSnapshot
from bot.song_identity import VideoIdentity
from bot.version_identity import VersionAssignment, VersionIdentityStatus, RecordingVersionIdentity
from bot.submission_workflow import SubmissionRequest
from tests.batch_b_harness_contract import RunScope
from tests.batch_b_harness_database import verified_connection, read_inventory
from tests.batch_b_mysql_faults import FaultConnection


def request(scope: RunScope, number: int) -> SubmissionRequest:
    """@description: Use predeclared subject identities and explicit synthetic full-duration evidence.
    @param scope: Registered test run, never user-provided song evidence.
    @param number: One of three predeclared requests.
    @return: Pending intake for the production repository.
    """
    operation = scope.run_id + ":" + str(number)  # [registered request]: Database still allocates actual acceptance sequence.
    subject = scope.subjects[operation]
    video = VideoIdentity(subject["video"])
    identity = VersionAssignment(video, VersionIdentityStatus.CONFIRMED, RecordingVersionIdentity(subject["version"]), scope.run_id + ":proof")
    return SubmissionRequest("https://youtu.be/" + video.video_id, operation, subject["user"], 999, identity,
                             ReviewSnapshot(video), 0, DurationEvidence(video, 180, "synthetic-test-only", True, True))


def repository(settings: Any, scope: RunScope, fault: dict | None = None) -> SubmissionRepository:
    """@description: Keep all business transactions on application credentials, including fault-injection cases.
    @param settings: Validated application account credentials.
    @param scope: Registered synthetic Owner and subjects.
    @param fault: Optional one-shot client fault; MySQL itself is never mocked.
    @return: Production repository with worker-owned real connections.
    """
    def factory() -> Any:
        """@description: Open a fresh authenticated application session in its calling worker.
        @param: none
        @return: Real PyMySQL connection or client-fault adapter.
        """
        from tests.batch_b_harness import require_worker_audits
        require_worker_audits(scope.audit_directory, active_allowed=True)
        connection = verified_connection(settings, "jukebox101_test_app")
        try:
            return FaultConnection(connection, fault if fault is not None else {}, scope.audit_directory)
        except BaseException:
            connection.close()
            raise
    return SubmissionRepository(factory, scope.owner_id)


def approve(repo: SubmissionRepository, scope: RunScope, number: int) -> Any:
    """@description: Exercise Owner approval through production duration and revision gates.
    @param repo: Real application repository.
    @param scope: Registered request and synthetic Owner.
    @param number: Previously accepted request.
    @return: Persisted approved review.
    """
    operation = request(scope, number).operation_id
    return repo.review(ReviewCommand(operation + ":approve", operation, scope.owner_id, "synthetic reviewed song", 1,
                                    approval_source=ApprovalSource.OWNER, song_eligible=True))


def observe(settings: Any) -> dict:
    """@description: Read committed facts through a separate real session rather than inspecting a fake store.
    @param settings: Validated application settings.
    @return: Full reviewed inventory, kept inside the test process.
    """
    connection = verified_connection(settings, "jukebox101_test_app")
    try:
        with connection.cursor() as cursor:
            return read_inventory(cursor)
    finally:
        connection.rollback()
        connection.close()


def assert_accounting(rows: dict, awards: int) -> None:
    """@description: Reconcile actual Ledger, balances, unique claims and original/durable receipts.
    @param rows: Fresh committed inventory.
    @param awards: Expected number of +1 test-only settlements.
    @return: None.
    """
    validate_payloads(rows)
    assert len(rows["credit_ledger"]) == len(rows["jb2_claims"]) == len(rows["jb2_settlements"]) == awards
    assert sum(row["credit_balance"] for row in rows["users"]) == awards
    assert sum(row["credit_delta"] for row in rows["credit_ledger"]) == awards
    assert {row["ledger_id"] for row in rows["jb2_claims"]} == {row["ledger_id"] for row in rows["credit_ledger"]}
    assert {row["operation_id"] for row in rows["jb2_events"]} == {row["operation_id"] for row in rows["jb2_receipts"]}
    assert len({row["version_id"] for row in rows["jb2_claims"]}) == awards
    assert all(row["credit_delta"] == 1 and row["reason"] == "submission_award" for row in rows["credit_ledger"])


def validate_payloads(rows: dict) -> None:
    """@description: Preserve corrupted JSON projections or receipts rather than deleting inconsistent persistence evidence.
    @param rows: Full inventory of committed data.
    @return: None; constructors and explicit cross-field checks must all agree.
    """
    from bot.database.persisted_snapshot import decode_current_identity, decode_current_review
    for row in rows["jb2_videos"]:
        decode_current_identity(row)
        decode_current_review(row)
    for row in rows["jb2_receipts"]:
        validate_original_receipt(row, rows)
    for row in rows["jb2_settlements"]:
        validate_settlement_receipt(row)
    for row in rows["jb2_review_history"]:
        validate_review_history(row)
    for row in rows["jb2_evidence"]:
        validate_evidence_payload(row)


def validate_original_receipt(row: dict, rows: dict) -> None:
    """@description: Bind the immutable receipt payload to its actual original accepted event.
    @param row: Stored original receipt.
    @param rows: Complete inventory containing the event authority.
    @return: None.
    """
    from bot.database.submission_codec import decode_contract
    from bot.submission_workflow import SubmissionResult
    from tests.batch_b_harness_contract import HarnessSafetyError
    result = decode_contract(row["receipt_json"], SubmissionResult)  # [original business meaning]: Never recomputed from later review.
    event = next((item for item in rows["jb2_events"] if item["operation_id"] == row["operation_id"]), None)
    if event is None:
        raise HarnessSafetyError("RECEIPT_EVENT_MISSING")
    if (result.candidate.operation_id, result.candidate.submitter_id, result.candidate.identity.video.video_id,
        result.candidate.accepted_sequence, result.video.video_id, result.reward.operation_id, result.create_share_event) != (
            event["operation_id"], event["discord_user_id"], event["video_id"], event["accepted_sequence"], event["video_id"], event["operation_id"], True):
        raise HarnessSafetyError("ORIGINAL_RECEIPT_CONTENT_INCONSISTENCY")


def validate_settlement_receipt(row: dict) -> None:
    """@description: Require the stored settlement payload to attest the same actual awarded operation.
    @param row: Durable settlement row.
    @return: None.
    """
    from bot.database.submission_codec import decode_contract
    from bot.reward_candidates import RewardDecision, RewardCandidateStatus
    from tests.batch_b_harness_contract import HarnessSafetyError
    result = decode_contract(row["result_json"], RewardDecision)
    if result.operation_id != row["operation_id"] or result.status is not RewardCandidateStatus.AWARDED:
        raise HarnessSafetyError("SETTLEMENT_RECEIPT_CONTENT_INCONSISTENCY")


def validate_review_history(row: dict) -> None:
    """@description: Keep historical review JSON subjects and revisions consistent with immutable SQL keys.
    @param row: Actual review history row.
    @return: None.
    """
    from bot.database.submission_codec import decode_contract
    from tests.batch_b_harness_contract import HarnessSafetyError
    review = decode_contract(row["review_json"], ReviewSnapshot)
    if (review.video.video_id, review.version) != (row["video_id"], row["revision"]):
        raise HarnessSafetyError("REVIEW_HISTORY_CONTENT_INCONSISTENCY")


def validate_evidence_payload(row: dict) -> None:
    """@description: Preserve evidence bound to the wrong video instead of treating its parent key as sufficient.
    @param row: Actual evidence snapshot containing identity and review contracts.
    @return: None.
    """
    from bot.database.submission_codec import decode_contract
    from tests.batch_b_harness_contract import HarnessSafetyError
    identity, review = decode_contract(row["evidence_json"], tuple)
    if (identity.video.video_id, review.video.video_id) != (row["video_id"], row["video_id"]):
        raise HarnessSafetyError("EVIDENCE_PAYLOAD_CONTENT_INCONSISTENCY")
