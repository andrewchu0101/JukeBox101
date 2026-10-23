"""Verify exchange candidate selection excludes submitted, held, blocked and unverified recording versions.

Pure synthetic facts only; no database, Discord, environment file or network is involved.
"""

import random
from collections import Counter

from bot.exchange_selection import SettledVersion, deliverable_versions, draw_exchange_candidate


def settled(version_id: str, song_id: int, youtube_url: str | None = "https://www.youtube.com/watch?v=synthetic", verified: bool = True) -> SettledVersion:
    """@description: Build one synthetic settled version with explicit delivery facts.
    @param version_id: Canonical synthetic recording version.
    @param song_id: Synthetic song row that would be delivered.
    @param youtube_url: Verified delivery link, or None when absent.
    @param verified: Whether the song resolution is verified.
    @return: Settled version fact.
    """
    return SettledVersion(version_id=version_id, video_id=f"video-{song_id}", song_id=song_id, youtube_url=youtube_url, verified=verified)


POOL = (settled("v-a", 1), settled("v-b", 2), settled("v-c", 3))


def test_draw_returns_one_of_the_eligible_versions() -> None:
    """@description: A draw with no exclusions returns one settled version that is deliverable.
    @param: none
    @return: None.
    """
    drawn = draw_exchange_candidate(POOL, frozenset(), frozenset(), random.Random(1))
    assert drawn in POOL


def test_submitted_version_is_never_drawn() -> None:
    """@description: The requester's own recording version is excluded even when it is the only other option.
    @param: none
    @return: None.
    """
    for seed in range(50):
        drawn = draw_exchange_candidate(POOL, frozenset({"v-a", "v-b"}), frozenset(), random.Random(seed))
        assert drawn.version_id == "v-c"


def test_held_or_in_flight_song_is_never_drawn() -> None:
    """@description: A song the requester already holds in any recommendation state is excluded.
    @param: none
    @return: None.
    """
    for seed in range(50):
        drawn = draw_exchange_candidate(POOL, frozenset(), frozenset({1, 2}), random.Random(seed))
        assert drawn.song_id == 3


def test_unverified_or_linkless_versions_are_not_deliverable() -> None:
    """@description: A version without a verified YouTube link is never recommended.
    @param: none
    @return: None.
    """
    pool = (settled("v-a", 1, verified=False), settled("v-b", 2, youtube_url=None), settled("v-c", 3))
    assert deliverable_versions(pool) == (settled("v-c", 3),)
    assert draw_exchange_candidate(pool, frozenset(), frozenset(), random.Random(7)).version_id == "v-c"


def test_no_eligible_candidate_returns_none() -> None:
    """@description: When every version is excluded the result is None, so the caller charges nothing.
    @param: none
    @return: None.
    """
    assert draw_exchange_candidate(POOL, frozenset({"v-a", "v-b", "v-c"}), frozenset(), random.Random(3)) is None
    assert draw_exchange_candidate((), frozenset(), frozenset(), random.Random(3)) is None


def test_draws_are_uniform_over_versions_not_over_song_rows() -> None:
    """@description: Each eligible recording version has the same chance, independent of how many rows it has.
    @param: none
    @return: None.
    """
    rng = random.Random(20261023)
    counts = Counter(draw_exchange_candidate(POOL, frozenset(), frozenset(), rng).version_id for _ in range(3000))
    assert set(counts) == {"v-a", "v-b", "v-c"}
    assert all(800 <= count <= 1200 for count in counts.values())


def test_draw_is_reproducible_regardless_of_input_order() -> None:
    """@description: The same seed selects the same version even when the settled facts arrive in a different order.
    @param: none
    @return: None.
    """
    reversed_pool = tuple(reversed(POOL))
    first = draw_exchange_candidate(POOL, frozenset(), frozenset(), random.Random(99))
    second = draw_exchange_candidate(reversed_pool, frozenset(), frozenset(), random.Random(99))
    assert first == second


def test_a_requester_video_id_blocks_its_song_even_when_the_version_is_unresolved() -> None:
    """@description: A Video ID the requester submitted is excluded even when no version is known for that submission.
    @param: none
    @return: None.
    """
    for seed in range(20):
        drawn = draw_exchange_candidate(POOL, frozenset(), frozenset(), random.Random(seed), frozenset({"video-2", "video-3"}))
        assert drawn.song_id == 1
