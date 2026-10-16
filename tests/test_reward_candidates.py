"""Exercise first-version reward priority entirely with synthetic intake snapshots.

No READY result commits money, and existing awards are immutable input facts rather than simulated writes.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import permutations

import pytest

from bot.review import ReviewStatus
from bot.reward_candidates import (
    RewardCandidateStatus as Status, RewardDecisionReason as Reason,
    RewardEvaluationInput, evaluate_reward_candidates,
)
from bot.version_identity import RecordingVersionIdentity, VersionExclusion, VersionIdentityStatus
from tests.version_candidate_support import make_candidate, make_context


def test_same_recording_mv_audio_share_first_reward() -> None:
    """@description: Reserve one first-reward opportunity across explicitly matched video aliases.
    @param: none
    @return: None.
    """
    # [same recording]: MV and Audio roles are fixture intent, not inferred platform metadata.
    music_video, official_audio = make_candidate(1), make_candidate(2)
    decisions = evaluate_reward_candidates(make_context(music_video, official_audio))  # [initial priority]: One READY opportunity.
    assert [item.status for item in decisions] == [Status.READY, Status.WAITING_PREDECESSOR]
    # [settled history]: This flag represents an externally supplied fact, never a payout here.
    settled = replace(music_video, awarded=True)
    after = evaluate_reward_candidates(make_context(settled, official_audio))  # [post-settlement view]: No second opportunity.
    assert [item.status for item in after] == [Status.AWARDED, Status.INELIGIBLE]
    assert after[1].reason is Reason.VERSION_ALREADY_AWARDED


def test_trusted_official_remixes_compete_independently() -> None:
    """@description: Keep evidenced different arrangements eligible for independent first rewards.
    @param: none
    @return: None.
    """
    # [trusted distinct versions]: Explicit internal fixtures supply evidence, not title parsing.
    candidates = (make_candidate(1, "original"), make_candidate(2, "edm-remix"), make_candidate(3, "dubstep-remix"))
    assert all(item.status is Status.READY for item in evaluate_reward_candidates(make_context(*candidates)))


@pytest.mark.parametrize("review_status", [ReviewStatus.PENDING_VALIDATION, ReviewStatus.PENDING_REVIEW, ReviewStatus.VALIDATION_FAILED])
def test_later_approval_cannot_overtake_pending_predecessor(review_status: ReviewStatus) -> None:
    """@description: Ignore review completion speed and temporary validation failures when ordering claims.
    @param review_status: Earlier unresolved review stage.
    @return: None.
    """
    # [earlier claim]: Its accepted order remains authoritative despite incomplete review.
    earlier, later = make_candidate(1, review_status=review_status), make_candidate(2)
    decisions = evaluate_reward_candidates(make_context(later, earlier))  # [priority result]: Input order cannot select the winner.
    assert decisions[0].status is Status.WAITING_VALIDATION
    assert decisions[1].status is Status.WAITING_PREDECESSOR
    assert decisions[1].blocking_operation_ids == (earlier.operation_id,)


@pytest.mark.parametrize("earlier_version", [None, "original"])
def test_rejection_releases_next_candidate(earlier_version: str | None) -> None:
    """@description: Release priority only after explicit rejection rather than failure or age.
    @param earlier_version: Known or unresolved identity of the rejected predecessor.
    @return: None.
    """
    # [rejected claim]: Actual Phase 2B.1 negative review removes its reward eligibility.
    earlier = make_candidate(1, earlier_version, review_status=ReviewStatus.REJECTED)
    decisions = evaluate_reward_candidates(make_context(earlier, make_candidate(2)))  # [released priority]: Later valid candidate may proceed.
    assert [item.status for item in decisions] == [Status.INELIGIBLE, Status.READY]


def test_unknown_identity_is_not_a_new_song_and_blocks_later_claim() -> None:
    """@description: Avoid optimistic novelty when an earlier video's recording remains unknown.
    @param: none
    @return: None.
    """
    # [unresolved first claim]: Passing video review does not establish a distinct recording.
    decisions = evaluate_reward_candidates(make_context(make_candidate(1, None), make_candidate(2)))
    assert decisions[0].status is Status.WAITING_IDENTITY
    assert decisions[1].status is Status.WAITING_PREDECESSOR


@pytest.mark.parametrize("excluded_version,expected", [("original", Status.READY), ("unrelated", Status.WAITING_PREDECESSOR)])
def test_unknown_blocker_requires_scoped_exclusion(excluded_version: str, expected: Status) -> None:
    """@description: Release an unknown predecessor only with evidence excluding the target version.
    @param excluded_version: Canonical version excluded by trusted synthetic evidence.
    @param expected: Whether the target's priority blocker can be released.
    @return: None.
    """
    # [negative evidence]: This explicit assertion is supplied by trusted review, not inferred here.
    earlier, later = make_candidate(1, None), make_candidate(2)
    exclusion = VersionExclusion(earlier.identity.video, RecordingVersionIdentity(excluded_version), "synthetic-distinction")  # [scope proof]: Explicit excluded version.
    decisions = evaluate_reward_candidates(make_context(earlier, later, exclusions=(exclusion,)))  # [release result]: Other versions remain blocked.
    assert decisions[0].status is Status.WAITING_IDENTITY
    assert decisions[1].status is expected


def test_historical_catalog_does_not_reopen_first_reward() -> None:
    """@description: Preserve catalog history without requiring a previously known winner or ledger row.
    @param: none
    @return: None.
    """
    # [legacy catalog]: Confirmed preexisting versions are permanently unavailable for first reward.
    decisions = evaluate_reward_candidates(make_context(make_candidate(1), make_candidate(2), history=("original",)))
    assert all(item.status is Status.INELIGIBLE and item.reason is Reason.HISTORICAL_VERSION for item in decisions)


def test_pending_older_than_24_hours_retains_priority() -> None:
    """@description: Keep time-based expiry and abandonment policies outside the evaluator.
    @param: none
    @return: None.
    """
    # [audit age]: A later share occurs 48 hours after the original still-pending submission.
    accepted_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    earlier = make_candidate(1, review_status=ReviewStatus.PENDING_VALIDATION, accepted_at=accepted_at)  # [aged predecessor]: Priority does not expire.
    later = make_candidate(2, accepted_at=accepted_at + timedelta(hours=48))  # [later claim]: Review speed cannot bypass age.
    assert evaluate_reward_candidates(make_context(earlier, later))[1].status is Status.WAITING_PREDECESSOR
    assert evaluate_reward_candidates(make_context(earlier, later))[1].blocking_operation_ids == ("op-1",)


def test_audit_times_cannot_override_accepted_sequence() -> None:
    """@description: Use asserted database order even when timestamps have a conflicting order.
    @param: none
    @return: None.
    """
    # [clock disagreement]: Neither Discord time nor an audit timestamp chooses the winner.
    earlier = make_candidate(1, accepted_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
    later = make_candidate(2, accepted_at=datetime(2026, 1, 1, tzinfo=timezone.utc))  # [misleading clock]: Earlier timestamp, later intake.
    assert evaluate_reward_candidates(make_context(later, earlier))[0].operation_id == "op-1"


def test_known_conflict_freezes_only_related_version() -> None:
    """@description: Suspend affected unsettled rewards without blocking confirmed unrelated recordings.
    @param: none
    @return: None.
    """
    # [scoped conflict]: One recording's evidence conflict must not freeze an unrelated remix.
    conflict = make_candidate(1, identity_status=VersionIdentityStatus.CONFLICT)
    decisions = evaluate_reward_candidates(make_context(conflict, make_candidate(2), make_candidate(3, "distinct-remix")))  # [conflict scope]: Separate recording stays ready.
    assert [item.status for item in decisions] == [Status.CONFLICT, Status.CONFLICT, Status.READY]


def test_conflict_does_not_revoke_an_existing_award() -> None:
    """@description: Treat awarded facts as history while suspending further affected settlements.
    @param: none
    @return: None.
    """
    # [existing award]: Conflicting later evidence never produces a refund or revocation action.
    awarded = make_candidate(1, awarded=True)
    conflict = make_candidate(2, identity_status=VersionIdentityStatus.CONFLICT)  # [new evidence conflict]: Suspend only unsettled claims.
    decisions = evaluate_reward_candidates(make_context(awarded, conflict, make_candidate(3)))  # [preserved history]: Never revoke the award.
    assert [item.status for item in decisions] == [Status.AWARDED, Status.CONFLICT, Status.CONFLICT]
    assert awarded.awarded is True


def test_unknown_conflict_is_conservative_unless_excluded() -> None:
    """@description: Require explicit scope evidence before isolating a conflict of unknown identity.
    @param: none
    @return: None.
    """
    # [unscoped conflict]: Its possible recording remains unknown.
    conflict, later = make_candidate(1, None, identity_status=VersionIdentityStatus.CONFLICT), make_candidate(2)
    assert evaluate_reward_candidates(make_context(conflict, later))[1].status is Status.CONFLICT
    exclusion = VersionExclusion(conflict.identity.video, later.identity.version, "synthetic-distinction")  # [scope release]: Proven unrelated recording.
    assert evaluate_reward_candidates(make_context(conflict, later, exclusions=(exclusion,)))[1].status is Status.READY


def test_confirmed_distinct_pending_recording_does_not_block() -> None:
    """@description: Avoid global waiting once authoritative evidence confirms different versions.
    @param: none
    @return: None.
    """
    # [different versions]: Pending review is scoped to its own confirmed recording.
    earlier = make_candidate(1, "other", review_status=ReviewStatus.VALIDATION_FAILED)
    assert evaluate_reward_candidates(make_context(earlier, make_candidate(2)))[1].status is Status.READY


def test_review_approved_is_not_reward_ready() -> None:
    """@description: Keep the review enum and priority enum semantically distinct.
    @param: none
    @return: None.
    """
    # [approved later review]: Qualification passes but a predecessor still owns priority.
    later = make_candidate(2)
    earlier = make_candidate(1, review_status=ReviewStatus.PENDING_REVIEW)  # [priority owner]: Still awaiting a song decision.
    decision = evaluate_reward_candidates(make_context(earlier, later))[1]  # [reward outcome]: Separate from approved review.
    assert later.review.status is ReviewStatus.APPROVED
    assert decision.status is Status.WAITING_PREDECESSOR
    assert not isinstance(decision.status, ReviewStatus)


def test_evaluation_is_deterministic_across_input_order_and_retries() -> None:
    """@description: Make repeated pure evaluations stable without consuming reward eligibility.
    @param: none
    @return: None.
    """
    # [immutable evaluation scope]: No evaluation call marks a READY operation as awarded.
    candidates = (make_candidate(1), make_candidate(2), make_candidate(3, "remix"))
    expected = evaluate_reward_candidates(make_context(*candidates))  # [stable result]: Baseline for permutations and retries.
    for ordering in permutations(candidates):
        assert evaluate_reward_candidates(make_context(*ordering)) == expected
    assert evaluate_reward_candidates(make_context(*candidates, candidates[0])) == expected
    assert all(candidate.awarded is False for candidate in candidates)


def test_incomplete_scope_cannot_authorize_ready() -> None:
    """@description: Fail closed when the caller cannot assert all possible predecessors are present.
    @param: none
    @return: None.
    """
    # [incomplete context]: A lone candidate is not necessarily the earliest real submission.
    decision = evaluate_reward_candidates(RewardEvaluationInput((make_candidate(2),)))[0]
    assert decision.status is Status.WAITING_PREDECESSOR and decision.reason is Reason.INCOMPLETE_SNAPSHOT


def test_positive_and_negative_evidence_conflict() -> None:
    """@description: Reject simultaneous inclusion and exclusion evidence for the same version.
    @param: none
    @return: None.
    """
    # [contradiction]: Trusted assertions disagree rather than permitting optimistic separation.
    earlier = make_candidate(1)
    exclusion = VersionExclusion(earlier.identity.video, earlier.identity.version, "synthetic-conflict")  # [negative assertion]: Contradicts confirmed inclusion.
    decisions = evaluate_reward_candidates(make_context(earlier, make_candidate(2), exclusions=(exclusion,)))  # [frozen results]: No optimistic settlement.
    assert all(item.status is Status.CONFLICT for item in decisions)


def test_incompatible_video_mappings_freeze_both_groups() -> None:
    """@description: Avoid choosing either canonical mapping when the same video has conflicting evidence.
    @param: none
    @return: None.
    """
    # [mapping conflict]: Each implicated version's unsettled aliases must also freeze.
    original = make_candidate(1)
    incompatible = make_candidate(2, "remix", video_id=original.identity.video.video_id)  # [conflicting alias]: Same video mapped to another version.
    decisions = evaluate_reward_candidates(make_context(original, incompatible, make_candidate(3), make_candidate(4, "remix")))  # [affected groups]: Both mappings freeze.
    assert all(item.status is Status.CONFLICT for item in decisions)


def test_duplicate_existing_awards_freeze_unsettled_candidate() -> None:
    """@description: Surface historical duplicate rewards without attempting automatic money recovery.
    @param: none
    @return: None.
    """
    # [duplicate history]: Two existing awards are input conflicts, not writes from this test.
    decisions = evaluate_reward_candidates(make_context(make_candidate(1, awarded=True), make_candidate(2, awarded=True), make_candidate(3)))
    assert [item.status for item in decisions] == [Status.AWARDED, Status.AWARDED, Status.CONFLICT]


def test_later_existing_award_does_not_silently_discard_earlier_claim() -> None:
    """@description: Report discovered priority violations instead of revoking or rewarding automatically.
    @param: none
    @return: None.
    """
    # [priority conflict]: A later historical award cannot retroactively erase an earlier pending claim.
    earlier = make_candidate(1, review_status=ReviewStatus.PENDING_VALIDATION)
    decisions = evaluate_reward_candidates(make_context(earlier, make_candidate(2, awarded=True)))  # [historical violation]: Preserve money facts, flag priority conflict.
    assert [item.status for item in decisions] == [Status.CONFLICT, Status.AWARDED]


@pytest.mark.parametrize("change", [{"accepted_sequence": 0}, {"accepted_sequence": True}, {"submitter_id": 0},
                                     {"operation_id": ""}, {"awarded": 1}, {"accepted_at": datetime(2026, 1, 1)}])
def test_invalid_candidate_facts_are_refused(change: dict) -> None:
    """@description: Refuse invalid sequence, identity and historical facts before evaluation.
    @param change: Malformed candidate field.
    @return: None.
    """
    with pytest.raises(ValueError):
        replace(make_candidate(1), **change)


def test_conflicting_retries_and_tied_sequences_are_refused() -> None:
    """@description: Avoid arbitrarily resolving invalid request-id or intake-order collisions.
    @param: none
    @return: None.
    """
    # [invalid intake facts]: Future persistence must guarantee stable operations and unique sequences.
    first = make_candidate(1)
    with pytest.raises(ValueError, match="operation"):
        evaluate_reward_candidates(make_context(first, replace(first, submitter_id=9)))
    with pytest.raises(ValueError, match="sequence"):
        evaluate_reward_candidates(make_context(first, replace(first, operation_id="another-operation")))


def test_conflicting_video_reviews_suspend_related_claims() -> None:
    """@description: Freeze inconsistent current qualification snapshots instead of skipping a rejection.
    @param: none
    @return: None.
    """
    # [current review conflict]: The same video's simultaneous authoritative projections disagree.
    rejected = make_candidate(1, review_status=ReviewStatus.REJECTED)
    # [conflicting review]: A different request cannot silently override the shared video evidence.
    approved = make_candidate(2, video_id=rejected.identity.video.video_id)
    # [affected group]: Even another video of this recording must not settle under conflicting evidence.
    decisions = evaluate_reward_candidates(make_context(rejected, approved, make_candidate(3)))
    assert all(item.status is Status.CONFLICT for item in decisions)


def test_later_unknown_identity_does_not_block_an_earlier_known_version() -> None:
    """@description: Scope ordinary unresolved priority blocking to earlier accepted submissions only.
    @param: none
    @return: None.
    """
    # [later unknown]: Its possible same-version claim cannot precede the known earlier contribution.
    decisions = evaluate_reward_candidates(make_context(make_candidate(1), make_candidate(2, None)))
    assert [item.status for item in decisions] == [Status.READY, Status.WAITING_IDENTITY]


@pytest.mark.parametrize("changes", [
    {"candidates": []}, {"candidates": ("untrusted",)}, {"historical_versions": set()},
    {"historical_versions": frozenset({"original"})}, {"exclusions": []},
    {"exclusions": ("untrusted",)}, {"complete": 1},
])
def test_evaluation_context_rejects_mutable_or_untyped_facts(changes: dict) -> None:
    """@description: Avoid treating malformed snapshot assertions as sufficient settlement context.
    @param changes: Invalid collection or trust-boundary input.
    @return: None.
    """
    with pytest.raises(ValueError):
        replace(make_context(make_candidate(1)), **changes)


def test_candidate_review_cannot_belong_to_another_video() -> None:
    """@description: Preserve the Phase 2B.1 binding between identity and full-video qualification.
    @param: none
    @return: None.
    """
    with pytest.raises(ValueError):
        replace(make_candidate(1), review=make_candidate(2).review)


def test_unresolved_award_history_freezes_unsettled_claims() -> None:
    """@description: Preserve existing money facts while refusing to guess their recording scope.
    @param: none
    @return: None.
    """
    # [unscoped settlement]: An existing award with unresolved identity requires later reconciliation.
    decisions = evaluate_reward_candidates(make_context(make_candidate(1, None, awarded=True), make_candidate(2)))
    assert [item.status for item in decisions] == [Status.AWARDED, Status.CONFLICT]
