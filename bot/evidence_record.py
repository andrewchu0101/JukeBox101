"""Record auditable recording-version evidence without trusting a source label by itself.

Platform metadata stays pending by rule; owner inspections and recording references become reliable only with a named version.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from bot.song_identity import VideoIdentity
from bot.version_identity import RecordingVersionIdentity, require_reference

MAX_REASON_LENGTH = 1024  # [audit explanation limit]: Matches the persisted reason column.


class EvidenceSourceType(Enum):
    """Name where a claim came from; the name alone never proves the claim."""

    OWNER_REVIEW = "owner_review"  # [internal inspection]: An Owner's written judgment after review.
    RECORDING_REFERENCE = "recording_reference"  # [documented reference]: An internal citation to a known recording.
    PLATFORM_METADATA = "platform_metadata"  # [weak signal]: Titles, tags, channels and IDs are never sufficient.


class EvidenceReliability(Enum):
    """Separate usable identity evidence from evidence that must remain pending."""

    RELIABLE = "reliable"  # [identity input]: Trusted provenance that names a version.
    PENDING = "pending"  # [kept for review]: Recorded for audit but not used to confirm identity.


@dataclass(frozen=True)
class EvidenceRecord:
    """Carry one immutable evidence fact with provenance, time, reason and judge."""

    evidence_id: str  # [evidence identity]: Stable reference cited by versions and audit rows.
    video: VideoIdentity  # [evidence subject]: The exact video this record describes.
    source_type: EvidenceSourceType  # [origin category]: Not proof of reliability by itself.
    source_reference: str  # [provenance pointer]: Where the claim was obtained, without fetching it here.
    retrieved_at: datetime  # [retrieval time]: Timezone-aware, so later records can be ordered.
    reason: str  # [audit explanation]: Why the judge considers this evidence relevant.
    judged_by: int  # [judge identity]: Internal Owner Discord identity, never inferred from the record content.
    version: RecordingVersionIdentity | None = None  # [asserted version]: Unset means no identity claim.


def record_evidence(evidence_id: str, video: VideoIdentity, source_type: EvidenceSourceType, source_reference: str,
                    retrieved_at: datetime, reason: str, judged_by: int,
                    version: RecordingVersionIdentity | None = None) -> EvidenceRecord:
    """@description: Refuse evidence whose origin, time or judge cannot be audited later.
    @param evidence_id: Stable identifier for this evidence fact.
    @param video: Video the evidence describes.
    @param source_type: Category of the source.
    @param source_reference: Pointer to where the claim was obtained.
    @param retrieved_at: Timezone-aware retrieval time.
    @param reason: Non-padded audit explanation.
    @param judged_by: Positive internal judge identity.
    @param version: Optional asserted canonical version.
    @return: Immutable validated evidence record.
    """
    _validate_provenance(evidence_id, source_type, source_reference, retrieved_at, reason, judged_by)
    if not isinstance(video, VideoIdentity) or (version is not None and not isinstance(version, RecordingVersionIdentity)):
        raise ValueError("Valid video and optional version identity are required")
    return EvidenceRecord(evidence_id, video, source_type, source_reference, retrieved_at, reason, judged_by, version)


def assess_evidence(record: EvidenceRecord) -> EvidenceReliability:
    """@description: Decide whether a record may be used as a version assertion, using only its declared facts.
    @param record: Validated evidence record.
    @return: RELIABLE only for a named version from a non-weak source.
    """
    if record.source_type is EvidenceSourceType.PLATFORM_METADATA or record.version is None:
        return EvidenceReliability.PENDING
    return EvidenceReliability.RELIABLE


def _validate_provenance(evidence_id: str, source_type: EvidenceSourceType, source_reference: str,
                         retrieved_at: datetime, reason: str, judged_by: int) -> None:
    """@description: Keep every audit field nonempty, unpadded and typed before the record exists.
    @param evidence_id: Stable identifier.
    @param source_type: Declared source category.
    @param source_reference: Provenance pointer.
    @param retrieved_at: Retrieval time.
    @param reason: Audit explanation.
    @param judged_by: Judge identity.
    @return: None.
    """
    require_reference(evidence_id)
    require_reference(source_reference)
    require_reference(reason)
    if len(reason) > MAX_REASON_LENGTH:
        raise ValueError("Audit reason is too long")
    if not isinstance(source_type, EvidenceSourceType):
        raise ValueError("A declared evidence source type is required")
    if not isinstance(retrieved_at, datetime) or retrieved_at.tzinfo is None:
        raise ValueError("A timezone-aware retrieval time is required")
    if type(judged_by) is not int or judged_by <= 0:
        raise ValueError("A positive internal judge identity is required")
