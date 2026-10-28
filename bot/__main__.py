"""Start the explicitly configured Discord application without remote command synchronization.

Only executing this module loads credentials and logs in; imports remain offline and inert.
"""

import asyncio
import logging
import os
import signal
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import partial
from typing import Callable

import discord

from bot.config import (SECRET_FILE_SUFFIX, ConfigurationError, load_database_settings, load_formal_exchange_enabled,
                        load_nickel_claim_enabled)
from bot.database.repository import MusicRepository
from bot.database.connection import open_connection
from bot.database.submission_repository import SubmissionRepository
from bot.acceptance_config import environment_snowflake, load_development_guild_settings
from bot.gateway_listener import GuildInteractionGuard
from bot.discord_app import JukeBoxClient, OwnerReviewWiring
from bot.owner_cli import TOKEN_TTL
from bot.owner_review import OwnerReviewService
from bot.secret_file_policy import ContainerMountedFile, ContainerProcess, MountedDirectory, evaluate_sanctioned_mount
from bot.services import MusicService


MOUNTED_SECRET_SETTINGS = ("DISCORD_BOT_TOKEN", "DB_PASSWORD")  # [file-mode secrets]: Settings whose _FILE pointer is judged before login.
EXPECTED_UID_ENV = "SECRET_EXPECTED_UID"  # [expected owner]: Numeric uid the mounted secret must carry; never defaulted.
EXPECTED_GID_ENV = "SECRET_EXPECTED_GID"  # [expected group]: Numeric gid the mounted secret must carry; never defaulted.


def _utc_now() -> datetime:
    """@description: Supply the Owner review clock so previews expire by real elapsed time.
    @param: none
    @return: Current timezone-aware UTC instant.
    """
    return datetime.now(timezone.utc)


def build_owner_review_wiring(repository: SubmissionRepository, owner_id: int, guild_id: int) -> OwnerReviewWiring:
    """@description: Build the Owner review collaborators over the same repository type the Owner CLI uses, keeping one preview store for the process.
    @param repository: Submission repository shared with the rest of the entry, so no second write path exists.
    @param owner_id: Configured Owner Discord user id, read once by the entry.
    @param guild_id: Private guild where the owner group is registered.
    @return: Wiring whose provider always returns the single service built here.
    """
    review_service = OwnerReviewService(repository, owner_id, _utc_now, token_ttl=TOKEN_TTL)  # [single preview store]: Tokens live only in this instance.
    return OwnerReviewWiring(lambda: review_service, owner_id, guild_id, _utc_now)


def _expected_identity(environment_name: str) -> int:
    """@description: Read a declared numeric identity, refusing absent, signed or non-ASCII text so no owner is ever guessed.
    @param environment_name: SECRET_EXPECTED_UID or SECRET_EXPECTED_GID.
    @return: Non-negative integer identity.
    """
    raw_identity = os.environ.get(environment_name, "")
    if not raw_identity.isascii() or not raw_identity.isdecimal():
        raise ConfigurationError(f"{environment_name} must be a non-negative integer when secret files are used")
    return int(raw_identity)


def _opens_read_only(secret_file_path: str) -> bool:
    """@description: Probe whether this non-root process can open the secret read-only, without reading or printing its content.
    @param secret_file_path: Path named by the _FILE setting; never printed.
    @return: True when the read-only open succeeds.
    """
    try:
        with open(secret_file_path, "rb"):
            return True
    except OSError:
        return False


def _mounted_secret_reasons(secret_file_path: str, expected_uid: int, expected_gid: int) -> list[str]:
    """@description: Judge one mounted secret under the approved 0400 scheme, using its numeric owner, group, parent directory and a real read probe.
    @param secret_file_path: Path named by the _FILE setting; never printed.
    @param expected_uid: Declared numeric owner of the mounted file.
    @param expected_gid: Declared numeric group of the mounted file.
    @return: Stable reason codes for every refusal; empty when the file passes.
    """
    file_status = os.stat(secret_file_path)
    parent_status = os.stat(os.path.dirname(secret_file_path) or ".")
    mounted_file = ContainerMountedFile(
        owner_uid=file_status.st_uid, group_gid=file_status.st_gid, mode=stat.S_IMODE(file_status.st_mode),
        is_blank=False,  # [blank check]: read_secret_file already refused empty content before this point.
    )
    parent_directory = MountedDirectory(owner_uid=parent_status.st_uid, group_gid=parent_status.st_gid, mode=stat.S_IMODE(parent_status.st_mode))
    process = ContainerProcess(effective_uid=os.geteuid(), group_ids=frozenset({os.getegid(), *os.getgroups()}))
    decision = evaluate_sanctioned_mount(mounted_file, parent_directory, process, opens_read_only=_opens_read_only(secret_file_path),
                                         expected_owner_uid=expected_uid, expected_group_gid=expected_gid)
    return [reason.value for reason in decision.reasons]


def verify_mounted_secrets() -> None:
    """@description: Refuse file-mounted secrets whose numeric identity or permissions are unsafe for this process, before any login or connection.
    @param: none
    @return: None; raises ConfigurationError naming the setting and reason codes only.
    """
    for setting_name in MOUNTED_SECRET_SETTINGS:
        secret_file_path = os.environ.get(setting_name + SECRET_FILE_SUFFIX, "")
        if not secret_file_path.strip():
            continue
        expected_uid = _expected_identity(EXPECTED_UID_ENV)
        expected_gid = _expected_identity(EXPECTED_GID_ENV)
        reasons = _mounted_secret_reasons(secret_file_path, expected_uid, expected_gid)
        if reasons:
            raise ConfigurationError(f"{setting_name} secret file refused: " + ",".join(reasons))


LOGGER = logging.getLogger(__name__)
SHUTDOWN_DEADLINE_SECONDS = 20  # [shutdown budget]: Total cleanup time; Compose stop_grace_period is 30 seconds.
INTERACTION_WAIT_SECONDS = 15  # [interaction wait]: In-flight interactions get this long before any is cancelled.
CANCELLATION_SETTLE_SECONDS = 1  # [cancel settle]: Brief wait so cancelled work records its cancellation before the report.
CLOSE_RESERVED_SECONDS = 4  # [close reserve]: Budget kept free so the Discord close is always attempted with at least this much time.
SHUTDOWN_FAILED_EXIT_CODE = 1  # [cleanup failed]: A cleanup step raised; the failing step and class are logged.
SHUTDOWN_INCOMPLETE_EXIT_CODE = 3  # [shutdown incomplete]: Work was still running at the deadline; database work may be unfinished.
INTERACTION_TASK_PREFIX = "discord.py: on_interaction"  # [in-flight work]: discord.py names each interaction dispatch task with this prefix (pinned discord.py 2.7.1).


class ShutdownGate:
    """Refuse every new interaction once shutdown has begun, in front of the existing admission guard."""

    def __init__(self, guard: Callable) -> None:
        """@description: Wrap the admission guard so shutdown can close it without changing its rules.
        @param self: Gate instance.
        @param guard: Existing admission callable returning an awaitable boolean.
        @return: None.
        """
        self._guard = guard  # [admission rules]: Unchanged; only consulted while the gate is open.
        self._admitting = True  # [admission open]: Becomes False at the first shutdown step.

    def stop_admitting(self) -> None:
        """@description: Close admission permanently for this process.
        @param self: Gate instance.
        @return: None.
        """
        self._admitting = False

    async def __call__(self, interaction) -> bool:
        """@description: Admit an interaction only while the gate is open and the existing guard admits it.
        @param self: Gate instance.
        @param interaction: Incoming Discord interaction.
        @return: True only when the gate is open and the guard admits the interaction.
        """
        if not self._admitting:
            return False
        return await self._guard(interaction)


@dataclass
class ShutdownReport:
    """Collect what the bounded shutdown left unfinished or failed, so the exit status tells the truth."""

    incomplete_count: int = 0  # [unfinished work]: Interaction tasks still running at the deadline.
    failed_steps: list[str] = field(default_factory=list)  # [cleanup failures]: Step name and exception class only.


def _exit_code(report: ShutdownReport) -> int:
    """@description: Map the shutdown report to one exit status, with incomplete work taking precedence over cleanup failures.
    @param report: Outcome of the bounded shutdown.
    @return: 0 for a clean stop, otherwise the status naming the first category that applies.
    """
    if report.incomplete_count:
        return SHUTDOWN_INCOMPLETE_EXIT_CODE
    if report.failed_steps:
        return SHUTDOWN_FAILED_EXIT_CODE
    return 0


def _in_flight_interactions() -> set:
    """@description: Find the interaction tasks that are still running, excluding the shutdown task itself.
    @param: none
    @return: Set of unfinished interaction dispatch tasks.
    """
    current = asyncio.current_task()
    return {task for task in asyncio.all_tasks()
            if task is not current and not task.done() and task.get_name().startswith(INTERACTION_TASK_PREFIX)}


def _exit_now(exit_code: int) -> None:
    """@description: End the process with the decided status without waiting for worker threads that may still hold a database call.
    @param exit_code: Status decided from the shutdown report.
    @return: None; the process ends here.
    """
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)


def _report_and_exit(report: ShutdownReport) -> None:
    """@description: Log each unfinished or failed item by category only, then end the process with the matching status.
    @param report: Outcome of the bounded shutdown.
    @return: None; the process ends here.
    """
    if report.incomplete_count:
        LOGGER.error("Shutdown incomplete: %d interaction task(s) still running after %d seconds; database work in worker threads "
                     "may be unfinished and is governed by its durable recovery records.", report.incomplete_count, INTERACTION_WAIT_SECONDS)
    for failed_step in report.failed_steps:
        LOGGER.error("Shutdown step failed: %s", failed_step)
    _exit_now(_exit_code(report))


def _seconds_until(moment: float) -> float:
    """@description: Report how long until an event-loop instant, never below zero.
    @param moment: Event-loop time of a shutdown step boundary.
    @return: Seconds left, or 0.0 when the boundary has passed.
    """
    return max(0.0, moment - asyncio.get_running_loop().time())


async def _close_within_budget(client, deadline: float, report: ShutdownReport) -> None:
    """@description: Close the Discord client inside the remaining cleanup budget, reporting a timeout or failure by step and class only.
    @param client: Running Discord client.
    @param deadline: Event-loop time at which the whole cleanup budget ends.
    @param report: Receives the close outcome.
    @return: None.
    """
    remaining = _seconds_until(deadline)
    if remaining <= 0:
        report.failed_steps.append("close_discord:budget_spent")  # [budget spent]: No time is left, so close is not attempted and the report says so.
        return
    try:
        await asyncio.wait_for(client.close(), timeout=remaining)
    except asyncio.TimeoutError:
        report.failed_steps.append("close_discord:timeout")  # [close never returned]: Reported explicitly so it is not mistaken for a Docker kill.
    except Exception as error:  # [cleanup boundary]: Log the step and class only, never the message or a traceback.
        report.failed_steps.append(f"close_discord:{type(error).__name__}")


async def _shut_down(client, gate: ShutdownGate, report: ShutdownReport) -> None:
    """@description: Stop new work, give in-flight interactions their wait, cancel any still running, then close Discord inside the reserved budget.
    @param client: Running Discord client.
    @param gate: Admission gate closed before anything waits.
    @param report: Receives the unfinished count and any cleanup failure.
    @return: None; the process ends here.
    """
    started = asyncio.get_running_loop().time()  # [budget start]: Every step boundary below is measured from this one instant.
    interaction_deadline = started + INTERACTION_WAIT_SECONDS  # [interaction share]: Only tasks still running at this instant are cancelled.
    close_deadline = started + SHUTDOWN_DEADLINE_SECONDS  # [total budget]: The Discord close must finish before this instant.
    gate.stop_admitting()  # [admission first]: No new interaction can start while in-flight work drains.
    in_flight = _in_flight_interactions()
    if in_flight:
        _, unfinished = await asyncio.wait(in_flight, timeout=_seconds_until(interaction_deadline))
        report.incomplete_count = len(unfinished)
        for task in unfinished:
            task.cancel()  # [deadline reached]: Thread-backed database calls keep running; the report says so.
        if unfinished:
            settle_until = close_deadline - CLOSE_RESERVED_SECONDS  # [close reserve]: Cancel settling never eats the time reserved for close.
            await asyncio.wait(unfinished, timeout=min(CANCELLATION_SETTLE_SECONDS, _seconds_until(settle_until)))
    await _close_within_budget(client, close_deadline, report)
    _report_and_exit(report)


def _termination_handler(client, gate: ShutdownGate) -> Callable:
    """@description: Build the SIGTERM handler that starts one bounded shutdown on the running event loop.
    @param client: Discord client to close.
    @param gate: Admission gate to close first.
    @return: Signal handler; only the first SIGTERM starts a shutdown.
    """
    started: list[int] = []  # [shutdown started]: A repeated SIGTERM must not start a second shutdown.

    def handle(signal_number: int, frame) -> None:
        """@description: Begin the shutdown task, or exit cleanly when no Gateway loop has started yet.
        @param signal_number: Signal delivered by the orchestrator.
        @param frame: Interrupted frame, unused.
        @return: None.
        """
        if started:
            return
        started.append(signal_number)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _exit_now(0)  # [nothing started]: No connection or work exists before the Gateway loop runs.
            return
        # [loop wake-up]: run_coroutine_threadsafe writes to the loop's wake-up pipe; a plain create_task would not wake a blocked selector.
        asyncio.run_coroutine_threadsafe(_shut_down(client, gate, ShutdownReport()), loop)

    return handle


def main() -> None:
    """@description: Separate explicit login from independently testable client construction.
    @param: none
    @return: None; configuration failures terminate before attempting login.
    """
    logging.basicConfig(level=logging.INFO)
    try:
        guild_settings = load_development_guild_settings()
        owner_id = environment_snowflake("DISCORD_OWNER_USER_ID")
        database_settings = load_database_settings()
        formal_exchange = load_formal_exchange_enabled()  # [formal /exchange]: Off unless the setting is explicitly enabled.
        nickel_claim = load_nickel_claim_enabled()  # [/claim]: Off unless the setting is explicitly enabled.
        verify_mounted_secrets()
    except ConfigurationError as configuration_error:
        raise SystemExit(str(configuration_error)) from None
    submission_repository = SubmissionRepository(partial(open_connection, database_settings), owner_id)
    service = MusicService(MusicRepository(database_settings), submission_repository, formal_exchange=formal_exchange,
                           nickel_claim_enabled=nickel_claim, nickel_connection_factory=partial(open_connection, database_settings))
    owner_review = build_owner_review_wiring(submission_repository, owner_id, guild_settings.guild_id)
    gate = ShutdownGate(GuildInteractionGuard(guild_settings))
    client = JukeBoxClient(service, interaction_guard=gate, owner_review=owner_review)
    signal.signal(signal.SIGTERM, _termination_handler(client, gate))
    try:
        client.run(guild_settings.discord.token, log_handler=None)
    except (discord.LoginFailure, discord.HTTPException, OSError) as startup_error:
        raise SystemExit("Discord startup failed: " + type(startup_error).__name__) from None


if __name__ == "__main__":
    main()
