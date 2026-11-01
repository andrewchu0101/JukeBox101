"""Protect controlled-host configuration without requiring Docker or production settings.

The real Docker Compose render is exercised separately by runtime_smoke. These
offline cases verify that wrong host paths and weaker health remain rejected.
"""

import pytest

from scripts.ci.runtime_smoke import assert_controlled_policy


@pytest.fixture(params=['defaults', 'overrides'])
def controlled_profile(request):
    """@description: Model rendered configuration for default and administrator-reviewed host profiles.
    @param request: Select actual-host defaults or explicit synthetic overrides.
    @return: Synthetic rendered configuration and its expected environment.
    """
    environment = dict(JB_IMAGE='ghcr.io/andrewchu0101/jukebox101@sha256:' + 'a' * 64,
                       DB_NAME='synthetic_database', DB_USER='synthetic_app')
    if request.param == 'overrides':
        environment.update(JB_EGRESS_NETWORK='synthetic-egress', JB_DATABASE_NETWORK='synthetic-db',
                           JB_DB_CA_FILE='/synthetic/ca.crt')
    bot = dict(image=environment['JB_IMAGE'], user='10001:10001', read_only=True, cap_drop=['ALL'],
               security_opt=['no-new-privileges:true'],
               healthcheck={'test': ['CMD', 'python', '-m', 'bot.healthcheck']},
               deploy={'resources': {'limits': {'cpus': '1.0', 'memory': '536870912'}}},
               environment=dict(DB_NAME=environment['DB_NAME'], DB_USER=environment['DB_USER'],
                                DB_SSLMODE='verify-full', JUKEBOX_NICKEL_CLAIM='off', JUKEBOX_FORMAL_EXCHANGE='off'),
               secrets=[{'source': name} for name in ('discord_bot_token', 'db_password', 'db_ca.crt')])
    networks = {'egress': {'external': True, 'name': environment.get('JB_EGRESS_NETWORK', 'jukebox101_egress')},
                'database': {'external': True, 'name': environment.get('JB_DATABASE_NETWORK', 'jukebox101_database')}}
    return dict(services={'bot': bot}, networks=networks,
                secrets={'db_ca.crt': {'file': environment.get('JB_DB_CA_FILE', '/etc/jukebox101/supabase-ca-2021.crt')}}), environment


def test_reviewed_controlled_profiles_are_accepted(controlled_profile):
    """@description: Accept verified host defaults and reviewed overrides without reducing runtime policy.
    @param controlled_profile: Synthetic rendered profile with no production credential.
    @return: None.
    """
    config, environment = controlled_profile
    assert_controlled_policy(config, environment)


@pytest.mark.parametrize('fault', ['ca', 'network', 'private_network', 'shared_network', 'database', 'role',
                                  'health', 'tls', 'port', 'mount', 'root', 'writable', 'capabilities',
                                  'privileges', 'claim', 'exchange', 'memory', 'cpu', 'service', 'command', 'image'])
def test_host_drift_and_weaker_runtime_policy_are_rejected(controlled_profile, fault):
    """@description: Refuse host misrouting and weaker isolation before any service operation.
    @param controlled_profile: Private synthetic configuration and explicit expected settings.
    @param fault: One unsafe runtime or host-contract change.
    @return: None.
    """
    config, environment = controlled_profile
    mutate_profile(config, environment, fault)
    with pytest.raises(AssertionError):
        assert_controlled_policy(config, environment)


def mutate_profile(config, environment, fault):
    """@description: Apply one isolated host-contract fault without involving external services.
    @param config: Mutable synthetic Compose result.
    @param environment: Expected settings, changed only to model duplicate network names.
    @param fault: Requested unsafe field replacement.
    @return: None.
    """
    bot = config['services']['bot']
    changes = {'health': (bot['healthcheck'], 'test', ['CMD', 'python', '-m', 'bot.readiness']),
               'tls': (bot['environment'], 'DB_SSLMODE', 'verify-ca'), 'port': (bot, 'ports', ['8080:8080']),
               'mount': (bot, 'volumes', ['/var/run/docker.sock:/var/run/docker.sock']),
               'root': (bot, 'user', '0:0'), 'writable': (bot, 'read_only', False),
               'capabilities': (bot, 'cap_drop', []), 'privileges': (bot, 'security_opt', []),
               'claim': (bot['environment'], 'JUKEBOX_NICKEL_CLAIM', 'on'),
               'exchange': (bot['environment'], 'JUKEBOX_FORMAL_EXCHANGE', 'on'),
               'memory': (bot['deploy']['resources']['limits'], 'memory', '1073741824'),
               'cpu': (bot['deploy']['resources']['limits'], 'cpus', '2.0'),
               'command': (bot, 'command', ['/bin/sh']), 'image': (bot, 'image', 'unreviewed:latest'),
               'database': (bot['environment'], 'DB_NAME', 'wrong_database'),
               'role': (bot['environment'], 'DB_USER', 'postgres'),
               'service': (config['services'], 'postgres', {}),
               'ca': (config['secrets']['db_ca.crt'], 'file', '/wrong/ca.crt'),
               'network': (config['networks']['database'], 'name', 'other-project'),
               'private_network': (config['networks']['database'], 'external', False)}
    if fault == 'shared_network':
        environment.update(JB_EGRESS_NETWORK='same-network', JB_DATABASE_NETWORK='same-network')
    else:
        target, key, value = changes[fault]
        target[key] = value
