"""Provide a fixed bot-only Docker adapter without installing or invoking it on import.

All mutable container operations target one administrator-owned Compose service.
Real Docker integration and production compatibility remain required acceptance tests.
"""

import json
import re
import subprocess
import time
from pathlib import Path

from deploy.host_policy import clean_docker_environment, require_file_hash, require_owned_path, require_secret_metadata
from deploy.receipt import IMAGE, REPOSITORY
from scripts.ci.runtime_smoke import assert_bot_policy

COMPOSE_PATH = Path('/etc/jukebox101/compose.cd.yaml')
ENV_PATH = Path('/etc/jukebox101/bot.env')
PROJECT = 'jukebox101'
SERVICE = 'bot'


class HostDockerBackend:
    """Own fixed Docker arguments and continuously verify one selected Gateway."""

    def __init__(self, policy: dict, initial_release: str, runner=subprocess.run, clock=time.monotonic, pause=time.sleep):
        """@description: Bind root-owned policy and injectable OS boundaries for offline adapter tests.
        @param self: Bot lifecycle adapter.
        @param policy: Administrator-validated non-secret deployment policy.
        @param initial_release: Previous registry digest from the trusted durable journal.
        @param runner: Subprocess boundary; tests provide a command recorder.
        @param clock: Monotonic deadline source.
        @param pause: Poll interval primitive.
        @return: None.
        """
        self.policy = policy
        self.runner = runner
        self.clock = clock
        self.pause = pause
        self.selected = initial_release  # [single release]: Mutable only inside the controller's host lock.
        self.expected_image_id = None

    def _reference(self, digest: str) -> str:
        """@description: Permit only immutable digests in the fixed registry namespace.
        @param self: Adapter.
        @param digest: Candidate or saved previous release digest.
        @return: Registry ref; local legacy image requires separate reviewed bootstrap.
        """
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
            raise ValueError('INVALID_IMAGE_DIGEST')
        return f'{IMAGE}@{digest}'

    def _docker(self, arguments: list[str], *, timeout: int = 90) -> str:
        """@description: Target only the local Docker socket and suppress raw diagnostic output.
        @param self: Adapter holding the host lock.
        @param arguments: Explicit fixed argv, not a shell command.
        @param timeout: Bounded operation budget in seconds.
        @return: Captured stdout; nonzero status raises a safe category.
        """
        image = self._reference(self.selected) if self.selected else ''
        result = self.runner(['/usr/bin/docker', '--host=unix:///var/run/docker.sock', *arguments],
                             env=clean_docker_environment(image), cwd='/', text=True,
                             capture_output=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError('DOCKER_OPERATION_FAILED:' + arguments[0])
        return result.stdout

    def _compose(self, arguments: list[str], *, timeout: int = 90) -> str:
        """@description: Fix project, env and Compose paths independently of SSH/GitHub input.
        @param self: Adapter.
        @param arguments: Fixed service operation selected by this module.
        @param timeout: Bounded operation budget.
        @return: Captured stdout.
        """
        self._require_host_policy()
        return self._docker(['compose', '--project-name', PROJECT, '--env-file', str(ENV_PATH),
                             '--file', str(COMPOSE_PATH), *arguments], timeout=timeout)

    def preflight(self, candidate: str, previous: str) -> None:
        """@description: Refuse policy/config drift, unbootstrapped rollback or extra managed Gateways.
        @param self: Adapter.
        @param candidate: Independently verified registry digest.
        @param previous: Last-known-good registry digest, not an assumed local v7 tag.
        @return: None; all failures occur before stopping the old Bot.
        """
        self._require_host_policy()
        self._reference(candidate)
        self._reference(previous)
        self.selected = previous
        self._validate_config()
        if len(self._container_ids()) != 1:
            raise RuntimeError('EXPECTED_ONE_MANAGED_GATEWAY')
        self.expected_image_id = self._inspect_image(previous)
        if self._healthy_sample() is None:
            raise RuntimeError('PREVIOUS_GATEWAY_UNHEALTHY')

    def _require_host_policy(self) -> None:
        """@description: Enforce acceptance and immutable host configuration even during recovery.
        @param self: Adapter.
        @return: None; no Compose operation is permitted before these checks.
        """
        if self.policy.get('enabled') is not True or self.policy.get('docker_acceptance_verified') is not True:
            raise RuntimeError('DOCKER_ADAPTER_NOT_ACCEPTED')
        if self.policy.get('baseline_supports_gateway_health') is not True:
            raise RuntimeError('MANUAL_BOOTSTRAP_REQUIRED')
        require_owned_path(Path('/usr/bin/docker'))
        require_file_hash(COMPOSE_PATH, self.policy['compose_sha256'])
        require_file_hash(ENV_PATH, self.policy['env_sha256'])
        require_secret_metadata(self.policy['secret_paths'])

    def _validate_config(self) -> None:
        """@description: Reject extra services, host mounts, privileges or nonfixed networking.
        @param self: Adapter using trusted non-secret configuration.
        @return: None.
        """
        config = json.loads(self._compose(['config', '--format', 'json']))
        if set(config['services']) != {SERVICE}:
            raise RuntimeError('EXTRA_COMPOSE_SERVICES')
        bot = config['services'][SERVICE]
        assert_bot_policy(bot)
        if bot['image'] != self._reference(self.selected) or bot.get('command') or bot.get('entrypoint'):
            raise RuntimeError('COMPOSE_IMAGE_OR_COMMAND_MISMATCH')
        expected_application = str(self.policy['application_id'])
        if bot.get('labels', {}).get('com.jukebox101.gateway.application') != expected_application:
            raise RuntimeError('GATEWAY_LABEL_MISMATCH')
        if bot['environment'].get('DISCORD_EXPECTED_APPLICATION_ID') != expected_application:
            raise RuntimeError('GATEWAY_APPLICATION_MISMATCH')
        forbidden = ('build', 'volumes', 'devices', 'cap_add', 'pid', 'ipc', 'userns_mode', 'cgroup')
        if any(bot.get(key) for key in forbidden):
            raise RuntimeError('UNSAFE_COMPOSE_OPTIONS')
        approved_networks = set(self.policy['network_names'])
        actual_networks = {value['name'] for value in config['networks'].values()}
        if actual_networks != approved_networks or len(approved_networks) != 2:
            raise RuntimeError('NETWORK_POLICY_MISMATCH')
        paths = {name: item['file'] for name, item in config['secrets'].items()}
        if paths != self.policy['secret_paths']:
            raise RuntimeError('SECRET_MOUNT_POLICY_MISMATCH')

    def _container_ids(self) -> list[str]:
        """@description: Count every running managed Gateway, including other Compose projects.
        @param self: Adapter.
        @return: IDs carrying the administrator's Gateway application label.
        """
        application = str(self.policy['application_id'])
        if not application.isascii() or not application.isdecimal():
            raise ValueError('INVALID_APPLICATION_ID')
        label = f'com.jukebox101.gateway.application={application}'
        return self._docker(['ps', '--quiet', '--filter', f'label={label}']).split()

    def pull(self, candidate: str) -> None:
        """@description: Pull and inspect an immutable candidate while the old Gateway keeps running.
        @param self: Adapter.
        @param candidate: Verified candidate digest.
        @return: None; no candidate process or Discord login starts here.
        """
        self._docker(['pull', self._reference(candidate)], timeout=300)
        self._inspect_image(candidate)

    def _inspect_image(self, digest: str) -> str:
        """@description: Read image metadata only, without launching candidate code with real secrets.
        @param self: Adapter.
        @param digest: Approved candidate or previous registry digest.
        @return: Immutable local image ID.
        """
        template = ('{"id":{{json .Id}},"os":{{json .Os}},"arch":{{json .Architecture}},'
                    '"user":{{json .Config.User}},"entrypoint":{{json .Config.Entrypoint}},'
                    '"volumes":{{json .Config.Volumes}},"source":{{json (index .Config.Labels "org.opencontainers.image.source")}}}')
        metadata = json.loads(self._docker(['image', 'inspect', '--format', template, self._reference(digest)]))
        expected = {'os': 'linux', 'arch': 'amd64', 'user': '10001:10001', 'entrypoint': ['python', '-m', 'bot'],
                    'source': f'https://github.com/{REPOSITORY}'}
        if any(metadata.get(key) != value for key, value in expected.items()) or metadata.get('volumes'):
            raise RuntimeError('IMAGE_POLICY_MISMATCH')
        return metadata['id']

    def stop(self) -> None:
        """@description: Stop the fixed Bot service with bounded SIGTERM grace, never the stack.
        @param self: Adapter.
        @return: None; the controller separately requires confirmed exit.
        """
        self._compose(['stop', '--timeout', '30', SERVICE], timeout=45)

    def is_stopped(self) -> bool:
        """@description: Require no running labeled Gateway plus no running fixed Bot service.
        @param self: Adapter.
        @return: Whether it is safe to start exactly one Gateway.
        """
        services = self._compose(['ps', '--quiet', '--status', 'running', SERVICE]).split()
        return not self._container_ids() and not services

    def start(self, release: str) -> None:
        """@description: Start only one immutable Bot after a second stop confirmation.
        @param self: Adapter.
        @param release: Verified candidate or preserved previous registry digest.
        @return: None; startup or health failure triggers controller rollback.
        """
        if not self.is_stopped():
            raise RuntimeError('OLD_GATEWAY_STILL_RUNNING')
        self.selected = release
        self.expected_image_id = self._inspect_image(release)
        self._compose(['up', '--detach', '--no-deps', '--no-build', '--pull', 'never',
                       '--scale', 'bot=1', '--wait', '--wait-timeout', '180', SERVICE], timeout=200)

    def healthy(self, release: str) -> bool:
        """@description: Observe the same process/container, image and restart count continuously.
        @param self: Adapter.
        @param release: Expected immutable release.
        @return: True only after the full stability window passes.
        """
        if release != self.selected:
            return False
        deadline = self.clock() + 120
        baseline = None
        while self.clock() < deadline:
            sample = self._healthy_sample()
            if sample is None or (baseline is not None and sample != baseline):
                return False
            baseline = sample
            self.pause(5)
        return baseline is not None

    def _healthy_sample(self):
        """@description: Require running, Docker healthy, exact image and real combined app health.
        @param self: Adapter.
        @return: Stable container/restart fingerprint or None for an unhealthy sample.
        """
        identifiers = self._container_ids()
        service_ids = self._compose(['ps', '--quiet', '--status', 'running', SERVICE]).split()
        if len(identifiers) != 1 or identifiers != service_ids:
            return None
        template = '{"running":{{json .State.Running}},"health":{{json .State.Health.Status}},"image":{{json .Image}},"restarts":{{.RestartCount}}}'
        state = json.loads(self._docker(['inspect', '--format', template, identifiers[0]]))
        if not state['running'] or state['health'] != 'healthy' or state['image'] != self.expected_image_id:
            return None
        health = json.loads(self._docker(['exec', identifiers[0], 'python', '-m', 'bot.healthcheck'], timeout=35))
        if health.get('result') != 'ready' or health.get('problems'):
            return None
        return identifiers[0], state['restarts']
