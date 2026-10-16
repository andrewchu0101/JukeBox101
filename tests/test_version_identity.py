"""Test explicit version identity evidence without metadata inference.

Synthetic decisions cover confirmed equality, distinction, unresolved identity and conflicting mappings.
"""

from dataclasses import replace

import pytest

from bot.song_identity import VideoIdentity
from bot.version_identity import (
    RecordingVersionIdentity, VersionAssignment, VersionExclusion,
    VersionIdentityStatus, VersionRelation, compare_version_assignments,
)
from tests.version_candidate_support import make_candidate


@pytest.mark.parametrize("first_name,second_name,expected", [
    ("recording-1", "recording-1", VersionRelation.SAME),
    ("original", "official-edm-arrangement", VersionRelation.DIFFERENT),
    ("official-edm-arrangement", "official-dubstep-arrangement", VersionRelation.DIFFERENT),
    (None, "original", VersionRelation.UNRESOLVED),
])
def test_only_explicit_confirmed_mappings_establish_relation(first_name: str | None, second_name: str, expected: VersionRelation) -> None:
    """@description: Compare trusted canonical references instead of MV, Audio or Remix title keywords.
    @param first_name: First explicit fixture mapping or missing identity.
    @param second_name: Second explicit fixture mapping.
    @param expected: Required relation result.
    @return: None.
    """
    # [asserted mappings]: Different videos are related only by caller-supplied evidence.
    first = make_candidate(1, first_name).identity
    second = make_candidate(2, second_name).identity  # [comparison evidence]: Independent trusted mapping.
    assert compare_version_assignments(first, second) is expected
    assert compare_version_assignments(second, first) is expected


@pytest.mark.parametrize("value", ["", " ", " leading", "trailing ", None, 1])
def test_version_reference_must_be_valid(value: object) -> None:
    """@description: Prevent empty or coerced canonical identifiers from creating reward groups.
    @param value: Invalid version identifier input.
    @return: None.
    """
    with pytest.raises(ValueError):
        RecordingVersionIdentity(value)


@pytest.mark.parametrize("changes", [
    {"evidence_reference": None}, {"evidence_reference": ""}, {"version": None},
    {"status": "confirmed"}, {"video": "video000001"},
    {"status": VersionIdentityStatus.UNRESOLVED}, {"version": "original"},
])
def test_confirmation_requires_internal_evidence(changes: dict) -> None:
    """@description: Reject contradictory identity shapes rather than optimistically trusting them.
    @param changes: Invalid changes to a confirmed assertion.
    @return: None.
    """
    with pytest.raises(ValueError):
        replace(make_candidate(1).identity, **changes)


def test_same_video_cannot_assert_two_distinct_versions() -> None:
    """@description: Detect inconsistent positive evidence without merging or choosing a winner.
    @param: none
    @return: None.
    """
    # [conflicting mappings]: One platform video has two incompatible canonical assertions.
    first = make_candidate(1, "original").identity
    second = make_candidate(2, "remix", video_id=first.video.video_id).identity  # [contradiction]: Same video, different version.
    assert compare_version_assignments(first, second) is VersionRelation.CONFLICT


def test_explicit_conflict_and_unknown_remain_separate() -> None:
    """@description: Preserve the difference between missing evidence and trusted contradictions.
    @param: none
    @return: None.
    """
    # [identity conflict]: No identity resolution or corrective action is performed.
    conflict = make_candidate(1, identity_status=VersionIdentityStatus.CONFLICT).identity
    unknown = VersionAssignment(VideoIdentity("video000002"))  # [missing identity]: No optimistic canonical reference.
    assert compare_version_assignments(conflict, unknown) is VersionRelation.CONFLICT
    assert compare_version_assignments(unknown, unknown) is VersionRelation.UNRESOLVED


@pytest.mark.parametrize("reference", [None, "", " "])
def test_unknown_exclusion_requires_trusted_provenance(reference: object) -> None:
    """@description: Prevent bare claims from releasing unknown-identity priority blockers.
    @param reference: Missing or invalid internal evidence reference.
    @return: None.
    """
    with pytest.raises(ValueError):
        VersionExclusion(VideoIdentity("video000001"), RecordingVersionIdentity("original"), reference)
