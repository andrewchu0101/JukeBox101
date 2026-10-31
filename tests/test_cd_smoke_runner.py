"""Protect the rehearsal runner's local-only and truthful-result boundaries.

Subprocesses are faked here; the actual Docker execution is a separate lab layer.
"""

import subprocess
from unittest.mock import Mock, call

import pytest

from scripts.ci import cd_smoke


@pytest.mark.parametrize('endpoint', ['ssh://production.invalid', 'tcp://127.0.0.1:2375', 'https://example.invalid'])
def test_remote_docker_refused_before_any_command(monkeypatch, endpoint):
    """@description: Inherited remote daemon selectors cannot redirect test mutations.
    @param monkeypatch: Process environment isolation.
    @param endpoint: Non-Unix Docker address.
    @return: None; discovery and daemon commands are not run.
    """
    monkeypatch.delenv('DOCKER_CONTEXT', raising=False)
    monkeypatch.setenv('DOCKER_HOST', endpoint)
    runner = Mock(side_effect=AssertionError('unexpected external command'))
    monkeypatch.setattr(cd_smoke.subprocess, 'run', runner)
    with pytest.raises(ValueError, match='LOCAL_UNIX'):
        cd_smoke.local_docker_environment()
    runner.assert_not_called()


def test_context_endpoint_and_environment_are_filtered(monkeypatch):
    """@description: Resolve the selected context locally and omit inherited application secrets.
    @param monkeypatch: Fake context discovery boundary.
    @return: None.
    """
    monkeypatch.setenv('DOCKER_CONTEXT', 'synthetic-context')
    monkeypatch.setenv('DISCORD_BOT_TOKEN', 'synthetic-never-forward')
    monkeypatch.setenv('COMPOSE_FILE', '/synthetic/production.yaml')
    runner = Mock(return_value=subprocess.CompletedProcess([], 0, stdout='unix:///fake/docker.sock\n'))
    monkeypatch.setattr(cd_smoke.subprocess, 'run', runner)
    environment = cd_smoke.local_docker_environment()
    assert environment['DOCKER_HOST'] == 'unix:///fake/docker.sock'
    assert set(environment) <= {'PATH', 'HOME', 'DOCKER_HOST'}
    assert set(runner.call_args.kwargs['env']) <= {'PATH', 'HOME', 'DOCKER_CONFIG', 'DOCKER_CONTEXT', 'DOCKER_HOST'}


def test_failed_layer_overwrites_stale_pass_evidence(monkeypatch, tmp_path):
    """@description: An incomplete rehearsal never reuses a previous successful report.
    @param monkeypatch: Replace only the execution boundary.
    @param tmp_path: Isolated report scope.
    @return: None; original failure remains visible.
    """
    import json
    output = tmp_path / 'ci-evidence'
    output.mkdir()
    for filename in ('cd-smoke.json', 'cd-smoke.xml', 'cd-docker.json'):
        (output / filename).write_text('stale-success')
    monkeypatch.setattr(cd_smoke, 'ROOT', tmp_path)
    monkeypatch.setattr(cd_smoke, 'local_docker_environment', lambda: {})
    monkeypatch.setattr(cd_smoke, 'rehearse', Mock(side_effect=RuntimeError('synthetic failure')))
    with pytest.raises(RuntimeError, match='synthetic failure'):
        cd_smoke.main()
    receipt = json.loads((output / 'cd-smoke.json').read_text())
    assert receipt['result'] == 'failed'
    assert receipt['database'] == dict(driver_contract='not_run', real_connection='not_run', real_persistence='not_verified')
    assert not (output / 'cd-smoke.xml').exists() and not (output / 'cd-docker.json').exists()


def test_successful_simulation_does_not_claim_real_database_verification(monkeypatch, tmp_path):
    """@description: Passing isolated layers proves only the mock driver contract, never real transport or storage.
    @param monkeypatch: Replace execution boundaries; no daemon or database is contacted.
    @param tmp_path: Private synthetic Compose evidence directory.
    @return: None; a green simulation must retain unverified live database fields.
    """
    (tmp_path / 'cd-docker.json').write_text('[]')
    monkeypatch.setattr(cd_smoke.subprocess, 'check_output', Mock(return_value='sha256:' + 'a' * 64))
    monkeypatch.setattr(cd_smoke.subprocess, 'run', Mock(return_value=subprocess.CompletedProcess([], 0)))
    monkeypatch.setattr(cd_smoke, 'offline', Mock(return_value={'result': 'passed'}))
    receipt = cd_smoke.rehearse({}, tmp_path)
    assert 'foundation' in cd_smoke.TESTS and 'repository' in cd_smoke.TESTS
    assert receipt['database'] == dict(driver_contract='passed_mock_boundary',
                                       real_connection='not_run', real_persistence='not_verified')


def test_failed_pytest_still_removes_only_its_container(monkeypatch, tmp_path):
    """@description: A failed test exit remains failed and triggers narrowly scoped cleanup.
    @param monkeypatch: Substitute the Docker CLI boundary.
    @param tmp_path: Private evidence directory.
    @return: None; no daemon call is performed by this unit test.
    """
    runner = Mock(side_effect=[subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 1),
                              subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)])
    monkeypatch.setattr(cd_smoke.subprocess, 'run', runner)
    with pytest.raises(subprocess.CalledProcessError):
        cd_smoke.offline('sha256:' + 'a' * 64, {}, tmp_path)
    commands = [call.args[0] for call in runner.call_args_list]
    name = commands[0][commands[0].index('--name') + 1]
    assert name.startswith('jb-cd-smoke-')
    assert commands[-1] == ['docker', 'rm', '-f', name]
    assert '--network' in commands[0] and 'none' in commands[0] and '--pull' in commands[0]
    assert not any(option in commands[0] for option in ('--volume', '-v', '--env', '-e'))


def test_cancelled_compose_transaction_cleans_its_project(monkeypatch):
    """@description: Deadline cancellation bypasses controller exception handling and reaches scoped cleanup.
    @param monkeypatch: Replace the lab Docker boundary, keeping actual temporary-directory lifetime.
    @return: None; cancellation remains non-success and no Docker daemon is contacted.
    """
    import runpy
    namespace = runpy.run_path(str(cd_smoke.ROOT / 'scripts/dev/verify-cd-docker.py'))
    scope = namespace['rehearse'].__globals__
    backend = Mock(project='jb-cd-lab-synthetic', environment={})
    constructor = Mock(return_value=backend)
    monkeypatch.setitem(scope, 'DockerLabBackend', constructor)
    monkeypatch.setitem(scope, 'verify_transaction', Mock(side_effect=SystemExit('synthetic deadline')))
    daemon = Mock(return_value='')
    monkeypatch.setitem(scope, 'docker', daemon)
    with pytest.raises(SystemExit, match='synthetic deadline'):
        namespace['rehearse']('succeeded')
    assert backend.compose_run.call_args.args[0] == ['down', '--timeout', '2']
    assert daemon.call_args.args[0][-1] == 'label=com.docker.compose.project=jb-cd-lab-synthetic'
    assert not constructor.call_args.args[0].exists()


def test_disposable_client_keeps_compose_without_copying_credentials(monkeypatch, tmp_path):
    """@description: Preserve user-installed Compose discovery while leaving synthetic credentials behind.
    @param monkeypatch: Select a private fake Docker client source.
    @param tmp_path: Source and disposable client directories.
    @return: None; plugin metadata is inspected but the fake executable is never run.
    """
    original = tmp_path / 'original'
    plugins = original / 'cli-plugins'
    plugins.mkdir(parents=True)
    plugin = plugins / 'docker-compose'
    plugin.write_text('synthetic plugin; never execute')
    (original / 'config.json').write_text('{"auths":{"synthetic.invalid":{"auth":"fake"}}}')
    monkeypatch.setenv('DOCKER_CONFIG', str(original))
    destination = tmp_path / 'disposable'
    assert cd_smoke.prepare_docker_client(destination) == str(destination)
    assert (destination / 'cli-plugins/docker-compose').resolve() == plugin
    assert not (destination / 'config.json').exists()


@pytest.mark.parametrize('remaining', ['', 'synthetic-container'])
def test_cleanup_pauses_deadline_and_preserves_failures(monkeypatch, remaining):
    """@description: The lab deadline cannot cancel down; leftovers remain a failure after bounded cleanup.
    @param monkeypatch: Substitute timer and Docker boundaries without waiting.
    @param remaining: Empty listing or a synthetic leftover container identifier.
    @return: None; the active-work timer is restored in both outcomes.
    """
    import runpy
    namespace = runpy.run_path(str(cd_smoke.ROOT / 'scripts/dev/verify-cd-docker.py'))
    alarm = Mock(return_value=7)
    monkeypatch.setattr(namespace['signal'], 'alarm', alarm)
    monkeypatch.setitem(namespace['cleanup'].__globals__, 'docker', Mock(return_value=remaining))
    backend = Mock(project='jb-cd-lab-synthetic', environment={})
    if remaining:
        with pytest.raises(AssertionError, match='CLEANUP_INCOMPLETE'):
            namespace['cleanup'](backend)
    else:
        namespace['cleanup'](backend)
    backend.compose_run.assert_called_once_with(['down', '--timeout', '2'], timeout=30)
    assert alarm.call_args_list == [call(0), call(7)]
