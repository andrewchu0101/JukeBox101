"""Choose one settled recording version for an exchange reservation from already verified facts.

Inputs are settled versions with their verified YouTube link plus the requester's excluded versions and songs; the output is one uniformly drawn version or none.
"""

from dataclasses import dataclass
from random import Random
from typing import Iterable


@dataclass(frozen=True)
class SettledVersion:
    """Represent one recording version whose first lawful reward has already been settled.

    Carries the verified delivery facts so exchange never guesses a YouTube target.
    """

    version_id: str  # [canonical recording version]: The unit of uniform selection, never a Video ID.
    video_id: str  # [claimed video]: Lets a requester's own Video ID block this song even when its version is unresolved.
    song_id: int  # [delivery song]: The song row whose link would be recommended.
    youtube_url: str | None  # [verified delivery link]: None means the version is not deliverable.
    verified: bool  # [resolution status]: Only verified songs with a link may be delivered.


def deliverable_versions(settled: Iterable[SettledVersion]) -> tuple[SettledVersion, ...]:
    """@description: Keep only versions whose delivery link is verified, so exchange never recommends unverified material.
    @param settled: Settled versions with their song resolution facts.
    @return: Deliverable versions sorted by canonical version so seeded draws do not depend on input order.
    """
    usable = [item for item in settled if item.verified and item.youtube_url]  # [deliverable facts]: Verified link only.
    return tuple(sorted(usable, key=lambda item: item.version_id))


def draw_exchange_candidate(settled: Iterable[SettledVersion], excluded_versions: frozenset[str],
                            excluded_song_ids: frozenset[int], rng: Random,
                            excluded_video_ids: frozenset[str] = frozenset()) -> SettledVersion | None:
    """@description: Draw uniformly among versions the requester may receive, excluding submitted, held and in-flight ones.
    @param settled: Settled versions with their delivery facts.
    @param excluded_versions: Versions the requester submitted, already holds, or has in flight.
    @param excluded_song_ids: Songs the requester already has in any recommendation state.
    @param rng: Injected random source so tests can fix draws without changing the business rule.
    @param excluded_video_ids: Video identifiers the requester submitted, whatever their version state.
    @return: The drawn version, or None when no eligible candidate exists.
    """
    eligible = [item for item in deliverable_versions(settled)
                if item.version_id not in excluded_versions and item.song_id not in excluded_song_ids
                and item.video_id not in excluded_video_ids]  # [eligible pool]: Exclusions apply per version and per requester Video ID.
    if not eligible:
        return None
    return rng.choice(eligible)
