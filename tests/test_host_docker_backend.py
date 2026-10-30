"""Test fixed host Docker commands with a recorder and virtual stability clock.

This is adapter simulation; no image, Docker daemon, real DB or Discord is exercised.
"""

import json
from types import SimpleNamespace

import pytest

from deploy.docker_backend import HostDockerBackend
from deploy.receipt import IMAGE, REPOSITORY

OLD = 'sha256:' + '1' * 64
NEW = 'sha256:' + '2' * 64
IMAGE_ID = 'sha256:' + '3' * 64


def rendered_config():
    """@description: Provide a synthetic rendered policy using no secret content.
    @param: none
    @return: Minimal valid controlled Bot configuration.
    """
    environment = dict(JUKEBOX_NICKEL_CLAIM='off', JUKEBOX_FORMAL_EXCHANGE='off',
                       DB_SSLMODE='verify-full', DISCORD_EXPECTED_APPLICATION_ID='123')
    bot = {'image': f'{IMAGE}@{OLD}', 'user': '10001:10001', 'read_only': True, 'cap_drop': ['ALL'],
           'security_opt': ['no-new-privileges:true'], 'healthcheck': {'test': ['CMD', 'python', '-m', 'bot.healthcheck']},
           'environment': environment, 'deploy': {'resources': {'limits': {'memory': '536870912', 'cpus': '1.0'}}},
           'labels': {'com.jukebox101.gateway.application': '123'},
           'secrets': [{'source': name} for name in ('discord_bot_token', 'db_password', 'db_ca.crt')]}
    return {'services': {'bot': bot}, 'networks': {'egress': {'name': 'fake-egress'}, 'database': {'name': 'fake-db'}},
            'secrets': {name: {'file': '/fake/' + name} for name in ('discord_bot_token', 'db_password', 'db_ca.crt')}}


class DockerRecorder:
    """Model only the Docker boundary; distinguish simulations from actual daemon results."""

    def __init__(self):
        """@description: Initialize synthetic container state and virtual observation time.
        @param self: Recorder fixture.
        @return: None.
        """
        self.calls = []
        self.config = rendered_config()
        self.running = ['container-one']
        self.state = dict(running=True, health='healthy', image=IMAGE_ID, restarts=0)
        self.app = dict(result='ready', problems=[])
        self.metadata = dict(id=IMAGE_ID, os='linux', arch='amd64', user='10001:10001',
                             entrypoint=['python', '-m', 'bot'], volumes=None,
                             source=f'https://github.com/{REPOSITORY}')
        self.elapsed = 0
        self.after_pause = None

    def __call__(self, argv, **kwargs):
        """@description: Record fixed argv and emulate safe daemon responses.
        @param self: Synthetic recorder.
        @param argv: Explicit Docker arguments.
        @param kwargs: Environment, cwd and timeout bounds.
        @return: Synthetic completed subprocess.
        """
        self.calls.append((argv, kwargs))
        command = argv[2:]
        match command[0]:
            case 'compose':
                output = self.compose(command[7:])
            case 'image':
                output = json.dumps(self.metadata)
            case 'ps':
                output = '\n'.join(self.running)
            case 'inspect':
                output = json.dumps(self.state)
            case 'exec':
                output = json.dumps(self.app)
            case _:
                output = ''
        return SimpleNamespace(returncode=0, stdout=output)

    def compose(self, arguments):
        """@description: Model fixed-service config, stop, start and ps only.
        @param self: Recorder.
        @param arguments: Compose subcommand after fixed prefix.
        @return: Synthetic stdout.
        """
        if arguments[0] == 'config':
            return json.dumps(self.config)
        if arguments[0] == 'stop':
            self.running = []
        elif arguments[0] == 'up':
            self.running = ['container-one']
        return '\n'.join(self.running) if arguments[0] == 'ps' else ''

    def pause(self, seconds):
        """@description: Advance simulated time without waiting or claiming real stability acceptance.
        @param self: Recorder.
        @param seconds: Simulated elapsed interval.
        @return: None.
        """
        self.elapsed += seconds
        if self.after_pause:
            self.after_pause()


@pytest.fixture
def adapter(monkeypatch):
    """@description: Replace host filesystem probes and Docker boundary, never the lifecycle policy.
    @param monkeypatch: Isolated boundary replacement.
    @return: Adapter, recorder and metadata probe record.
    """
    checks = []
    monkeypatch.setattr('deploy.docker_backend.require_owned_path', lambda path: checks.append(('path', str(path))))
    monkeypatch.setattr('deploy.docker_backend.require_file_hash', lambda path, digest: checks.append(('hash', str(path))))
    monkeypatch.setattr('deploy.docker_backend.require_secret_metadata', lambda paths: checks.append(('metadata', paths)))
    recorder = DockerRecorder()
    policy = dict(enabled=True, docker_acceptance_verified=True, baseline_supports_gateway_health=True,
                  application_id='123', compose_sha256='a' * 64, env_sha256='b' * 64,
                  network_names=['fake-egress', 'fake-db'],
                  secret_paths={name: entry['file'] for name, entry in recorder.config['secrets'].items()})
    backend = HostDockerBackend(policy, OLD, recorder, clock=lambda: recorder.elapsed, pause=recorder.pause)
    return backend, recorder, checks


def test_stop_before_start_and_continuous_exact_image_health(adapter):
    """@description: Require old health and confirmed exit before starting one digest, then observe 120 virtual seconds.
    @param adapter: Simulated Docker boundary.
    @return: None.
    """
    backend, recorder, checks = adapter
    backend.preflight(NEW, OLD)
    backend.pull(NEW)
    with pytest.raises(RuntimeError, match='OLD_GATEWAY_STILL_RUNNING'):
        backend.start(NEW)
    backend.stop()
    assert backend.is_stopped()
    backend.start(NEW)
    assert backend.healthy(NEW) and recorder.elapsed == 120
    assert checks and any(item[0] == 'metadata' for item in checks)
    up = next(argv for argv, _ in recorder.calls if 'up' in argv)
    assert up[-1] == 'bot' and '--no-deps' in up and '--no-build' in up
    assert up[up.index('--scale') + 1] == 'bot=1'
    assert up[up.index('--pull') + 1] == 'never'
    for argv, options in recorder.calls:
        assert argv[:2] == ['/usr/bin/docker', '--host=unix:///var/run/docker.sock']
        assert not {'down', 'prune', 'migrate', 'restore', '--remove-orphans'} & set(argv)
        assert options['cwd'] == '/' and 'DOCKER_HOST' not in options['env']


@pytest.mark.parametrize('field,value', [('health', 'unhealthy'), ('image', 'wrong-image'), ('running', False)])
def test_old_unhealthy_refuses_before_stop(adapter, field, value):
    """@description: Reject an unhealthy or wrong-image baseline before any destructive action.
    @param adapter: Simulated Docker boundary.
    @param field: Baseline runtime metadata.
    @param value: Unsafe metadata value.
    @return: None.
    """
    backend, recorder, _ = adapter
    recorder.state[field] = value
    with pytest.raises(RuntimeError, match='PREVIOUS_GATEWAY_UNHEALTHY'):
        backend.preflight(NEW, OLD)
    assert not any('stop' in argv or 'up' in argv for argv, _ in recorder.calls)


@pytest.mark.parametrize('fault', ['disconnect', 'restart', 'replacement', 'duplicate', 'image'])
def test_ready_then_loss_fails_stability_gate(adapter, fault):
    """@description: Detect loss after an initial healthy sample instead of trusting READY once.
    @param adapter: Simulated runtime boundary and clock.
    @param fault: Failure injected after first sample.
    @return: None.
    """
    backend, recorder, _ = adapter
    backend.expected_image_id = IMAGE_ID

    def change():
        """@description: Change one independently observed health dimension.
        @param: none
        @return: None.
        """
        match fault:
            case 'disconnect':
                recorder.app = {'result': 'not_ready', 'problems': ['gateway_disconnected']}
            case 'restart':
                recorder.state['restarts'] = 1
            case 'replacement':
                recorder.running = ['container-two']
            case 'duplicate':
                recorder.running.append('container-two')
            case _:
                recorder.state['image'] = 'wrong-image'

    recorder.after_pause = change
    assert not backend.healthy(OLD)
    assert recorder.elapsed == 5


@pytest.mark.parametrize('fault', ['extra_service', 'port', 'mount', 'network', 'secret', 'flag', 'command', 'label', 'image'])
def test_config_policy_drift_refused(adapter, fault):
    """@description: Refuse unexpected host reach, service changes and altered command or Application identity.
    @param adapter: Synthetic rendered configuration.
    @param fault: Distinct unsafe configuration change.
    @return: None.
    """
    backend, recorder, _ = adapter
    bot = recorder.config['services']['bot']
    match fault:
        case 'extra_service':
            recorder.config['services']['db'] = {}
        case 'port':
            bot['ports'] = [{'published': '8080'}]
        case 'mount':
            bot['volumes'] = [{'source': '/var/run/docker.sock'}]
        case 'network':
            recorder.config['networks']['database']['name'] = 'other-project'
        case 'secret':
            recorder.config['secrets']['db_password']['file'] = '/other/secret'
        case 'flag':
            bot['environment']['JUKEBOX_NICKEL_CLAIM'] = 'on'
        case 'command':
            bot['command'] = ['/bin/sh']
        case 'label':
            bot['labels']['com.jukebox101.gateway.application'] = '999'
        case _:
            bot['image'] = 'unreviewed:latest'
    with pytest.raises((RuntimeError, AssertionError)):
        backend.preflight(NEW, OLD)
    assert not any('stop' in argv or 'up' in argv for argv, _ in recorder.calls)


@pytest.mark.parametrize('gate', ['enabled', 'docker_acceptance_verified', 'baseline_supports_gateway_health'])
def test_no_docker_before_host_acceptance(adapter, gate):
    """@description: Deny service operations until every host-specific acceptance gate passes.
    @param adapter: Simulated boundary with no actual Docker.
    @param gate: Required acceptance gate to remove.
    @return: None.
    """
    backend, recorder, _ = adapter
    backend.policy.pop(gate)
    with pytest.raises(RuntimeError):
        backend.preflight(NEW, OLD)
    assert recorder.calls == []


@pytest.mark.parametrize('digest', ['v7', 'latest', 'sha256:' + 'A' * 64, 'x; touch /tmp/untrusted'])
def test_mutable_and_invalid_release_refused(adapter, digest):
    """@description: Accept only a fixed GHCR repository with an immutable lowercase digest.
    @param adapter: Simulated boundary.
    @param digest: Invalid release selector.
    @return: None.
    """
    backend, recorder, _ = adapter
    with pytest.raises(ValueError):
        backend.preflight(digest, OLD)
    assert recorder.calls == []
