"""Boundary for the approved local live run: start the production entry point with hidden secrets, an in-process formal switch and no dotenv
loading, and admit the synthetic candidate through the production pending-intake path.

Nothing here starts a process, connects to a service or writes a secret; the local launcher supplies the hidden reader and the fixed identifiers.
"""

from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

SECRET_NAMES = ("DISCORD_BOT_TOKEN", "DB_PASSWORD")  # [hidden inputs]: Read by name only; values never pass through arguments or files.
REQUIRED_FIXED_NAMES = ("DISCORD_OWNER_USER_ID", "DISCORD_DEV_GUILD_ID", "DISCORD_EXPECTED_APPLICATION_ID", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER")
FORMAL_SWITCH_NAME = "JUKEBOX_FORMAL_EXCHANGE"  # [formal /exchange]: Set only in this process, never written to a file.


class LiveRunRefused(RuntimeError):
    """Refuse a live-run start or intake before any connection, carrying a reason code and never a secret."""


def start_live_entry(fixed: Mapping[str, str], read_secret: Callable[[str], str], environ: MutableMapping[str, str],
                     production_main: Callable[[], None]) -> None:
    """@description: Apply the approved identifiers and the in-process formal switch, read both secrets hidden, then hand control to the production entry point.
    @param fixed: Non-secret identifiers and the database target approved for this run.
    @param read_secret: Hidden reader that takes a secret's name and returns its value; the value is kept only in the process environment.
    @param environ: Process environment mapping, updated for this process only.
    @param production_main: Production entry point, called without arguments.
    @return: None; refuses before reading any secret when an approved identifier is missing.
    """
    missing = [name for name in REQUIRED_FIXED_NAMES if not fixed.get(name)]
    if missing:
        raise LiveRunRefused("FIXED_SETTING_MISSING:" + ",".join(missing))  # [names only]: Values are never printed.
    environ.update(fixed)
    environ[FORMAL_SWITCH_NAME] = "1"
    for name in SECRET_NAMES:
        secret = read_secret(name)
        if not secret:
            raise LiveRunRefused("SECRET_EMPTY:" + name)
        environ[name] = secret
    production_main()


def admit_synthetic_candidate(repository: Any, owner_id: int, submitter_id: int, source_url: str, operation_id: str) -> None:
    """@description: Admit the approved synthetic candidate through the production pending-intake boundary, which creates the submitter row itself.
    @param repository: Production submission repository whose pending intake applies every existing guard.
    @param owner_id: Configured Owner identifier, which the synthetic submitter must never be.
    @param submitter_id: Approved synthetic submitter identifier, a positive integer.
    @param source_url: Public YouTube URL of the candidate, normalized by the existing parser.
    @param operation_id: Stable operation identifier for this intake.
    @return: None; the repository raises for any guard it refuses.
    """
    if type(submitter_id) is not int or submitter_id <= 0:
        raise LiveRunRefused("SYNTHETIC_SUBMITTER_INVALID")
    if submitter_id == owner_id:
        raise LiveRunRefused("SYNTHETIC_SUBMITTER_IS_OWNER")
    repository.accept_pending(submitter_id, source_url, operation_id)
