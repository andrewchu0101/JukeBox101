"""Verify PostgreSQL 18.6/TLS and the exact runtime image on disposable Docker resources.

Each invocation owns random names and an internal-only network. JUnit evidence
requires at least the reviewed baseline and zero unexpected skips or errors.
"""

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.ci.check_junit import summarize

POSTGRES_IMAGE = 'postgres:18.6-bookworm'


class DockerLab:
    """Track only resources created successfully by this isolated invocation."""

    def __init__(self):
        """@description: Allocate random test-only names and an in-memory password.
        @param self: Disposable lab.
        @return: None.
        """
        self.docker = os.environ.get('DOCKER', 'docker')
        self.name = 'jb-ci-' + secrets.token_hex(8)
        self.network = self.name + '-net'
        self.volume = self.name + '-data'
        self.password = secrets.token_urlsafe(32)
        self.cleanup = []

    def run(self, arguments, *, check=True, source=None):
        """@description: Bound Docker calls without echoing credential-bearing argv or output on errors.
        @param self: Disposable lab.
        @param arguments: Explicit Docker argv, never a shell string.
        @param check: Require zero status unless probing readiness.
        @param source: Optional Python stdin for the exact runtime image.
        @return: Captured subprocess result.
        """
        result = subprocess.run([self.docker, *arguments], input=source, text=True,
                                capture_output=True, timeout=900)
        if check and result.returncode:
            raise RuntimeError(f'Docker {arguments[0]} failed ({result.returncode})')
        return result

    def create(self):
        """@description: Start one marked test database with no host ports or Internet route.
        @param self: Disposable lab.
        @return: None.
        """
        self.run(['network', 'create', '--internal', self.network])
        self.cleanup.append(['network', 'rm', self.network])
        self.run(['volume', 'create', '--label', 'com.jukebox101.disposable=ci', self.volume])
        self.cleanup.append(['volume', 'rm', self.volume])
        self.run(['run', '-d', '--name', self.name, '--network', self.network, '--network-alias', 'postgres',
                  '--mount', f'type=volume,source={self.volume},target=/var/lib/postgresql',
                  '-e', 'POSTGRES_PASSWORD=' + self.password, POSTGRES_IMAGE,
                  '-c', 'cluster_name=jukebox101-disposable-test'])
        self.cleanup.append(['rm', '-f', self.name])
        for _ in range(60):
            if self.run(['exec', '-u', 'postgres', self.name, 'pg_isready', '-U', 'postgres'], check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('disposable server not ready')
        version = self.run(['exec', '-u', 'postgres', self.name, 'psql', '-U', 'postgres', '-tAc', 'SHOW server_version_num'])
        if version.stdout.strip() != '180006':
            raise RuntimeError('require PostgreSQL 18.6 exactly')
        environment = dict(os.environ, DOCKER=self.docker, PG_CONTAINER=self.name)
        subprocess.run(['bash', 'scripts/dev/enable-test-tls.sh'], env=environment, check=True, timeout=120)

    def test_arguments(self):
        """@description: Pass only this lab's credentials to the disposable test network.
        @param self: Disposable lab.
        @return: Shared Docker network and environment arguments.
        """
        return ['--network', self.network, '-e', 'JB_PG_TEST_HOST=postgres',
                '-e', 'JB_PG_TEST_ADMIN_USER=postgres', '-e', 'JB_PG_TEST_ADMIN_PASSWORD=' + self.password]

    def postgres_tests(self, output):
        """@description: Preserve JUnit even when pytest fails, with a non-decreasing coverage floor.
        @param self: Disposable lab.
        @param output: Host directory for safe CI evidence.
        @return: Structured test totals.
        """
        name = self.name + '-tests'
        image = os.environ.get('TEST_IMAGE', 'jukebox101-test:v7-local')
        self.run(['create', '--name', name, *self.test_arguments(), image,
                  '-q', '-p', 'no:cacheprovider', '-m', 'postgres', '--junitxml=/tmp/postgres.xml'])
        self.cleanup.append(['rm', '-f', name])
        result = self.run(['start', '-a', name], check=False)
        self.run(['cp', f'{name}:/tmp/postgres.xml', str(output / 'postgres.xml')])
        totals = summarize(output / 'postgres.xml', 35)
        status = self.run(['inspect', '--format', '{{.State.ExitCode}}', name])
        if result.returncode or status.stdout.strip() != '0':
            raise RuntimeError('postgres test process failed')
        return totals

    def secret_volume(self, image):
        """@description: Prepare read-only dummy secret mounts, never a real Bot credential.
        @param self: Disposable lab.
        @param image: The exact reviewed runtime image ID.
        @return: Random named volume holding test-only 0400 files for uid 10001.
        """
        volume = self.name + '-secrets'
        self.run(['volume', 'create', '--label', 'com.jukebox101.disposable=ci', volume])
        self.cleanup.append(['volume', 'rm', volume])
        source = """import os,pathlib
root=pathlib.Path('/run/ci-secrets')
os.chown(root,0,10001)
os.chmod(root,0o750)
for name in ('token','db_password'):
 path=root/name
 path.write_text('SYNTHETIC-CI-NOT-A-SECRET')
 os.chown(path,10001,10001)
 os.chmod(path,0o400)
"""
        self.run(['run', '--rm', '-i', '--network', 'none', '--user', '0:0', '--read-only',
                  '--mount', f'type=volume,source={volume},target=/run/ci-secrets',
                  '--entrypoint', 'python', image, '-'], source=source)
        return volume

    def runtime_tests(self):
        """@description: Exercise the built immutable runtime, without rebuilding or Discord login.
        @param self: Disposable lab.
        @return: Safe structured runtime receipt.
        """
        requested = os.environ['RUNTIME_IMAGE']
        image = self.run(['image', 'inspect', '--format', '{{.Id}}', requested]).stdout.strip()
        volume = self.secret_volume(image)
        source = Path('scripts/dev/verify-runtime-postgres.py').read_text()
        result = self.run(['run', '--rm', '-i', *self.test_arguments(), '--read-only',
                           '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                           '--tmpfs', '/tmp:rw,nosuid,nodev,size=33554432', '--memory', '512m', '--cpus', '1',
                           '--mount', f'type=volume,source={volume},target=/run/ci-secrets,readonly',
                           '--entrypoint', 'python', image, '-'], source=source)
        return {'result': 'passed', 'image_id': image, 'checks': result.stdout.splitlines()}

    def close(self):
        """@description: Attempt every owned cleanup even if one removal fails.
        @param self: Disposable lab.
        @return: None; incomplete cleanup fails the run visibly.
        """
        failures = []
        for arguments in reversed(self.cleanup):
            try:
                self.run(arguments)
            except (RuntimeError, subprocess.TimeoutExpired):
                failures.append(arguments[0])
        if failures:
            raise RuntimeError('disposable cleanup failed: ' + ','.join(failures))


def main():
    """@description: Require both real PG/TLS tests and exact-runtime verification.
    @param: none
    @return: None; all created resources are cleaned in finally.
    """
    output = Path('ci-evidence')
    output.mkdir(exist_ok=True)
    lab = DockerLab()
    try:
        lab.create()
        evidence = {'postgres': lab.postgres_tests(output), 'runtime': lab.runtime_tests()}
        (output / 'postgres-runtime.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print(json.dumps({'result': 'passed', 'postgres': evidence['postgres'], 'runtime': 'passed'}))
    finally:
        lab.close()


if __name__ == '__main__':
    main()
