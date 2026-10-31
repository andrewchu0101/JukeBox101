"""Operator command for the API data lifecycle: read retention health, and preview or run the purge.

Inputs are the operator's explicit database settings and the configured Owner; outputs are counts printed as text, never values, channels or secrets.
`purge` only previews unless --apply is given; the Bot runs the same purge by itself, so this command is the manual fallback.
"""

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from functools import partial
from typing import Any

from bot.database.connection import open_connection
from bot.database.submission_repository import SubmissionRepository
from bot.owner_cli import OperatorRefused, operator_settings, owner_identity


def _parser() -> argparse.ArgumentParser:
    """@description: Define the read-only status command and the purge command that previews unless told to apply.
    @param: none
    @return: Argument parser.
    """
    parser = argparse.ArgumentParser(prog="api_retention", description="API data retention (operator).")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("status", help="Read retention health from the database clock; changes nothing.")
    purge = commands.add_parser("purge", help="Preview what would be cleared; add --apply to clear it.")
    purge.add_argument("--apply", action="store_true", help="Actually clear expired and completed observations.")
    return parser


def _status(repository: Any) -> list[str]:
    """@description: Describe retention health in counts and days only.
    @param repository: Repository the operator reads through.
    @return: Output lines.
    """
    status = repository.api_retention_status()
    last = "never" if status.last_success_age_days is None else f"{status.last_success_age_days:.1f} days ago"
    oldest = "none" if status.oldest_overdue_days is None else f"{status.oldest_overdue_days:.1f} days"
    return [f"rows holding API values: {status.unpurged_rows}", f"overdue rows: {status.overdue_rows}", f"oldest overdue: {oldest}",
            f"last successful purge: {last}", "writes: none"]


def _purge(repository: Any, apply: bool) -> list[str]:
    """@description: Preview the purge, or run it when explicitly applied.
    @param repository: Repository the operator reads and writes through.
    @param apply: True to clear.
    @return: Output lines.
    """
    expired, completed = repository.api_purge_preview()
    lines = [f"past expiry: {expired}", f"decided cases still holding values: {completed}"]
    if not apply:
        return lines + ["writes: none (preview only; add --apply to clear)"]
    return lines + [f"cleared: {repository.purge_api_observations()}"]


def main(argv: list | None = None, *, environ: Mapping[str, str] | None = None,
         repository_factory: Callable[[Any, int], Any] | None = None) -> int:
    """@description: Run one retention subcommand; with no subcommand nothing is read, connected or written.
    @param argv: Arguments after the program name, or None for the process arguments.
    @param environ: Environment mapping, or None for the process environment.
    @param repository_factory: Builds the repository from validated settings and the Owner; None uses the production connection.
    @return: Process exit code: 0 on success, 1 on any refusal or failure.
    """
    args = _parser().parse_args(argv)
    if args.command is None:
        _parser().print_usage()
        return 0
    source = os.environ if environ is None else environ
    try:
        owner = owner_identity(source)
        settings = operator_settings(source)
        factory = repository_factory or (lambda s, o: SubmissionRepository(partial(open_connection, s), o, api_retention=True))
        repository = factory(settings, owner)
        lines = _status(repository) if args.command == "status" else _purge(repository, args.apply)
    except OperatorRefused as refusal:
        lines, code = [f"refused: {refusal}"], 1
    except Exception as failure:  # [class name only]: Messages can echo record contents, so only the class is printed.
        lines, code = [f"failed: {type(failure).__name__}"], 1
    else:
        code = 0
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
