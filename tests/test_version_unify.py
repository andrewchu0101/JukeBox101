"""Verify the unified version rules through public functions only: resolve_video_version and classify_version_claim.

Synthetic evidence and claims only. Each test cites the Spec rule it exercises (docs/recovery-batchb-private-beta-spec.md
§16.1 S3, §16.4 TK-23, §17.2 PB-EVD; Q12, Q16, Q17, Q18). Nothing here calls YouTube, MySQL or Discord.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.evidence_record import EvidenceSourceType, VersionClaimKind, record_evidence
from bot.song_identity import VideoIdentity
from bot.version_categories import VersionClaim, VersionClaimStatus, classify_version_claim
from bot.version_evidence import resolve_video_version
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus

VIDEO = VideoIdentity("Synth0001A1")
OTHER_VIDEO = VideoIdentity("Synth0002B2")
RETRIEVED = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
ORIGINAL = RecordingVersionIdentity("orig-A")
COVER = RecordingVersionIdentity("cover-B")


def owner_record(evidence_id: str, version: RecordingVersionIdentity, *, video: VideoIdentity = VIDEO,
                 kind: VersionClaimKind | None = None, hours: int = 0, owner_confirmed: bool = True,
                 source: EvidenceSourceType = EvidenceSourceType.OWNER_REVIEW):
    """@description: Build one synthetic audited record for the unify rules, defaulting to a reliable basic record.
    @param evidence_id: Unique synthetic record identifier.
    @param version: Asserted canonical recording version.
    @param video: Video the record describes.
    @param kind: Category the Owner marked, or None for a basic record with no declared category.
    @param hours: Retrieval offset used to order records.
    @param owner_confirmed: Whether the Owner confirmed the inspection at the trusted boundary.
    @param source: Evidence source type; platform metadata is never reliable alone.
    @return: Immutable evidence record.
    """
    return record_evidence(evidence_id, video, source, f"synthetic-{evidence_id}", RETRIEVED + timedelta(hours=hours),
                           "synthetic inspection", 777, version, owner_confirmed=owner_confirmed, version_kind=kind)


def test_basic_owner_confirmed_record_without_category_confirms_the_basic_version():
    """@description: A reliable owner-confirmed record with no declared category establishes the basic recording version (S3, Q18).
    @param: none
    @return: None.
    """
    result = resolve_video_version(VIDEO, [owner_record("ev-1", ORIGINAL)])
    assert result == VersionAssignment(VIDEO, VersionIdentityStatus.CONFIRMED, ORIGINAL, "ev-1")


def test_two_basic_records_with_different_versions_conflict():
    """@description: Two reliable basic records that disagree about one video give CONFLICT, not a winner (S3).
    @param: none
    @return: None.
    """
    records = [owner_record("ev-1", ORIGINAL), owner_record("ev-2", COVER, hours=1)]
    assert resolve_video_version(VIDEO, records).status is VersionIdentityStatus.CONFLICT


def test_declared_category_without_reliable_evidence_neither_confirms_nor_blocks_a_basic_record():
    """@description: A declared REMIX that is still pending is ignored, so the basic record of the same version still confirms (TK-23, Q18).
    @param: none
    @return: None.
    """
    pending_remix = owner_record("ev-2", ORIGINAL, kind=VersionClaimKind.REMIX, hours=1, owner_confirmed=False)
    result = resolve_video_version(VIDEO, [owner_record("ev-1", ORIGINAL), pending_remix])
    assert result == VersionAssignment(VIDEO, VersionIdentityStatus.CONFIRMED, ORIGINAL, "ev-1")


def test_same_version_cross_link_records_do_not_conflict():
    """@description: Two records asserting the same canonical version for one video form one identity, not a conflict (S3, TK-23 查重).
    @param: none
    @return: None.
    """
    records = [owner_record("ev-1", ORIGINAL), owner_record("ev-2", ORIGINAL, hours=1,
                                                             kind=VersionClaimKind.SAME_RECORDING_AUDIO_MV)]
    result = resolve_video_version(VIDEO, records)
    assert (result.status, result.version, result.evidence_reference) == (
        VersionIdentityStatus.CONFIRMED, ORIGINAL, "ev-2")


def test_same_recording_asserted_by_two_videos_resolves_to_one_version_identity():
    """@description: Two videos that assert the same canonical version share one identity, so no second version basis appears (TK-23 查重; the award duplicate rule stays in reward review).
    @param: none
    @return: None.
    """
    first = resolve_video_version(VIDEO, [owner_record("ev-1", ORIGINAL)])
    second = resolve_video_version(OTHER_VIDEO, [owner_record("ev-2", ORIGINAL, video=OTHER_VIDEO)])
    assert first.version == second.version == ORIGINAL
    assert (first.status, second.status) == (VersionIdentityStatus.CONFIRMED, VersionIdentityStatus.CONFIRMED)


def test_edit_record_neither_confirms_nor_contradicts_a_basic_version():
    """@description: An EDIT record beside a basic record neither confirms another version nor creates a conflict (Q17).
    @param: none
    @return: None.
    """
    records = [owner_record("ev-1", ORIGINAL), owner_record("ev-2", COVER, kind=VersionClaimKind.EDIT, hours=1)]
    result = resolve_video_version(VIDEO, records)
    assert (result.status, result.version, result.evidence_reference) == (
        VersionIdentityStatus.CONFIRMED, ORIGINAL, "ev-1")


def test_platform_metadata_and_unconfirmed_basic_records_never_decide():
    """@description: Titles, tags and unconfirmed records never decide a version, even beside a reliable basic record (S3, Q18).
    @param: none
    @return: None.
    """
    records = [
        owner_record("ev-1", ORIGINAL),
        owner_record("ev-2", COVER, hours=1, source=EvidenceSourceType.PLATFORM_METADATA),
        owner_record("ev-3", COVER, hours=2, owner_confirmed=False),
    ]
    result = resolve_video_version(VIDEO, records)
    assert (result.status, result.version, result.evidence_reference) == (
        VersionIdentityStatus.CONFIRMED, ORIGINAL, "ev-1")


def test_platform_metadata_alone_never_confirms_a_basic_version():
    """@description: A basic title-level record from platform metadata cannot establish a recording version (S3, Q18).
    @param: none
    @return: None.
    """
    result = resolve_video_version(VIDEO, [owner_record("ev-1", ORIGINAL, source=EvidenceSourceType.PLATFORM_METADATA)])
    assert result == VersionAssignment(VIDEO)


@pytest.mark.parametrize("kind", [VersionClaimKind.REMIX, VersionClaimKind.LIVE, VersionClaimKind.COVER,
                                  VersionClaimKind.RE_RECORDING])
def test_basic_record_does_not_satisfy_a_distinct_category_claim(kind):
    """@description: A basic record with no category is not the category evidence a distinct claim requires (TK-23, Q18).
    @param kind: Distinct version category claimed.
    @return: None.
    """
    decision = classify_version_claim(VersionClaim(kind, VIDEO), (owner_record("ev-1", COVER),))
    assert decision.status is VersionClaimStatus.NEEDS_EVIDENCE


@pytest.mark.parametrize("kind", [VersionClaimKind.REMIX, VersionClaimKind.LIVE, VersionClaimKind.COVER,
                                  VersionClaimKind.RE_RECORDING])
def test_declared_distinct_category_with_reliable_evidence_confirms_its_version(kind):
    """@description: A reliable record that declares a distinct category confirms its asserted version (TK-23).
    @param kind: Distinct version category declared on the record.
    @return: None.
    """
    record = owner_record("ev-1", COVER, kind=kind)
    assert classify_version_claim(VersionClaim(kind, VIDEO), (record,)).status is VersionClaimStatus.DISTINCT_VERSION_ALLOWED
    assert resolve_video_version(VIDEO, [record]) == VersionAssignment(VIDEO, VersionIdentityStatus.CONFIRMED, COVER, "ev-1")
