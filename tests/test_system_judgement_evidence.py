"""Verify system-judged version evidence: it can confirm a version without impersonating the Owner, and nothing else may use judge 0."""

from datetime import datetime, timezone

import pytest

from bot.evidence_record import EvidenceReliability, EvidenceSourceType, assess_evidence, record_evidence
from bot.song_identity import VideoIdentity
from bot.version_evidence import resolve_video_version
from bot.version_identity import RecordingVersionIdentity, VersionIdentityStatus

VIDEO = VideoIdentity("Synth0001A1")
VERSION = RecordingVersionIdentity("orig-A")
RETRIEVED = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def system_record(**overrides):
    """@description: Build a system-judged evidence record.
    @param overrides: Fields replaced by a test.
    @return: Evidence record.
    """
    values = dict(evidence_id="sys-1", video=VIDEO, source_type=EvidenceSourceType.SYSTEM_JUDGEMENT, source_reference="auto-compare:policy-v2",
                  retrieved_at=RETRIEVED, reason="no concern against recent songs", judged_by=0, version=VERSION)
    values.update(overrides)
    return record_evidence(**values)


def test_system_evidence_with_a_version_is_reliable_and_not_owner_confirmed() -> None:
    """@description: The system may confirm, but the record must never claim the Owner did.
    @param: none
    @return: None.
    """
    record = system_record()
    assert record.owner_confirmed is False and assess_evidence(record) is EvidenceReliability.RELIABLE


def test_system_evidence_resolves_the_video_to_a_confirmed_version() -> None:
    """@description: The reward evaluator consumes the same assignment it gets from Owner evidence.
    @param: none
    @return: None.
    """
    result = resolve_video_version(VIDEO, [system_record()])
    assert (result.status, result.version, result.evidence_reference) == (VersionIdentityStatus.CONFIRMED, VERSION, "sys-1")


def test_system_evidence_cannot_claim_owner_confirmation() -> None:
    """@description: Owner confirmation is set only at the Owner boundary.
    @param: none
    @return: None.
    """
    with pytest.raises(ValueError):
        system_record(owner_confirmed=True)


@pytest.mark.parametrize("source", [EvidenceSourceType.OWNER_REVIEW, EvidenceSourceType.RECORDING_REFERENCE, EvidenceSourceType.PLATFORM_METADATA])
def test_judge_zero_is_refused_for_every_other_source(source) -> None:
    """@description: Only the system source may use the system judge identity.
    @param source: Any non-system source type.
    @return: None.
    """
    with pytest.raises(ValueError):
        system_record(source_type=source)


@pytest.mark.parametrize("judge", [1, 777])
def test_the_system_source_requires_the_system_judge(judge) -> None:
    """@description: A positive judge identity cannot be attached to a system judgement.
    @param judge: Owner-like identity.
    @return: None.
    """
    with pytest.raises(ValueError):
        system_record(judged_by=judge)


def test_system_evidence_without_a_version_stays_pending() -> None:
    """@description: A judgement that names no version decides nothing.
    @param: none
    @return: None.
    """
    assert assess_evidence(system_record(version=None)) is EvidenceReliability.PENDING
