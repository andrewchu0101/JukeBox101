"""Exercise persistent ingress, locks and cancellation using private local files and pipes.

No SSH server, systemd unit, production credential or Docker daemon is used.
"""

import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deploy.controller import Controller, parse_request
from deploy.host_ingress import handle, read_line
from deploy.host_policy import clean_docker_environment, require_owned_path, require_secret_metadata
from deploy.host_worker import process_entry, require_enabled, run_queue
from deploy.journal import exclusive, persist
from deploy.lab_backend import ProcessLabBackend, initialize
from deploy.queue import RequestQueue, read_request
from deploy.receipt import ReceiptRefused

OLD = 'sha256:' + '1' * 64
NEW = 'sha256:' + '2' * 64
LINE = f'deploy-v1 {"a" * 40} {NEW} 42 2'
REQUEST = parse_request(LINE)
GATES = dict(enabled=True, crypto_acceptance_verified=True, docker_acceptance_verified=True,
             baseline_supports_gateway_health=True)


@pytest.mark.parametrize('content,expected', [(b'status-v1 42 2\n', 'status-v1 42 2\n'), (b'', '')])
def test_pipe_input_requires_eof(content, expected):
    """@description: Read bounded input until a real client EOF.
    @param content: Synthetic request bytes.
    @param expected: Decoded complete input.
    @return: None.
    """
    reader, writer = os.pipe()
    with os.fdopen(reader, 'rb', buffering=0) as stream:
        os.write(writer, content)
        os.close(writer)
        assert read_line(stream, 0.2) == expected


@pytest.mark.parametrize('content', [b'', b'd', b'status-v1 42 2\n'])
def test_partial_or_non_eof_input_times_out(content):
    """@description: A partial sender cannot hold a root helper blocked indefinitely.
    @param content: Input left open, including apparently complete line without EOF.
    @return: None.
    """
    reader, writer = os.pipe()
    try:
        with os.fdopen(reader, 'rb', buffering=0) as stream:
            os.write(writer, content)
            started = time.monotonic()
            with pytest.raises(ValueError, match='INPUT_TIMEOUT'):
                read_line(stream, 0.03)
            assert time.monotonic() - started < 1
    finally:
        os.close(writer)


@pytest.mark.parametrize('content', [b'x' * 257, b'\xff'])
def test_invalid_bytes_refused(content):
    """@description: Reject oversized and non-ASCII protocol messages before queue access.
    @param content: Invalid request bytes.
    @return: None.
    """
    reader, writer = os.pipe()
    with os.fdopen(reader, 'rb', buffering=0) as stream:
        os.write(writer, content)
        os.close(writer)
        with pytest.raises((ValueError, UnicodeError)):
            read_line(stream, 0.2)


def test_queue_idempotency_and_disabled_ingress(tmp_path):
    """@description: Persist request identity without granting deployment authority.
    @param tmp_path: Isolated queue directory.
    @return: None.
    """
    queue = RequestQueue(tmp_path)
    with pytest.raises(ReceiptRefused):
        handle(LINE, queue, {})
    assert not list(tmp_path.glob('*.json'))
    assert handle(LINE, queue, GATES) == 'pending'
    assert handle(LINE, queue, GATES) == 'pending'
    assert handle('status-v1 42 2', queue, {}) == 'pending'
    assert queue.submit(dict(REQUEST, digest=OLD)) == 'rejected'
    assert queue.status(43, 1) == 'rejected'
    with exclusive(tmp_path / 'ingress'), pytest.raises(BlockingIOError):
        queue.submit(dict(REQUEST, run=43))


@pytest.mark.parametrize('fault', ['symlink', 'hardlink', 'wrong_id', 'oversized', 'status'])
def test_corrupt_queue_refused(tmp_path, fault):
    """@description: Refuse aliases, corrupt identities and oversized persisted input.
    @param tmp_path: Isolated queue directory.
    @param fault: Queue integrity failure.
    @return: None.
    """
    queue = RequestQueue(tmp_path)
    queue.submit(REQUEST)
    path = tmp_path / '42-2.json'
    if fault in {'symlink', 'hardlink'}:
        original = tmp_path / 'original'
        path.rename(original)
        path.symlink_to(original) if fault == 'symlink' else os.link(original, path)
    elif fault == 'wrong_id':
        path.rename(tmp_path / '43-2.json')
        path = tmp_path / '43-2.json'
    elif fault == 'oversized':
        path.write_text('x' * 4097)
    else:
        persist(path, {'request': REQUEST, 'status': 'unknown'})
    with pytest.raises(ValueError):
        read_request(path)


def test_queue_worker_real_process_transaction(tmp_path):
    """@description: Complete one durable queue request using an actual isolated fake Gateway process.
    @param tmp_path: Private lab and queue scope.
    @return: None; all children are stopped even on failure.
    """
    state, queued = tmp_path / 'state', tmp_path / 'requests'
    state.mkdir()
    queued.mkdir()
    initialize(state, OLD, {})
    backend = ProcessLabBackend(state)
    backend.start(OLD)
    try:
        queue = RequestQueue(queued)
        assert queue.submit(REQUEST) == 'pending'
        controller = Controller(state, backend, authorize=lambda _: True)
        assert run_queue(queued, controller) == ['succeeded']
        assert queue.status(42, 2) == 'succeeded'
        assert run_queue(queued, controller) == []
        assert backend.healthy(NEW)
    finally:
        backend.stop()


def test_abrupt_worker_loss_preserves_running_receipt(tmp_path):
    """@description: Leave recoverable queue state when a controller disappears mid-transaction.
    @param tmp_path: Isolated persistent queue scope.
    @return: None.
    """
    queue = RequestQueue(tmp_path)
    queue.submit(REQUEST)
    controller = Mock()
    controller.execute.side_effect = SystemExit(143)
    with pytest.raises(SystemExit):
        process_entry(tmp_path / '42-2.json', controller)
    assert queue.status(42, 2) == 'running'
    controller.execute.side_effect = None
    controller.execute.return_value = 'rolled_back'
    assert run_queue(tmp_path, controller) == ['rolled_back']
    assert queue.status(42, 2) == 'rolled_back'


@pytest.mark.parametrize('gate', list(GATES))
def test_every_host_acceptance_gate_required(gate):
    """@description: Default-deny each independent unattended acceptance gate.
    @param gate: Gate whose explicit acceptance is removed.
    @return: None.
    """
    policy = dict(GATES)
    policy.pop(gate)
    with pytest.raises(ReceiptRefused):
        require_enabled(policy)


def test_untrusted_paths_and_environment(tmp_path):
    """@description: Reject writable and linked control paths while excluding inherited endpoint selectors.
    @param tmp_path: Private root-owned test boundary.
    @return: None.
    """
    path = tmp_path / 'policy'
    path.write_text('{}')
    require_owned_path(path, boundary=tmp_path, owner=os.getuid())
    path.chmod(0o666)
    with pytest.raises(PermissionError):
        require_owned_path(path, boundary=tmp_path, owner=os.getuid())
    linked = tmp_path / 'link'
    linked.symlink_to(path)
    with pytest.raises(PermissionError):
        require_owned_path(linked, boundary=tmp_path, owner=os.getuid())
    environment = clean_docker_environment('synthetic')
    assert 'DOCKER_HOST' not in environment and 'COMPOSE_FILE' not in environment
    assert 'PYTHONPATH' not in environment and environment['JB_IMAGE'] == 'synthetic'


@pytest.mark.parametrize('fault', [None, 'uid', 'gid', 'mode', 'hardlink', 'symlink', 'empty'])
def test_secret_metadata_never_reads_content(monkeypatch, fault):
    """@description: Enforce application-owned read-only mounts using metadata alone.
    @param monkeypatch: Fake stat boundary; no real secret files are created or read.
    @param fault: Invalid mount metadata, or None for a valid synthetic file.
    @return: None.
    """
    fields = dict(st_mode=stat.S_IFREG | 0o400, st_uid=10001, st_gid=10001, st_nlink=1, st_size=10)
    changes = {'uid': ('st_uid', 0), 'gid': ('st_gid', 0), 'mode': ('st_mode', stat.S_IFREG | 0o600),
               'hardlink': ('st_nlink', 2), 'symlink': ('st_mode', stat.S_IFLNK | 0o400), 'empty': ('st_size', 0)}
    if fault:
        key, value = changes[fault]
        fields[key] = value
    metadata = SimpleNamespace(**fields)
    monkeypatch.setattr('deploy.host_policy.require_owned_path', Mock())
    monkeypatch.setattr(Path, 'lstat', lambda _: metadata)
    monkeypatch.setattr(Path, 'stat', lambda _: metadata)
    monkeypatch.setattr(Path, 'read_text', Mock(side_effect=AssertionError('secret content read')))
    monkeypatch.setattr(Path, 'read_bytes', Mock(side_effect=AssertionError('secret content read')))
    paths = {name: '/fake/' + name for name in ('discord_bot_token', 'db_password', 'db_ca.crt')}
    if fault:
        with pytest.raises(PermissionError):
            require_secret_metadata(paths)
    else:
        require_secret_metadata(paths)
