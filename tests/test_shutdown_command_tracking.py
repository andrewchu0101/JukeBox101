"""Verify that shutdown waits for, or cancels and reports, slash-command work that discord.py dispatches in its own CommandTree-invoker task.

Real discord.py command dispatch runs offline: a genuine Interaction is handed to the real SafeCommandTree, and the
admission gate used in production sits in front of it. No Discord connection, login, or MySQL connection is made.
The shutdown timing constants are scaled down as module attributes so the tests stay fast.
"""

import asyncio
import time

import pytest
from discord import Interaction, app_commands

from bot import __main__ as entry
from bot.discord_app import JukeBoxClient

PROBE_COMMAND_NAME = "probe"  # [test command]: Slash command registered only inside these tests.
SCALED_INTERACTION_WAIT_SECONDS = 0.3  # [test-only interaction wait]: Short share of the deadline for in-flight commands.
SCALED_SETTLE_SECONDS = 0.1  # [test-only settle]: Short cancellation settle window.
SCALED_CLOSE_RESERVED_SECONDS = 0.5  # [test-only close reserve]: Short budget kept for the fake Discord close.
SCALED_DEADLINE_SECONDS = 2  # [test-only deadline]: Short total cleanup budget.


class FakeDiscordClose:
    """Record Discord close calls and their order relative to command events, without any network use."""

    def __init__(self, events: list) -> None:
        """@description: Keep a shared event log so tests can prove the close happened after command work ended.
        @param self: Fake close recorder.
        @param events: Ordered list shared with command callbacks.
        @return: None.
        """
        self.events = events  # [event order]: Shared log of command and close events.

    async def close(self) -> None:
        """@description: Stand in for the Discord client close and record that it ran.
        @param self: Fake close recorder.
        @return: None.
        """
        self.events.append("close")


@pytest.fixture
def exit_codes(monkeypatch) -> list:
    """@description: Capture the exit status the shutdown would end the process with, instead of ending the test process.
    @param monkeypatch: Pytest fixture for scoped module attribute changes.
    @return: List receiving each exit status the shutdown chose.
    """
    recorded: list[int] = []
    monkeypatch.setattr(entry, "_exit_now", recorded.append)
    return recorded


@pytest.fixture
def scaled_budget(monkeypatch) -> None:
    """@description: Shorten the shared shutdown constants used by _shut_down so the tests run in seconds.
    @param monkeypatch: Pytest fixture for scoped module attribute changes.
    @return: None.
    """
    monkeypatch.setattr(entry, "SHUTDOWN_DEADLINE_SECONDS", SCALED_DEADLINE_SECONDS)
    monkeypatch.setattr(entry, "INTERACTION_WAIT_SECONDS", SCALED_INTERACTION_WAIT_SECONDS)
    monkeypatch.setattr(entry, "CANCELLATION_SETTLE_SECONDS", SCALED_SETTLE_SECONDS)
    monkeypatch.setattr(entry, "CLOSE_RESERVED_SECONDS", SCALED_CLOSE_RESERVED_SECONDS)


def _build_client(command_callback, guard=None):
    """@description: Build the production client shape with one probe command and a real shutdown gate in front of the tree.
    @param command_callback: Coroutine function acting as the slash command body.
    @param guard: Optional existing admission guard; defaults to admitting everything.
    @return: Tuple of (client, gate).
    """
    async def admit_everything(interaction) -> bool:
        return True

    gate = entry.ShutdownGate(guard or admit_everything)
    client = JukeBoxClient(object(), interaction_guard=gate)
    client.loop = asyncio.get_running_loop()  # [dispatch loop]: discord.py schedules the invoker task on this loop.
    client.tree.add_command(app_commands.Command(name=PROBE_COMMAND_NAME, description="test probe", callback=command_callback))
    return client, gate


def _slash_interaction(client: JukeBoxClient) -> Interaction:
    """@description: Build a genuine slash-command Interaction in a direct-message context, with no guild lookup needed.
    @param client: Client whose connection state the interaction uses.
    @return: Interaction ready for SafeCommandTree dispatch.
    """
    payload = {
        "id": "1", "application_id": "2", "type": 2, "token": "test-token", "version": 1, "channel_id": "3",
        "locale": "en-US", "attachment_size_limit": 8388608,
        "data": {"id": "5", "name": PROBE_COMMAND_NAME, "type": 1, "options": []},
        "user": {"id": "6", "username": "tester", "discriminator": "0", "avatar": None},
    }
    return Interaction(data=payload, state=client._connection)


def test_a_slash_command_still_running_at_shutdown_is_waited_for_before_the_close(scaled_budget, exit_codes):
    """@description: A command whose database or DM work is still running when SIGTERM arrives is awaited to completion, and only then is Discord closed with a clean status.
    @param scaled_budget: Shortened shutdown constants.
    @param exit_codes: Captured exit status.
    @return: None.
    """
    async def scenario() -> None:
        events: list[str] = []
        started = asyncio.Event()

        async def slow_command(interaction) -> None:
            started.set()
            await asyncio.sleep(0.3)  # [simulated database work]: Still running when shutdown begins.
            events.append("command_finished")

        client, gate = _build_client(slow_command)
        client.tree._from_interaction(_slash_interaction(client))
        await started.wait()
        close = FakeDiscordClose(events)
        await entry._shut_down(close, gate, entry.ShutdownReport())
        assert events == ["command_finished", "close"]

    asyncio.run(scenario())
    assert exit_codes == [0]


def test_a_slash_command_that_outlives_the_wait_is_cancelled_closed_and_reported_incomplete(scaled_budget, exit_codes):
    """@description: A command still running past the interaction wait is cancelled, Discord still closes, and the status is the incomplete code.
    @param scaled_budget: Shortened shutdown constants.
    @param exit_codes: Captured exit status.
    @return: None.
    """
    async def scenario() -> list:
        events: list[str] = []
        started = asyncio.Event()

        async def stuck_command(interaction) -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            finally:
                events.append("command_cancelled")

        client, gate = _build_client(stuck_command)
        client.tree._from_interaction(_slash_interaction(client))
        await started.wait()
        await entry._shut_down(FakeDiscordClose(events), gate, entry.ShutdownReport())
        return events

    events = asyncio.run(scenario())
    assert events == ["command_cancelled", "close"]
    assert exit_codes == [entry.SHUTDOWN_INCOMPLETE_EXIT_CODE]


def test_a_command_waiting_on_a_database_thread_past_the_wait_is_reported_as_incomplete(scaled_budget, exit_codes):
    """@description: Database work running in a worker thread cannot be stopped by cancellation; the stop reports it as incomplete and still returns within the budget.
    @param scaled_budget: Shortened shutdown constants.
    @param exit_codes: Captured exit status.
    @return: None.
    """
    async def scenario() -> float:
        started = asyncio.Event()

        async def database_command(interaction) -> None:
            started.set()
            await asyncio.to_thread(time.sleep, 1.0)  # [worker thread]: Cancellation of the awaiting task cannot stop this thread.

        client, gate = _build_client(database_command)
        client.tree._from_interaction(_slash_interaction(client))
        await started.wait()
        begun = time.monotonic()
        await entry._shut_down(FakeDiscordClose([]), gate, entry.ShutdownReport())
        return time.monotonic() - begun

    elapsed = asyncio.run(scenario())
    assert exit_codes == [entry.SHUTDOWN_INCOMPLETE_EXIT_CODE]
    assert elapsed < 1.0


def test_a_command_dispatched_after_shutdown_begins_is_refused_and_its_body_never_runs(scaled_budget, exit_codes):
    """@description: Once shutdown has started, a command arriving afterwards is refused by the gate, so no unwaited work starts.
    @param scaled_budget: Shortened shutdown constants.
    @param exit_codes: Captured exit status.
    @return: None.
    """
    async def scenario() -> list:
        events: list[str] = []

        async def command_body(interaction) -> None:
            events.append("command_ran")

        client, gate = _build_client(command_body)
        gate.stop_admitting()
        client.tree._from_interaction(_slash_interaction(client))
        await entry._shut_down(FakeDiscordClose(events), gate, entry.ShutdownReport())
        return events

    assert asyncio.run(scenario()) == ["close"]
    assert exit_codes == [0]


def test_shutdown_that_starts_while_the_guard_is_deciding_still_waits_for_the_admitted_command(scaled_budget, exit_codes):
    """@description: A command already inside the admission guard when shutdown begins is tracked from the start of the guard, so shutdown waits for it instead of missing it.
    @param scaled_budget: Shortened shutdown constants.
    @param exit_codes: Captured exit status.
    @return: None.
    """
    async def scenario() -> list:
        events: list[str] = []
        guard_entered = asyncio.Event()

        async def slow_guard(interaction) -> bool:
            guard_entered.set()
            await asyncio.sleep(0.2)  # [guard decision]: Shutdown begins during this await.
            return True

        async def command_body(interaction) -> None:
            events.append("command_finished")

        client, gate = _build_client(command_body, guard=slow_guard)
        client.tree._from_interaction(_slash_interaction(client))
        await guard_entered.wait()
        await entry._shut_down(FakeDiscordClose(events), gate, entry.ShutdownReport())
        return events

    assert asyncio.run(scenario()) == ["command_finished", "close"]
    assert exit_codes == [0]


def test_a_finished_command_is_no_longer_tracked_so_it_cannot_delay_shutdown(scaled_budget):
    """@description: Once a command task finishes it leaves the admitted set, so later shutdowns never wait on finished work.
    @param scaled_budget: Shortened shutdown constants.
    @return: None.
    """
    async def scenario() -> tuple[int, int]:
        async def quick_command(interaction) -> None:
            return None

        client, gate = _build_client(quick_command)
        client.tree._from_interaction(_slash_interaction(client))
        await asyncio.sleep(0.05)
        return len(gate.unfinished_admitted_tasks()), len(gate._admitted_tasks)

    assert asyncio.run(scenario()) == (0, 0)


def test_exit_status_reports_incomplete_work_before_cleanup_failure():
    """@description: The exit status keeps the existing precedence, with unfinished work reported ahead of a cleanup failure, and each category maps to its own code.
    @return: None.
    """
    assert entry._exit_code(entry.ShutdownReport(incomplete_count=1, failed_steps=["close_discord:RuntimeError"])) == entry.SHUTDOWN_INCOMPLETE_EXIT_CODE
    assert entry._exit_code(entry.ShutdownReport(failed_steps=["close_discord:RuntimeError"])) == entry.SHUTDOWN_FAILED_EXIT_CODE
    assert entry._exit_code(entry.ShutdownReport()) == 0


def test_the_shared_shutdown_contract_constants_are_unchanged():
    """@description: The production budget stays at 20 seconds with the 15/1/4 split and exit code 3 for incomplete work.
    @return: None.
    """
    assert entry.SHUTDOWN_DEADLINE_SECONDS == 20
    assert entry.INTERACTION_WAIT_SECONDS == 15
    assert entry.CANCELLATION_SETTLE_SECONDS == 1
    assert entry.CLOSE_RESERVED_SECONDS == 4
    assert entry.SHUTDOWN_INCOMPLETE_EXIT_CODE == 3
