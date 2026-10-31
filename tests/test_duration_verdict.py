"""Offline contract of the API duration verdict: a pass/reject conclusion without seconds, and the unchanged encoding of every snapshot without one.

The verdict lets the review keep its hard duration gate while the raw API value lives only in the short-lived observation table.
"""

import json

import pytest

from bot.database.review_persistence import ReviewCommand, review_command_digest
from bot.database.submission_codec import decode_contract, encode_contract
from bot.eligibility import DurationEligibility, DurationEvidence
from bot.review import (
    ApprovalSource, DurationVerdict, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, approve_review, record_duration_verdict,
    record_validation_error, ValidationError,
)
from bot.song_identity import VideoIdentity

VIDEO = VideoIdentity("aaaaaaaaaaa")
OTHER = VideoIdentity("bbbbbbbbbbb")
OWNER_EVIDENCE = DurationEvidence(VIDEO, 189, "owner-check", True, True)
# [frozen encodings]: Captured from the code before the verdict existed; a snapshot without a verdict must keep these exact bytes.
GOLDEN_PLAIN = ('{"codec":1,"value":{"contract":"ReviewSnapshot","fields":{"approval_source":null,"duration":null,"ordering_sequence":null,'
                '"priority_released":false,"reexamination_pending":false,"status":{"enum":"ReviewStatus","value":"pending_validation"},'
                '"validation_error":null,"version":0,"video":{"contract":"VideoIdentity","fields":{"video_id":"aaaaaaaaaaa"}}}}}')
GOLDEN_DIGEST = "4fd0381c317935ad53984bc5af31fc0a123d3f7a9a960017812f608123c35ead"


def passing() -> DurationVerdict:
    """@description: Build a passing verdict for the test video.
    @param: none
    @return: Verdict.
    """
    return DurationVerdict(VIDEO, DurationEligibility.PASS)


def test_snapshot_without_a_verdict_keeps_its_exact_encoding():
    """@description: Rows written by the new code stay readable by the previous release unless a verdict is present.
    @param: none
    @return: None.
    """
    assert encode_contract(ReviewSnapshot(VIDEO)) == GOLDEN_PLAIN


def test_command_digest_without_a_verdict_is_unchanged():
    """@description: Decisions recorded before the verdict existed still replay against the same digest.
    @param: none
    @return: None.
    """
    command = ReviewCommand("d1", "op1", 0, "reason", 0, duration=DurationEvidence(VIDEO, 189, "src", True, True))
    assert review_command_digest(command) == GOLDEN_DIGEST


def test_verdict_round_trips_through_the_codec_and_carries_no_seconds():
    """@description: The persisted verdict names the outcome only; no duration value, channel or time is encoded.
    @param: none
    @return: None.
    """
    snapshot = record_duration_verdict(ReviewSnapshot(VIDEO), passing(), 0)
    text = encode_contract(snapshot)
    assert decode_contract(text, ReviewSnapshot) == snapshot
    assert "seconds" not in text and "channel" not in text and "fetched" not in text


def test_pass_verdict_opens_owner_review_and_reject_verdict_rejects():
    """@description: The verdict follows the same transitions as duration evidence.
    @param: none
    @return: None.
    """
    assert record_duration_verdict(ReviewSnapshot(VIDEO), passing(), 0).status is ReviewStatus.PENDING_REVIEW
    rejected = record_duration_verdict(ReviewSnapshot(VIDEO), DurationVerdict(VIDEO, DurationEligibility.REJECT), 0)
    assert rejected.status is ReviewStatus.REJECTED and rejected.priority_released is True


def test_verdict_for_another_video_is_refused():
    """@description: A conclusion about one video cannot open the review of another.
    @param: none
    @return: None.
    """
    with pytest.raises(InvalidReviewTransition):
        record_duration_verdict(ReviewSnapshot(VIDEO), DurationVerdict(OTHER, DurationEligibility.PASS), 0)


def test_only_pass_or_reject_is_a_verdict():
    """@description: A pending outcome is no conclusion, so it cannot be stored as one.
    @param: none
    @return: None.
    """
    with pytest.raises(InvalidReviewTransition):
        DurationVerdict(VIDEO, DurationEligibility.PENDING_VALIDATION)


def test_pending_review_without_any_duration_basis_is_still_refused():
    """@description: The hard gate is unchanged: no evidence and no verdict cannot be a pending review or an approval.
    @param: none
    @return: None.
    """
    with pytest.raises(InvalidReviewTransition):
        ReviewSnapshot(VIDEO, 1, ReviewStatus.PENDING_REVIEW)


def test_a_passing_verdict_alone_supports_approval():
    """@description: The owner approves on the verdict exactly as on evidence, and the snapshot stays approved.
    @param: none
    @return: None.
    """
    pending = record_duration_verdict(ReviewSnapshot(VIDEO), passing(), 0)
    approved = approve_review(pending, ApprovalSource.OWNER, 1, song_eligible=True)
    assert approved.status is ReviewStatus.APPROVED and approved.duration is None and approved.duration_verdict == passing()


def test_failed_validation_drops_the_verdict():
    """@description: A validation error removes every usable duration basis, verdict included.
    @param: none
    @return: None.
    """
    pending = record_duration_verdict(ReviewSnapshot(VIDEO), passing(), 0)
    failed = record_validation_error(pending, ValidationError.METADATA_UNAVAILABLE, 1)
    assert failed.duration_verdict is None


def test_snapshot_encoded_before_the_verdict_existed_decodes():
    """@description: Old rows have no verdict field and must decode with the default.
    @param: none
    @return: None.
    """
    document = json.loads(GOLDEN_PLAIN)
    assert "duration_verdict" not in document["value"]["fields"]
    assert decode_contract(GOLDEN_PLAIN, ReviewSnapshot).duration_verdict is None
