"""Run persistent queued Bot deployments through verified receipts and fixed Docker policy.

Nothing runs on import. The production CLI uses fixed root-owned paths and stays
disabled until independent crypto/Docker acceptance and explicit enablement exist.
"""

import sys
from pathlib import Path

from deploy.controller import Controller
from deploy.docker_backend import HostDockerBackend
from deploy.host_policy import load_policy, require_owned_path
from deploy.journal import exclusive, load, persist
from deploy.queue import read_request
from deploy.receipt import GithubReceiptVerifier, ReceiptRefused

STATE = Path('/var/lib/jukebox101-cd')
QUEUE = STATE / 'requests'


def require_enabled(policy: dict) -> None:
    """@description: Keep every unattended path disabled until explicit tested host acceptance.
    @param policy: Administrator-owned non-secret policy.
    @return: None; an incomplete gate rejects before queue/daemon operations.
    """
    keys = ('enabled', 'crypto_acceptance_verified', 'docker_acceptance_verified', 'baseline_supports_gateway_health')
    if any(policy.get(key) is not True for key in keys):
        raise ReceiptRefused('CD_NOT_ACCEPTED')


def process_entry(path: Path, controller: Controller) -> str:
    """@description: Preserve SSH-independent progress and truthful terminal results.
    @param path: Trusted pending/running queue file.
    @param controller: Locked, recovery-capable deployment controller.
    @return: Result of this request; abrupt termination leaves durable running state.
    """
    entry = read_request(path)
    if entry['status'] not in {'pending', 'running'}:
        return entry['status']
    entry['status'] = 'running'
    persist(path, entry)
    try:
        entry['status'] = controller.execute(entry['request'])
    except (ReceiptRefused, ValueError, PermissionError, FileNotFoundError) as error:
        entry.update(status='rejected', error_class=type(error).__name__)
    persist(path, entry)
    return entry['status']


def run_queue(directory: Path, controller: Controller) -> list[str]:
    """@description: Serialize workers while allowing independent new SSH submissions.
    @param directory: Trusted queue directory, private temporary scope in tests.
    @param controller: Verified lifecycle controller.
    @return: Statuses processed this pass; no request is discarded on failure.
    """
    with exclusive(directory / 'worker'):
        entries = sorted(directory.glob('*.json'), key=lambda path: tuple(int(part) for part in path.stem.split('-')))
        pending = [path for path in entries if read_request(path)['status'] in {'pending', 'running'}]
        return [process_entry(path, controller) for path in pending]


def main() -> int:
    """@description: Process only the fixed host queue after all explicit acceptance gates pass.
    @param: none
    @return: Nonzero for failure or disabled policy; never reports a rollback as success.
    """
    policy = load_policy()
    require_enabled(policy)
    require_owned_path(STATE)
    require_owned_path(QUEUE)
    journal = load(STATE / 'journal.json')
    backend = HostDockerBackend(policy, journal['last_known_good'])
    verifier = GithubReceiptVerifier(policy)
    controller = Controller(STATE, backend, authorize=verifier.verify)
    results = run_queue(QUEUE, controller)
    return 0 if all(result == 'succeeded' for result in results) else 1


if __name__ == '__main__':
    sys.exit(main())
