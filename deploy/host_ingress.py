"""Accept a bounded SSH stdin protocol into a fixed root-owned persistent queue.

No shell, Docker command, path selector or deployment credential is accepted in
the protocol. The shipped policy disables this not-yet-installed entrypoint.
"""

import os
import re
import select
import sys
import time

from deploy.controller import parse_request
from deploy.host_policy import load_policy, require_owned_path
from deploy.host_worker import QUEUE, require_enabled
from deploy.queue import RequestQueue

STATUS = re.compile(r'status-v1 ([1-9][0-9]{0,19}) ([1-9][0-9]{0,5})\n?')


def read_line(stream, timeout: float = 5) -> str:
    """@description: Bound SSH input length and elapsed time, requiring EOF before action.
    @param stream: Binary stdin pipe from the forced command.
    @param timeout: Total input deadline in seconds, shortened only in isolated tests.
    @return: ASCII protocol line; malformed, oversized or stalled input raises.
    """
    deadline = time.monotonic() + timeout
    content = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
            raise ValueError('INPUT_TIMEOUT')
        chunk = os.read(stream.fileno(), 257 - len(content))
        if not chunk:
            return content.decode('ascii')
        content.extend(chunk)
        if len(content) > 256:
            raise ValueError('INPUT_TOO_LARGE')


def handle(line: str, queue: RequestQueue, policy: dict) -> str:
    """@description: Separate read-only status from enabled submission without executing deployments.
    @param line: Bounded ASCII protocol request.
    @param queue: Fixed root-owned queue.
    @param policy: Administrator-owned configuration.
    @return: One safe protocol status word.
    """
    status = STATUS.fullmatch(line)
    if status:
        return queue.status(int(status.group(1)), int(status.group(2)))
    request = parse_request(line)
    require_enabled(policy)
    return queue.submit(request)


def main() -> int:
    """@description: Validate fixed host policy before accepting or querying deployment state.
    @param: none
    @return: Zero only for accepted/existing receipt states, not deployment success.
    """
    try:
        line = read_line(sys.stdin.buffer)
        policy = load_policy()
        require_owned_path(QUEUE)
        result = handle(line, RequestQueue(QUEUE), policy)
    except (OSError, ValueError, KeyError, RuntimeError):
        result = 'rejected'
    print(result)
    return 2 if result == 'rejected' else 0


if __name__ == '__main__':
    sys.exit(main())
