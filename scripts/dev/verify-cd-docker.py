"""Exercise the durable controller against real, randomly named Docker Compose services.

Health is synthetic, with no Discord, database, SSH, registry or secret access.
This proves container switching/rollback mechanics, not production acceptance.
"""

import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.controller import Controller, parse_request
from deploy.journal import persist
from scripts.ci.cd_smoke import local_docker_environment, prepare_docker_client

OLD = 'sha256:' + '1' * 64
NEW = 'sha256:' + '2' * 64


def docker(arguments: list[str], environment=None, timeout=60) -> str:
    """@description: Bound local lab Docker calls without accepting shell text or production credentials.
    @param arguments: Explicit argv targeting only this lab's allocated resources.
    @param environment: Synthetic Compose inputs or the caller's Docker connection environment.
    @param timeout: Bounded CLI duration in seconds, shortened for cleanup.
    @return: Captured output; failure is reported without unbounded daemon diagnostics.
    """
    result = subprocess.run(['docker', *arguments], env=environment, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError('CD_LAB_DOCKER_FAILED:' + arguments[0])
    return result.stdout.strip()


class DockerLabBackend:
    """Own one disposable Compose project and immutable local image, never a live host policy."""

    def __init__(self, directory: Path, previous_health: str):
        """@description: Create isolated lab configuration without touching host CD policy.
        @param self: Docker rehearsal backend.
        @param directory: Newly allocated temporary directory.
        @param previous_health: Synthetic old health when started again during rollback.
        @return: None.
        """
        self.directory = directory
        self.project = 'jb-cd-lab-' + secrets.token_hex(8)
        configuration = directory / 'docker-client'
        self.environment = dict(local_docker_environment(), DOCKER_CONFIG=prepare_docker_client(configuration))
        self.image = docker(['image', 'inspect', '--format', '{{.Id}}', os.environ.get('TEST_IMAGE', 'jukebox101-test:v7-local')], self.environment)
        self.modes = {OLD: previous_health, NEW: 'good'}
        self.selected = OLD
        self.events = []
        command = 'import time; time.sleep(300)'
        probe = "import os; raise SystemExit(os.environ['LAB_HEALTH'] != 'good')"
        bot = dict(image=self.image, pull_policy='never', user='10002:10002', read_only=True, network_mode='none',
                   cap_drop=['ALL'], security_opt=['no-new-privileges:true'], mem_limit='128m',
                   entrypoint=['python', '-c', command], environment={'LAB_HEALTH': '${LAB_HEALTH:?}'},
                   healthcheck=dict(test=['CMD', 'python', '-c', probe], interval='1s', timeout='2s', retries=1),
                   labels={'com.jukebox101.disposable': 'cd-lab'})
        self.compose = directory / 'compose.json'
        self.compose.write_text(json.dumps(dict(services={'bot': bot})))

    def compose_run(self, arguments: list[str], health=None, timeout=60) -> str:
        """@description: Fix Compose project/path and provide only synthetic service health as input.
        @param self: Isolated backend.
        @param arguments: Fixed lifecycle subcommand.
        @param health: Explicit synthetic health for bootstrap or selected release.
        @param timeout: Explicit bounded cleanup or lifecycle duration in seconds.
        @return: Captured output from the local daemon.
        """
        environment = dict(self.environment, LAB_HEALTH=health or self.modes[self.selected], COMPOSE_DISABLE_ENV_FILE='1')
        return docker(['compose', '--project-name', self.project, '--env-file', '/dev/null',
                       '--file', str(self.compose), *arguments], environment, timeout=timeout)

    def preflight(self, candidate: str, previous: str) -> None:
        """@description: Check only this lab's synthetic releases and actual existing container health.
        @param self: Isolated backend.
        @param candidate: Synthetic next release identifier.
        @param previous: Synthetic previous release identifier.
        @return: None; an unhealthy baseline refuses deployment.
        """
        if {candidate, previous} != {OLD, NEW} or not self.healthy(previous):
            raise RuntimeError('CD_LAB_PREFLIGHT')

    def pull(self, candidate: str) -> None:
        """@description: Verify the existing immutable local image without accessing any registry.
        @param self: Isolated backend.
        @param candidate: Synthetic release identifier.
        @return: None; this lab deliberately does not test publication or provenance.
        """
        if candidate not in self.modes or docker(['image', 'inspect', '--format', '{{.Id}}', self.image], self.environment) != self.image:
            raise RuntimeError('CD_LAB_IMAGE_CHANGED')

    def is_stopped(self) -> bool:
        """@description: Query actual running containers in the unique lab service.
        @param self: Isolated backend.
        @return: Whether no previous lab service is running.
        """
        return not self.compose_run(['ps', '--quiet', '--status', 'running', 'bot'])

    def stop(self) -> None:
        """@description: Stop only the one named disposable Compose service.
        @param self: Isolated backend.
        @return: None; controller separately confirms exit.
        """
        self.compose_run(['stop', '--timeout', '2', 'bot'])
        self.events.append('stopped')

    def start(self, release: str) -> None:
        """@description: Refuse overlap, then start a real Compose service with the synthetic health mode.
        @param self: Isolated backend.
        @param release: One synthetic release identifier.
        @return: None; Docker unhealthy startup raises and exercises controller rollback.
        """
        if not self.is_stopped():
            raise RuntimeError('CD_LAB_GATEWAY_OVERLAP')
        self.selected = release
        self.events.append('start:' + release)
        self.compose_run(['up', '--detach', '--force-recreate', '--no-build', '--pull', 'never',
                          '--scale', 'bot=1', '--wait', '--wait-timeout', '15', 'bot'])

    def healthy(self, release: str) -> bool:
        """@description: Require exactly one real running container with the immutable image and healthy probe.
        @param self: Isolated backend.
        @param release: Expected synthetic selected identifier.
        @return: Real Docker health for this lab, not Discord or database readiness.
        """
        identifiers = self.compose_run(['ps', '--quiet', '--status', 'running', 'bot']).split()
        if len(identifiers) != 1 or release != self.selected:
            return False
        state = json.loads(docker(['inspect', '--format', '{{json .}}', identifiers[0]], self.environment))
        return state['State']['Health']['Status'] == 'healthy' and state['Image'] == self.image


def verify_replays(controller: Controller, backend: DockerLabBackend, request: dict, expected: str) -> dict:
    """@description: Prove duplicate, conflicting and stale requests do not restart the lab Gateway.
    @param controller: Real durable controller for this lab.
    @param backend: Randomly named Compose project.
    @param request: Completed fake request.
    @param expected: Saved terminal result.
    @return: Evidence of unchanged container identity and lifecycle events.
    """
    events = list(backend.events)
    identity = backend.compose_run(['ps', '--all', '--quiet', 'bot'])
    assert controller.execute(request) == expected
    assert controller.execute(dict(request, digest=OLD)) == 'rejected'
    assert controller.execute(dict(request, run=41)) == 'rejected'
    assert controller.execute(dict(request, attempt=2)) == 'rejected'
    assert backend.events == events
    assert backend.compose_run(['ps', '--all', '--quiet', 'bot']) == identity
    return dict(duplicate=expected, conflict='rejected', stale='rejected', same_run_retry='rejected', unchanged=True)


def verify_retry(controller: Controller, backend: DockerLabBackend, request: dict, expected: str) -> str:
    """@description: Retry a rolled-back release with a newer run, or confirm a failed rollback is latched.
    @param controller: Lab controller.
    @param backend: Lab-only synthetic health adapter.
    @param request: Original fake release identity.
    @param expected: Original terminal result.
    @return: New-run result or not_needed; an attempt bump alone never bypasses the highwater rule.
    """
    if expected == 'succeeded':
        return 'not_needed'
    backend.modes[NEW] = 'good'
    actual = controller.execute(dict(request, run=43))
    assert actual == ('succeeded' if expected == 'rolled_back' else 'rejected')
    if actual == 'succeeded':
        assert backend.healthy(NEW)
    return actual


def verify_transaction(directory: Path, backend: DockerLabBackend, expected: str) -> dict:
    """@description: Exercise real Compose switching and snapshot the initial result before retries.
    @param directory: Private journal scope.
    @param backend: Disposable service already bootstrapped healthy.
    @param expected: Required first transaction result.
    @return: Synthetic request/outcome and real lifecycle evidence.
    """
    backend.modes[NEW] = 'good' if expected == 'succeeded' else 'bad'
    persist(directory / 'journal.json', dict(version=1, last_known_good=OLD, highwater=0, requests={}))
    request = parse_request(f'deploy-v1 {"a" * 40} {NEW} 42 1')
    controller = Controller(directory, backend, authorize=lambda _: True)
    try:
        backend.start(NEW)
    except RuntimeError as error:
        assert str(error) == 'CD_LAB_GATEWAY_OVERLAP'
    else:
        raise AssertionError('overlapping Gateway start was accepted')
    actual = controller.execute(request)
    assert actual == expected, (actual, expected)
    journal = json.loads((directory / 'journal.json').read_text())
    assert journal['last_known_good'] == (NEW if expected == 'succeeded' else OLD)
    assert backend.events[0] == 'stopped'
    if expected != 'rollback_failed':
        assert backend.healthy(journal['last_known_good'])
    replay = verify_replays(controller, backend, request, expected)
    retry = verify_retry(controller, backend, request, expected)
    return dict(scenario=expected, result=actual, events=backend.events, image_id=backend.image,
                project=backend.project, replays=replay, new_run_retry=retry, overlap_refused=True)


def rehearse(expected: str) -> dict:
    """@description: Demonstrate success, candidate rollback, or latched rollback failure with real containers.
    @param expected: Required terminal result for this disposable scenario.
    @return: Secret-free receipt; finally removes only this invocation's project containers.
    """
    with tempfile.TemporaryDirectory(prefix='jb-cd-lab-') as temporary:
        directory = Path(temporary)
        backend = DockerLabBackend(directory, 'bad' if expected == 'rollback_failed' else 'good')
        print(json.dumps(dict(scenario=expected, project=backend.project, health='synthetic')), flush=True)
        try:
            backend.compose_run(['up', '--detach', '--wait', '--wait-timeout', '15', 'bot'], health='good')
            receipt = verify_transaction(directory, backend, expected)
        finally:
            cleanup(backend)
        return dict(receipt, cleanup='verified')


def cleanup(backend: DockerLabBackend) -> None:
    """@description: Pause active-work cancellation while bounded cleanup removes this project.
    @param backend: This invocation's disposable project and local client configuration.
    @return: None; cleanup errors remain failures, and an unspent active-work budget is restored.
    """
    remaining_seconds = signal.alarm(0)
    try:
        backend.compose_run(['down', '--timeout', '2'], timeout=30)
        remaining = docker(['ps', '--all', '--quiet', '--filter',
                            'label=com.docker.compose.project=' + backend.project], backend.environment, timeout=10)
        assert not remaining, 'CD_LAB_CLEANUP_INCOMPLETE'
    finally:
        if remaining_seconds:
            signal.alarm(remaining_seconds)


def deadline_exceeded(signum, frame) -> None:
    """@description: Cancel the rehearsal through finally without being treated as candidate failure.
    @param signum: Local timeout or termination signal.
    @param frame: Interrupted Python frame, never inspected.
    @return: Never; SystemExit bypasses controller rollback handling and permits scoped cleanup.
    """
    raise SystemExit('CD_LAB_DEADLINE_OR_TERMINATION')


def main() -> None:
    """@description: Preserve evidence for three real Compose transactions with synthetic health only.
    @param: none
    @return: None; any mismatch or cleanup error fails CI.
    """
    signal.signal(signal.SIGALRM, deadline_exceeded)
    signal.signal(signal.SIGTERM, deadline_exceeded)
    signal.alarm(90)  # [lab deadline]: Cleanup retains its separately bounded Docker calls.
    try:
        receipts = [rehearse(result) for result in ('succeeded', 'rolled_back', 'rollback_failed')]
    finally:
        signal.alarm(0)
    output = Path('ci-evidence')
    output.mkdir(exist_ok=True)
    (output / 'cd-docker.json').write_text(json.dumps(receipts, indent=2) + '\n')
    print(json.dumps(dict(result='passed', scenarios=[item['result'] for item in receipts], health='synthetic')))


if __name__ == '__main__':
    if not __debug__:
        raise SystemExit('CD_LAB_ASSERTIONS_REQUIRED')
    main()
