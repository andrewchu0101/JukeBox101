"""Expose an independently guarded migration command for future human-authorized use.

A fresh read-only inventory must pass before the dedicated runner may create its own DDL connection.
"""

from bot.database.connection import open_connection
from bot.database.submission_migration import apply_migration, MIGRATION_AUTHORIZATION
from bot.database.test_profiles import profile_settings, MIGRATION_SWITCH
from tests.batch_b_preflight import collect_preflight, run_report


def migrate() -> dict:
    """@description: Refuse unsafe inventory and require dedicated credentials before any DDL connection.
    @param: none
    @return: Sanitized execution outcome without pretending DDL is transactional.
    """
    settings = profile_settings(MIGRATION_SWITCH, True)  # [dedicated credentials]: Never application settings.
    report = collect_preflight(settings, True)  # [fresh inventory]: Existing or nonempty assets always require manual review.
    if report["stop_reasons"]:
        return report
    applied = apply_migration(lambda: open_connection(settings), MIGRATION_AUTHORIZATION, settings=settings)
    return {"status": "MIGRATION_APPLIED" if applied else "ALREADY_APPLIED", "migration_authorized": True}


def main() -> None:
    """@description: Keep driver errors and partial-DDL diagnostics free of secrets.
    @param: none
    @return: None.
    """
    run_report(migrate)


if __name__ == "__main__":
    main()
