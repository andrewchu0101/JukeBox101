"""Check the production command and disabled-feature contract without logging in.

Repositories are constructed over a connection trap; disabled business routes must
return without database, Discord, compensation or settlement side effects.
"""

import asyncio
import json
import os

from bot.command_contract import verify_local_contract
from bot.config import ConfigurationError, load_formal_exchange_enabled, load_nickel_claim_enabled
from bot.database.submission_repository import SubmissionRepository
from bot.discord_app import JukeBoxClient
from bot.services import MusicService


def forbid_connection():
    """@description: Prove closed business routes do not open a database connection.
    @param: none
    @return: Never; any call fails the policy check.
    """
    raise RuntimeError('disabled route opened a connection')


async def check_policy() -> dict:
    """@description: Exercise real closed routes and the full local command baseline.
    @param: none
    @return: Secret-free policy receipt; a drift or side effect raises.
    """
    if any(os.environ.get(key) != 'off' for key in ('JUKEBOX_FORMAL_EXCHANGE', 'JUKEBOX_NICKEL_CLAIM')):
        raise ConfigurationError('CI/CD requires explicit off feature policy')
    if load_formal_exchange_enabled() or load_nickel_claim_enabled():
        raise ConfigurationError('closed feature policy mismatch')
    repository = SubmissionRepository(forbid_connection, 123)
    service = MusicService(None, repository, nickel_connection_factory=forbid_connection)
    client = JukeBoxClient(service)
    commands = verify_local_contract(client.tree)
    if 'claim' in {command['name'] for command in commands}:
        raise RuntimeError('claim registered')
    await service.exchange(123)
    await service.daily(123, True)
    await service.claim(123, 'ci-disabled')
    if service.auto_pipeline is not None or service.formal_exchange_enabled or service.synthetic_exchange_enabled or repository._compensation_enabled:
        raise RuntimeError('business policy enabled')
    await client.close()
    return {'result': 'passed', 'claim_registered': False, 'formal_exchange': False,
            'compensation': False, 'local_contract': 'matched', 'login': 'not_run'}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(check_policy()), sort_keys=True))
