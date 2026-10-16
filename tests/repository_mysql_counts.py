"""Report six-table acceptance counts only after explicit isolated-test authorization.

This manual entry performs identity-validated reads, prints no credentials and refuses nonempty baselines.
"""

import json

import pymysql

from bot.config import ConfigurationError
from tests.repository_mysql_support import authorized_repository_settings, require_empty_tables, verified_counts


def main() -> None:
    """@description: Give manual acceptance a comparable safe before/after count snapshot.
    @param: none
    @return: None; missing authorization, nonempty counts or database errors fail the process.
    """
    try:
        counts = verified_counts(authorized_repository_settings())
        print(json.dumps(counts, sort_keys=True))
        require_empty_tables(counts)
    except (ConfigurationError, pymysql.MySQLError) as check_error:
        raise SystemExit("Repository count check failed: " + type(check_error).__name__) from None


if __name__ == "__main__":
    main()
