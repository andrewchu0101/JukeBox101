"""Command-line entry for PostgreSQL schema migrations, run by the operator as the schema-owner migrator role.

Reads DB_* settings (migrator credentials, DB_PASSWORD or DB_PASSWORD_FILE) from the environment only; prints a JSON
summary without secrets. `status` and `plan` never write; `apply` requires the explicit --apply flag.
"""

import argparse
import json
import sys

from bot.config import ConfigurationError, load_database_settings
from bot.database.connection import open_connection
from bot.database.errors import DatabaseError
from bot.database.migration_runner import MigrationFailed, MigrationRefused, run_migrations

EXIT_REFUSED = 2  # [no change]: Configuration, identity, lock or history checks refused before any schema change.
EXIT_FAILED = 3  # [rolled back]: A migration failed and its transaction was rolled back.


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    """@description: Require an explicit mode, and an explicit flag before any schema change.
    @param argv: Command-line arguments without the program name.
    @return: Parsed mode and apply flag.
    """
    parser = argparse.ArgumentParser(prog="python -m bot.migrate", description="JukeBox101 PostgreSQL migrations")
    parser.add_argument("mode", choices=("status", "plan", "apply"))
    parser.add_argument("--apply", action="store_true", help="required together with mode 'apply'")
    arguments = parser.parse_args(argv)
    if arguments.mode == "apply" and not arguments.apply:
        parser.error("mode 'apply' changes the schema; add --apply to confirm")
    return arguments


def main(argv: list[str] | None = None) -> int:
    """@description: Run one migration command and map refusals and failures to distinct exit codes.
    @param argv: Optional argument list for tests; None reads sys.argv.
    @return: Process exit code (0 success, 2 refused, 3 failed and rolled back).
    """
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        connection = open_connection(load_database_settings())
    except (ConfigurationError, DatabaseError) as error:
        print(json.dumps({"result": "refused", "reason": type(error).__name__, "detail": str(error) if isinstance(error, ConfigurationError) else None}))
        return EXIT_REFUSED
    try:
        summary = run_migrations(connection, apply=arguments.mode == "apply")
    except MigrationRefused as refusal:
        print(json.dumps({"result": "refused", "reason": str(refusal)}))
        return EXIT_REFUSED
    except MigrationFailed as failure:
        print(json.dumps({"result": "failed", "reason": str(failure)}))
        return EXIT_FAILED
    finally:
        connection.close()
    print(json.dumps({"result": "ok", "mode": arguments.mode, **summary}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
