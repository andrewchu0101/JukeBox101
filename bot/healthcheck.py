"""Combine database readiness with evidence from the running main Gateway.

No Discord login, business command, database write or public port is used.
"""

import json
import os
import sys

from bot.config import ConfigurationError, load_formal_exchange_enabled, load_nickel_claim_enabled
from bot.database.errors import DatabaseError
from bot.gateway_health import check_gateway
from bot.readiness import check_readiness


def check_health() -> dict:
    """@description: Keep DB success separate from Gateway and disabled-feature policy.
    @param: none
    @return: Combined secret-free result and independent readiness components.
    """
    problems = []
    try:
        if load_formal_exchange_enabled() or load_nickel_claim_enabled():
            problems.append('FEATURE_POLICY_ENABLED')
        application_id = int(os.environ['DISCORD_EXPECTED_APPLICATION_ID'])
        gateway = check_gateway(application_id)
        database = check_readiness()
    except (ConfigurationError, DatabaseError, OSError, ValueError, KeyError) as error:
        return {'result': 'not_ready', 'problems': ['HEALTH_DEPENDENCY_FAILED'], 'error_class': type(error).__name__}
    problems.extend(gateway['problems'])
    problems.extend(database['problems'])
    if database.get('tls_mode') != 'verify-full':
        problems.append('DATABASE_TLS_POLICY')
    if database.get('account') != os.environ.get('DB_USER') or database.get('database') != os.environ.get('DB_NAME'):
        problems.append('DATABASE_IDENTITY_MISMATCH')
    return {'result': 'not_ready' if problems else 'ready', 'problems': problems,
            'gateway': gateway, 'database': database}


def main() -> int:
    """@description: Return nonzero unless the main Gateway and database are healthy.
    @param: none
    @return: Healthcheck process exit code.
    """
    summary = check_health()
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary['result'] == 'ready' else 1


if __name__ == '__main__':
    sys.exit(main())
