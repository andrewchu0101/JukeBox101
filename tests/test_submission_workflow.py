"""Exercise complete submission coordination with synthetic trusted intake and evidence.

Manifest fixtures simulate repository guarantees explicitly; no test performs persistence or provider calls.
"""

from dataclasses import replace
from random import Random

import pytest

from bot.eligibility import DurationEvidence
from bot.review import ApprovalSource, InvalidReviewTransition, ReviewSnapshot, ReviewStatus, StaleReviewVersion, ValidationError
from bot.reward_candidates import RewardCandidate, RewardCandidateStatus as Status, RewardDecisionReason as Reason
from bot.share_replies import APPROVED_SHARE_REPLIES, APPROVED_SHARE_REPLY_POOLS, ShareScenario, select_share_reply
from bot.submission_snapshot import SnapshotManifest, SubmissionSnapshot, aggregate_snapshot, snapshot_is_complete
from bot.submission_workflow import SubmissionRequest, submit_offline
from bot.urls import InvalidVideoUrl
from bot.version_identity import VersionAssignment, VersionExclusion, VersionIdentityStatus
from tests.version_candidate_support import make_candidate


def request_for(candidate: RewardCandidate) -> SubmissionRequest:
    """@description: Simulate a trusted intake request without deriving identity from its URL.
    @param candidate: Explicit synthetic intake, identity and review facts.
    @return: Request preserving the current review version.
    """
    return SubmissionRequest(f"https://youtu.be/{candidate.identity.video.video_id}?t=10&list=PLtest",
                             candidate.operation_id, candidate.submitter_id, candidate.accepted_sequence,
                             candidate.identity, candidate.review, candidate.review.version)


def snapshot_for(current: RewardCandidate, *earlier: RewardCandidate,
                 historical: tuple[VersionAssignment, ...] = (), certified: bool = True) -> SubmissionSnapshot:
    """@description: Explicitly simulate an independent repository manifest for the complete synthetic universe.
    @param current: Operation already assigned its synthetic successful-intake sequence.
    @param earlier: All existing operation rows in the fixture universe.
    @param historical: All synthetic legacy video assertions, including unresolved ones.
    @param certified: Whether the fixture certifies global coverage.
    @return: Existing facts plus a manifest that expects the current operation after aggregation.
    """
    # [synthetic scope certificate]: Real repositories must not manufacture this list from filtered result rows.
    manifest = SnapshotManifest("revision-1", frozenset(item.operation_id for item in (*earlier, current)),
                                frozenset(item.video.video_id for item in historical),
                                max(item.accepted_sequence for item in (*earlier, current)), certified, len(historical))
    return SubmissionSnapshot(manifest, "revision-1", earlier, historical)


@pytest.mark.parametrize("source", list(ApprovalSource))
@pytest.mark.parametrize("seconds,expected", [(59, Status.INELIGIBLE), (60, Status.READY), (61, Status.READY),
                                             (719, Status.READY), (720, Status.READY), (721, Status.INELIGIBLE),
                                             (None, Status.WAITING_VALIDATION)])
def test_full_url_duration_review_workflow(source: ApprovalSource, seconds: int | None, expected: Status) -> None:
    """@description: Run normalization and both review paths through exact full-video duration gates.
    @param source: Trusted synthetic approval path, not an authorization proof.
    @param seconds: Exact synthetic original-video duration.
    @param expected: Offline reward result after legitimate transitions.
    @return: None.
    """
    # [new intake]: No preapproved review is needed for the full validation path.
    candidate = make_candidate(1, review_status=ReviewStatus.PENDING_VALIDATION)
    # [trusted duration]: URL timestamps never replace full original-video duration.
    evidence = DurationEvidence(candidate.identity.video, seconds, "synthetic-duration", True, True)
    # [review action]: Failed/unknown duration remains pending or rejected without attempting approval.
    request = replace(request_for(candidate), duration=evidence,
                      approval_source=source if seconds is not None and 60 <= seconds <= 720 else None,
                      song_eligible=True if seconds is not None and 60 <= seconds <= 720 else None)
    result = submit_offline(request, snapshot_for(candidate))  # [workflow result]: Pure decision only.
    assert result.reward.status is expected
    assert result.video.canonical_url == "https://www.youtube.com/watch?v=video000001"
    assert result.create_share_event


@pytest.mark.parametrize("earlier_status,expected", [(ReviewStatus.PENDING_VALIDATION, Status.WAITING_PREDECESSOR),
                                                    (ReviewStatus.VALIDATION_FAILED, Status.WAITING_PREDECESSOR),
                                                    (ReviewStatus.REJECTED, Status.READY)])
def test_accepted_order_survives_review_speed(earlier_status: ReviewStatus, expected: Status) -> None:
    """@description: Protect earlier pending submissions and release only explicit rejection.
    @param earlier_status: Earlier claimant's current trusted review state.
    @param expected: Later claimant's scheduling outcome.
    @return: None.
    """
    # [queue facts]: B is already approved but cannot overtake A merely through review speed.
    earlier, current = make_candidate(1, review_status=earlier_status), make_candidate(2)
    assert submit_offline(request_for(current), snapshot_for(current, earlier)).reward.status is expected


@pytest.mark.parametrize("version,awarded,expected", [("original", False, Status.WAITING_PREDECESSOR),
                                                     ("original", True, Status.INELIGIBLE),
                                                     ("official-remix", False, Status.READY)])
def test_recording_aliases_and_trusted_remixes(version: str, awarded: bool, expected: Status) -> None:
    """@description: Share first-reward identity across MV/audio while preserving evidenced different remixes.
    @param version: Trusted current recording identity, never inferred from title text.
    @param awarded: Externally supplied prior settlement fact.
    @param expected: Current scheduling decision.
    @return: None.
    """
    # [recording evidence]: Distinct video IDs can explicitly represent the same recording.
    earlier, current = make_candidate(1, awarded=awarded), make_candidate(2, version)
    result = submit_offline(request_for(current), snapshot_for(current, earlier))  # [alias outcome]: No new payment.
    assert result.reward.status is expected
    assert result.scenario is (ShareScenario.SAME_VERSION_DIFFERENT_VIDEO if version == "original" else None)


@pytest.mark.parametrize("historical_version,expected", [("original", Status.INELIGIBLE),
                                                        ("different", Status.READY), (None, Status.WAITING_IDENTITY)])
def test_legacy_catalog_keeps_known_and_unknown_versions(historical_version: str | None, expected: Status) -> None:
    """@description: Prevent unresolved historical recordings from being optimistically treated as novel.
    @param historical_version: Known or unknown legacy recording identity.
    @param expected: Conservative reward result.
    @return: None.
    """
    # [catalog history]: A legacy video has no invented submission sequence or reward recipient.
    legacy, current = make_candidate(1, historical_version), make_candidate(2)
    assert submit_offline(request_for(current), snapshot_for(current, historical=(legacy.identity,))).reward.status is expected


def test_unknown_legacy_requires_scoped_trusted_exclusion() -> None:
    """@description: Release an unknown catalog blocker only with evidence excluding the target version.
    @param: none
    @return: None.
    """
    # [legacy uncertainty]: Merely being a different video does not prove a different recording.
    legacy, current = make_candidate(1, None), make_candidate(2)
    # [certified history]: Proven exclusion is retained independently of positive identity.
    snapshot = snapshot_for(current, historical=(legacy.identity,))
    snapshot = replace(snapshot, exclusions=(VersionExclusion(legacy.identity.video, current.identity.version, "proof"),),
                       manifest=replace(snapshot.manifest, exclusion_references=frozenset({"proof"})))
    assert submit_offline(request_for(current), snapshot).reward.status is Status.READY


@pytest.mark.parametrize("omission", ["predecessor", "catalog", "revision", "certificate", "watermark", "current"])
def test_missing_scope_never_becomes_ready(omission: str) -> None:
    """@description: Detect missing manifest entries and never infer completeness from the remaining rows.
    @param omission: Missing row or invalid repository coverage assertion.
    @return: None.
    """
    # [independent scope]: Manifest still names the absent facts even when supplied rows omit them.
    earlier, current = make_candidate(1, "different"), make_candidate(2)
    snapshot = snapshot_for(current, earlier, historical=(earlier.identity,))
    # [incomplete variants]: Each case independently corrupts one part of the certified scope.
    snapshots = {
        "predecessor": replace(snapshot, candidates=()),
        "catalog": replace(snapshot, historical=()),
        "revision": replace(snapshot, revision="stale"),
        "certificate": replace(snapshot, manifest=replace(snapshot.manifest, coverage_certified=False)),
        "watermark": replace(snapshot, manifest=replace(snapshot.manifest, through_sequence=1)),
        "current": replace(snapshot, manifest=replace(snapshot.manifest, operation_ids=frozenset({earlier.operation_id}))),
    }
    assert submit_offline(request_for(current), snapshots[omission]).reward.reason is Reason.INCOMPLETE_SNAPSHOT


def test_retry_preserves_original_outcome_and_creates_no_event() -> None:
    """@description: Replay the trusted receipt rather than allowing retry evidence to rewrite business results.
    @param: none
    @return: None.
    """
    # [original pending result]: A later review input cannot change what this operation originally returned.
    candidate = make_candidate(1, review_status=ReviewStatus.PENDING_VALIDATION)
    original = submit_offline(request_for(candidate), snapshot_for(candidate))
    # [retry scope]: Existing operation is already present in the trusted current projection.
    snapshot = replace(snapshot_for(candidate), candidates=(candidate, candidate))
    retry = submit_offline(replace(request_for(candidate), expected_review_version=999), snapshot, original_result=original)
    assert not retry.create_share_event
    assert retry.reward == original.reward and retry.candidate == original.candidate
    assert retry.scenario is ShareScenario.RETRY_SAME_OPERATION
    assert retry.reply == APPROVED_SHARE_REPLIES[ShareScenario.RETRY_SAME_OPERATION]
    with pytest.raises(ValueError, match="original result"):
        submit_offline(request_for(candidate), snapshot)


def test_active_repeat_retains_event_without_second_reward() -> None:
    """@description: Retain deliberate new shares as events while honoring an existing first-version award.
    @param: none
    @return: None.
    """
    # [existing settlement]: This fact is input only; tests never write a ledger.
    earlier = make_candidate(1, awarded=True)
    current = make_candidate(2, video_id=earlier.identity.video.video_id, submitter_id=earlier.submitter_id)
    result = submit_offline(request_for(current), snapshot_for(current, earlier))  # [new share]: Not a technical replay.
    assert result.create_share_event and result.scenario is ShareScenario.DUPLICATE_SELF
    assert result.reward.status is Status.INELIGIBLE


@pytest.mark.parametrize("historical", [False, True])
def test_conflicting_identity_freezes_unsettled_workflow(historical: bool) -> None:
    """@description: Preserve conflicting evidence from either live candidates or the legacy catalog.
    @param historical: Whether the conflicting fact comes from legacy history.
    @return: None.
    """
    # [trusted conflict]: Explicit contradictory evidence is not treated as a confirmed different remix.
    conflict = make_candidate(1, identity_status=VersionIdentityStatus.CONFLICT)
    current = make_candidate(2)
    snapshot = snapshot_for(current, historical=(conflict.identity,)) if historical else snapshot_for(current, conflict)
    assert submit_offline(request_for(current), snapshot).reward.status is Status.CONFLICT


def test_repeated_evaluation_is_identical_and_immutable() -> None:
    """@description: Keep READY repeatable as a pure evaluation rather than a unique reward claim.
    @param: none
    @return: None.
    """
    # [fixed synthetic facts]: No state is consumed by evaluating this operation twice.
    current = make_candidate(1)
    snapshot = snapshot_for(current)
    first = submit_offline(request_for(current), snapshot)
    assert submit_offline(request_for(current), snapshot) == first
    assert snapshot.candidates == () and first.reward.status is Status.READY
    assert not snapshot_is_complete(snapshot)
    assert snapshot_is_complete(replace(snapshot, candidates=(first.candidate,)))


@pytest.mark.parametrize("url", ["https://youtube.com/playlist?list=PLtest", "https://youtube.com.evil/watch?v=video000001"])
def test_workflow_reuses_strict_url_parser(url: str) -> None:
    """@description: Reject playlists and forged hostnames before producing any event intent.
    @param url: Unsupported or hostile source URL.
    @return: None.
    """
    # [valid internal facts]: A malformed URL cannot borrow the eligibility of a valid video.
    current = make_candidate(1)
    with pytest.raises(InvalidVideoUrl):
        submit_offline(replace(request_for(current), source_url=url), snapshot_for(current))


@pytest.mark.parametrize("scenario", list(ShareScenario))
def test_approved_pools_are_isolated_and_reproducible(scenario: ShareScenario) -> None:
    """@description: Keep random selection inside each immutable approved scenario pool.
    @param scenario: One stable approved presentation context.
    @return: None.
    """
    assert APPROVED_SHARE_REPLY_POOLS[scenario] == (APPROVED_SHARE_REPLIES[scenario],)
    assert select_share_reply(scenario, Random(7)) == select_share_reply(scenario, Random(7))
    assert select_share_reply(scenario, Random(8)) == APPROVED_SHARE_REPLIES[scenario]
    with pytest.raises(TypeError):
        APPROVED_SHARE_REPLY_POOLS[scenario] = ("unapproved",)


def test_stale_review_and_wrong_subject_fail_closed() -> None:
    """@description: Keep old revisions and another video's evidence out of the integrated workflow.
    @param: none
    @return: None.
    """
    # [current subject]: The request must preserve both review revision and evidence identity.
    current, other = make_candidate(1), make_candidate(2)
    with pytest.raises(StaleReviewVersion):
        submit_offline(replace(request_for(current), expected_review_version=0), snapshot_for(current))
    with pytest.raises(ValueError):
        submit_offline(replace(request_for(current), source_url=f"https://youtu.be/{other.identity.video.video_id}"), snapshot_for(current))


@pytest.mark.parametrize("reliable,full_video", [(False, True), (True, False)])
def test_untrusted_or_clip_duration_cannot_approve(reliable: bool, full_video: bool) -> None:
    """@description: Prevent both review paths from converting unreliable or clipped evidence into approval.
    @param reliable: Synthetic trust assertion for the duration source.
    @param full_video: Whether evidence refers to the original complete video.
    @return: None.
    """
    # [pending validation]: Approved status cannot be fabricated by the workflow.
    current = make_candidate(1, review_status=ReviewStatus.PENDING_VALIDATION)
    evidence = DurationEvidence(current.identity.video, 180, "synthetic", reliable, full_video)
    request = replace(request_for(current), duration=evidence, approval_source=ApprovalSource.OWNER, song_eligible=True)
    with pytest.raises(InvalidReviewTransition):
        submit_offline(request, snapshot_for(current))


def test_validation_failure_remains_retryable_without_reward() -> None:
    """@description: Distinguish temporary external verification failure from an explicit rejection.
    @param: none
    @return: None.
    """
    # [synthetic failure]: No external operation actually occurs.
    current = make_candidate(1, review_status=ReviewStatus.PENDING_VALIDATION)
    request = replace(request_for(current), validation_error=ValidationError.METADATA_UNAVAILABLE)
    result = submit_offline(request, snapshot_for(current))  # [retryable outcome]: No eligibility claim.
    assert result.candidate.review.status is ReviewStatus.VALIDATION_FAILED
    assert result.reward.status is Status.WAITING_VALIDATION


def test_unrelated_legacy_conflict_does_not_block_distinct_version() -> None:
    """@description: Scope known legacy evidence conflicts to their affected recording versions.
    @param: none
    @return: None.
    """
    # [unrelated conflicted version]: Trusted identity scopes the affected version explicitly.
    legacy = make_candidate(1, "unrelated", identity_status=VersionIdentityStatus.CONFLICT)
    current = make_candidate(2)
    assert submit_offline(request_for(current), snapshot_for(current, historical=(legacy.identity,))).reward.status is Status.READY


def test_contradictory_legacy_video_mapping_is_not_a_new_remix() -> None:
    """@description: Reject conflicting assertions for one legacy video rather than treating it as a new version.
    @param: none
    @return: None.
    """
    # [same-video contradiction]: Different recording assertions cannot both hold for this exact video.
    current = make_candidate(2)
    legacy = make_candidate(1, "different", video_id=current.identity.video.video_id)
    assert submit_offline(request_for(current), snapshot_for(current, historical=(legacy.identity,))).reward.status is Status.CONFLICT


def test_unknown_current_identity_never_claims_duplicate_or_ready() -> None:
    """@description: Keep unknown recording identity separate from verified duplicate presentation.
    @param: none
    @return: None.
    """
    # [unresolved identity]: A reliable duration and approved review do not identify a recording.
    current, earlier = make_candidate(2, None), make_candidate(1)
    result = submit_offline(request_for(current), snapshot_for(current, earlier))  # [unknown outcome]: No novelty assumption.
    assert result.reward.status is Status.WAITING_IDENTITY
    assert result.scenario is ShareScenario.IDENTITY_UNRESOLVED
    assert result.reply == APPROVED_SHARE_REPLIES[ShareScenario.IDENTITY_UNRESOLVED]


def test_candidate_revision_is_not_silently_selected_from_conflicting_rows() -> None:
    """@description: Refuse inconsistent current projections instead of choosing a convenient old review.
    @param: none
    @return: None.
    """
    # [contradictory projection]: One existing operation cannot simultaneously have two current review states.
    earlier, current = make_candidate(1), make_candidate(2)
    stale = replace(earlier, review=ReviewSnapshot(earlier.identity.video))
    with pytest.raises(ValueError, match="conflicting snapshots"):
        submit_offline(request_for(current), snapshot_for(current, earlier, stale))


def test_retry_rejects_contradictions_even_with_valid_receipt() -> None:
    """@description: Keep the A08 all-history validation active in the integrated retry path.
    @param: none
    @return: None.
    """
    # [original receipt]: Legitimate earlier result cannot authorize a contradictory operation reuse.
    current = make_candidate(1)
    original = submit_offline(request_for(current), snapshot_for(current))
    # [contradictory retry history]: A valid first match must not hide the later mismatch.
    snapshot = replace(snapshot_for(current), candidates=(current, replace(current, submitter_id=99)))
    with pytest.raises(ValueError):
        submit_offline(request_for(current), snapshot, original_result=original)


def test_exact_legacy_video_does_not_claim_a_different_video_alias() -> None:
    """@description: Avoid selecting cover-change copy when catalog history contains the identical video.
    @param: none
    @return: None.
    """
    # [exact legacy entry]: No historical submitter is invented to select a self/other scenario.
    current = make_candidate(1)
    result = submit_offline(request_for(current), snapshot_for(current, historical=(current.identity,)))
    assert result.reward.status is Status.INELIGIBLE
    assert result.scenario is None and result.reply is None


def test_receipt_without_matching_history_cannot_create_a_new_event() -> None:
    """@description: Fail closed when a supplied replay receipt is missing its operation projection.
    @param: none
    @return: None.
    """
    # [missing replay row]: An original receipt must never be silently treated as a fresh share.
    current = make_candidate(1)
    snapshot = snapshot_for(current)
    original = submit_offline(request_for(current), snapshot)
    with pytest.raises(ValueError, match="matching operation history"):
        submit_offline(request_for(current), snapshot, original_result=original)
