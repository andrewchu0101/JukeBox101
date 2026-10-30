"""Verify the live-acceptance step guards offline: candidate row identity before the single verified update, the settlement window scope,
the control-row transition, and the approved Discord identifiers.

Red-first: these tests were written before the guard functions they name, so the first run fails on the missing names.
"""

import pytest

from tests.live_acceptance_guard import (
    LiveGuardRefused, assert_candidate_before_update, assert_control_transition, assert_discord_identifiers,
    assert_settlement_scope, expected_intake_control,
)

C_URL = "https://youtu.be/cUfDOS2SINM"
C_TRACK = "cUfDOS2SINM"


def candidate_row(**overrides) -> dict:
    """@description: A candidate row as the intake leaves it: pending, no delivery link, the approved source URL.
    @param overrides: Field values to change for a negative case.
    @return: Row dictionary.
    """
    row = {"platform": "youtube", "platform_track_id": C_TRACK, "source_url": C_URL,
           "resolution_status": "pending", "youtube_url": None}
    row.update(overrides)
    return row


def test_the_candidate_row_is_accepted_only_when_it_is_the_pending_approved_intake() -> None:
    """@description: The pending row with the approved URL and no delivery link is the one row allowed for the update.
    @return: None.
    """
    assert_candidate_before_update(candidate_row(), C_URL, C_TRACK)


@pytest.mark.parametrize("override", [
    {"resolution_status": "verified"},
    {"youtube_url": "https://youtu.be/cUfDOS2SINM"},
    {"source_url": "https://youtu.be/aHTI0SXGVS0"},
    {"platform": "spotify"},
    {"platform_track_id": "aHTI0SXGVS0"},
])
def test_any_other_candidate_state_is_refused_before_the_update(override) -> None:
    """@description: A row that is not the pending approved intake, or already has a link or another identity, is refused before any write.
    @param override: Field change that makes the row different from the approved state.
    @return: None.
    """
    with pytest.raises(LiveGuardRefused):
        assert_candidate_before_update(candidate_row(**override), C_URL, C_TRACK)


def test_the_settlement_window_may_name_only_the_two_approved_operations() -> None:
    """@description: The settlement window settles exactly the approved operations, no more and no fewer.
    @return: None.
    """
    assert_settlement_scope({"op-c", "op-d"}, {"op-c", "op-d"})
    with pytest.raises(LiveGuardRefused):
        assert_settlement_scope({"op-c", "op-d", "op-x"}, {"op-c", "op-d"})
    with pytest.raises(LiveGuardRefused):
        assert_settlement_scope({"op-c"}, {"op-c", "op-d"})


def test_the_intake_control_is_the_reviewed_state_with_settlement_off() -> None:
    """@description: The intake control state carries the reviewed flags with settlement disabled and both counters at zero.
    @return: None.
    """
    assert expected_intake_control() == {"schema_version": "2b2-b1", "accepted_sequence": 0, "revision": 0,
                                         "snapshot_ready": 1, "legacy_writers_quiesced": 1, "settlement_enabled": 0}


def test_a_control_transition_may_change_only_the_named_switch() -> None:
    """@description: Switching settlement changes only settlement_enabled; counters and the other flags are unchanged.
    @return: None.
    """
    before = expected_intake_control()
    after = dict(before, settlement_enabled=1)
    assert_control_transition(before, after, {"settlement_enabled"})
    with pytest.raises(LiveGuardRefused):
        assert_control_transition(before, dict(before, settlement_enabled=1, revision=1), {"settlement_enabled"})


def test_the_discord_identifiers_must_match_the_approved_values_exactly() -> None:
    """@description: Guild, application and Owner identifiers match the approved values; any other value is refused before login.
    @return: None.
    """
    assert_discord_identifiers(1559749813011087392, 1559751168723521576, 456708412697215018)
    with pytest.raises(LiveGuardRefused):
        assert_discord_identifiers(1559749813011087392, 1559751168723521576, 456708412697215019)
