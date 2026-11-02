"""Run the real host deploy script against a fake Docker daemon and private state.

Nothing here touches a real Docker daemon, SSH server, registry or production file.
The fake models only what the script observes: containers, image tags, labels and health.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'deploy' / 'host' / 'jukebox101-deploy'
COMMIT = 'a' * 40
DIGEST = 'sha256:' + 'b' * 64
OLD_DIGEST = 'sha256:' + 'c' * 64
IMAGE = 'ghcr.io/andrewchu0101/jukebox101'
CANDIDATE = f'{IMAGE}@{DIGEST}'
ROLLBACK = 'jukebox101-rollback:previous'
GOOD_FACTS = f'{COMMIT} https://github.com/andrewchu0101/JukeBox101 10001:10001'
FAKE_DOCKER = r'''import json, os, pathlib, sys
path = pathlib.Path(os.environ['FAKE_DOCKER_STATE']) / 'state.json'
state = json.loads(path.read_text())
args = sys.argv[1:]
state['calls'].append(args[:2] if args[0] != 'compose' else [a for a in args if a in ('up', 'stop')])


def save():
    path.write_text(json.dumps(state))


def running(flag, value):
    return [c for c in state['running'] if c.get(flag) == value]


if args[0] == 'pull':
    sys.exit(0 if args[1] in state['images'] else 1)
if args[:2] == ['image', 'inspect']:
    ref = args[-1]
    if ref == 'jukebox101-rollback:previous':
        sys.exit(0 if ref in state['tags'] else 1)
    print(state['images'][ref])
elif args[0] == 'ps':
    selector = args[3]
    key, _, value = selector.partition('=')
    for container in state['running']:
        if key == 'label' and container.get('labeled') or key == 'name' and value == f"^/{container.get('name')}$":
            print(container['id'])
elif args[0] == 'stop':
    state['running'] = [c for c in state['running'] if c['id'] not in args]
elif args[0] == 'tag':
    state['tags'][args[2]] = args[1]
elif args[0] == 'inspect':
    container = next(c for c in state['running'] if c['id'] == args[-1])
    if '.Image' in args[2] and 'Health' not in args[2]:
        print(container['image'])
    else:
        if container['image'] in state['flaky']:
            container['restarts'] = container.get('restarts', 0) + 1
        print(container['id'], 'true', 'healthy', container.get('restarts', 0))
elif args[0] == 'compose':
    if 'up' in args:
        image = os.environ['JB_IMAGE']
        if image in state['bad'] or state['tags'].get(image) in state['bad']:
            print('detail-from-compose', file=sys.stderr)
            save()
            sys.exit(1)
        resolved = state['tags'].get(image, image)
        state['running'] = [dict(id=f"c{len(state['calls'])}", image=resolved, labeled=True, name='bot')]
    elif 'stop' in args:
        state['running'] = [c for c in state['running'] if c.get('name') != 'bot']
save()
'''


@pytest.fixture
def host(tmp_path):
    """@description: Build an isolated fake host whose script reads only test-owned files.
    @param tmp_path: Private filesystem scope.
    @return: Helper object with run(), state and file locations.
    """
    return Host(tmp_path)


class Host:
    """Own the fake daemon state, fixed script configuration and invocation helpers."""

    def __init__(self, root: Path):
        """@description: Prepare config files, a fake docker and a running old Bot.
        @param self: Fake host.
        @param root: Private temporary directory.
        @return: None.
        """
        self.root, self.state_dir = root, root / 'state'
        self.state_dir.mkdir()
        (root / 'docker').write_text(f'#!{sys.executable}\n' + FAKE_DOCKER)
        (root / 'docker').chmod(0o755)
        (root / 'compose.yaml').write_text('services: {}\n')
        (root / 'bot.env').write_text('DISCORD_EXPECTED_APPLICATION_ID=1559751168723521576\n')
        for name in ('compose.yaml', 'bot.env'):
            (root / name).chmod(0o644)
        self.flock = shutil.which('flock') or self._stub_flock()
        self.write_state(running=[dict(id='old', image=OLD_DIGEST, labeled=True, name='bot')],
                         images={CANDIDATE: GOOD_FACTS}, tags={}, bad=[], flaky=[], calls=[])

    def _stub_flock(self) -> str:
        """@description: Allow local runs on systems without util-linux; CI Linux uses the real flock.
        @param self: Fake host.
        @return: Path of a no-op lock shim.
        """
        stub = self.root / 'flock'
        stub.write_text('#!/bin/sh\nexit 0\n')
        stub.chmod(0o755)
        return str(stub)

    def write_state(self, **values) -> None:
        """@description: Replace fake daemon state.
        @param self: Fake host.
        @param values: Complete daemon state fields.
        @return: None.
        """
        (self.root / 'state.json').write_text(json.dumps(values))

    def edit_state(self, **values) -> None:
        """@description: Change selected fake daemon fields while preserving others.
        @param self: Fake host.
        @param values: Fields to replace.
        @return: None.
        """
        current = self.state
        current.update(values)
        self.write_state(**current)

    @property
    def state(self) -> dict:
        """@description: Read fake daemon state after a script run.
        @param self: Fake host.
        @return: Parsed state.
        """
        return json.loads((self.root / 'state.json').read_text())

    def run(self, line=None, *, soak=0, run=42, attempt=1, digest=DIGEST):
        """@description: Execute the real script with a single protocol line and private configuration.
        @param self: Fake host.
        @param line: Exact stdin text, or None for a well-formed request.
        @param soak: Observation seconds; 0 skips sleeping.
        @param run: Release run id in the generated request.
        @param attempt: Release attempt in the generated request.
        @param digest: Candidate digest in the generated request.
        @return: Completed process.
        """
        line = f'deploy-v2 {COMMIT} {digest} {run} {attempt}\n' if line is None else line
        environment = dict(os.environ, JB_STATE_DIR=str(self.state_dir), JB_COMPOSE_FILE=str(self.root / 'compose.yaml'),
                           JB_ENV_FILE=str(self.root / 'bot.env'), JB_CONF_FILE=str(self.root / 'deploy.conf'),
                           JB_DOCKER=str(self.root / 'docker'), JB_FLOCK=self.flock, JB_OWNER_UID=str(os.getuid()),
                           JB_SOAK_SECONDS=str(soak), JB_SOAK_STEP='1', FAKE_DOCKER_STATE=str(self.root),
                           JB_SECRET_DIR=str(self.root / 'secrets'))
        return subprocess.run(['bash', str(SCRIPT)], input=line, env=environment, text=True,
                              capture_output=True, timeout=60)


def test_new_release_replaces_old_bot_and_keeps_the_old_image_for_rollback(host):
    """@description: A healthy candidate becomes the only running Bot and the old image stays recoverable.
    @param host: Fake host.
    @return: None.
    """
    result = host.run()
    assert (result.returncode, result.stdout) == (0, 'succeeded\n')
    state = host.state
    assert [c['image'] for c in state['running']] == [CANDIDATE]
    assert state['tags'][ROLLBACK] == OLD_DIGEST
    assert (host.state_dir / 'highwater').read_text() == '42\n'
    assert (host.state_dir / 'current').read_text() == f'{DIGEST}\n'


def test_unhealthy_candidate_restores_the_previous_image_and_still_fails(host):
    """@description: Rollback must report rolled_back, never success.
    @param host: Fake host.
    @return: None.
    """
    host.edit_state(bad=[CANDIDATE])
    result = host.run()
    assert result.stdout == 'rolled_back\n' and result.returncode == 0
    assert [c['image'] for c in host.state['running']] == [OLD_DIGEST]
    assert not (host.state_dir / 'locked').exists()


def test_candidate_that_destabilises_during_observation_is_rolled_back(host):
    """@description: A restart during the soak window counts as unhealthy.
    @param host: Fake host.
    @return: None.
    """
    host.edit_state(flaky=[CANDIDATE])
    assert host.run(soak=2).stdout == 'rolled_back\n'


def test_failed_rollback_locks_further_deployments(host):
    """@description: Never keep deploying onto a host that a person has not checked.
    @param host: Fake host.
    @return: None.
    """
    host.edit_state(bad=[CANDIDATE, OLD_DIGEST])
    assert host.run().stdout == 'rollback_failed\n'
    assert (host.state_dir / 'locked').exists()
    host.edit_state(bad=[], images={CANDIDATE: GOOD_FACTS})
    refused = host.run(run=43)
    assert (refused.returncode, refused.stdout) == (2, 'rejected\n')


def test_first_install_without_a_previous_bot_has_no_rollback(host):
    """@description: With nothing to restore, a failed first deployment is reported as needing a person.
    @param host: Fake host.
    @return: None.
    """
    host.edit_state(running=[], bad=[CANDIDATE])
    assert host.run().stdout == 'rollback_failed\n'


@pytest.mark.parametrize('facts', [
    f'{"d" * 40} https://github.com/andrewchu0101/JukeBox101 10001:10001',
    f'{COMMIT} https://github.com/someone/else 10001:10001',
    f'{COMMIT} https://github.com/andrewchu0101/JukeBox101 root',
])
def test_image_that_does_not_match_the_commit_source_and_user_is_refused_before_stopping(host, facts):
    """@description: Refuse before touching the running Bot when image provenance labels do not match.
    @param host: Fake host.
    @param facts: Wrong revision, source or user.
    @return: None.
    """
    host.edit_state(images={CANDIDATE: facts})
    result = host.run()
    assert (result.returncode, result.stdout) == (2, 'rejected\n')
    assert [c['id'] for c in host.state['running']] == ['old']
    assert ['compose', 'up'] not in host.state['calls'] and 'up' not in sum(host.state['calls'], [])


def test_stale_or_repeated_run_numbers_are_refused(host):
    """@description: An older workflow run must not roll the host back to an older image.
    @param host: Fake host.
    @return: None.
    """
    assert host.run(run=50).stdout == 'succeeded\n'
    older = host.run(run=49)
    assert (older.returncode, older.stdout) == (2, 'rejected\n')


def test_same_run_and_attempt_is_idempotent_and_does_not_redeploy(host):
    """@description: A retry after a dropped SSH session returns the recorded answer without touching Docker.
    @param host: Fake host.
    @return: None.
    """
    assert host.run().stdout == 'succeeded\n'
    calls_before = len(host.state['calls'])
    assert host.run().stdout == 'succeeded\n'
    assert len(host.state['calls']) == calls_before
    other_image = host.run(digest='sha256:' + 'e' * 64)
    assert other_image.stdout == 'rejected\n'


@pytest.mark.parametrize('line', ['', 'status-v1 1 1\n', f'deploy-v2 {COMMIT} sha256:short 1 1\n',
                                  f'deploy-v2 {COMMIT} {DIGEST} 0 1\n', f'deploy-v2 {COMMIT} {DIGEST} 1 1 extra\n',
                                  'x' * 400, f'deploy-v2 {COMMIT.upper()} {DIGEST} 1 1\n'])
def test_malformed_requests_are_refused_without_state_changes(host, line):
    """@description: Only the exact protocol line can reach Docker.
    @param host: Fake host.
    @param line: Malformed stdin.
    @return: None.
    """
    result = host.run(line)
    assert (result.returncode, result.stdout) == (2, 'rejected\n')
    assert host.state['calls'] == [] and not list((host.state_dir / 'results').glob('*'))


def test_two_running_gateways_are_refused(host):
    """@description: Refuse when more than one Bot already uses this Discord application.
    @param host: Fake host.
    @return: None.
    """
    host.edit_state(running=[dict(id='a', image=OLD_DIGEST, labeled=True), dict(id='b', image=OLD_DIGEST, labeled=True)])
    assert host.run().stdout == 'rejected\n'
    assert len(host.state['running']) == 2


def test_unlabeled_legacy_container_is_taken_over_when_the_admin_names_it(host):
    """@description: The first deployment can replace a hand-started Bot that lacks the application label.
    @param host: Fake host.
    @return: None.
    """
    (host.root / 'deploy.conf').write_text('LEGACY_CONTAINER=jukebox101-bot\n')
    host.edit_state(running=[dict(id='legacy', image=OLD_DIGEST, labeled=False, name='jukebox101-bot')])
    assert host.run().stdout == 'succeeded\n'
    assert host.state['tags'][ROLLBACK] == OLD_DIGEST
    assert [c['id'] for c in host.state['running']] != ['legacy']


def test_refusal_says_why_on_stderr_while_stdout_stays_one_word(host):
    """@description: A person reading the Actions log must see the cause without logging in to the host.
    @param host: Fake host.
    @return: None.
    """
    (host.state_dir / 'locked').touch()
    locked = host.run()
    assert (locked.stdout, 'is locked' in locked.stderr) == ('rejected\n', True)
    (host.state_dir / 'locked').unlink()
    assert host.run(run=50).stdout == 'succeeded\n'
    stale = host.run(run=49)
    assert (stale.stdout, 'not newer than the highest deployed run 50' in stale.stderr) == ('rejected\n', True)
    assert 'deploy-v2 <commit>' in host.run('nonsense\n').stderr


def test_each_admission_refusal_names_its_own_cause(host):
    """@description: Provenance, permissions and gateway-count refusals must be distinguishable.
    @param host: Fake host.
    @return: None.
    """
    host.edit_state(images={CANDIDATE: f'{"d" * 40} https://github.com/andrewchu0101/JukeBox101 10001:10001'})
    assert 'image labels do not match' in host.run().stderr
    host.edit_state(images={CANDIDATE: GOOD_FACTS}, running=[dict(id='a', image=OLD_DIGEST, labeled=True),
                                                             dict(id='b', image=OLD_DIGEST, labeled=True)])
    assert 'more than one Gateway' in host.run(attempt=2).stderr
    (host.root / 'bot.env').chmod(0o664)
    assert 'bot.env must be' in host.run(attempt=3).stderr


def test_compose_failure_is_explained_without_publishing_raw_docker_output(host):
    """@description: The public workflow log gets a fixed sentence; raw compose output stays on the host.
    @param host: Fake host.
    @return: None.
    """
    host.edit_state(bad=[CANDIDATE])
    result = host.run()
    assert result.stdout == 'rolled_back\n'
    assert 'docker compose up failed' in result.stderr and 'restoring the previous image' in result.stderr
    assert 'detail-from-compose' not in result.stderr
    assert 'detail-from-compose' in (host.state_dir / 'last-compose-failure.log').read_text()


def test_failed_rollback_says_the_host_is_locked(host):
    """@description: The log must tell the maintainer which file to remove after checking the Bot.
    @param host: Fake host.
    @return: None.
    """
    host.edit_state(bad=[CANDIDATE, OLD_DIGEST])
    result = host.run()
    assert (result.stdout, 'host is locked until' in result.stderr) == ('rollback_failed\n', True)


@pytest.mark.parametrize('name', ['compose.yaml', 'bot.env'])
def test_group_writable_host_configuration_is_refused(host, name):
    """@description: Configuration other accounts can edit cannot be trusted by a root deployment.
    @param host: Fake host.
    @param name: Compose or env file made writable.
    @return: None.
    """
    (host.root / name).chmod(0o664)
    assert host.run().stdout == 'rejected\n'
    assert [c['id'] for c in host.state['running']] == ['old']




COMPOSE_WITH_YOUTUBE_KEY = 'services: {}\nsecrets:\n  youtube_api_key:\n    file: /etc/jukebox101/secrets/youtube_api_key.txt\n'  # [adopted compose]: Mounts the key secret.


def mount_youtube_key(host, *, present: bool) -> None:
    """@description: Make the host compose file mount the YouTube key secret, with or without the key file on disk.
    @param host: Fake host.
    @param present: Whether the key file exists in the secret directory.
    @return: None.
    """
    (host.root / 'compose.yaml').write_text(COMPOSE_WITH_YOUTUBE_KEY)
    (host.root / 'compose.yaml').chmod(0o644)
    if present:
        (host.root / 'secrets').mkdir()
        (host.root / 'secrets' / 'youtube_api_key.txt').write_text('SYNTHETIC-KEY-CONTENT')


def test_a_compose_file_that_mounts_the_youtube_key_needs_the_key_file_before_anything_is_stopped(host):
    """@description: A missing secret file would otherwise be discovered only after the old Bot was stopped.
    @param host: Fake host.
    @return: None.
    """
    mount_youtube_key(host, present=False)
    result = host.run()
    assert (result.stdout, 'youtube_api_key.txt' in result.stderr) == ('rejected\n', True)
    assert [c['id'] for c in host.state['running']] == ['old'] and 'stop' not in sum(host.state['calls'], [])


def test_a_present_youtube_key_file_lets_the_release_proceed_and_is_never_printed(host):
    """@description: Only existence is checked by the host; the content stays private.
    @param host: Fake host.
    @return: None.
    """
    mount_youtube_key(host, present=True)
    result = host.run()
    assert result.stdout == 'succeeded\n' and 'SYNTHETIC-KEY-CONTENT' not in result.stdout + result.stderr


def test_a_compose_file_without_the_youtube_key_keeps_working_unchanged(host):
    """@description: A host that has not adopted the new compose file must not be blocked by the new check.
    @param host: Fake host.
    @return: None.
    """
    assert host.run().stdout == 'succeeded\n'


def test_a_youtube_key_path_that_is_a_directory_is_refused(host):
    """@description: A directory is not a secret file, and compose would mount it incorrectly.
    @param host: Fake host.
    @return: None.
    """
    mount_youtube_key(host, present=False)
    (host.root / 'secrets' / 'youtube_api_key.txt').mkdir(parents=True)
    assert host.run().stdout == 'rejected\n'
