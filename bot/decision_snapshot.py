"""Freeze the facts an Owner preview was based on, so a confirmation refuses when any of them changed.

The snapshot covers the persisted version identity, its canonical root and the Owner evidence set for one video; the
review revision alone does not capture identity, canonical mapping or evidence changes, which is why it is checked too.
"""

from dataclasses import dataclass

from bot.review import StaleReviewVersion
from bot.version_identity import RecordingVersionIdentity, VersionAssignment


@dataclass(frozen=True)
class DecisionSnapshot:
    """Carry the identity, canonical root and evidence identifiers that one Owner preview observed."""

    identity: VersionAssignment  # [previewed identity]: Persisted assertion exactly as the Owner saw it.
    canonical_version: RecordingVersionIdentity | None  # [canonical root]: Root the identity resolved to at preview time.
    evidence_fingerprints: tuple[str, ...]  # [evidence set]: Digest of each stored Owner evidence record's full content, in stored order.


def snapshot_intent(snapshot: DecisionSnapshot) -> tuple:
    """@description: Flatten a snapshot into primitive facts so its digest is stable across processes and encodings.
    @param snapshot: Previewed facts bound into one decision's intent.
    @return: Tuple of identifiers, statuses and evidence fingerprints in a fixed order.
    """
    identity = snapshot.identity
    version_value = identity.version.value if identity.version is not None else None
    canonical_value = snapshot.canonical_version.value if snapshot.canonical_version is not None else None
    return (identity.video.video_id, identity.status.value, version_value, identity.evidence_reference, canonical_value,
            snapshot.evidence_fingerprints)


def require_unchanged_snapshot(previewed: DecisionSnapshot | None, current: DecisionSnapshot) -> None:
    """@description: Refuse a confirmation whose previewed facts no longer match the stored state, before any write happens.
    @param previewed: Snapshot the Owner's preview carried, or None for an intent that cites no preview.
    @param current: Snapshot read inside the confirmation's locked transaction.
    @return: None; raises StaleReviewVersion so the Owner must preview the decision again.
    """
    if previewed is None or previewed == current:
        return
    raise StaleReviewVersion("Identity, canonical mapping or evidence changed after preview; preview the decision again")
