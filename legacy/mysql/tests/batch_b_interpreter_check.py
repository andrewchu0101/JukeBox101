"""Report whether the interpreter that will run Batch B has assertions enabled.

The PowerShell launcher runs this module before any credential prompt, so an interpreter started with -O, -OO or PYTHONOPTIMIZE is refused with exit 3. It imports only the assertion guard, never a database driver, and prints nothing.
"""

import sys

from tests.batch_b_assertion_guard import AssertionsDisabledError, require_assertions_enabled

ASSERTIONS_ENABLED_STATUS = 0  # [pre-check accepted]: Interpreter keeps every assert statement.
ASSERTIONS_DISABLED_STATUS = 3  # [pre-check refused]: Same approved refusal code as the acceptance entry points.


def main() -> int:
    """@description: Convert the interpreter's own assertion state into a process status the launcher can check.
    @param: none
    @return: 0 when assertions are enabled, otherwise 3.
    """
    try:
        require_assertions_enabled()
    except AssertionsDisabledError:
        return ASSERTIONS_DISABLED_STATUS
    return ASSERTIONS_ENABLED_STATUS


if __name__ == "__main__":
    sys.exit(main())
