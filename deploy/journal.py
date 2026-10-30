"""Persist a bounded deployment journal with an exclusive controller lock.

Paths belong to the administrator or to a random disposable rehearsal directory.
"""

import contextlib
import fcntl
import json
import os
from pathlib import Path

MAX_REQUESTS = 128


@contextlib.contextmanager
def exclusive(directory: Path):
    """@description: Serialize every deployment, recovery and rollback in one scope.
    @param directory: Trusted journal directory, never obtained from an SSH request.
    @return: Context holding the advisory file lock; busy raises BlockingIOError.
    """
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / 'controller.lock').open('a+') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def persist(path: Path, document: dict) -> None:
    """@description: Commit a journal replacement and its directory entry to disk.
    @param path: Trusted journal file path.
    @param document: Secret-free state to preserve across controller loss.
    @return: None; failed durability raises before another side effect.
    """
    pending = path.with_suffix('.new')
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(document, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(pending, path)
    directory_descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def load(path: Path) -> dict:
    """@description: Refuse corrupt or oversized persistent state instead of guessing.
    @param path: Existing journal initialized by a trusted bootstrap operation.
    @return: Bounded state document.
    """
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('journal oversized')
    document = json.loads(path.read_text())
    if document.get('version') != 1 or len(document['requests']) > MAX_REQUESTS:
        raise ValueError('journal contract mismatch')
    return document
