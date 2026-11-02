"""Pin that a stored enum value the running code does not know is refused loudly, never silently coerced.

This is the property that makes rolling back to a build without SYSTEM_JUDGEMENT safe in one direction (the old build refuses to read the new
evidence rows instead of misreading them) and limited in the other (the Owner tools for those videos stop working until the rows are gone).
Pure codec test on a synthetic record; no database.
"""

import json
from datetime import datetime, timezone

import pytest

from bot.database.submission_codec import decode_contract, encode_contract
from bot.evidence_record import EvidenceRecord, EvidenceSourceType, record_evidence
from bot.song_identity import VideoIdentity
from bot.version_identity import RecordingVersionIdentity

TIME = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def stored_system_evidence() -> str:
    """@description: Encode a system-judged evidence record the way the database stores it.
    @param: none
    @return: JSON text.
    """
    record = record_evidence("sys-1", VideoIdentity("Synth0001A1"), EvidenceSourceType.SYSTEM_JUDGEMENT, "auto-compare", TIME,
                             "no concern", 0, RecordingVersionIdentity("rv-1"))
    return encode_contract(record)


def test_the_system_source_is_stored_under_its_enum_name_and_value() -> None:
    """@description: Document the exact stored form so a rollback check can search for it.
    @param: none
    @return: None.
    """
    assert '"system_judgement"' in stored_system_evidence() and '"EvidenceSourceType"' in stored_system_evidence()


def test_a_stored_enum_value_unknown_to_the_running_code_is_refused(monkeypatch) -> None:
    """@description: Remove the member from the lookup, as an older build would see it, and decoding must raise ValueError.
    @param monkeypatch: Temporary member removal.
    @return: None.
    """
    stored = stored_system_evidence()
    monkeypatch.delitem(EvidenceSourceType._value2member_map_, "system_judgement")
    with pytest.raises(ValueError):
        decode_contract(stored, EvidenceRecord)


def test_a_made_up_enum_value_is_refused_by_the_current_code() -> None:
    """@description: Unknown values are refused today as well, not mapped to a default.
    @param: none
    @return: None.
    """
    payload = json.loads(stored_system_evidence())
    assert "system_judgement" in json.dumps(payload)
    tampered = json.dumps(payload).replace("system_judgement", "future_source")
    with pytest.raises(ValueError):
        decode_contract(tampered, EvidenceRecord)
