"""Refuse Batch B acceptance when the interpreter strips assert statements.

Batch B verdicts rely on assert statements in scenarios and cases, so each executable entry point calls this guard before any database connection or side effect. The guard raises explicit exceptions instead of using assert, so it still works under python -O.
"""

import functools
import json
import sys
from collections.abc import Callable

ASSERTIONS_DISABLED_EXIT_CODE = 3  # [refusal status]: Approved non-success code; never read as PASS by the launcher or operators.
ASSERTIONS_DISABLED_SAFETY_CODE = 'ASSERTIONS_DISABLED'  # [fixed refusal code]: Printed to operators instead of raw interpreter details.
ASSERTIONS_ENABLED_OPTIMIZE_LEVEL = 0  # [assert-preserving level]: Only the default optimize level keeps every assert executable.


class AssertionsDisabledError(RuntimeError):
    """Signal that assert-based Batch B verdicts would be silently removed by the running interpreter."""


def require_assertions_enabled(optimize_level: int | None = None, debug_flag: bool | None = None) -> None:
    """@description: Stop Batch B before any side effect so a stripped interpreter can never report PASS.
    @param optimize_level: Interpreter optimize level to check; None reads the running interpreter.
    @param debug_flag: Interpreter __debug__ state to check; None reads the running interpreter.
    @return: None; the function returns normally only when assertions are enabled.
    """
    # [effective optimize level]: Injected value for tests, otherwise the running interpreter's level.
    effective_optimize_level = sys.flags.optimize if optimize_level is None else optimize_level
    # [effective debug state]: Injected value for tests, otherwise the running interpreter's __debug__.
    effective_debug_flag = __debug__ if debug_flag is None else debug_flag
    if effective_optimize_level != ASSERTIONS_ENABLED_OPTIMIZE_LEVEL or effective_debug_flag is False:
        raise AssertionsDisabledError('Batch B requires assert statements; run without -O')


def run_guarded(entry_point: Callable[[], int | None]) -> int | None:
    """@description: Turn an assertion-stripped run into the fixed refusal status before any command body runs.
    @param entry_point: Zero-argument command body that is skipped when assertions are disabled.
    @return: Refusal status when refused; otherwise whatever the command body returns.
    """
    try:
        require_assertions_enabled()
    except AssertionsDisabledError:
        print(json.dumps({'stage': 'REFUSED_ASSERTIONS_DISABLED', 'safety_code': ASSERTIONS_DISABLED_SAFETY_CODE}, ensure_ascii=True))
        return ASSERTIONS_DISABLED_EXIT_CODE
    return entry_point()


def guarded_command(command: Callable) -> Callable:
    """@description: Refuse a Batch B command under -O without adding lines to the command body.
    @param command: Command function whose body must never start with assertions disabled.
    @return: Wrapper that runs the guard first and then calls the unchanged command.
    """
    @functools.wraps(command)
    def wrapper(*arguments, **keywords):
        """@description: Run the assertion guard before delegating to the protected command.
        @param arguments: Positional arguments passed to the command unchanged.
        @param keywords: Keyword arguments passed to the command unchanged.
        @return: Whatever the command returns.
        """
        require_assertions_enabled()
        return command(*arguments, **keywords)
    return wrapper
