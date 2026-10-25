"""Verify the live-acceptance guards offline: the database target, the single-row candidate update scope, and the remote command classification.

Recorded honestly: these tests were written after tests/live_acceptance_guard.py, so their first run is a post-hoc check, not a red-first cycle.
"""

import pytest

from tests.live_acceptance_guard import (
    EXPECTED_COMMANDS, LiveGuardRefused, assert_isolated_target, assert_single_candidate_update, classify_remote_commands,
)


def test_the_isolated_target_is_accepted_only_when_every_value_matches() -> None:
    """@description: The exact isolated destination passes.
    @return: None.
    """
    assert_isolated_target("127.0.0.1", 3307, "jukebox101_test", "jukebox101_test_exchange")


@pytest.mark.parametrize("target", [
    ("127.0.0.1", 3306, "jukebox101_test", "jukebox101_test_exchange"),
    ("localhost", 3307, "jukebox101_test", "jukebox101_test_exchange"),
    ("127.0.0.1", 3307, "jukebox101", "jukebox101_test_exchange"),
    ("127.0.0.1", 3307, "jukebox101_test", "root"),
    ("127.0.0.1", 3307, "jukebox101_test", "jukebox101_test_migrator"),
])
def test_any_other_destination_is_refused_before_connecting(target) -> None:
    """@description: 3306, a different host, schema or account, and the migration account are all refused.
    @param target: Destination tuple under test.
    @return: None.
    """
    with pytest.raises(LiveGuardRefused):
        assert_isolated_target(*target)


def test_the_candidate_update_is_allowed_only_for_the_approved_track_and_exactly_one_row() -> None:
    """@description: The one permitted pending-to-verified update matches the approved candidate and touches exactly one row.
    @return: None.
    """
    assert_single_candidate_update(1, "cUfDOS2SINM", "cUfDOS2SINM")


@pytest.mark.parametrize("matched, track", [(0, "cUfDOS2SINM"), (2, "cUfDOS2SINM"), (1, "aHTI0SXGVS0")])
def test_a_candidate_update_that_is_not_one_approved_row_is_refused_before_writing(matched, track) -> None:
    """@description: Zero or several rows, or another track, refuse the update before any write.
    @param matched: Rows matched.
    @param track: Track identifier of the row.
    @return: None.
    """
    with pytest.raises(LiveGuardRefused):
        assert_single_candidate_update(matched, "cUfDOS2SINM", track)


def test_an_empty_remote_guild_is_classified_empty_and_an_unknown_set_stops_the_sync() -> None:
    """@description: No remote commands means the sync may proceed; any other set is reported as other, never overwritten.
    @return: None.
    """
    assert classify_remote_commands(()) == "empty"
    assert classify_remote_commands(("share", "balance")) == "other"


def test_the_approved_five_commands_classify_as_exact_in_any_order() -> None:
    """@description: The exact approved five, in any order, classify as exact.
    @return: None.
    """
    assert classify_remote_commands(tuple(reversed(EXPECTED_COMMANDS))) == "exact"


def test_only_the_exact_live_schema_target_is_accepted_and_every_other_destination_is_refused() -> None:
    """@description: The live schema guard accepts only the approved host, port, schema and exchange account; 3306, other schemas and other accounts are refused.
    @return: None.
    """
    from tests.live_acceptance_guard import LiveGuardRefused, assert_live_schema_target
    assert_live_schema_target("127.0.0.1", 3307, "jukebox101_live_20261025", "jukebox101_test_exchange")
    for host, port, database, user in [("127.0.0.1", 3306, "jukebox101_live_20261025", "jukebox101_test_exchange"),
                                       ("127.0.0.1", 3307, "jukebox101_test", "jukebox101_test_exchange"),
                                       ("127.0.0.1", 3307, "jukebox101_live_20261025", "root"),
                                       ("127.0.0.1", 3307, "jukebox101_live_20261025_x", "jukebox101_test_exchange")]:
        with pytest.raises(LiveGuardRefused):
            assert_live_schema_target(host, port, database, user)
