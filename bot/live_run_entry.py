"""Boundary for the approved local live run: start the production entry point with hidden secrets, an in-process formal switch and no dotenv
loading, and admit the synthetic candidate through the production pending-intake path.

Nothing here starts a process, connects to a service or writes a secret; the local launcher supplies the hidden reader and the fixed identifiers.
"""

from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

TOKEN_NAME = "DISCORD_BOT_TOKEN"  # [hidden input]: Typed by the user; the value never passes through arguments or files.
DB_PASSWORD_NAME = "DB_PASSWORD"  # [in-memory]: Supplied by the approved DPAPI reader, never typed and never printed.
REQUIRED_FIXED_NAMES = ("DISCORD_OWNER_USER_ID", "DISCORD_DEV_GUILD_ID", "DISCORD_EXPECTED_APPLICATION_ID", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER")
FORMAL_SWITCH_NAME = "JUKEBOX_FORMAL_EXCHANGE"  # [formal /exchange]: Set only in this process, never written to a file.


class LiveRunRefused(RuntimeError):
    """Refuse a live-run start or intake before any connection, carrying a reason code and never a secret."""


def start_live_entry(fixed: Mapping[str, str], read_token: Callable[[str], str], read_db_password: Callable[[], str],
                     environ: MutableMapping[str, str], production_main: Callable[[], None], *, formal_exchange: bool) -> None:
    """@description: Apply the approved identifiers and an explicit formal switch, read the token hidden and the database password from memory, then start the production entry point.
    @param fixed: Non-secret identifiers and the database target approved for this run.
    @param read_token: Hidden reader that takes the token's name and returns its value; the value is kept only in the process environment.
    @param read_db_password: In-memory supplier of the exchange account password from the approved DPAPI reader; never printed or stored.
    @param environ: Process environment mapping, updated for this process only.
    @param production_main: Production entry point, called without arguments.
    @param formal_exchange: True opens the formal /exchange route for this process; False keeps it on the fixed reply.
    @return: None; refuses before the token is requested when an approved identifier is missing.
    """
    if type(formal_exchange) is not bool:
        raise LiveRunRefused("FORMAL_SWITCH_MUST_BE_EXPLICIT_BOOLEAN")
    missing = [name for name in REQUIRED_FIXED_NAMES if not fixed.get(name)]
    if missing:
        raise LiveRunRefused("FIXED_SETTING_MISSING:" + ",".join(missing))  # [names only]: Values are never printed.
    environ.update(fixed)
    environ[FORMAL_SWITCH_NAME] = "1" if formal_exchange else "0"
    token = read_token(TOKEN_NAME)
    if not token:
        raise LiveRunRefused("SECRET_EMPTY:" + TOKEN_NAME)
    environ[TOKEN_NAME] = token
    password = read_db_password()
    if not password:
        raise LiveRunRefused("SECRET_EMPTY:" + DB_PASSWORD_NAME)
    environ[DB_PASSWORD_NAME] = password
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


def parse_output_field(output: str, label: str) -> str:
    """@description: Read one `label: value` line that an operator command printed, so the next write reuses exactly what its preview showed.
    @param output: Captured standard output of one operator command.
    @param label: Field label, such as digest or current revision.
    @return: The value after the label, stripped.
    """
    for line in output.splitlines():
        if line.startswith(label + ": "):
            return line.split(": ", 1)[1].strip()
    raise LiveRunRefused("OUTPUT_FIELD_MISSING:" + label.replace(" ", "_"))


def run_preview_then_write(run: Callable[[list], tuple], preview_argv: list, write_argv_for: Callable[[str, str], list],
                           *, needs_revision: bool = True) -> str:
    """@description: Run one preview, read its digest and, when the step is revision-bound, its current revision, then run the write with exactly those values; any refusal stops the step.
    @param run: Callable that runs one operator command and returns (exit code, captured output).
    @param preview_argv: Arguments of the preview command.
    @param write_argv_for: Builds the write arguments from the preview's current revision (None when not revision-bound) and digest.
    @param needs_revision: False for the settlement window, whose preview prints a digest but no revision.
    @return: Captured output of the write command.
    """
    code, preview_output = run(preview_argv)
    if code != 0:
        raise LiveRunRefused("PREVIEW_REFUSED")
    digest = parse_output_field(preview_output, "digest")
    revision = parse_output_field(preview_output, "current revision") if needs_revision else None
    code, write_output = run(write_argv_for(revision, digest))
    if code != 0:
        raise LiveRunRefused("WRITE_REFUSED")
    return write_output


APPROVED_SONGS = {  # [Owner-provided facts]: Full-video seconds and canonical link of each approved candidate; no other source is assumed.
    "c": {"seconds": 189, "url": "https://youtu.be/cUfDOS2SINM", "version_name": "XG - HYPNOTIZE (official music video, original)"},
    "d": {"seconds": 176, "url": "https://youtu.be/aHTI0SXGVS0", "version_name": "XG - IS THIS LOVE (official music video, original)"},
}
VERSION_KIND = "basic"  # [approved category]: No same-recording claim; the Owner's human check compared no other audio source.


def run_owner_sequence(run: Callable[[list], tuple], operations: Mapping[str, str], decisions: Mapping[str, str], reasons: Mapping[str, str]) -> dict:
    """@description: Run duration, approval and version confirmation for both approved songs in order, then the named settlement window; each write reuses its own preview's digest and the revision the previous step printed.
    @param run: Callable that runs one operator command and returns (exit code, captured output).
    @param operations: Operation identifiers keyed by c and d.
    @param decisions: Decision identifiers keyed by step name, such as duration-c, approve-c, version-c and so on.
    @param reasons: Reason text keyed by step name, drafted only from the Owner's human checks.
    @return: Captured output of the final settlement write.
    """
    for label in ("c", "d"):
        song = APPROVED_SONGS[label]
        operation = operations[label]
        duration = ["--operation", operation, "--seconds", str(song["seconds"]), "--source", song["url"],
                    "--decision-id", decisions["duration-" + label], "--reason", reasons["duration-" + label],
                    "--full-video-confirmed", "--reliable-confirmed"]
        revision = parse_output_field(run_preview_then_write(run, ["duration", "preview", *duration],
                                                             lambda rev, dig, d=duration: ["duration", "write", *d, "--expected-revision", rev, "--digest", dig]),
                                      "revision")
        approve = ["--operation", operation, "--reason", reasons["approve-" + label], "--evidence-reference", song["url"],
                   "--is-song-confirmed"]
        revision = parse_output_field(run_preview_then_write(run, ["approve", "preview", "--expected-revision", revision, *approve],
                                                             lambda rev, dig, a=approve: ["approve", "write", *a, "--expected-revision", rev, "--digest", dig]),
                                      "revision")
        version = ["--operation", operation, "--version-name", song["version_name"], "--version-kind", VERSION_KIND,
                   "--source", song["url"], "--reason", reasons["version-" + label]]
        revision = parse_output_field(run_preview_then_write(run, ["version", "preview", "--expected-revision", revision, *version],
                                                             lambda rev, dig, v=version: ["version", "write", *v, "--expected-revision", rev, "--digest", dig]),
                                      "revision")
    settle = ["--operation", operations["c"], "--operation", operations["d"]]
    return {"settlement_output": run_preview_then_write(run, ["settle", "preview", *settle], lambda rev, dig: ["settle", "write", *settle, "--digest", dig],
                                                        needs_revision=False)}
