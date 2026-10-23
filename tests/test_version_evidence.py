"""Verify version resolution from evidence: unresolved, confirmed and conflicting, with no title or ID inference.

Offline synthetic evidence only; the reward evaluator is not invoked here.
"""

from datetime import datetime, timedelta, timezone

from bot.evidence_record import EvidenceSourceType, record_evidence
from bot.song_identity import VideoIdentity
from bot.version_evidence import resolve_video_version
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus

VIDEO = VideoIdentity("Synth0001A1")
OTHER_VIDEO = VideoIdentity("Synth0002B2")
RETRIEVED = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)
ORIGINAL = RecordingVersionIdentity("orig-A")
COVER = RecordingVersionIdentity("cover-B")


def evidence(evidence_id: str, version: RecordingVersionIdentity | None = ORIGINAL, *, video=VIDEO,
             source=EvidenceSourceType.OWNER_REVIEW, hours: int = 0):
    """@description: Create one synthetic audited evidence record for version resolution.
    @param evidence_id: Unique synthetic record identifier.
    @param version: Asserted canonical version, or None for a non-version note.
    @param video: Video the record refers to.
    @param source: Evidence source type; platform metadata is never reliable alone.
    @param hours: Retrieval offset used to order records.
    @return: Immutable evidence record.
    """
    return record_evidence(evidence_id, video, source, f"synthetic-{evidence_id}", RETRIEVED + timedelta(hours=hours),
                           "synthetic inspection", 777, version)


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


def test_single_reliable_version_confirms_with_latest_evidence_reference():
    """@description: Confirm the asserted version and cite the most recently retrieved supporting record.
    @param: none
    @return: None.
    """
    result = resolve_video_version(VIDEO, [evidence("ev-1", hours=0), evidence("ev-2", hours=1)])
    assert (result.status, result.version, result.evidence_reference) == (
        VersionIdentityStatus.CONFIRMED, ORIGINAL, "ev-2")


def test_contradictory_reliable_versions_become_conflict():
    """@description: Keep two contradicting trusted assertions visible as a conflict, not a winner.
    @param: none
    @return: None.
    """
    result = resolve_video_version(VIDEO, [evidence("ev-1", ORIGINAL), evidence("ev-2", COVER)])
    assert result.status is VersionIdentityStatus.CONFLICT
    assert result.version is None
    assert result.evidence_reference in {"ev-1", "ev-2"}


def test_pending_noise_does_not_override_a_reliable_confirmation():
    """@description: Ignore platform metadata that disagrees with a reliable owner inspection.
    @param: none
    @return: None.
    """
    records = [evidence("ev-1", ORIGINAL),
               evidence("ev-2", COVER, source=EvidenceSourceType.PLATFORM_METADATA, hours=2)]
    assert resolve_video_version(VIDEO, records).status is VersionIdentityStatus.CONFIRMED


def test_evidence_for_another_video_is_ignored():
    """@description: Scope resolution strictly to the video whose identity is being decided.
    @param: none
    @return: None.
    """
    assert resolve_video_version(VIDEO, [evidence("ev-1", video=OTHER_VIDEO)]) == VersionAssignment(VIDEO)
