"""Resolve one video's recording version from audited evidence without inferring identity from titles or IDs.

A reliable record confirms its asserted version only when the Owner marked it with a category that the TK-23 rules accept;
reliable records that assert different versions give CONFLICT; pending records never decide.
"""

from collections.abc import Iterable

from bot.evidence_record import EvidenceRecord, EvidenceReliability, VersionClaimKind, assess_evidence
from bot.song_identity import VideoIdentity
from bot.version_categories import VersionClaim, VersionClaimStatus, classify_version_claim
from bot.version_identity import VersionAssignment, VersionIdentityStatus

CONFIRMING_CLAIM_STATUSES = frozenset({VersionClaimStatus.SAME_RECORDING, VersionClaimStatus.DISTINCT_VERSION_ALLOWED})


def resolve_video_version(video: VideoIdentity, records: Iterable[EvidenceRecord]) -> VersionAssignment:
    """@description: Turn audited evidence for one video into the identity input the reward evaluator already consumes.
    Rules (docs/recovery-batchb-private-beta-spec.md §16.4 TK-23, §17.2 and §16.1 S3; Q12, Q16, Q17, Q18):
    - Pending records (platform metadata, not Owner-confirmed, or naming no version) never decide.
    - EDIT records (clip, excerpt, extended, speed or pitch) neither confirm nor contradict a version (Q17).
    - A record that names no category (version_kind None) never confirms a version, distinct or not (Q18).
    - A record confirms its asserted version only when classify_version_claim accepts its own category as SAME_RECORDING
      or DISTINCT_VERSION_ALLOWED; NEEDS_EVIDENCE and NOT_SEPARATELY_REWARDED never confirm.
    - Reliable records asserting different versions give CONFLICT, whatever their categories (S3: 矛盾為 CONFLICT).
    - Otherwise the latest confirming record's version is CONFIRMED; with no confirming record the identity is UNRESOLVED.
    @param video: Video whose recording identity is being decided.
    @param records: Evidence facts, possibly including other videos or pending noise.
    @return: Unresolved, confirmed or conflicting assignment; never an award decision.
    """
    reliable = [record for record in records if record.video == video and _counts_toward_identity(record)]
    if not reliable:
        return VersionAssignment(video)
    if len({record.version for record in reliable}) > 1:
        latest_contradiction = max(reliable, key=_recency_key)  # [cited evidence]: Most recent trusted fact among the disagreement.
        return VersionAssignment(video, VersionIdentityStatus.CONFLICT, None, latest_contradiction.evidence_id)
    confirming = [record for record in reliable if _confirms_version(record)]
    if not confirming:
        return VersionAssignment(video)
    latest = max(confirming, key=_recency_key)  # [cited evidence]: Most recent record that the category rules accept.
    return VersionAssignment(video, VersionIdentityStatus.CONFIRMED, latest.version, latest.evidence_id)


def _counts_toward_identity(record: EvidenceRecord) -> bool:
    """@description: Keep only trusted, non-edit facts, since edits never create or contradict a recording version.
    @param record: Evidence record for any video.
    @return: True when the record is RELIABLE and its category is not EDIT.
    """
    return assess_evidence(record) is EvidenceReliability.RELIABLE and record.version_kind is not VersionClaimKind.EDIT


def _confirms_version(record: EvidenceRecord) -> bool:
    """@description: Ask the TK-23 category rules whether one reliable record may confirm the version it asserts.
    @param record: Reliable evidence record that names a version.
    @return: True only when the record names a category and classify_version_claim accepts it.
    """
    if record.version_kind is None:
        return False
    decision = classify_version_claim(VersionClaim(record.version_kind, record.video), (record,))
    return decision.status in CONFIRMING_CLAIM_STATUSES


def _recency_key(record: EvidenceRecord) -> tuple:
    """@description: Order evidence by retrieval time so the identity cites the most recent supporting fact.
    @param record: Evidence record being compared.
    @return: Retrieval time and identifier, which is deterministic when times tie.
    """
    return (record.retrieved_at, record.evidence_id)
