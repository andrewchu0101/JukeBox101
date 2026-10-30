"""Verify persisted contracts round-trip completely and unknown types remain refused.

Pure codec tests on synthetic values; no database, MySQL or Discord.
"""

import json
from datetime import datetime, timezone

import pytest

from bot.database.submission_codec import decode_contract, encode_contract
from bot.evidence_record import EvidenceRecord, EvidenceSourceType, VersionClaimKind, record_evidence
from bot.song_identity import VideoIdentity
from bot.song_judgement import ContentJudgement
from bot.version_identity import RecordingVersionIdentity

VIDEO = VideoIdentity("Synth0001A1")
TIME = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


def test_evidence_with_version_kind_round_trips_identically():
    """@description: A record carrying the claimed category must survive storage unchanged.
    @param: none
    @return: None.
    """
    record = record_evidence("ev-remix", VIDEO, EvidenceSourceType.OWNER_REVIEW, "synthetic-note", TIME,
                             "synthetic inspection", 777, RecordingVersionIdentity("synth-remix"),
                             owner_confirmed=True, version_kind=VersionClaimKind.REMIX)
    assert decode_contract(encode_contract(record), EvidenceRecord) == record


def test_full_content_judgement_round_trips_identically():
    """@description: The complete content judgement, including its video and judge, must be stored and read back.
    @param: none
    @return: None.
    """
    judgement = ContentJudgement(VIDEO, 777, "synthetic song judgement", TIME, "ev-content-1", True)
    assert decode_contract(encode_contract(judgement), ContentJudgement) == judgement


def test_legacy_evidence_without_version_kind_decodes_with_none():
    """@description: Evidence stored before the category field existed must decode with no declared category.
    @param: none
    @return: None.
    """
    record = record_evidence("ev-old", VIDEO, EvidenceSourceType.OWNER_REVIEW, "synthetic-note", TIME,
                             "synthetic inspection", 777, RecordingVersionIdentity("synth-remix"), owner_confirmed=True)
    document = json.loads(encode_contract(record))
    document["value"]["fields"].pop("version_kind")
    decoded = decode_contract(json.dumps(document), EvidenceRecord)
    assert decoded.version_kind is None


def test_unknown_enum_name_is_refused_on_decode():
    """@description: A stored enum name outside the allowlist must never be reconstructed.
    @param: none
    @return: None.
    """
    document = json.loads(encode_contract(record_evidence("ev-x", VIDEO, EvidenceSourceType.OWNER_REVIEW, "n", TIME,
                                                          "r", 777, RecordingVersionIdentity("v"))))
    document["value"]["fields"]["source_type"] = {"enum": "UnknownKind", "value": "owner_review"}
    with pytest.raises(ValueError):
        decode_contract(json.dumps(document), EvidenceRecord)


def test_unknown_contract_name_is_refused_on_decode():
    """@description: A stored contract name outside the allowlist must never be reconstructed.
    @param: none
    @return: None.
    """
    payload = json.dumps({"codec": 1, "value": {"contract": "NotAContract", "fields": {}}})
    with pytest.raises(ValueError):
        decode_contract(payload, EvidenceRecord)
