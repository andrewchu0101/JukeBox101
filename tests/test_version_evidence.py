"""Verify version resolution from evidence: unresolved, confirmed and conflicting, with no title or ID inference.

Offline synthetic evidence only; the reward evaluator is not invoked here. A reliable record confirms its basic version when it
declares no category, or when the Owner marked it with a category that can confirm it (TK-23); an edit category never confirms.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.song_identity import VideoIdentity
from bot.version_evidence import resolve_video_version
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus

VIDEO = VideoIdentity("Synth0001A1")
OTHER_VIDEO = VideoIdentity("Synth0002B2")
RETRIEVED = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
ORIGINAL = RecordingVersionIdentity("orig-A")
COVER = RecordingVersionIdentity("cover-B")
CONFIRMING_KINDS = (VersionClaimKind.SAME_RECORDING_AUDIO_MV, VersionClaimKind.REMASTER, VersionClaimKind.REMIX,
                    VersionClaimKind.LIVE, VersionClaimKind.COVER, VersionClaimKind.RE_RECORDING)


def evidence(evidence_id: str, version: RecordingVersionIdentity | None = ORIGINAL, *, video=VIDEO,
             source=EvidenceSourceType.OWNER_REVIEW, hours: int = 0, owner_confirmed: bool = True,
             kind: VersionClaimKind | None = VersionClaimKind.SAME_RECORDING_AUDIO_MV):
    """@description: Create one synthetic audited evidence record for version resolution.
    @param evidence_id: Unique synthetic record identifier.
    @param version: Asserted canonical version, or None for a non-version note.
    @param video: Video the record refers to.
    @param source: Evidence source type; platform metadata is never reliable alone.
    @param hours: Retrieval offset used to order records.
    @param owner_confirmed: Whether the trusted Owner boundary confirmed the inspection.
    @param kind: Category the Owner marked on the record; None names no category.
    @return: Immutable evidence record.
    """
    return record_evidence(evidence_id, video, source, f"synthetic-{evidence_id}", RETRIEVED + timedelta(hours=hours),
                           "synthetic inspection", 777, version, owner_confirmed=owner_confirmed, version_kind=kind)


def test_no_evidence_leaves_identity_unresolved():
    """@description: Keep a video pending when no evidence exists at all.
    @param: none
    @return: None.
    """
    assert resolve_video_version(VIDEO, []) == VersionAssignment(VIDEO)


def test_platform_metadata_alone_leaves_identity_unresolved():
    """@description: Refuse to confirm a recording from a title, tag, channel or video identifier.
    @param: none
    @return: None.
    """
    records = [evidence("ev-1", source=EvidenceSourceType.PLATFORM_METADATA)]
    assert resolve_video_version(VIDEO, records) == VersionAssignment(VIDEO)


@pytest.mark.parametrize("kind", CONFIRMING_KINDS)
def test_single_reliable_version_with_matching_kind_confirms_with_latest_evidence_reference(kind):
    """@description: Confirm the asserted version when the Owner named a confirming category, citing the latest such record.
    @param kind: Confirming category the Owner marked on the evidence.
    @return: None.
    """
    result = resolve_video_version(VIDEO, [evidence("ev-1", hours=0, kind=kind), evidence("ev-2", hours=1, kind=kind)])
    assert (result.status, result.version, result.evidence_reference) == (
        VersionIdentityStatus.CONFIRMED, ORIGINAL, "ev-2")


def test_reliable_record_without_version_kind_confirms_the_basic_version():
    """@description: A reliable owner-confirmed record with no declared category establishes the basic recording version (S3, Q18).
    @param: none
    @return: None.
    """
    assert resolve_video_version(VIDEO, [evidence("ev-1", kind=None)]) == VersionAssignment(
        VIDEO, VersionIdentityStatus.CONFIRMED, ORIGINAL, "ev-1")


def test_reliable_record_without_version_kind_confirms_a_basic_distinct_version():
    """@description: A basic record asserting a distinct recording identity confirms that identity without a declared category (S3).
    @param: none
    @return: None.
    """
    assert resolve_video_version(VIDEO, [evidence("ev-1", COVER, kind=None)]) == VersionAssignment(
        VIDEO, VersionIdentityStatus.CONFIRMED, COVER, "ev-1")


@pytest.mark.parametrize("kind", [VersionClaimKind.REMIX, VersionClaimKind.LIVE, VersionClaimKind.COVER,
                                  VersionClaimKind.RE_RECORDING])
def test_distinct_kind_confirms_only_with_the_matching_category(kind):
    """@description: Confirm a distinct version when the record's own category is that distinct category.
    @param kind: Distinct version category the Owner marked.
    @return: None.
    """
    result = resolve_video_version(VIDEO, [evidence("ev-1", COVER, kind=kind)])
    assert (result.status, result.version, result.evidence_reference) == (VersionIdentityStatus.CONFIRMED, COVER, "ev-1")


def test_edit_category_never_confirms_a_version():
    """@description: Clip, excerpt, extended, speed or pitch processing creates no version identity (Q17).
    @param: none
    @return: None.
    """
    assert resolve_video_version(VIDEO, [evidence("ev-1", kind=VersionClaimKind.EDIT)]) == VersionAssignment(VIDEO)


def test_edit_record_is_ignored_beside_a_basic_record():
    """@description: An edit record beside a basic record is ignored; the basic record alone confirms the version (Q17).
    @param: none
    @return: None.
    """
    records = [evidence("ev-1", kind=VersionClaimKind.EDIT), evidence("ev-2", kind=None, hours=1)]
    assert resolve_video_version(VIDEO, records) == VersionAssignment(
        VIDEO, VersionIdentityStatus.CONFIRMED, ORIGINAL, "ev-2")


def test_unconfirmed_owner_inspection_leaves_identity_unresolved():
    """@description: Keep a versioned inspection that the Owner has not confirmed from resolving identity.
    @param: none
    @return: None.
    """
    assert resolve_video_version(VIDEO, [evidence("ev-1", owner_confirmed=False)]) == VersionAssignment(VIDEO)


def test_contradictory_reliable_versions_become_conflict():
    """@description: Keep two contradicting trusted assertions visible as a conflict, not a winner, whatever their categories.
    @param: none
    @return: None.
    """
    result = resolve_video_version(VIDEO, [evidence("ev-1", ORIGINAL, kind=None), evidence("ev-2", COVER, kind=None)])
    assert result.status is VersionIdentityStatus.CONFLICT
    assert result.version is None
    assert result.evidence_reference in {"ev-1", "ev-2"}


def test_matching_category_contradicted_by_a_plain_record_becomes_conflict():
    """@description: A confirmed category claim is not silently overridden, and is not silently kept, when a plain record disagrees.
    @param: none
    @return: None.
    """
    records = [evidence("ev-1", COVER, kind=VersionClaimKind.COVER), evidence("ev-2", ORIGINAL, kind=None, hours=1)]
    assert resolve_video_version(VIDEO, records).status is VersionIdentityStatus.CONFLICT


def test_pending_noise_does_not_override_a_reliable_confirmation():
    """@description: Ignore platform metadata that disagrees with a reliable owner inspection.
    @param: none
    @return: None.
    """
    records = [evidence("ev-1", ORIGINAL),
               evidence("ev-2", COVER, source=EvidenceSourceType.PLATFORM_METADATA, hours=2, kind=VersionClaimKind.COVER)]
    assert resolve_video_version(VIDEO, records).status is VersionIdentityStatus.CONFIRMED


def test_evidence_for_another_video_is_ignored():
    """@description: Scope resolution strictly to the video whose identity is being decided.
    @param: none
    @return: None.
    """
    assert resolve_video_version(VIDEO, [evidence("ev-1", video=OTHER_VIDEO)]) == VersionAssignment(VIDEO)
