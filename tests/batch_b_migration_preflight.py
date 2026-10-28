"""Expose the dedicated migrator inventory without any DDL or write authorization.

This entry reads only TEST_MIGRATION_DB credentials and reuses the conservative read-only inventory.
"""

from tests.batch_b_assertion_guard import require_assertions_enabled, run_guarded
from tests import batch_b_preflight
from bot.database.test_profiles import profile_settings

PREFLIGHT_SWITCH = "JUKEBOX_RUN_BATCH_B_MIGRATION_PREFLIGHT"  # [read authorization]: Does not enable execution.


def main() -> None:
    """@description: Run the shared sanitized CLI boundary using only the migration profile.
    @param: none
    @return: None.
    """
    require_assertions_enabled()  # [pre-command gate]: Refuse before any connection or side effect.
    batch_b_preflight.run_report(lambda: batch_b_preflight.collect_preflight(profile_settings(PREFLIGHT_SWITCH, True), True))


if __name__ == "__main__":
    raise SystemExit(run_guarded(main))
