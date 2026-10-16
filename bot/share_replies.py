"""Expose approved share-reply scenarios separately from identity and reward decisions.

Only the five user-approved lines are available; this offline adapter performs no randomization or Discord calls.
"""

from enum import Enum
from random import Random
from types import MappingProxyType

from bot.reward_candidates import RewardCandidate
from bot.version_identity import VersionIdentityStatus, VersionRelation, compare_version_assignments


class ShareScenario(Enum):
    """Provide stable presentation keys without embedding prose into the music domain."""

    DUPLICATE_SELF = "DUPLICATE_SELF"
    DUPLICATE_OTHER = "DUPLICATE_OTHER"
    RETRY_SAME_OPERATION = "RETRY_SAME_OPERATION"
    SAME_VERSION_DIFFERENT_VIDEO = "SAME_VERSION_DIFFERENT_VIDEO"
    IDENTITY_UNRESOLVED = "IDENTITY_UNRESOLVED"


# [approved copy]: Exact user-approved text; never interpreted as a settlement or delivery result.
APPROVED_SHARE_REPLIES = MappingProxyType({
    ShareScenario.DUPLICATE_SELF: "又是這首？看來你真的很喜歡它！💿",
    ShareScenario.DUPLICATE_OTHER: "有人搶先一步把這首放進點唱機了！💿",
    ShareScenario.RETRY_SAME_OPERATION: "收到收到！不用連按兩次，我聽見了！🎵",
    ShareScenario.SAME_VERSION_DIFFERENT_VIDEO: "換了張封面，我還是認得這首歌！💿",
    ShareScenario.IDENTITY_UNRESOLVED: "這首歌好像有點耳熟……讓我確認一下！🔍",
})

# [approved scenario pools]: Immutable and isolated; extensions require explicit copy approval.
APPROVED_SHARE_REPLY_POOLS = MappingProxyType({scenario: (text,) for scenario, text in APPROVED_SHARE_REPLIES.items()})


def select_share_reply(scenario: ShareScenario, random_source: Random | None = None) -> str:
    """@description: Select only approved scenario-specific copy without affecting business decisions.
    @param scenario: Stable presentation key from the independent classifier.
    @param random_source: Optional injectable random generator for reproducible selection.
    @return: One exact approved line from this scenario's immutable pool.
    """
    # [approved selection]: No caller-supplied text enters the usable catalog.
    pool = APPROVED_SHARE_REPLY_POOLS[scenario]
    return random_source.choice(pool) if random_source is not None else pool[0]


def classify_share_scenario(candidate: RewardCandidate, previous: tuple[RewardCandidate, ...]) -> ShareScenario | None:
    """@description: Select a deterministic presentation context without changing reward eligibility.
    @param candidate: Trusted current operation with its latest identity assertion.
    @param previous: Earlier operation history, or an identical operation replay.
    @return: One approved scenario key, or None when no approved line fits.
    """
    # [operation replay]: A retry is valid only after every matching history record agrees.
    has_matching_operation = False
    for item in previous:
        if item.operation_id == candidate.operation_id:
            if (item.submitter_id, item.identity.video, item.accepted_sequence) != (candidate.submitter_id, candidate.identity.video, candidate.accepted_sequence):
                raise ValueError("Operation replay has a different submitter or video")
            has_matching_operation = True
    if has_matching_operation:
        return ShareScenario.RETRY_SAME_OPERATION
    if candidate.identity.status is not VersionIdentityStatus.CONFIRMED:
        return ShareScenario.IDENTITY_UNRESOLVED
    # [prior history]: Later accepted operations cannot make an earlier share a duplicate.
    earlier = tuple(item for item in previous if item.accepted_sequence < candidate.accepted_sequence)
    return _duplicate_scenario(candidate, earlier)


def _duplicate_scenario(candidate: RewardCandidate, earlier: tuple[RewardCandidate, ...]) -> ShareScenario | None:
    """@description: Prefer exact-video duplicates before confirmed cross-video version aliases.
    @param candidate: Current confirmed operation.
    @param earlier: Earlier operation history supplied by the caller.
    @return: Specific duplicate scenario, or None for a distinct version.
    """
    # [same-video history]: Exact identity repeats have precedence over version aliases.
    same_video = tuple(item for item in earlier if item.identity.video == candidate.identity.video)
    if any(item.submitter_id == candidate.submitter_id for item in same_video):
        return ShareScenario.DUPLICATE_SELF
    if same_video:
        return ShareScenario.DUPLICATE_OTHER
    if any(compare_version_assignments(item.identity, candidate.identity) is VersionRelation.SAME for item in earlier):
        return ShareScenario.SAME_VERSION_DIFFERENT_VIDEO
    return None


def approved_share_reply(scenario: ShareScenario) -> str:
    """@description: Return only exact approved copy while leaving actual command delivery unimplemented.
    @param scenario: Stable presentation key selected by a future adapter.
    @return: The single approved line for this scenario.
    """
    return APPROVED_SHARE_REPLIES[scenario]
