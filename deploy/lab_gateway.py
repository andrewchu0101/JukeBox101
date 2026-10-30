"""Run a fake Gateway process in a disposable rehearsal directory, without networking.

A shared lease excludes overlap; per-generation stop requests also work after the
controller is killed. This simulates Discord health, not Discord protocol acceptance.
"""

import fcntl
import json
import signal
import sys
import time
from pathlib import Path


def main() -> None:
    """@description: Model a connected or failed Gateway while holding its singleton lease.
    @param: none
    @return: None on a generation-specific stop request or SIGTERM.
    """
    directory, generation, release = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if not (directory / 'LAB_ONLY').is_file():
        raise SystemExit('refusing unmarked lab')
    stopped = []
    signal.signal(signal.SIGTERM, lambda *_: stopped.append(True))
    with (directory / 'gateway.lock').open('a+') as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        started = time.monotonic()
        while not stopped and not (directory / f'stop-{generation}').exists():
            modes = json.loads((directory / 'modes.json').read_text())
            mode = modes.get(release, 'healthy')
            connected = mode == 'healthy' or (mode == 'disconnect' and time.monotonic() - started < 0.2)
            snapshot = {'generation': generation, 'release': release, 'db_ready': True,
                        'connected': connected, 'sample': time.monotonic()}
            pending = directory / f'health-{generation}.new'
            pending.write_text(json.dumps(snapshot))
            pending.replace(directory / 'health.json')
            time.sleep(0.02)


if __name__ == '__main__':
    main()
