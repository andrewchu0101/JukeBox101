"""Aggregate immutable offline submission facts against an independently supplied scope manifest.

The future repository must certify global coverage; local checks detect missing manifest entries, not dishonest manifests.
"""

from dataclasses import dataclass

from bot.reward_candidates import RewardCandidate, RewardEvaluationInput
from bot.version_identity import VersionAssignment, VersionExclusion, VersionIdentityStatus, require_reference


@dataclass(frozen=True)
class SnapshotManifest:
    """Describe repository-certified global coverage at one consistent read revision."""

    revision: str  # [read revision]: All facts must come from this same consistent read.
    operation_ids: frozenset[str]  # [expected intake]: Includes every accepted operation through the watermark.
    historical_video_ids: frozenset[str]  # [expected catalog]: Includes unresolved legacy videos too.
    through_sequence: int  # [intake watermark]: Reliable database order, simulated offline only.
    coverage_certified: bool = False  # [external guarantee]: Cannot be inferred from the supplied rows.
    historical_assertion_count: int = 0  # [catalog coverage]: Includes conflicting assertions for the same video.
    exclusion_references: frozenset[str] = frozenset()  # [negative evidence coverage]: Required proof references.

    def __post_init__(self) -> None:
        """@description: Reject malformed coverage assertions without inventing completeness.
        @param self: Trusted repository manifest, never direct user input.
        @return: None.
        """
        require_reference(self.revision)
        if type(self.through_sequence) is not int or self.through_sequence < 0:
            raise ValueError("Invalid intake watermark")
        if type(self.coverage_certified) is not bool:
            raise ValueError("Coverage certification must be explicit")
        if type(self.historical_assertion_count) is not int or self.historical_assertion_count < 0:
            raise ValueError("Invalid historical assertion count")
        for identifiers in (self.operation_ids, self.historical_video_ids, self.exclusion_references):
            if not isinstance(identifiers, frozenset):
                raise ValueError("Manifest identifiers must be immutable")
            for identifier in identifiers:
                require_reference(identifier)


@dataclass(frozen=True)
class SubmissionSnapshot:
    """Keep current review revisions, provenance and legacy evidence without merging assertions."""

    manifest: SnapshotManifest  # [coverage authority]: Independent expected scope, not derived from rows.
    revision: str  # [fact revision]: Mismatch makes the snapshot incomplete.
    candidates: tuple[RewardCandidate, ...] = ()  # [current projection]: One authoritative revision per operation.
    historical: tuple[VersionAssignment, ...] = ()  # [catalog evidence]: Unknown and conflicting history remain visible.
    exclusions: tuple[VersionExclusion, ...] = ()  # [trusted negative evidence]: Scoped version exclusions only.

    def __post_init__(self) -> None:
        """@description: Keep mutable or malformed facts outside snapshot aggregation.
        @param self: Internally supplied consistent-read facts.
        @return: None.
        """
        if not isinstance(self.manifest, SnapshotManifest):
            raise ValueError("A repository manifest is required")
        require_reference(self.revision)
        for values, expected_type in ((self.candidates, RewardCandidate),
                                      (self.historical, VersionAssignment), (self.exclusions, VersionExclusion)):
            if not isinstance(values, tuple) or not all(isinstance(item, expected_type) for item in values):
                raise ValueError("Snapshot facts must be immutable typed tuples")


def snapshot_is_complete(snapshot: SubmissionSnapshot) -> bool:
    """@description: Withhold completeness when any certified scope entry or read revision is missing.
    @param snapshot: Facts and independently certified expected scope.
    @return: Local coverage agreement, conditional on the repository certification being truthful.
    """
    # [manifest authority]: Sequence gaps alone cannot establish or refute database completeness.
    manifest = snapshot.manifest
    return (manifest.coverage_certified and snapshot.revision == manifest.revision
            and _scope_rows_match(snapshot)
            and all(item.accepted_sequence <= manifest.through_sequence for item in snapshot.candidates))


def _scope_rows_match(snapshot: SubmissionSnapshot) -> bool:
    """@description: Detect missing operation, catalog or exclusion rows against independent expectations.
    @param snapshot: Immutable row facts and the trusted expected scope.
    @return: Whether all locally checkable row coverage requirements match.
    """
    return (frozenset(item.operation_id for item in snapshot.candidates) == snapshot.manifest.operation_ids
            and frozenset(item.video.video_id for item in snapshot.historical) == snapshot.manifest.historical_video_ids
            and len(snapshot.historical) == snapshot.manifest.historical_assertion_count
            and frozenset(item.evidence_reference for item in snapshot.exclusions) == snapshot.manifest.exclusion_references)


def aggregate_snapshot(snapshot: SubmissionSnapshot) -> RewardEvaluationInput:
    """@description: Reuse the candidate evaluator while retaining unknown history for an additional gate.
    @param snapshot: Trusted current candidates, catalog evidence and manifest.
    @return: Evaluation scope whose completeness is checked against the manifest.
    """
    # [known catalog]: Confirmed legacy versions never reopen first-reward eligibility.
    historical_versions = frozenset(item.version for item in snapshot.historical
                                    if item.status is VersionIdentityStatus.CONFIRMED)
    return RewardEvaluationInput(snapshot.candidates, historical_versions, snapshot.exclusions,
                                 snapshot_is_complete(snapshot))
