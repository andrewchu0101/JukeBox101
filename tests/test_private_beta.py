"""Exercise private Beta intake and query wiring entirely through mocks and the existing SQL fake.

Tests trap external boundaries and distinguish durable replay from an uncertain acknowledgement.
"""

from tests.intake_fixture import seed_intake
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import psycopg
from psycopg import errors as pg_errors
import pytest

from bot.database.submission_repository import SubmissionRepository
from bot.discord_app import JukeBoxClient
from bot.services import MusicService, ServiceUnavailable
from tests.submission_persistence_fake import FakeDatabase
from tests.test_submission_persistence import approve, intake

URL = "https://youtu.be/Abc_123-Xyz"


@pytest.fixture
def beta_context(monkeypatch):
    """@description: Run production intake using fake transactions with settlement explicitly disabled.
    @param monkeypatch: External boundary replacement facility.
    @return: Existing fake, injected submission repository, original reader and actual service.
    """
    monkeypatch.setattr("psycopg.connect", MagicMock(side_effect=AssertionError("No real MySQL")))
    database = FakeDatabase()  # [offline storage]: No driver connection is created.
    database.state["control"]["settlement_enabled"] = False
    submissions = SubmissionRepository(database.connect, 777)
    reader = MagicMock()  # [read boundary]: Only existing balance queries are permitted.
    return database, submissions, reader, MusicService(reader, submissions)


def test_pending_and_replay_use_one_durable_operation(beta_context):
    """@description: Keep Discord retries from creating another event or award.
    @param beta_context: Offline service and persistence boundary.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    first = asyncio.run(service.share(123, URL, "discord:456"))
    replay = asyncio.run(service.share(123, URL + "?si=tracking", "discord:456"))
    assert "已儲存" in first and "pending_validation" in first
    assert "重複操作" in replay
    assert len(database.state["jb2_events"]) == 1
    assert not database.state["credit_ledger"] and not database.state["jb2_claims"]
    assert not database.state["jb2_versions"]
    assert all(connection.closed for connection in database.connections)
    reader.submit_pending.assert_not_called()


@pytest.mark.parametrize("fault, committed", [("before", False), ("after", True)])
def test_unknown_commit_is_not_retried_automatically(beta_context, fault, committed):
    """@description: Preserve ambiguity and reuse the same receipt identity for an explicit technical replay.
    @param beta_context: Offline service and persistence boundary.
    @param fault: Acknowledgement loss before or after simulated commit.
    @param committed: Whether the first attempt reached fake durable storage.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    database.commit_fault = fault
    reply = asyncio.run(service.share(123, URL, "discord:456"))
    assert "結果未知" in reply and "勿重新投稿" in reply and "discord:456" in reply
    assert len(database.connections) == 1
    assert bool(database.state["jb2_events"]) is committed
    asyncio.run(service.share(123, URL, "discord:456"))
    assert len(database.state["jb2_events"]) == 1
    assert not database.state["credit_ledger"]


@pytest.mark.parametrize("switch,value", [("snapshot_ready", False), ("legacy_writers_quiesced", False), ("settlement_enabled", True)])
def test_control_gate_fails_closed(beta_context, switch, value):
    """@description: Refuse pending intake without approved isolation or with settlement enabled.
    @param beta_context: Offline service and persistence boundary.
    @param switch: Control prerequisite under test.
    @param value: Unsafe control state.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    database.state["control"][switch] = value
    before = deepcopy(database.state)
    with pytest.raises(ServiceUnavailable, match="啟用檢查"):
        asyncio.run(service.share(123, URL, "discord:456"))
    assert database.state == before
    assert all(connection.closed for connection in database.connections)


def test_missing_key_and_invalid_url_do_not_connect(beta_context):
    """@description: Reject malformed intake before entering a worker connection.
    @param beta_context: Offline service and persistence boundary.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    with pytest.raises(ServiceUnavailable, match="識別碼"):
        asyncio.run(service.share(123, URL))
    with pytest.raises(ValueError):
        asyncio.run(service.share(123, "https://evil.example/watch?v=Abc_123-Xyz", "discord:456"))
    assert not database.connections


def test_replay_with_changed_actor_or_video_is_rejected(beta_context):
    """@description: Prevent replay keys from reassigning another submitter's persisted event.
    @param beta_context: Offline service and persistence boundary.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    submissions.accept_pending(123, URL, "discord:456")
    with pytest.raises(ValueError):
        submissions.accept_pending(999, URL, "discord:456")
    with pytest.raises(ValueError):
        submissions.accept_pending(123, "https://youtu.be/Zbc_123-Xyz", "discord:456")
    assert len(database.state["jb2_events"]) == 1


def test_concurrent_replay_records_one_event(beta_context):
    """@description: Exercise shared repository serialization for simultaneous technical retries.
    @param beta_context: Offline fake and repository.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    with ThreadPoolExecutor(max_workers=2) as executor:
        attempts = [executor.submit(submissions.accept_pending, 123, URL, "discord:456") for index in range(2)]
        results = [attempt.result() for attempt in attempts]
    assert sorted(result.create_share_event for result in results) == [False, True]
    assert len(database.state["jb2_events"]) == 1
    assert not database.state["credit_ledger"]


def test_repeat_of_reviewed_video_preserves_evidence(beta_context):
    """@description: Reuse trusted current review without treating a fresh public share as an evidence update.
    @param beta_context: Offline fake and repository.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    request = intake(1)
    seed_intake(submissions, request)
    approve(submissions, request.operation_id)
    video_id = request.identity.video.video_id
    before = deepcopy(database.state["jb2_videos"][video_id])
    result = submissions.accept_pending(123, "https://youtu.be/" + video_id, "discord:456")
    assert result.candidate.review.status.value == "approved"
    assert database.state["jb2_videos"][video_id] == before
    assert not database.state["credit_ledger"]


def test_history_query_is_scoped_bounded_and_select_only():
    """@description: Verify ownership in SQL rather than relying on presentation filtering.
    @param: none
    @return: None.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [{"operation_id": "discord:456", "review_status": "pending_validation"}]
    repository = SubmissionRepository(MagicMock(return_value=connection), 777)
    assert repository.history(123) == cursor.fetchall.return_value
    statement, parameters = cursor.execute.call_args.args
    assert statement.startswith("SELECT ") and "WHERE e.discord_user_id=%s" in statement
    assert "ORDER BY e.accepted_sequence DESC LIMIT %s" in statement
    assert parameters[0] == 123 and parameters[1] > 0
    assert cursor.execute.call_count == 1
    connection.commit.assert_not_called()
    connection.close.assert_called_once()


@pytest.mark.parametrize("failed", [False, True])
def test_history_select_closes_on_success_and_failure(failed):
    """@description: Release worker-local read resources even when fetching fails.
    @param failed: Synthetic query failure.
    @return: None.
    """
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    repository = SubmissionRepository(MagicMock(return_value=connection), 777)
    if failed:
        cursor.execute.side_effect = psycopg.OperationalError("synthetic-secret")
        with pytest.raises(psycopg.OperationalError):
            repository.history(123)
    else:
        repository.history(123)
    connection.close.assert_called_once()
    connection.commit.assert_not_called()


def test_history_presents_current_review_not_delivery_history(beta_context):
    """@description: Keep another user's rows outside the public service query contract.
    @param beta_context: Actual service with replaceable scoped query.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    submissions.history = MagicMock(return_value=[dict(operation_id="discord:456", video_id="Abc_123-Xyz",
                                                     review_status="pending_validation", reward_status="waiting_identity")])
    reply = asyncio.run(service.history(123))
    assert "本人投稿" in reply and "pending_validation" in reply and "discord:456" in reply
    assert "https://www.youtube.com/watch?v=Abc_123-Xyz" in reply
    submissions.history.assert_called_once_with(123)
    reader.history.assert_not_called()


def test_balance_reuses_reader_and_daily_does_not_write(beta_context):
    """@description: Avoid introducing settlement or the legacy preference writer into Beta.
    @param beta_context: Existing repositories and actual service.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    reader.balance.return_value = 7
    assert "目前可用 Nickel：7。" in asyncio.run(service.balance(123))
    reader.balance.assert_called_once_with(123)
    assert "未保存" in asyncio.run(service.daily(123, True))
    reader.set_daily.assert_not_called()
    assert not database.connections


# v7 multi-guild: DISCORD_DEV_GUILD_ID is no longer a production setting (see test_multi_guild_entry.py).
@pytest.mark.parametrize("missing", ["DISCORD_EXPECTED_APPLICATION_ID", "DISCORD_OWNER_USER_ID"])
def test_private_startup_missing_configuration_never_logs_in(database_settings, monkeypatch, missing):
    """@description: Keep production startup closed when the private target or Owner is unspecified.
    @param database_settings: Synthetic database configuration, never connected.
    @param monkeypatch: Environment and transport isolation.
    @param missing: Mandatory setting to remove.
    @return: None.
    """
    from bot import __main__ as entry
    for name in ("DISCORD_EXPECTED_APPLICATION_ID", "DISCORD_OWNER_USER_ID"):
        monkeypatch.setenv(name, "456")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "synthetic-token")
    monkeypatch.delenv(missing)
    login = MagicMock()
    monkeypatch.setattr(discord.Client, "run", login)
    monkeypatch.setattr("psycopg.connect", MagicMock(side_effect=AssertionError("No network")))
    with pytest.raises(SystemExit, match=missing):
        entry.main()
    login.assert_not_called()


def test_repository_history_excludes_other_users_and_tracks_review(beta_context):
    """@description: Exercise caller isolation and the current review after a shared-video decision.
    @param beta_context: Existing fake with the actual intake and query repository.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    request = intake(1)
    seed_intake(submissions, request)
    submissions.accept_pending(123, URL, "discord:456")
    submissions.accept_pending(999, URL, "discord:789")
    approve(submissions, request.operation_id)
    assert [row["operation_id"] for row in submissions.history(123)] == ["discord:456"]
    assert [row["operation_id"] for row in submissions.history(999)] == ["discord:789"]
    assert submissions.history(555) == []
    assert submissions.history(request.submitter_id)[0]["review_status"] == "approved"
    assert "沒有你的投稿" in asyncio.run(service.history(555))
    assert all(connection.closed for connection in database.connections)


@pytest.mark.parametrize("command_name,options,expected", [
    ("share", [{"name": "url", "type": 3, "value": URL}], "已儲存"),
    ("history", [], "沒有你的投稿"), ("balance", [], "目前可用 Nickel：5。"),
])
def test_private_sdk_dispatch_uses_real_service(beta_context, monkeypatch, command_name, options, expected):
    """@description: Route real registered slash callbacks into pending intake and scoped queries.
    @param beta_context: Actual service with only fake or mocked repositories.
    @param monkeypatch: Local SDK completion-event trap.
    @param command_name: Existing slash command under test.
    @param options: Existing registered option payload.
    @param expected: Required truthful result fragment.
    @return: None.
    """
    from bot.acceptance_config import DevelopmentGuildSettings
    from bot.config import DiscordSettings
    from bot.gateway_listener import GuildInteractionGuard
    database, submissions, reader, service = beta_context
    reader.balance.return_value = 5
    settings = DevelopmentGuildSettings(DiscordSettings("synthetic-token"), 456, 789)
    client = JukeBoxClient(service, interaction_guard=GuildInteractionGuard(settings))
    monkeypatch.setattr(client, "dispatch", MagicMock())
    interaction = _private_interaction(command_name, options)
    asyncio.run(client.tree._call(interaction))
    reply = interaction.edit_original_response.call_args.kwargs
    assert expected in reply["content"] and reply["allowed_mentions"].everyone is False
    interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    reader.submit_pending.assert_not_called()


def _private_interaction(command_name: str, options: list) -> SimpleNamespace:
    """@description: Supply an authenticated synthetic interaction to the actual SDK dispatcher.
    @param command_name: Existing command name.
    @param options: Structured command arguments excluding any recipient override.
    @return: Captured private response transport with no network collaborator.
    """
    return SimpleNamespace(id=456, guild_id=456, application_id=789, _state=MagicMock(), guild=None,
                           type=discord.InteractionType.application_command, user=SimpleNamespace(id=123),
                           command_failed=False, response=SimpleNamespace(is_done=MagicMock(return_value=False),
                           send_message=AsyncMock(), defer=AsyncMock()), edit_original_response=AsyncMock(),
                           data={"name": command_name, "type": 1, "options": options})


@pytest.mark.parametrize("guild_id,application_id", [(None, 789), (999, 789), (456, 999)])
def test_private_guard_blocks_before_any_repository(beta_context, monkeypatch, guild_id, application_id):
    """@description: Prevent private persistence callbacks for DMs or an unexpected application or guild.
    @param beta_context: Actual service and fake state.
    @param monkeypatch: SDK completion-event injection.
    @param guild_id: Unauthorized incoming guild or DM.
    @param application_id: Incoming application ID.
    @return: None.
    """
    from bot.acceptance_config import DevelopmentGuildSettings
    from bot.config import DiscordSettings
    from bot.gateway_listener import GuildInteractionGuard
    database, submissions, reader, service = beta_context
    client = JukeBoxClient(service, interaction_guard=GuildInteractionGuard(
        DevelopmentGuildSettings(DiscordSettings("synthetic-token"), 456, 789)))
    monkeypatch.setattr(client, "dispatch", MagicMock())
    interaction = _private_interaction("share", [{"name": "url", "type": 3, "value": URL}])
    interaction.guild_id, interaction.application_id = guild_id, application_id
    asyncio.run(client.tree._call(interaction))
    assert interaction.command_failed and not database.connections
    interaction.response.defer.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


@pytest.mark.parametrize("operation", ["share", "history", "balance"])
def test_database_failures_reply_privately_without_driver_details(beta_context, operation):
    """@description: Return safe failure messages instead of fake success or raw driver secrets.
    @param beta_context: Existing service with injected repository failure.
    @param operation: Public operation to fail.
    @return: None.
    """
    database, submissions, reader, service = beta_context
    target = reader if operation == "balance" else submissions
    method = "accept_pending" if operation == "share" else operation
    setattr(target, method, MagicMock(side_effect=psycopg.OperationalError("synthetic-secret")))
    interaction = _private_interaction(operation, [])
    client = JukeBoxClient(service)
    arguments = (URL,) if operation == "share" else ()
    asyncio.run(getattr(client.handlers, operation)(interaction, *arguments))
    reply = interaction.edit_original_response.call_args.kwargs["content"]
    assert "暫時無法使用" in reply and "synthetic-secret" not in reply
    assert not database.connections
