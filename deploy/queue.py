"""Store bounded root-owned deployment requests independently of SSH client lifetime.

Submission never runs Docker or validates a caller-provided receipt as authority;
the persistent worker verifies provenance again under the deployment lock.
"""

import json
import re
import stat
from pathlib import Path

from deploy.controller import TERMINAL, parse_request
from deploy.journal import MAX_REQUESTS, exclusive, persist

QUEUE_NAME = re.compile(r'[1-9][0-9]{0,19}-[1-9][0-9]{0,5}\.json')


def request_key(request: dict) -> str:
    """@description: Derive a filesystem-safe idempotency key only after strict parsing.
    @param request: Candidate request fields.
    @return: Numeric run-attempt key.
    """
    validated = parse_request(f"deploy-v1 {request['commit']} {request['digest']} {request['run']} {request['attempt']}")
    if validated != request:
        raise ValueError('REQUEST_TYPES_MISMATCH')
    return f"{request['run']}-{request['attempt']}"


class RequestQueue:
    """Serialize bounded submissions without holding the long-running deployment lock."""

    def __init__(self, directory: Path):
        """@description: Bind a trusted fixed queue directory; no caller chooses this path.
        @param self: Root-owned queue.
        @param directory: Administrator-owned state directory, private temporary scope in tests.
        @return: None.
        """
        self.directory = directory

    def submit(self, request: dict) -> str:
        """@description: Persist one request atomically and reject same-key content substitution.
        @param self: Queue.
        @param request: Parsed bounded deployment request.
        @return: pending, existing status, or rejected.
        """
        key = request_key(request)
        with exclusive(self.directory / 'ingress'):
            path = self.directory / (key + '.json')
            if path.exists():
                existing = read_request(path)
                return existing['status'] if existing['request'] == request else 'rejected'
            if len(list(self.directory.glob('*.json'))) >= MAX_REQUESTS:
                return 'rejected'
            persist(path, {'request': request, 'status': 'pending'})
        return 'pending'

    def status(self, run: int, attempt: int) -> str:
        """@description: Return only one bounded status word, never journal internals or secrets.
        @param self: Queue.
        @param run: Parsed positive workflow run id.
        @param attempt: Parsed positive workflow attempt id.
        @return: Public request status or rejected if no receipt exists.
        """
        if type(run) is not int or type(attempt) is not int or run < 1 or attempt < 1:
            return 'rejected'
        path = self.directory / f'{run}-{attempt}.json'
        return read_request(path)['status'] if path.exists() else 'rejected'


def read_request(path: Path) -> dict:
    """@description: Reject malformed or oversized persisted requests before reconciliation.
    @param path: Root-owned queue entry, never a request-supplied file path.
    @return: Validated bounded queue document.
    """
    metadata = path.lstat()
    if not QUEUE_NAME.fullmatch(path.name) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError('QUEUE_ENTRY_METADATA')
    if metadata.st_size > 4096:
        raise ValueError('QUEUE_ENTRY_TOO_LARGE')
    entry = json.loads(path.read_text())
    if entry['status'] not in TERMINAL | {'pending', 'running'}:
        raise ValueError('QUEUE_STATUS_INVALID')
    if path.stem != request_key(entry['request']):
        raise ValueError('QUEUE_ID_MISMATCH')
    return entry
