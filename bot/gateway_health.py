"""Publish process-bound Gateway evidence on local tmpfs without a public endpoint.

Only the Gateway event loop writes evidence; Linux boot/start identity and a held
file lock prevent stale files or a second local process from passing health checks.
"""

import asyncio
import json
import math
import os
import time
import uuid
from pathlib import Path

HEALTH_PATH = Path('/tmp/jukebox101-gateway.json')
WRITE_INTERVAL_SECONDS = 2
MAX_SAMPLE_AGE_SECONDS = 10
MAX_ACK_AGE_SECONDS = 90  # Discord heartbeat ACK, distinct from our event-loop sample.


def process_identity(pid: int) -> dict:
    """@description: Bind evidence to a live Linux process even after PID reuse.
    @param pid: Expected Gateway PID in this PID namespace.
    @return: Boot id and kernel process start ticks; dead/zombie processes raise.
    """
    stat_path = Path('/proc/self/stat') if pid == os.getpid() else Path(f'/proc/{pid}/stat')
    fields = stat_path.read_text().rsplit(')', 1)[1].split()
    if fields[0] in {'Z', 'X'}:
        raise ProcessLookupError('gateway process exited')
    return {'pid': pid, 'start_ticks': fields[19],
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


class GatewayHealth:
    """Own one application's local lease and evidence for this process lifetime."""

    def __init__(self, application_id: int, path: Path = HEALTH_PATH) -> None:
        """@description: Start disconnected and invalidate evidence from any earlier run.
        @param self: Evidence writer owned by the main process.
        @param application_id: Configured Discord application identifier.
        @param path: Private tmpfs evidence location, overridden only by tests.
        @return: None.
        """
        self.path = path
        self.application_id = application_id
        self.identity = process_identity(os.getpid())
        self.instance_id = uuid.uuid4().hex  # [generation]: New on every start, never supplied by a caller.
        self.state = 'starting'
        self.ack_at = None
        self.task = None
        self.lease = None

    def __enter__(self):
        """@description: Refuse a second local Gateway before any Discord login.
        @param self: Evidence owner.
        @return: This owner with the application lease held.
        """
        import fcntl
        self.lease = self.path.with_name(f'jukebox101-{self.application_id}.lock').open('a+')
        try:
            fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.publish()
        except BaseException:
            self.lease.close()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """@description: Invalidate health before releasing the local singleton lease.
        @param self: Evidence owner.
        @param exc_type: Context exception type, if any.
        @param exc_value: Context exception, deliberately not logged.
        @param traceback: Context traceback, deliberately not logged.
        @return: None.
        """
        try:
            self.mark('stopped')
        finally:
            self.lease.close()

    def mark(self, state: str) -> None:
        """@description: Invalidate READY immediately on disconnect or shutdown.
        @param self: Evidence owner.
        @param state: Lifecycle state reported by the Discord client.
        @return: None.
        """
        self.state = state
        if state != 'ready':
            self.ack_at = None
        self.publish()

    def acknowledge(self) -> None:
        """@description: Record receipt of a real Gateway OP 11 heartbeat ACK.
        @param self: Evidence owner.
        @return: None.
        """
        self.ack_at = time.monotonic()
        self.publish()

    def publish(self) -> None:
        """@description: Replace a bounded, secret-free snapshot atomically.
        @param self: Main process evidence owner.
        @return: None; I/O failure fails health rather than preserving apparent success.
        """
        snapshot = {**self.identity, 'instance_id': self.instance_id,
                    'application_id': self.application_id, 'state': self.state,
                    'sample_at': time.monotonic(), 'ack_at': self.ack_at}
        pending = self.path.with_suffix(f'.{self.instance_id}.new')
        descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, 'w') as output:
            json.dump(snapshot, output)
        os.replace(pending, self.path)

    async def monitor(self, client) -> None:
        """@description: Keep evidence fresh only while the owning event loop runs.
        @param self: Main process evidence owner.
        @param client: Gateway whose connection state is sampled.
        @return: None; cancellation ends the writer.
        """
        while not client.is_closed():
            if not client.is_ready() and self.state == 'ready':
                self.mark('disconnected')
            self.publish()
            await asyncio.sleep(WRITE_INTERVAL_SECONDS)


def evidence_problems(snapshot: dict, identity: dict, application_id: int, now: float) -> list[str]:
    """@description: Require current process, READY and two independent fresh clocks.
    @param snapshot: Parsed bounded local evidence.
    @param identity: Kernel identity of the expected main process.
    @param application_id: Application expected by deployment configuration.
    @param now: Monotonic time, injectable for deterministic expiry tests.
    @return: Stable failure codes without secrets.
    """
    problems = []
    if any(snapshot.get(key) != value for key, value in identity.items()):
        problems.append('GATEWAY_PROCESS_MISMATCH')
    if snapshot.get('application_id') != application_id or not snapshot.get('instance_id'):
        problems.append('GATEWAY_APPLICATION_MISMATCH')
    if snapshot.get('state') != 'ready':
        problems.append('GATEWAY_NOT_READY')
    for field, maximum, problem in [('sample_at', MAX_SAMPLE_AGE_SECONDS, 'GATEWAY_SAMPLE_STALE'),
                                     ('ack_at', MAX_ACK_AGE_SECONDS, 'GATEWAY_ACK_STALE')]:
        instant = snapshot.get(field)
        if type(instant) not in (int, float) or not math.isfinite(instant) or not 0 <= now - instant <= maximum:
            problems.append(problem)
    return problems


def check_gateway(application_id: int, *, path: Path = HEALTH_PATH, expected_pid: int = 1) -> dict:
    """@description: Reject missing, malformed, dead-process and stale evidence.
    @param application_id: Expected Discord application identifier.
    @param path: Evidence path; production uses the fixed tmpfs path.
    @param expected_pid: ENTRYPOINT Python is PID 1; test processes supply their PID.
    @return: Safe Gateway-only health summary.
    """
    try:
        if path.stat().st_size > 4096:
            raise ValueError('oversized evidence')
        snapshot = json.loads(path.read_text())
        if not isinstance(snapshot, dict):
            raise ValueError('invalid evidence')
        problems = evidence_problems(snapshot, process_identity(expected_pid), application_id, time.monotonic())
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        problems = ['GATEWAY_EVIDENCE_UNAVAILABLE']
    return {'result': 'not_ready' if problems else 'ready', 'problems': problems}
