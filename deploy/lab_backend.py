"""Use real local fake-Gateway subprocesses for isolated deployment rehearsals.

No Docker socket, SSH endpoint, production credentials or database is used. A
production Compose adapter is intentionally absent until independent trust exists.
"""

import fcntl
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

from deploy.journal import persist


def initialize(directory: Path, previous: str, modes: dict) -> None:
    """@description: Bootstrap only a newly allocated disposable lab directory.
    @param directory: New random temporary directory owned by this test.
    @param previous: Fake last-known-good digest.
    @param modes: Explicit fake health outcomes, never real Discord credentials.
    @return: None; refuses an existing journal.
    """
    if (directory / 'journal.json').exists():
        raise ValueError('lab already initialized')
    (directory / 'LAB_ONLY').write_text(secrets.token_hex(16))
    (directory / 'modes.json').write_text(json.dumps(modes))
    persist(directory / 'journal.json', {'version': 1, 'last_known_good': previous, 'highwater': 0, 'requests': {}})


class ProcessLabBackend:
    """Bound every operation to one marked directory and one local process lease."""

    def __init__(self, directory: Path):
        """@description: Refuse a production path or an unmarked directory.
        @param self: Fake process backend.
        @param directory: Disposable fixture directory containing LAB_ONLY.
        @return: None.
        """
        if not (directory / 'LAB_ONLY').is_file():
            raise ValueError('not a rehearsal directory')
        self.directory = directory
        self.children = []

    def preflight(self, candidate: str, previous: str) -> None:
        """@description: Verify fake release inputs before any stop side effect.
        @param self: Fake process backend.
        @param candidate: Candidate digest.
        @param previous: Saved compatible digest; no schema changes are performed.
        @return: None; test sentinel requests preflight refusal.
        """
        if (self.directory / 'reject-preflight').exists():
            raise RuntimeError('preflight refused')
        if not candidate.startswith('sha256:') or not previous.startswith('sha256:'):
            raise ValueError('invalid lab digest')

    def pull(self, candidate: str) -> None:
        """@description: Record a simulated pull; this backend never accesses a registry.
        @param self: Fake process backend.
        @param candidate: Fake candidate digest.
        @return: None.
        """
        (self.directory / 'pulled').write_text(candidate)

    def is_stopped(self) -> bool:
        """@description: Prove the process lease is free even after a controller restart.
        @param self: Fake process backend.
        @return: True only when no fake Gateway holds the exclusive lease.
        """
        with (self.directory / 'gateway.lock').open('a+') as lease:
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True

    def stop(self) -> None:
        """@description: Stop only this lab generation and wait for confirmed lease release.
        @param self: Fake process backend.
        @return: None; ambiguous termination prevents a new start.
        """
        active = self.directory / 'active.json'
        if not active.exists():
            if not self.is_stopped():
                raise RuntimeError('unknown process')
            return
        generation = json.loads(active.read_text())['generation']
        (self.directory / f'stop-{generation}').touch()
        deadline = time.monotonic() + 5
        while not self.is_stopped():
            if time.monotonic() > deadline:
                raise RuntimeError('stop timeout')
            time.sleep(0.02)
        for child in self.children:
            child.wait(timeout=5)
        self.children.clear()

    def start(self, release: str) -> None:
        """@description: Start one fake Gateway only after confirmed old-process exit.
        @param self: Fake process backend.
        @param release: Fake release digest.
        @return: None; requires the new generation to acquire its lease.
        """
        if not self.is_stopped():
            raise RuntimeError('overlap refused')
        generation = secrets.token_hex(12)
        persist(self.directory / 'active.json', {'generation': generation, 'release': release})
        environment = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'LD_LIBRARY_PATH') if key in os.environ}
        child = subprocess.Popen([sys.executable, '-m', 'deploy.lab_gateway', str(self.directory), generation, release],
                                 env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
        self.children.append(child)
        deadline = time.monotonic() + 5
        while not self._sample_matches(generation):
            if child.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('fake gateway failed to start')
            time.sleep(0.02)

    def _sample_matches(self, generation: str) -> bool:
        """@description: Refuse a READY file retained from an earlier fake process.
        @param self: Fake process backend.
        @param generation: Newly expected fake instance.
        @return: Whether the process has produced its own health sample.
        """
        path = self.directory / 'health.json'
        return path.exists() and json.loads(path.read_text())['generation'] == generation

    def healthy(self, release: str) -> bool:
        """@description: Observe continuously instead of trusting one simulated READY sample.
        @param self: Fake process backend.
        @param release: Expected fake digest.
        @return: True only while DB-ready and Gateway-connected samples stay fresh.
        """
        generation = json.loads((self.directory / 'active.json').read_text())['generation']
        deadline = time.monotonic() + 0.4  # Short lab window, not a production timing claim.
        while time.monotonic() < deadline:
            sample = json.loads((self.directory / 'health.json').read_text())
            matched = sample['generation'] == generation and sample['release'] == release
            if self.is_stopped() or not matched or not sample['connected'] or not sample['db_ready']:
                return False
            if not 0 <= time.monotonic() - sample['sample'] < 0.3:
                return False
            time.sleep(0.02)
        return True
