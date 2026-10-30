"""Opt-in read-only check of migration 003 shape on an approved, already-migrated disposable target.

Written but NOT executed or collected in the offline round. It runs only when the existing application and harness switches are set and the migration-check switch is set; it issues SELECT statements against information_schema and never applies DDL.
"""

import os

import pytest

from tests.batch_b_harness_contract import HARNESS_SWITCH, RUN_SWITCH, load_profiles
from tests.batch_b_harness_database import verified_connection

MIGRATION_CHECK_SWITCH = "JUKEBOX_BATCH_B_EXCHANGE_MIGRATION_CHECK"  # [explicit opt-in]: Separate from every other real-test switch.
REQUIRED_COLUMNS = {"claim_token", "claimed_at", "released_at", "active_slot"}


@pytest.mark.mysql
@pytest.mark.skipif(
    any(os.environ.get(name) != "1" for name in (RUN_SWITCH, HARNESS_SWITCH, MIGRATION_CHECK_SWITCH)),
    reason="Migration 003 shape check requires the approved disposable target and three explicit opt-ins",
)
def test_migration_003_shape_is_present_on_the_approved_target() -> None:
    """@description: Confirm the columns, release-aware unique key and CHECK constraints exist after the approved migration.
    @param: none
    @return: None.
    """
    _, harness = load_profiles()
    connection = verified_connection(harness, harness.user)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS "
                           "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='recommendations'")
            assert REQUIRED_COLUMNS <= {row["name"] for row in cursor.fetchall()}
            cursor.execute("SELECT INDEX_NAME AS name FROM information_schema.STATISTICS "
                           "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='recommendations'")
            indexes = {row["name"] for row in cursor.fetchall()}
            assert "uq_recommendations_user_song_active" in indexes and "uq_recommendations_user_song" not in indexes
            cursor.execute("SELECT CONSTRAINT_NAME AS name FROM information_schema.CHECK_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE()")
            checks = {row["name"] for row in cursor.fetchall()}
            assert {"chk_recommendations_sending_token", "chk_recommendations_release"} <= checks
    finally:
        connection.rollback()
        connection.close()
