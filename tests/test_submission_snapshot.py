"""Check immutable repository coverage contracts using synthetic manifests only.

Tests demonstrate local omission detection without claiming to authenticate the external source's completeness.
"""

from dataclasses import replace

import pytest

from bot.submission_snapshot import SnapshotManifest, SubmissionSnapshot, aggregate_snapshot, snapshot_is_complete
from tests.version_candidate_support import make_candidate


def test_complete_manifest_is_not_inferred_from_empty_or_filtered_rows() -> None:
    """@description: Require an independent certificate before treating even an empty universe as complete.
    @param: none
    @return: None.
    """
    # [uncertified universe]: Matching empty sets do not imply repository coverage.
    manifest = SnapshotManifest("r1", frozenset(), frozenset(), 0)
    snapshot = SubmissionSnapshot(manifest, "r1")
    assert not snapshot_is_complete(snapshot)
    assert not aggregate_snapshot(snapshot).complete
    assert snapshot_is_complete(replace(snapshot, manifest=replace(manifest, coverage_certified=True)))


@pytest.mark.parametrize("field,value", [("through_sequence", True), ("through_sequence", -1),
                                        ("coverage_certified", 1), ("operation_ids", {"op-1"}),
                                        ("historical_video_ids", frozenset({""})), ("revision", " "),
                                        ("historical_assertion_count", True), ("historical_assertion_count", -1),
                                        ("exclusion_references", {"proof"})])
def test_malformed_manifest_is_rejected(field: str, value: object) -> None:
    """@description: Reject malformed scope facts rather than coercing them into trusted coverage.
    @param field: Manifest field being corrupted.
    @param value: Invalid synthetic value for that field.
    @return: None.
    """
    # [valid coverage contract]: Each parameter changes only one independently checked attribute.
    manifest = SnapshotManifest("r1", frozenset(), frozenset(), 0)
    with pytest.raises(ValueError):
        replace(manifest, **{field: value})


@pytest.mark.parametrize("field,value", [("candidates", []), ("candidates", (object(),)),
                                        ("historical", []), ("exclusions", []), ("revision", ""), ("manifest", None)])
def test_mutable_or_untyped_snapshot_facts_are_rejected(field: str, value: object) -> None:
    """@description: Keep snapshots immutable and preserve typed trusted-evidence boundaries.
    @param field: Snapshot attribute being corrupted.
    @param value: Invalid synthetic replacement.
    @return: None.
    """
    # [valid empty snapshot]: No row facts are silently accepted from arbitrary mutable containers.
    snapshot = SubmissionSnapshot(SnapshotManifest("r1", frozenset(), frozenset(), 0), "r1")
    with pytest.raises(ValueError):
        replace(snapshot, **{field: value})


def test_unknown_history_is_preserved_without_fabricating_version() -> None:
    """@description: Preserve unresolved catalog evidence while passing only confirmed history to the old evaluator.
    @param: none
    @return: None.
    """
    # [legacy facts]: Unknown history remains in the aggregate source for the workflow's conservative gate.
    known, unknown = make_candidate(1), make_candidate(2, None)
    manifest = SnapshotManifest("r1", frozenset(), frozenset({known.identity.video.video_id, unknown.identity.video.video_id}), 0, True, 2)
    snapshot = SubmissionSnapshot(manifest, "r1", historical=(known.identity, unknown.identity))
    assert aggregate_snapshot(snapshot).historical_versions == frozenset({known.identity.version})
    assert snapshot.historical[1].version is None and snapshot_is_complete(snapshot)


def test_missing_conflicting_catalog_assertion_or_exclusion_is_incomplete() -> None:
    """@description: Detect omitted same-video historical assertions and missing negative evidence.
    @param: none
    @return: None.
    """
    # [catalog manifest]: Two assertions for one video must not collapse into a misleading single row.
    known = make_candidate(1)
    manifest = SnapshotManifest("r1", frozenset(), frozenset({known.identity.video.video_id}), 0, True, 2)
    snapshot = SubmissionSnapshot(manifest, "r1", historical=(known.identity,))
    assert not snapshot_is_complete(snapshot)
    # [missing exclusion]: Independent scope says a proof exists, even though no row was supplied.
    manifest = replace(manifest, historical_assertion_count=1, exclusion_references=frozenset({"proof"}))
    assert not snapshot_is_complete(replace(snapshot, manifest=manifest))
