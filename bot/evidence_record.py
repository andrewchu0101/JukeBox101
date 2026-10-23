"""Record auditable recording-version evidence without trusting a source label by itself.

Platform metadata stays pending by rule; evidence becomes reliable only with a named version and the Owner's confirmation.
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


class VersionClaimKind(Enum):
    """List the TK-23 version categories; each member's rule is stated in classify_version_claim."""

    SAME_RECORDING_AUDIO_MV = "same_recording_audio_mv"  # [same recording]: MV and audio of one recording.
    REMIX = "remix"  # [distinct, traceable]: Requires traceable evidence.
    LIVE = "live"  # [distinct, performance]: Requires independent performance evidence.
    COVER = "cover"  # [distinct, performance]: Requires independent performance evidence.
    RE_RECORDING = "re_recording"  # [distinct, evidence]: A new recording requires evidence.
    REMASTER = "remaster"  # [same performance]: Remaster of the same original performance.
    EDIT = "edit"  # [not rewarded]: Clip, excerpt, extended, speed or pitch processing.


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
    owner_confirmed: bool = False  # [trusted confirmation]: Set only at the Owner boundary, never from input or source label.
    version_kind: VersionClaimKind | None = None  # [named category]: Unset names no category, so it satisfies no distinct claim.


def record_evidence(evidence_id: str, video: VideoIdentity, source_type: EvidenceSourceType, source_reference: str,
                    retrieved_at: datetime, reason: str, judged_by: int,
                    version: RecordingVersionIdentity | None = None, *, owner_confirmed: bool = False,
                    version_kind: VersionClaimKind | None = None) -> EvidenceRecord:
    """@description: Refuse evidence whose origin, time or judge cannot be audited later.
    @param evidence_id: Stable identifier for this evidence fact.
    @param video: Video the evidence describes.
    @param source_type: Category of the source.
    @param source_reference: Pointer to where the claim was obtained.
    @param retrieved_at: Timezone-aware retrieval time.
    @param reason: Non-padded audit explanation.
    @param judged_by: Positive internal judge identity.
    @param version: Optional asserted canonical version.
    @param owner_confirmed: Whether the configured Owner confirmed this evidence at the trusted boundary.
    @param version_kind: Optional version category the Owner marked on this evidence.
    @return: Immutable validated evidence record.
    """
    _validate_provenance(evidence_id, source_type, source_reference, retrieved_at, reason, judged_by)
    if not isinstance(video, VideoIdentity) or (version is not None and not isinstance(version, RecordingVersionIdentity)):
        raise ValueError("Valid video and optional version identity are required")
    if type(owner_confirmed) is not bool:
        raise ValueError("A boolean owner confirmation flag is required")
    if version_kind is not None and not isinstance(version_kind, VersionClaimKind):
        raise ValueError("A declared version category or None is required")
    return EvidenceRecord(evidence_id, video, source_type, source_reference, retrieved_at, reason, judged_by, version,
                          owner_confirmed, version_kind)


def assess_evidence(record: EvidenceRecord) -> EvidenceReliability:
    """@description: Decide whether a record may be used as a version assertion, using only its declared facts.
    @param record: Validated evidence record.
    @return: RELIABLE only for a named version from a non-weak source that the Owner confirmed.
    """
    if record.source_type is EvidenceSourceType.PLATFORM_METADATA or record.version is None:
        return EvidenceReliability.PENDING
    if record.owner_confirmed is not True:
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
