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


def compose_policy() -> None:
    """@description: Render only fake metadata and assert both base and review-host policies.
    @param: none
    @return: None; no real env file or secret is loaded.
    """
    with tempfile.TemporaryDirectory(prefix='jb-compose-') as directory:
        root = Path(directory)
        for name in ('discord_bot_token.txt', 'db_password.txt', 'db_migrator_password.txt', 'ca.crt'):
            (root / name).write_text('SYNTHETIC-CI-ONLY')
        environment = {'PATH': os.environ['PATH'], 'HOME': directory,
                       # Docker Desktop discovers its Compose plugin through this client config path.
                       'DOCKER_CONFIG': os.environ.get('DOCKER_CONFIG', str(Path.home() / '.docker')),
                       'DB_HOST': 'postgres.invalid', 'JB_SECRET_DIR': directory, 'JB_DB_CA_FILE': str(root / 'ca.crt'),
                       'DISCORD_EXPECTED_APPLICATION_ID': '789001', 'DISCORD_OWNER_USER_ID': '123',
                       'JUKEBOX_FORMAL_EXCHANGE': 'off', 'JUKEBOX_NICKEL_CLAIM': 'off',
                       'JB_IMAGE': 'ghcr.io/andrewchu0101/jukebox101@sha256:' + 'a' * 64,
                       'JB_EGRESS_NETWORK': 'ci-egress', 'JB_DATABASE_NETWORK': 'ci-database'}
        for filename in ('compose.yaml', 'deploy/controlled/compose.yaml'):
            arguments = ['docker', 'compose', '--env-file', '/dev/null', '-f', filename, 'config']
            run([*arguments, '--quiet'], environment=environment)
            config = json.loads(run([*arguments, '--format', 'json'], environment=environment))
            assert_bot_policy(config['services']['bot'])
            if filename.startswith('deploy/'):
                assert set(config['services']) == {'bot'} and 'build' not in config['services']['bot']


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
