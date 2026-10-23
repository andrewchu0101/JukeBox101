"""Resolve one video's recording version from audited evidence without inferring identity from titles or IDs.

Reliable records agree on one version (CONFIRMED), disagree (CONFLICT), or are absent (UNRESOLVED); pending records never decide.
"""

from collections.abc import Iterable

from bot.evidence_record import EvidenceRecord, EvidenceReliability, assess_evidence
from bot.song_identity import VideoIdentity
from bot.version_identity import VersionAssignment, VersionIdentityStatus


def resolve_video_version(video: VideoIdentity, records: Iterable[EvidenceRecord]) -> VersionAssignment:
    """@description: Turn audited evidence for one video into the identity input the reward evaluator already consumes.
    @param video: Video whose recording identity is being decided.
    @param records: Evidence facts, possibly including other videos or pending noise.
    @return: Unresolved, confirmed or conflicting assignment; never an award decision.
    """
    reliable = [record for record in records if record.video == video and assess_evidence(record) is EvidenceReliability.RELIABLE]
    if not reliable:
        return VersionAssignment(video)
    latest = max(reliable, key=lambda record: (record.retrieved_at, record.evidence_id))  # [cited evidence]: Most recent trusted fact.
    if len({record.version for record in reliable}) > 1:
        return VersionAssignment(video, VersionIdentityStatus.CONFLICT, None, latest.evidence_id)
    return VersionAssignment(video, VersionIdentityStatus.CONFIRMED, latest.version, latest.evidence_id)
