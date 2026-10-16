"""Serialize immutable submission contracts using explicit JSON type allowlists.

Database JSON is decoded without importing names or executing objects; existing constructors revalidate domain invariants.
"""

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from bot.eligibility import DurationEvidence
from bot.review import ApprovalSource, ReviewSnapshot, ReviewStatus, ValidationError
from bot.reward_candidates import RewardCandidate, RewardCandidateStatus, RewardDecision, RewardDecisionReason
from bot.share_replies import ShareScenario
from bot.song_identity import VideoIdentity
from bot.submission_workflow import SubmissionResult
from bot.urls import YouTubeVideo
from bot.version_identity import RecordingVersionIdentity, VersionAssignment, VersionIdentityStatus, VersionExclusion

# [persisted contracts]: Only reviewed internal value types may be reconstructed from database JSON.
CONTRACTS = {item.__name__: item for item in (DurationEvidence, ReviewSnapshot, RewardCandidate, RewardDecision,
             VideoIdentity, SubmissionResult, YouTubeVideo, RecordingVersionIdentity, VersionAssignment, VersionExclusion)}
# [enum allowlist]: Presentation keys are stored separately from business decisions.
ENUMS = {item.__name__: item for item in (ApprovalSource, ReviewStatus, ValidationError, RewardCandidateStatus,
         RewardDecisionReason, ShareScenario, VersionIdentityStatus)}


def encode_contract(value: object) -> str:
    """@description: Produce deterministic versioned JSON receipts without pickle or dynamic imports.
    @param value: Reviewed immutable domain contract.
    @return: Canonical JSON text with an explicit codec version.
    """
    return json.dumps({"codec": 1, "value": _pack(value)}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_contract(payload: str, expected: type) -> object:
    """@description: Fail closed on corrupt or unexpected persisted contract types.
    @param payload: Database JSON text in the supported codec format.
    @param expected: Exact required top-level domain type.
    @return: Revalidated immutable contract of the required type.
    """
    # [versioned receipt]: Unknown future codecs cannot silently change retry semantics.
    document = json.loads(payload)
    if document.get("codec") != 1 or set(document) != {"codec", "value"}:
        raise ValueError("Unsupported persisted contract codec")
    # [decoded fact]: Constructors preserve existing review and identity validation.
    value = _unpack(document["value"])
    if type(value) is not expected:
        raise ValueError("Unexpected persisted contract type")
    return value


def _pack(value: object) -> object:
    """@description: Restrict persistence to explicit safe primitives and approved value types.
    @param value: Domain value being encoded recursively.
    @return: JSON-compatible representation.
    """
    if value is None or type(value) in (str, int, bool):
        return value
    if isinstance(value, Enum) and ENUMS.get(type(value).__name__) is type(value):
        return {"enum": type(value).__name__, "value": value.value}
    if is_dataclass(value) and CONTRACTS.get(type(value).__name__) is type(value):
        return {"contract": type(value).__name__, "fields": {field.name: _pack(getattr(value, field.name)) for field in fields(value)}}
    return _pack_container_scalar(value)


def _pack_container_scalar(value: object) -> dict:
    """@description: Keep exact duration, timestamp and tuple encoding separate from domain type dispatch.
    @param value: Nonprimitive supported scalar or immutable tuple.
    @return: Tagged JSON representation.
    """
    if type(value) in (datetime, Decimal):
        return {"scalar": type(value).__name__, "value": str(value)}
    if isinstance(value, tuple):
        return {"tuple": [_pack(item) for item in value]}
    raise ValueError("Unsupported persisted value")


def _unpack(value: object) -> object:
    """@description: Reconstruct allowlisted data without trusting executable type names.
    @param value: Parsed JSON value.
    @return: Safe primitive or validated immutable domain object.
    """
    if value is None or type(value) in (str, int, bool):
        return value
    if not isinstance(value, dict):
        raise ValueError("Invalid persisted representation")
    if set(value) == {"enum", "value"} and value["enum"] in ENUMS:
        return ENUMS[value["enum"]](value["value"])
    if set(value) == {"contract", "fields"} and value["contract"] in CONTRACTS:
        return CONTRACTS[value["contract"]](**{key: _unpack(item) for key, item in value["fields"].items()})
    return _unpack_container_scalar(value)


def _unpack_container_scalar(value: dict) -> object:
    """@description: Decode only known nonexecutable container and scalar tags.
    @param value: Parsed tagged JSON representation.
    @return: Safe immutable tuple or exact scalar.
    """
    if set(value) == {"scalar", "value"}:
        return _decode_scalar(value["scalar"], value["value"])
    if set(value) == {"tuple"}:
        return tuple(_unpack(item) for item in value["tuple"])
    raise ValueError("Unknown persisted representation")


def _decode_scalar(kind: str, value: str) -> datetime | Decimal:
    """@description: Decode only exact duration and timestamp scalar formats.
    @param kind: Allowlisted scalar name.
    @param value: Persisted scalar text.
    @return: Exact Decimal or datetime value.
    """
    if kind == "datetime":
        return datetime.fromisoformat(value)
    if kind == "Decimal":
        return Decimal(value)
    raise ValueError("Unknown persisted scalar")
