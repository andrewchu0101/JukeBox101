"""Validate the fixed SSH protocol but refuse all host deployment requests.

Receipt verification and the production lifecycle adapter are not implemented;
this review-only helper cannot be enabled with an environment variable or file.
"""

import re
import sys

from deploy.controller import parse_request


def main() -> int:
    """@description: Provide a fail-closed interface without invoking Docker or a shell.
    @param: none
    @return: Refusal exit code even for syntactically valid deployment requests.
    """
    line = sys.stdin.buffer.read(257)
    try:
        request = line.decode('ascii')
        if not re.fullmatch(r'status-v1 [1-9][0-9]{0,19} [1-9][0-9]{0,5}\n?', request):
            parse_request(request)
    except (ValueError, UnicodeError):
        print('rejected')
        return 2
    print('rejected')
    return 2


if __name__ == '__main__':
    sys.exit(main())
