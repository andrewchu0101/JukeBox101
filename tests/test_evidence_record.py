"""Verify the minimal evidence record keeps provenance and never grants trust from a source label alone.

Synthetic records only; no platform metadata is fetched and no external source is contacted.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.evidence_record import EvidenceReliability, EvidenceSourceType, assess_evidence, record_evidence
from bot.song_identity import VideoIdentity
from bot.version_identity import RecordingVersionIdentity

VIDEO = VideoIdentity("Synth0001A1")
RETRIEVED = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def make(**overrides):
    """@description: Build one synthetic owner-inspection record with overridable provenance fields.
    @param overrides: Field replacements for a single scenario.
    @return: Evidence record built through the public constructor.
    """
    values = {
        "evidence_id": "ev-1", "video": VIDEO, "source_type": EvidenceSourceType.OWNER_REVIEW,
        "source_reference": "synthetic-note-1", "retrieved_at": RETRIEVED, "reason": "synthetic inspection",
        "judged_by": 777, "version": RecordingVersionIdentity("orig-A"),
    }
    values.update(overrides)
    return record_evidence(**values)


def test_record_keeps_identifier_source_time_reason_and_judge():
    """@description: Preserve the audit facts a later reviewer needs to trace the evidence.
    @param: none
    @return: None.
    """
    record = make()
    assert (record.evidence_id, record.source_reference, record.retrieved_at, record.reason, record.judged_by) == (
        "ev-1", "synthetic-note-1", RETRIEVED, "synthetic inspection", 777)


@pytest.mark.parametrize("overrides", [
    {"evidence_id": ""},
    {"source_reference": ""},
    {"reason": ""},
    {"reason": " padded reason "},
    {"retrieved_at": datetime(2026, 10, 20, 9, 0)},
    {"judged_by": 0},
    {"judged_by": True},
    {"source_type": "owner_review"},
])
def test_record_refuses_incomplete_or_ambiguous_provenance(overrides):
    """@description: Refuse evidence whose origin, time or judge cannot be audited.
    @param overrides: One invalid provenance field per scenario.
    @return: None.
    """
    with pytest.raises(ValueError):
        make(**overrides)


def test_owner_review_with_version_is_reliable():
    """@description: Accept an audited owner inspection that names a recording version.
    @param: none
    @return: None.
    """
    assert assess_evidence(make()) is EvidenceReliability.RELIABLE


def test_platform_metadata_is_never_reliable_even_with_a_version():
    """@description: Keep titles, tags, channels and video IDs from proving a recording.
    @param: none
    @return: None.
    """
    assert assess_evidence(make(source_type=EvidenceSourceType.PLATFORM_METADATA)) is EvidenceReliability.PENDING


def test_evidence_without_a_version_assertion_stays_pending():
    """@description: Treat a provenance note that asserts no version as insufficient for identity.
    @param: none
    @return: None.
    """
    assert assess_evidence(make(version=None)) is EvidenceReliability.PENDING


def test_later_retrieval_time_is_kept_as_its_own_fact():
    """@description: Keep retrieval time separate from judgment so later records can be ordered.
    @param: none
    @return: None.
    """
    later = make(evidence_id="ev-2", retrieved_at=RETRIEVED + timedelta(hours=1))
    assert later.retrieved_at > make().retrieved_at
