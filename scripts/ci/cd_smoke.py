"""Run a short CI/CD rehearsal using the existing offline tests and Compose lab.

Only a local Unix Docker endpoint is accepted. No runtime image, database,
registry publication, SSH request or production configuration is used.
"""

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from scripts.ci.check_junit import summarize

TESTS = ('ci_safety_contract', 'release_request', 'github_notifications',
         'deploy_controller', 'host_queue', 'cicd_smoke', 'cd_smoke_runner')
ROOT = Path(__file__).resolve().parents[2]


def local_docker_environment() -> dict:
    """@description: Resolve a local socket and discard inherited application and Compose settings.
    @param: none
    @return: Minimal Docker client environment; remote endpoints fail before daemon access.
    """
    keys = ('PATH', 'HOME', 'DOCKER_CONFIG', 'DOCKER_CONTEXT', 'DOCKER_HOST')
    discovery = {key: os.environ[key] for key in keys if key in os.environ}
    endpoint = discovery.get('DOCKER_HOST', '')
    if not endpoint or discovery.get('DOCKER_CONTEXT'):
        result = subprocess.run(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'],
                                env=discovery, capture_output=True, text=True, check=True, timeout=10)
        endpoint = result.stdout.strip()
    if not endpoint.startswith('unix:///') or '\n' in endpoint:
        raise ValueError('CD_SMOKE_REQUIRES_LOCAL_UNIX_DOCKER')
    return {**{key: discovery[key] for key in ('PATH', 'HOME') if key in discovery}, 'DOCKER_HOST': endpoint}


def prepare_docker_client(directory: Path) -> str:
    """@description: Preserve an installed user Compose plugin without copying registry authentication.
    @param directory: New disposable Docker client directory.
    @return: Configuration path containing only an optional Compose executable symlink.
    """
    directory.mkdir(exist_ok=True)
    original = Path(os.environ.get('DOCKER_CONFIG', str(Path.home() / '.docker')))
    plugin = original / 'cli-plugins/docker-compose'
    if plugin.is_file():
        destination = directory / 'cli-plugins'
        destination.mkdir()
        (destination / 'docker-compose').symlink_to(plugin.resolve())
    return str(directory)


def offline(image: str, environment: dict, output: Path) -> dict:
    """@description: Reuse focused tests inside one offline, non-root container without host mounts.
    @param image: Locally resolved immutable test image ID.
    @param environment: Local-only client environment without credentials.
    @param output: Evidence directory for this invocation.
    @return: Validated zero-skip JUnit totals; failed tests remain failures.
    """
    name = 'jb-cd-smoke-' + secrets.token_hex(8)
    command = ['docker', 'create', '--name', name, '--pull', 'never', '--network', 'none',
               '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', image,
               '-q', '-p', 'no:cacheprovider', *['tests/test_' + item + '.py' for item in TESTS],
               '--junitxml=/tmp/cd-smoke.xml']
    try:
        subprocess.run(command, env=environment, check=True, timeout=30, stdout=subprocess.DEVNULL)
        result = subprocess.run(['docker', 'start', '-a', name], env=environment, timeout=120)
        subprocess.run(['docker', 'cp', name + ':/tmp/cd-smoke.xml', str(output / 'cd-smoke.xml')],
                       env=environment, check=True, timeout=30, stdout=subprocess.DEVNULL)
        result.check_returncode()
        status = subprocess.check_output(['docker', 'inspect', '--format', '{{.State.ExitCode}}', name],
                                         env=environment, text=True, timeout=10)
        if status.strip() != '0':
            raise RuntimeError('CD_SMOKE_TEST_PROCESS_FAILED')
        return summarize(output / 'cd-smoke.xml', 108)
    finally:
        subprocess.run(['docker', 'rm', '-f', name], env=environment, check=True, timeout=30,
                       stdout=subprocess.DEVNULL)


def rehearse(environment: dict, output: Path) -> dict:
    """@description: Freeze a prebuilt local test image and run both rehearsal layers.
    @param environment: Local Docker environment with an empty temporary client configuration.
    @param output: Evidence directory, never a production state path.
    @return: Offline totals plus synthetic Compose outcomes.
    """
    image = os.environ.get('TEST_IMAGE', 'jukebox101-test:v7-local')
    image_id = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', image],
                                       env=environment, text=True, timeout=10).strip()
    subprocess.run(['docker', 'compose', 'version'], env=environment, capture_output=True, check=True, timeout=10)
    totals = offline(image_id, environment, output)
    subprocess.run([sys.executable, 'scripts/dev/verify-cd-docker.py'], cwd=ROOT,
                   env=dict(environment, TEST_IMAGE=image_id), check=True, timeout=240)
    return dict(offline=totals, compose=json.loads((output / 'cd-docker.json').read_text()), image_id=image_id)


def main() -> None:
    """@description: Write truthful evidence on success or failure and return nonzero on any failed layer.
    @param: none
    @return: None; no test dependency or image is installed implicitly.
    """
    output = ROOT / 'ci-evidence'
    output.mkdir(exist_ok=True)
    for filename in ('cd-smoke.json', 'cd-smoke.xml', 'cd-docker.json'):
        (output / filename).unlink(missing_ok=True)
    started = time.monotonic()
    receipt = dict(result='failed', scope='isolated deployment E2E simulation / CI/CD smoke test')
    try:
        environment = local_docker_environment()
        with tempfile.TemporaryDirectory(prefix='jb-cd-client-') as configuration:
            receipt.update(rehearse(dict(environment, DOCKER_CONFIG=prepare_docker_client(Path(configuration))), output))
        receipt['result'] = 'passed'
    finally:
        receipt['seconds'] = round(time.monotonic() - started, 2)
        (output / 'cd-smoke.json').write_text(json.dumps(receipt, indent=2) + '\n')
        print(json.dumps(dict(result=receipt['result'], seconds=receipt['seconds'],
                              evidence=str(output / 'cd-smoke.json'))), flush=True)


if __name__ == '__main__':
    main()
