"""Validate expected missing-parent failures without accepting unrelated integrity errors.

Parent-row checks and reviewed schema preflight identify constraints when MySQL hides their names.
"""

import re

import pymysql
from pymysql.constants import ER

MISSING_PARENT_ERROR_CODES = {ER.NO_REFERENCED_ROW, ER.NO_REFERENCED_ROW_2}
ABSENT_SONG_ID = 0


def assert_missing_parent_error(error: Exception, expected_constraint_name: str) -> None:
    """@description: Accept documented missing-parent variants while rejecting incorrect error evidence.
    @param error: Captured driver exception from a deliberately isolated foreign-key violation.
    @param expected_constraint_name: Constraint established by schema preflight and parent-row assertions.
    @return: None; fails on wrong type, code, message or a different disclosed constraint.
    """
    assert isinstance(error, pymysql.IntegrityError), "Expected a driver integrity error"
    assert error.args[0] in MISSING_PARENT_ERROR_CODES, "Expected a missing-parent foreign-key error"
    error_message = str(error.args[1])
    assert "foreign key constraint fails" in error_message.lower(), "Expected foreign-key rejection evidence"
    # [disclosed constraint]: Generic 1216 may hide names; 1452 must identify the expected key.
    constraint_match = re.search(r"\bCONSTRAINT\s+[`\"']([^`\"']+)[`\"']", error_message, re.IGNORECASE)
    if constraint_match is not None:
        assert constraint_match.group(1) == expected_constraint_name, "Unexpected foreign-key constraint"
    if error.args[0] == ER.NO_REFERENCED_ROW_2:
        assert constraint_match is not None, "Detailed foreign-key error must name its constraint"


def assert_recommendation_parent_conditions(cursor, recipient_discord_user_id: int) -> None:
    """@description: Isolate the missing-song key from the recommendation's recipient key.
    @param cursor: Cursor within the already validated disposable test transaction.
    @param recipient_discord_user_id: Existing fixture user used by the invalid recommendation.
    @return: None; fails if the valid parent is missing or song zero unexpectedly exists.
    """
    cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM users WHERE discord_user_id = %s) AS user_exists, EXISTS(SELECT 1 FROM songs WHERE song_id = %s) AS song_exists",
        (recipient_discord_user_id, ABSENT_SONG_ID),
    )
    assert cursor.fetchone() == {"user_exists": 1, "song_exists": 0}, "Only the song parent should be absent"


def assert_dispatch_parent_conditions(cursor, recipient_discord_user_id: int, canonical_song_id: int, recommendation_id: int) -> None:
    """@description: Isolate the compound owner key while validating all simple parent references.
    @param cursor: Cursor within the already validated disposable test transaction.
    @param recipient_discord_user_id: Existing alternate recipient borrowing another user's recommendation.
    @param canonical_song_id: Existing song linked to the original recommendation.
    @param recommendation_id: Existing recommendation whose owner differs from the alternate recipient.
    @return: None; fails unless only the compound ownership parent is absent.
    """
    cursor.execute(
        "SELECT EXISTS(SELECT 1 FROM users WHERE discord_user_id = %s) AS user_exists, EXISTS(SELECT 1 FROM songs WHERE song_id = %s) AS song_exists, EXISTS(SELECT 1 FROM recommendations WHERE recommendation_id = %s AND song_id = %s) AS recommendation_exists, EXISTS(SELECT 1 FROM recommendations WHERE recommendation_id = %s AND discord_user_id = %s AND song_id = %s) AS owner_exists",
        (recipient_discord_user_id, canonical_song_id, recommendation_id, canonical_song_id,
         recommendation_id, recipient_discord_user_id, canonical_song_id),
    )
    assert cursor.fetchone() == {"user_exists": 1, "song_exists": 1, "recommendation_exists": 1, "owner_exists": 0}, "Only the compound owner parent should be absent"
