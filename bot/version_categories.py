"""Classify one version claim against TK-23 category rules without reading titles, tags, channels or video IDs.

Inputs are a claimed category, its subject video and audited evidence; outputs are a same-recording, distinct-with-evidence,
pending or not-separately-rewarded decision that the reward review consumes. Only RELIABLE evidence counts toward a distinct version.
"""

from dataclasses import dataclass
from enum import Enum

from bot.evidence_record import EvidenceRecord, EvidenceReliability, VersionClaimKind, assess_evidence
from bot.song_identity import VideoIdentity
from bot.version_identity import require_reference


class VersionClaimStatus(Enum):
    """Name the four outcomes a version claim can receive, none of which is an award by itself."""

    SAME_RECORDING = "same_recording"  # [no separate unit]: The claim is the same recording as its subject.
    DISTINCT_VERSION_ALLOWED = "distinct_version_allowed"  # [evidence satisfied]: Reliable evidence supports a distinct version.
    NEEDS_EVIDENCE = "needs_evidence"  # [pending]: No reliable evidence of the required kind exists yet.
    NOT_SEPARATELY_REWARDED = "not_separately_rewarded"  # [no separate unit]: Processing of an existing recording.


@dataclass(frozen=True)
class VersionClaim:
    """Carry one asserted version category for a subject video; the name is a label, never proof."""

    kind: VersionClaimKind  # [asserted category]: One of the approved TK-23 categories.
    subject_video: VideoIdentity  # [claim subject]: The video whose version is being classified.
    asserted_version_name: str | None = None  # [label only]: Titles or names are never evidence (Q18).

    def __post_init__(self) -> None:
        """@description: Keep malformed claims from entering classification.
        @param self: Immutable version claim.
        @return: None.
        """
        if not isinstance(self.kind, VersionClaimKind) or not isinstance(self.subject_video, VideoIdentity):
            raise ValueError("A declared version category and subject video are required")
        if self.asserted_version_name is not None:
            require_reference(self.asserted_version_name)


@dataclass(frozen=True)
class VersionClaimDecision:
    """Pair a classification status with the audit reason that explains it."""

    status: VersionClaimStatus  # [classification]: One of the four TK-23 outcomes.
    reason: str  # [audit explanation]: Why this status applies, citing the rule.


def classify_version_claim(claim: VersionClaim, evidence: tuple[EvidenceRecord, ...]) -> VersionClaimDecision:
    """@description: Apply the TK-23 category rules so each claimed version is treated as the same recording, a distinct version, or not rewarded separately.
    Rules (docs/recovery-batchb-private-beta-spec.md §16.4 TK-23 and §17.2; Q17, Q18):
    MV and audio of one recording, and remaster of the same original performance, are the same recording only with reliable
    owner-confirmed evidence naming that category; a category name alone never proves it. Edits (clip, excerpt, extended,
    speed or pitch) are not separately rewarded (Q17); remix, live, cover and re-recording require the same kind of evidence.
    Titles, tags, channels and IDs never satisfy a rule (Q18).
    @param claim: Asserted version category for one subject video.
    @param evidence: Audited evidence facts, possibly about other videos or pending.
    @return: Classification decision with its audit reason.
    """
    if claim.kind is VersionClaimKind.EDIT:
        return VersionClaimDecision(VersionClaimStatus.NOT_SEPARATELY_REWARDED,
                                    "Clip, excerpt, extended, speed or pitch processing is not separately rewarded (Q17).")
    if not _has_reliable_evidence(claim, evidence):
        return VersionClaimDecision(VersionClaimStatus.NEEDS_EVIDENCE,
                                    "No reliable owner-confirmed evidence for this version category yet (TK-23, Q18).")
    if claim.kind in (VersionClaimKind.SAME_RECORDING_AUDIO_MV, VersionClaimKind.REMASTER):
        return VersionClaimDecision(VersionClaimStatus.SAME_RECORDING,
                                    "Reliable owner-confirmed evidence shows MV and audio, or a remaster, is one recording (TK-23).")
    return VersionClaimDecision(VersionClaimStatus.DISTINCT_VERSION_ALLOWED,
                                "Reliable owner-confirmed evidence supports a distinct version (TK-23, Q18).")


def _has_reliable_evidence(claim: VersionClaim, evidence: tuple[EvidenceRecord, ...]) -> bool:
    """@description: Accept only trusted evidence about this video that the Owner marked with the same category as the claim, so a label never proves a version.
    @param claim: Distinct-version claim being classified.
    @param evidence: Audited evidence facts for any video.
    @return: True when a RELIABLE record names this video and this claim's category.
    """
    return any(record.video == claim.subject_video and record.version_kind is claim.kind
               and assess_evidence(record) is EvidenceReliability.RELIABLE for record in evidence)
