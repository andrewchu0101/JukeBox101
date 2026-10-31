"""Read-only readiness check for the Bot's PostgreSQL application role, usable by operators and the Compose healthcheck.

Connects with the same settings as the Bot, verifies the role is not a superuser and that the control row allows
pending intake with settlement matching the configured mode. Prints a secret-free JSON summary; writes nothing.
"""

import json
import sys

from bot.config import ConfigurationError, load_auto_settlement_enabled, load_database_settings
from bot.database.connection import inspect_database_identity, transaction
from bot.database.errors import DatabaseError

EXPECTED_SCHEMA_VERSION = "2b2-b1"  # [control contract]: Matches submission_transaction's required version.
EXIT_NOT_READY = 1  # [not ready]: Reachable but not safe to accept submissions, or unreachable.


def readiness_problems(identity: dict, control: dict | None, automatic_settlement: bool = False) -> list[str]:
    """@description: Name every reason the database is not ready, so the operator sees all of them at once.
    @param identity: Role and privilege facts from the server.
    @param control: jb2_control row, or None when absent.
    @param automatic_settlement: Configured mode; the database flag must agree with it in both directions.
    @return: Stable problem codes; empty when ready.
    """
    problems = []
    if identity["is_superuser"]:
        problems.append("ROLE_IS_SUPERUSER")
    if control is None:
        return problems + ["CONTROL_ROW_MISSING"]
    if control["schema_version"] != EXPECTED_SCHEMA_VERSION:
        problems.append("SCHEMA_VERSION_MISMATCH")
    if not control["snapshot_ready"] or not control["legacy_writers_quiesced"]:
        problems.append("INTAKE_NOT_CERTIFIED")
    if control["settlement_enabled"] and not automatic_settlement:
        problems.append("SETTLEMENT_ENABLED")
    if automatic_settlement and not control["settlement_enabled"]:
        problems.append("SETTLEMENT_SWITCH_OFF")
    return problems


def check_readiness() -> dict:
    """@description: Collect identity and control facts in one read-only transaction.
    @param: none
    @return: Summary with result 'ready' or 'not_ready' and problem codes.
    """
    settings = load_database_settings()
    identity = inspect_database_identity(settings)
    with transaction(settings) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT schema_version, snapshot_ready, legacy_writers_quiesced, settlement_enabled "
                           "FROM jb2_control WHERE singleton_id = 1")
            control = cursor.fetchone()
    problems = readiness_problems(identity, control, load_auto_settlement_enabled())
    return {"result": "not_ready" if problems else "ready", "problems": problems, "account": identity["account"],
            "database": identity["database_name"], "server_version": identity["server_version"], "tls_mode": settings.sslmode}


def main() -> int:
    """@description: Print the readiness summary and return a healthcheck-friendly exit code.
    @param: none
    @return: 0 when ready, 1 otherwise.
    """
    try:
        summary = check_readiness()
    except ConfigurationError as error:
        summary = {"result": "not_ready", "problems": ["CONFIGURATION"], "detail": str(error)}
    except DatabaseError as error:
        summary = {"result": "not_ready", "problems": ["DATABASE_UNREACHABLE_OR_DENIED"], "error_class": type(error).__name__}
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["result"] == "ready" else EXIT_NOT_READY


if __name__ == "__main__":
    sys.exit(main())
