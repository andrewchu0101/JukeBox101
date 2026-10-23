"""Verify version-category rules from TK-23: same-recording, distinct-with-evidence and not-separately-rewarded claims.

Synthetic claims and evidence only; titles, tags, channels and video IDs are never inputs to the classification.
"""

from datetime import datetime, timezone

import pytest

from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.song_identity import VideoIdentity
from bot.version_categories import VersionClaim, VersionClaimStatus, classify_version_claim
from bot.version_identity import RecordingVersionIdentity

VIDEO = VideoIdentity("Synth0001A1")
OTHER_VIDEO = VideoIdentity("Synth0002B2")
RETRIEVED = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
DISTINCT_KINDS = (VersionClaimKind.REMIX, VersionClaimKind.LIVE, VersionClaimKind.COVER, VersionClaimKind.RE_RECORDING)
SAME_RECORDING_KINDS = (VersionClaimKind.SAME_RECORDING_AUDIO_MV, VersionClaimKind.REMASTER)
EVIDENCE_REQUIRED_KINDS = DISTINCT_KINDS + SAME_RECORDING_KINDS


def reliable_owner_evidence(kind: VersionClaimKind | None = None, video: VideoIdentity = VIDEO):
    """@description: Build one owner-confirmed, versioned evidence record that is reliable for identity.
    @param kind: Category the Owner marked on the evidence, or None when no category is named.
    @param video: Video the evidence describes.
    @return: Reliable evidence record.
    """
    return record_evidence("ev-1", video, EvidenceSourceType.OWNER_REVIEW, "synthetic-note-1", RETRIEVED,
                           "synthetic inspection", 777, RecordingVersionIdentity("orig-A"), owner_confirmed=True,
                           version_kind=kind)


def claim(kind: VersionClaimKind, video: VideoIdentity = VIDEO, asserted_version_name: str | None = None):
    """@description: Create one synthetic version claim for the subject video.
    @param kind: Version category being asserted.
    @param video: Subject video of the claim.
    @param asserted_version_name: Optional label the claim carries; never used as proof.
    @return: Immutable version claim.
    """
    return VersionClaim(kind, video, asserted_version_name)


def test_mv_and_audio_without_evidence_needs_evidence_not_same_recording():
    """@description: A category name alone never proves the MV and audio are one recording; no evidence keeps the claim pending.
    @param: none
    @return: None.
    """
    decision = classify_version_claim(claim(VersionClaimKind.SAME_RECORDING_AUDIO_MV), ())
    assert decision.status is VersionClaimStatus.NEEDS_EVIDENCE
    assert decision.reason


def test_remaster_without_evidence_needs_evidence_not_same_recording():
    """@description: A remaster label alone never proves it is the same original performance; no evidence keeps it pending.
    @param: none
    @return: None.
    """
    assert classify_version_claim(claim(VersionClaimKind.REMASTER), ()).status is VersionClaimStatus.NEEDS_EVIDENCE


@pytest.mark.parametrize("kind", SAME_RECORDING_KINDS)
def test_same_recording_category_with_reliable_evidence_of_the_same_kind_is_same_recording(kind):
    """@description: Treat MV, audio and remaster as one reward unit only when reliable owner-confirmed evidence names that category.
    @param kind: Same-recording category under test.
    @return: None.
    """
    decision = classify_version_claim(claim(kind), (reliable_owner_evidence(kind),))
    assert decision.status is VersionClaimStatus.SAME_RECORDING
    assert decision.reason


@pytest.mark.parametrize("kind", SAME_RECORDING_KINDS)
def test_reliable_evidence_of_another_kind_does_not_satisfy_a_same_recording_claim(kind):
    """@description: Keep a remix or live confirmation from silently proving an MV, audio or remaster claim.
    @param kind: Same-recording category under test.
    @return: None.
    """
    decision = classify_version_claim(claim(kind), (reliable_owner_evidence(VersionClaimKind.REMIX),))
    assert decision.status is VersionClaimStatus.NEEDS_EVIDENCE


@pytest.mark.parametrize("kind", SAME_RECORDING_KINDS)
def test_unconfirmed_other_video_or_platform_evidence_does_not_satisfy_a_same_recording_claim(kind):
    """@description: Count only owner-confirmed, non-metadata evidence about the claim's own video toward a same-recording claim.
    @param kind: Same-recording category under test.
    @return: None.
    """
    unconfirmed = record_evidence("ev-3", VIDEO, EvidenceSourceType.OWNER_REVIEW, "synthetic-note-3", RETRIEVED,
                                  "synthetic inspection", 777, RecordingVersionIdentity("orig-A"), version_kind=kind)
    other_video = reliable_owner_evidence(kind, OTHER_VIDEO)
    platform_record = record_evidence("ev-4", VIDEO, EvidenceSourceType.PLATFORM_METADATA, "synthetic-title-2",
                                      RETRIEVED, "synthetic title match", 777, RecordingVersionIdentity("orig-A"),
                                      owner_confirmed=True, version_kind=kind)
    decision = classify_version_claim(claim(kind), (unconfirmed, other_video, platform_record))
    assert decision.status is VersionClaimStatus.NEEDS_EVIDENCE


def test_asserted_name_alone_does_not_satisfy_a_same_recording_claim():
    """@description: A declared MV or remaster name is a claim to verify, not proof that the recordings match.
    @param: none
    @return: None.
    """
    decision = classify_version_claim(claim(VersionClaimKind.SAME_RECORDING_AUDIO_MV, asserted_version_name="Synth MV"), ())
    assert decision.status is VersionClaimStatus.NEEDS_EVIDENCE


def test_clip_excerpt_extended_speed_and_pitch_edits_are_not_separately_rewarded():
    """@description: Refuse a separate reward for clip, excerpt, extended, speed or pitch processing of a recording.
    @param: none
    @return: None.
    """
    decision = classify_version_claim(claim(VersionClaimKind.EDIT), (reliable_owner_evidence(VersionClaimKind.EDIT),))
    assert decision.status is VersionClaimStatus.NOT_SEPARATELY_REWARDED
    assert decision.reason


@pytest.mark.parametrize("kind", DISTINCT_KINDS)
def test_distinct_category_without_evidence_needs_evidence(kind):
    """@description: Hold each distinct performance category pending until traceable evidence exists.
    @param kind: Distinct version category under test.
    @return: None.
    """
    assert classify_version_claim(claim(kind), ()).status is VersionClaimStatus.NEEDS_EVIDENCE


@pytest.mark.parametrize("kind", DISTINCT_KINDS)
def test_distinct_category_with_reliable_evidence_of_the_same_kind_is_allowed_as_distinct(kind):
    """@description: Allow a distinct version only when reliable owner-confirmed evidence names the same category.
    @param kind: Distinct version category under test.
    @return: None.
    """
    decision = classify_version_claim(claim(kind), (reliable_owner_evidence(kind),))
    assert decision.status is VersionClaimStatus.DISTINCT_VERSION_ALLOWED


def test_reliable_evidence_of_another_distinct_kind_does_not_satisfy_a_live_claim():
    """@description: Keep a remix confirmation from silently satisfying a live performance claim.
    @param: none
    @return: None.
    """
    decision = classify_version_claim(claim(VersionClaimKind.LIVE), (reliable_owner_evidence(VersionClaimKind.REMIX),))
    assert decision.status is VersionClaimStatus.NEEDS_EVIDENCE


@pytest.mark.parametrize("kind", EVIDENCE_REQUIRED_KINDS)
def test_legacy_evidence_without_a_version_kind_does_not_satisfy_any_evidence_required_kind(kind):
    """@description: Refuse to count owner-confirmed evidence that names no category toward any category that needs evidence.
    @param kind: Distinct version category under test.
    @return: None.
    """
    assert classify_version_claim(claim(kind), (reliable_owner_evidence(None),)).status is VersionClaimStatus.NEEDS_EVIDENCE


@pytest.mark.parametrize("kind", EVIDENCE_REQUIRED_KINDS)
def test_platform_metadata_alone_never_satisfies_an_evidence_required_rule(kind):
    """@description: Keep titles, tags and channel names from proving a category even when owner-confirmed.
    @param kind: Distinct version category under test.
    @return: None.
    """
    platform_record = record_evidence("ev-title", VIDEO, EvidenceSourceType.PLATFORM_METADATA, "synthetic-title",
                                      RETRIEVED, "synthetic title match", 777, RecordingVersionIdentity("cover-B"),
                                      owner_confirmed=True, version_kind=kind)
    decision = classify_version_claim(claim(kind, asserted_version_name="Synth Live Title"), (platform_record,))
    assert decision.status is VersionClaimStatus.NEEDS_EVIDENCE


@pytest.mark.parametrize("kind", EVIDENCE_REQUIRED_KINDS)
def test_unconfirmed_or_other_video_evidence_does_not_satisfy_an_evidence_required_rule(kind):
    """@description: Count only owner-confirmed evidence about the claim's own subject video.
    @param kind: Distinct version category under test.
    @return: None.
    """
    unconfirmed = record_evidence("ev-2", VIDEO, EvidenceSourceType.OWNER_REVIEW, "synthetic-note-2", RETRIEVED,
                                  "synthetic inspection", 777, RecordingVersionIdentity("orig-A"))
    evidence_for_other_video = reliable_owner_evidence(kind, OTHER_VIDEO)
    decision = classify_version_claim(claim(kind), (unconfirmed, evidence_for_other_video))
    assert decision.status is VersionClaimStatus.NEEDS_EVIDENCE


def test_asserted_version_name_alone_does_not_satisfy_a_distinct_rule():
    """@description: Treat a named version label as a claim to verify, not as evidence.
    @param: none
    @return: None.
    """
    decision = classify_version_claim(claim(VersionClaimKind.REMIX, asserted_version_name="Synth Remix"), ())
    assert decision.status is VersionClaimStatus.NEEDS_EVIDENCE


def test_claim_refuses_a_kind_outside_the_approved_categories():
    """@description: Refuse a claim whose category is not one of the approved TK-23 categories.
    @param: none
    @return: None.
    """
    with pytest.raises(ValueError):
        VersionClaim("remix", VIDEO, None)
