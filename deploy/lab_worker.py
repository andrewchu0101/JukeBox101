"""Run a detached controller only for a marked disposable process rehearsal.

This intentionally permissive lab authorization cannot select a production backend.
The production SSH helper remains an unconditional rejection.
"""

import sys
from pathlib import Path

from deploy.controller import Controller, parse_request
from deploy.lab_backend import ProcessLabBackend


def main() -> int:
    """@description: Let a lab controller outlive its submitting client process.
    @param: none
    @return: Zero only for a confirmed successful candidate; rollback remains failure.
    """
    directory = Path(sys.argv[1])
    backend = ProcessLabBackend(directory)
    status = Controller(directory, backend, authorize=lambda _: True).execute(parse_request(sys.argv[2]))
    print(status)
    return 0 if status == 'succeeded' else 1


if __name__ == '__main__':
    sys.exit(main())
