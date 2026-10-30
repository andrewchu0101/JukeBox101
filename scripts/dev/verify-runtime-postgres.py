"""Feed to the immutable runtime's Python stdin on a disposable internal network.

Verify the server marker before writes, then use random disposable roles/databases
for TLS, CLI, file metadata and disabled-policy checks. Never log into Discord.
"""

import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path

import psycopg
from psycopg import sql


def admin_connection(database='postgres'):
    """@description: Connect only to the explicitly injected disposable test server.
    @param database: Disposable database or the maintenance database.
    @return: Autocommit connection using random test credentials.
    """
    return psycopg.connect(host=os.environ['JB_PG_TEST_HOST'], port=os.environ.get('JB_PG_TEST_PORT', '5432'),
                           user=os.environ.get('JB_PG_TEST_ADMIN_USER', 'postgres'),
                           password=os.environ['JB_PG_TEST_ADMIN_PASSWORD'], dbname=database,
                           sslmode='disable', autocommit=True, connect_timeout=5)


def create_resources(admin, database, passwords):
    """@description: Prepare unique least-privilege accounts and schema on the marked server.
    @param admin: Verified disposable cluster connection.
    @param database: Random database name.
    @param passwords: Random migrator and app role credentials, never printed.
    @return: None.
    """
    migrator, app = passwords
    for role, password in passwords.items():
        admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(
            sql.Identifier(role), sql.Literal(password)))
    admin.execute(sql.SQL('GRANT jukebox101_app_role TO {}').format(sql.Identifier(app)))
    admin.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(database), sql.Identifier(migrator)))
    with admin_connection(database) as setup:
        setup.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(database)))
        setup.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}, {}').format(
            sql.Identifier(database), sql.Identifier(migrator), sql.Identifier(app)))
        setup.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
        setup.execute(sql.SQL('CREATE SCHEMA jukebox101 AUTHORIZATION {}').format(sql.Identifier(migrator)))


def test_environment(directory, database, app_password, ca):
    """@description: Keep only synthetic file secrets and verifying TLS settings in the runtime probe.
    @param directory: Temporary tmpfs directory in the unprivileged runtime.
    @param database: Disposable database name.
    @param app_password: Random disposable app credential.
    @param ca: Disposable cluster's public CA, never a production trust replacement.
    @return: Child process environment; no production values are inherited.
    """
    root = Path(directory)
    (root / 'ca.crt').write_text(ca)
    private = root / 'secrets'
    private.mkdir(mode=0o750)
    for name, value in [('discord', 'SYNTHETIC-CI-NOT-A-TOKEN'), ('app', app_password)]:
        path = private / name
        path.write_text(value)
        path.chmod(0o400)
    environment = {key: os.environ[key] for key in ('PATH', 'PYTHONDONTWRITEBYTECODE') if key in os.environ}
    environment.update(DB_HOST=os.environ['JB_PG_TEST_HOST'], DB_PORT=os.environ.get('JB_PG_TEST_PORT', '5432'),
                       DB_NAME=database, DB_SSLMODE='verify-full', DB_SSLROOTCERT=str(root / 'ca.crt'),
                       DISCORD_BOT_TOKEN_FILE=str(private / 'discord'), SECRET_EXPECTED_UID='10001',
                       SECRET_EXPECTED_GID='10001', DISCORD_EXPECTED_APPLICATION_ID='789001',
                       DISCORD_OWNER_USER_ID='123', JUKEBOX_FORMAL_EXCHANGE='off', JUKEBOX_NICKEL_CLAIM='off')
    return environment


def execute(module, arguments, environment):
    """@description: Run the real CLI in the immutable runtime without printing secret-bearing errors.
    @param module: Trusted Python module name.
    @param arguments: Explicit CLI arguments.
    @param environment: Synthetic isolated settings.
    @return: Parsed JSON from a successful subprocess.
    """
    result = subprocess.run(['python', '-m', module, *arguments], env=environment,
                            text=True, capture_output=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f'{module} failed ({result.returncode})')
    print(json.dumps({'module': module, 'arguments': arguments, 'result': 'passed'}), flush=True)
    return json.loads(result.stdout)


def verify_cli(environment, passwords, directory):
    """@description: Use migrator only for the disposable schema, then app for runtime readiness.
    @param environment: Synthetic verifying-TLS settings.
    @param passwords: Ordered migrator and application credentials.
    @param directory: tmpfs directory holding the application password file.
    @return: App environment after all positive CLI assertions.
    """
    migrator, app = passwords
    environment.update(DB_USER=migrator, DB_PASSWORD=passwords[migrator])
    for arguments in (['status'], ['plan'], ['apply', '--apply'], ['status']):
        execute('bot.migrate', arguments, environment)
    environment.pop('DB_PASSWORD')
    environment.update(DB_USER=app, DB_PASSWORD_FILE=str(Path(directory) / 'secrets' / 'app'))
    output = execute('bot.readiness', [], environment)
    if output['result'] != 'ready' or output['problems'] or output['tls_mode'] != 'verify-full':
        raise RuntimeError('runtime DB/TLS readiness contract failed')
    if output['account'] != app or output['database'] != environment['DB_NAME']:
        raise RuntimeError('runtime role/database mismatch')
    execute('bot.runtime_policy', [], environment)
    return environment


def verify_metadata_and_negative_health(environment):
    """@description: Require real read-only dummy mounts and reject DB-only Gateway health.
    @param environment: Disposable app settings over verify-full.
    @return: None; metadata or false-positive health causes CI failure.
    """
    mounted = dict(environment, DISCORD_BOT_TOKEN_FILE='/run/ci-secrets/token',
                   DB_PASSWORD_FILE='/run/ci-secrets/db_password')
    metadata = subprocess.run(['python', '-c', 'from bot.__main__ import verify_mounted_secrets; verify_mounted_secrets()'],
                              env=mounted, capture_output=True, timeout=30)
    if metadata.returncode:
        raise RuntimeError('runtime read-only secret mount metadata refused')
    health = subprocess.run(['python', '-m', 'bot.healthcheck'], env=environment, text=True, capture_output=True, timeout=30)
    output = json.loads(health.stdout)
    if health.returncode != 1 or output['gateway']['result'] != 'not_ready' or output['database']['result'] != 'ready':
        raise RuntimeError('DB-only process incorrectly passed Gateway health')
    print(json.dumps({'mounted_secret_metadata': 'passed', 'db_ready_gateway_absent': 'rejected'}), flush=True)


def remove_resources(admin, database, passwords):
    """@description: Remove only the randomly named roles/database allocated by this invocation.
    @param admin: Verified disposable cluster connection.
    @param database: Random disposable database name.
    @param passwords: Random role names whose values are not logged.
    @return: None; cleanup errors fail the test and outer Docker cleanup still runs.
    """
    admin.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(database)))
    for role in reversed(passwords):
        admin.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(role)))


def main():
    """@description: Exercise exact runtime DB/TLS readiness without real Bot secrets or login.
    @param: none
    @return: None; scoped resources are always removed.
    """
    with admin_connection() as admin:
        if admin.execute('SHOW cluster_name').fetchone()[0] != 'jukebox101-disposable-test':
            raise RuntimeError('refusing unmarked server')
        if admin.execute('SHOW server_version_num').fetchone()[0] != '180006':
            raise RuntimeError('require PostgreSQL 18.6')
        suffix = secrets.token_hex(6)
        database, migrator, app = ['jb_t_' + label + suffix for label in ('cli', 'mig', 'app')]
        passwords = {role: secrets.token_urlsafe(32) for role in (migrator, app)}
        try:
            create_resources(admin, database, passwords)
            ca = admin.execute("SELECT pg_read_file('/var/lib/postgresql/jb-test-tls/ca.crt')").fetchone()[0]
            with tempfile.TemporaryDirectory() as directory:
                environment = test_environment(directory, database, passwords[app], ca)
                verify_cli(environment, passwords, directory)
                verify_metadata_and_negative_health(environment)
        finally:
            remove_resources(admin, database, passwords)
    print(json.dumps({'result': 'passed', 'cleanup': 'removed'}), flush=True)


if __name__ == '__main__':
    main()
