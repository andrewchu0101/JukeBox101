"""Represent trusted recording-version assertions independently of video URL syntax.

Offline callers supply canonical identities and evidence references; this module never infers music identity.
"""

from dataclasses import dataclass
from enum import Enum

from bot.song_identity import VideoIdentity


def require_reference(value: object) -> None:
    """@description: Refuse empty or ambiguous internal identifiers and evidence references.
    @param value: Trusted caller's opaque identifier or provenance reference.
    @return: None.
    """
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError("A nonempty reference without surrounding whitespace is required")


@dataclass(frozen=True)
class RecordingVersionIdentity:
    """Name one authoritative recording or arrangement version without deriving it from titles."""

    value: str  # [version identity]: Trusted canonical reference, not a video ID or title match.

    def __post_init__(self) -> None:
        """@description: Keep malformed version references out of candidate comparisons.
        @param self: Immutable canonical version identifier.
        @return: None.
        """
        require_reference(self.value)


class VersionIdentityStatus(Enum):
    """Distinguish missing identity evidence from confirmation and conflicting evidence."""

    UNRESOLVED = "unresolved"
    CONFIRMED = "confirmed"
    CONFLICT = "conflict"


class VersionRelation(Enum):
    """Describe confirmed equality or distinction without optimistic unknown matching."""

    SAME = "same"
    DIFFERENT = "different"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class VersionAssignment:
    """Bind one video to caller-confirmed identity evidence, never user-submitted claims."""

    video: VideoIdentity  # [evidence subject]: Platform identity established by the existing parser.
    status: VersionIdentityStatus = VersionIdentityStatus.UNRESOLVED  # [evidence state]: Never infer confirmation.
    version: RecordingVersionIdentity | None = None  # [canonical version]: Unknown stays unset.
    evidence_reference: str | None = None  # [trusted provenance]: Reference supplied by internal review.

    def __post_init__(self) -> None:
        """@description: Prevent an unresolved or unsupported assertion masquerading as confirmation.
        @param self: Immutable evidence assertion supplied by a trusted internal caller.
        @return: None.
        """
        if not isinstance(self.video, VideoIdentity) or not isinstance(self.status, VersionIdentityStatus):
            raise ValueError("Valid video and identity status are required")
        if self.version is not None and not isinstance(self.version, RecordingVersionIdentity):
            raise ValueError("Invalid recording version identity")
        if self.status is VersionIdentityStatus.UNRESOLVED:
            if self.version is not None or self.evidence_reference is not None:
                raise ValueError("Unresolved identity cannot assert a version or confirmed evidence")
            return
        require_reference(self.evidence_reference)
        if self.status is VersionIdentityStatus.CONFIRMED and self.version is None:
            raise ValueError("Confirmed identity requires a canonical version")


@dataclass(frozen=True)
class VersionExclusion:
    """Assert trusted evidence that a video cannot belong to a particular canonical version."""

    video: VideoIdentity  # [excluded subject]: May still have an unresolved positive identity.
    version: RecordingVersionIdentity  # [excluded version]: Scope of the proven distinction.
    evidence_reference: str  # [trusted provenance]: Never inferred from a Remix/MV/Audio title.

    def __post_init__(self) -> None:
        """@description: Scope unknown-identity release to an explicit, evidenced exclusion.
        @param self: Trusted internal negative identity assertion.
        @return: None.
        """
        if not isinstance(self.video, VideoIdentity) or not isinstance(self.version, RecordingVersionIdentity):
            raise ValueError("Valid video and excluded version are required")
        require_reference(self.evidence_reference)


def compare_version_assignments(first: VersionAssignment, second: VersionAssignment) -> VersionRelation:
    """@description: Compare authoritative assertions without interpreting titles or video IDs as recordings.
    @param first: First caller-supplied video identity assertion.
    @param second: Second caller-supplied video identity assertion.
    @return: Confirmed same/different, unresolved or conflicting relationship.
    """
    if VersionIdentityStatus.CONFLICT in {first.status, second.status}:
        return VersionRelation.CONFLICT
    if VersionIdentityStatus.UNRESOLVED in {first.status, second.status}:
        return VersionRelation.UNRESOLVED
    if first.video == second.video and first.version != second.version:
        return VersionRelation.CONFLICT
    return VersionRelation.SAME if first.version == second.version else VersionRelation.DIFFERENT
