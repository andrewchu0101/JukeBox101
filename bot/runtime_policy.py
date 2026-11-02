"""Check the production command and disabled-feature contract without logging in.

Repositories are constructed over a connection trap; disabled business routes must
return without database, Discord, compensation or settlement side effects.
"""

import asyncio
import json
import os

from bot.command_contract import verify_local_contract
from bot.config import (AUTO_REVIEW_ENV, AUTO_SETTLEMENT_ENV, AUTO_VERSION_CONFIRM_ENV, API_RETENTION_ENV, ConfigurationError,
                        load_api_retention_enabled, load_auto_review_enabled, load_auto_settlement_enabled,
                        load_auto_version_confirm_enabled, load_formal_exchange_enabled, load_nickel_claim_enabled)
from bot.database.submission_repository import SubmissionRepository
from bot.discord_app import JukeBoxClient
from bot.services import MusicService


def forbid_connection():
    """@description: Prove closed business routes do not open a database connection.
    @param: none
    @return: Never; any call fails the policy check.
    """
    raise RuntimeError('disabled route opened a connection')


def require_automatic_modes_closed() -> dict[str, str]:
    """@description: Prove the published image defaults to every automatic mode closed; an invalid value fails in its own loader.
    @param: none
    @return: Switch name to "off" for each automatic mode.
    """
    loaders = {AUTO_REVIEW_ENV: load_auto_review_enabled, API_RETENTION_ENV: load_api_retention_enabled,
               AUTO_SETTLEMENT_ENV: load_auto_settlement_enabled, AUTO_VERSION_CONFIRM_ENV: load_auto_version_confirm_enabled}
    if any(loader() for loader in loaders.values()):
        raise ConfigurationError('CI/CD requires every automatic mode off')
    return {name: 'off' for name in loaders}


def require_feature_switches_closed() -> None:
    """@description: Refuse any run where the legacy feature switches are not explicitly off.
    @param: none
    @return: None; a missing, enabling or inconsistent value raises.
    """
    if any(os.environ.get(key) != 'off' for key in ('JUKEBOX_FORMAL_EXCHANGE', 'JUKEBOX_NICKEL_CLAIM')):
        raise ConfigurationError('CI/CD requires explicit off feature policy')
    if load_formal_exchange_enabled() or load_nickel_claim_enabled():
        raise ConfigurationError('closed feature policy mismatch')


def require_business_routes_closed(service: MusicService, repository: SubmissionRepository) -> None:
    """@description: Prove the constructed service enabled no pipeline, exchange or compensation route.
    @param service: Service built over the connection trap.
    @param repository: Repository built over the connection trap.
    @return: None; any enabled route raises.
    """
    enabled = (service.auto_pipeline is not None, service.formal_exchange_enabled, service.synthetic_exchange_enabled, repository._compensation_enabled)
    if any(enabled):
        raise RuntimeError('business policy enabled')


async def check_policy() -> dict:
    """@description: Exercise real closed routes and the full local command baseline.
    @param: none
    @return: Secret-free policy receipt; a drift or side effect raises.
    """
    require_feature_switches_closed()
    automatic_modes = require_automatic_modes_closed()  # [closed by default]: The receipt records that no automatic mode is on.
    repository = SubmissionRepository(forbid_connection, 123)
    service = MusicService(None, repository, nickel_connection_factory=forbid_connection)
    client = JukeBoxClient(service)
    commands = verify_local_contract(client.tree)
    if 'claim' in {command['name'] for command in commands}:
        raise RuntimeError('claim registered')
    await service.exchange(123)
    await service.daily(123, True)
    await service.claim(123, 'ci-disabled')
    require_business_routes_closed(service, repository)
    await client.close()
    return {'result': 'passed', 'claim_registered': False, 'formal_exchange': False,
            'compensation': False, 'local_contract': 'matched', 'login': 'not_run', 'auto_modes': automatic_modes}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(check_policy()), sort_keys=True))
