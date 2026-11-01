"""Validate the exact runtime image and rendered Compose policy using fake inputs.

Rendered configuration stays in memory; only a secret-free receipt is printed.
This smoke does not start the Gateway or connect to Discord.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path


def run(arguments, *, environment=None) -> str:
    """@description: Bound Docker verification calls and avoid printing expanded configuration.
    @param arguments: Explicit argv without a shell.
    @param environment: Optional synthetic Compose environment.
    @return: Captured stdout; failures expose only the operation name.
    """
    result = subprocess.run(arguments, env=environment, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('runtime smoke failed: ' + arguments[1])
    return result.stdout


def assert_bot_policy(bot: dict) -> None:
    """@description: Preserve non-root, read-only, bounded and non-public Gateway operation.
    @param bot: Rendered Bot service from Compose, held only in memory.
    @return: None; policy drift fails before a deployment artifact can be accepted.
    """
    assert bot['user'] == '10001:10001' and bot['read_only'] is True
    assert 'ALL' in bot['cap_drop'] and 'no-new-privileges:true' in bot['security_opt']
    assert not bot.get('ports') and not bot.get('privileged') and not bot.get('network_mode')
    assert bot['healthcheck']['test'] == ['CMD', 'python', '-m', 'bot.healthcheck']
    assert bot['environment']['JUKEBOX_NICKEL_CLAIM'] == 'off'
    assert bot['environment']['JUKEBOX_FORMAL_EXCHANGE'] == 'off'
    assert int(bot['deploy']['resources']['limits']['memory']) <= 512 * 1024 * 1024
    assert float(bot['deploy']['resources']['limits']['cpus']) <= 1.0
    assert bot['environment']['DB_SSLMODE'] == 'verify-full'
    secrets = {entry['source'] for entry in bot['secrets']}
    assert secrets == {'discord_bot_token', 'db_password', 'db_ca.crt'}


def assert_controlled_policy(config: dict, environment: dict) -> None:
    """@description: Bind rendered host paths and identities while retaining every runtime safety check.
    @param config: Rendered controlled Compose document containing synthetic settings only.
    @param environment: Explicit expected settings used to render this isolated profile.
    @return: None; unexpected host paths, services or database identities fail verification.
    """
    assert set(config['services']) == {'bot'}
    bot = config['services']['bot']
    assert_bot_policy(bot)
    assert 'build' not in bot and not bot.get('command') and not bot.get('entrypoint')
    assert not bot.get('volumes')
    assert bot['image'] == environment['JB_IMAGE']
    expected = {environment.get('JB_EGRESS_NETWORK') or 'jukebox101_egress',
                environment.get('JB_DATABASE_NETWORK') or 'jukebox101_database'}
    assert len(expected) == 2
    assert {item['name'] for item in config['networks'].values()} == expected
    assert all(item.get('external') is True for item in config['networks'].values())
    assert config['secrets']['db_ca.crt']['file'] == (
        environment.get('JB_DB_CA_FILE') or '/etc/jukebox101/supabase-ca-2021.crt')
    for name in ('DB_NAME', 'DB_USER'):
        assert environment[name] and bot['environment'][name] == environment[name]


def synthetic_compose_environment(directory: str) -> dict:
    """@description: Create only disposable fake secrets and settings for Compose rendering.
    @param directory: Private temporary scope, never a production configuration directory.
    @return: Explicit synthetic environment with Docker client discovery preserved.
    """
    root = Path(directory)
    for name in ('discord_bot_token.txt', 'db_password.txt', 'db_migrator_password.txt', 'ca.crt'):
        (root / name).write_text('SYNTHETIC-CI-ONLY')
    return {'PATH': os.environ['PATH'], 'HOME': directory,
            'DOCKER_CONFIG': os.environ.get('DOCKER_CONFIG', str(Path.home() / '.docker')),
            'COMPOSE_DISABLE_ENV_FILE': '1', 'DB_HOST': 'postgres.invalid',
            'DB_NAME': 'synthetic_database', 'DB_USER': 'synthetic_app',
            'JB_SECRET_DIR': directory, 'JB_DB_CA_FILE': str(root / 'ca.crt'),
            'DISCORD_EXPECTED_APPLICATION_ID': '789001', 'DISCORD_OWNER_USER_ID': '123',
            'JUKEBOX_FORMAL_EXCHANGE': 'off', 'JUKEBOX_NICKEL_CLAIM': 'off',
            'JB_IMAGE': 'ghcr.io/andrewchu0101/jukebox101@sha256:' + 'a' * 64,
            'JB_EGRESS_NETWORK': 'ci-egress', 'JB_DATABASE_NETWORK': 'ci-database'}


def compose_policy() -> None:
    """@description: Render base, controlled override and actual-host-default profiles with fake settings.
    @param: none
    @return: None; no real env file, secret, Gateway or database is used.
    """
    with tempfile.TemporaryDirectory(prefix='jb-compose-') as directory:
        environment = synthetic_compose_environment(directory)
        excluded = {'JB_EGRESS_NETWORK', 'JB_DATABASE_NETWORK', 'JB_DB_CA_FILE'}
        defaults = {name: value for name, value in environment.items() if name not in excluded}
        profiles = [('compose.yaml', environment), ('deploy/controlled/compose.yaml', environment),
                    ('deploy/controlled/compose.yaml', defaults)]
        for filename, settings in profiles:
            arguments = ['docker', 'compose', '--env-file', '/dev/null', '-f', filename, 'config']
            run([*arguments, '--quiet'], environment=settings)
            config = json.loads(run([*arguments, '--format', 'json'], environment=settings))
            if filename.startswith('deploy/'):
                assert_controlled_policy(config, settings)
            else:
                assert_bot_policy(config['services']['bot'])


def main() -> None:
    """@description: Test imports and closed command routes in the same immutable runtime image.
    @param: none
    @return: None; writes only a safe receipt to ci-evidence.
    """
    image = os.environ['RUNTIME_IMAGE']
    identity = run(['docker', 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}} {{.Config.User}}', image]).strip()
    if identity != 'linux/amd64 10001:10001':
        raise RuntimeError('runtime identity mismatch')
    base = ['docker', 'run', '--rm', '--network', 'none', '--read-only', '--tmpfs', '/tmp',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '-e', 'JUKEBOX_FORMAL_EXCHANGE=off', '-e', 'JUKEBOX_NICKEL_CLAIM=off', '--entrypoint', 'python', image]
    run([*base, '-c', 'import bot.__main__, bot.healthcheck, discord, psycopg; import os; assert os.geteuid() == 10001'])
    policy = json.loads(run([*base, '-m', 'bot.runtime_policy']))
    compose_policy()
    output = Path('ci-evidence')
    output.mkdir(exist_ok=True)
    receipt = {'image_id': image, 'identity': identity, 'policy': policy, 'compose': 'passed'}
    (output / 'runtime-smoke.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
