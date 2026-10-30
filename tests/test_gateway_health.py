"""Verify Gateway evidence, pinned Discord events, singleton and fail-closed health.

Discord is fake and DB boundaries are trapped; these tests never log in.
"""

import asyncio
import json
import os
import time
from unittest.mock import MagicMock

import pytest

from bot import healthcheck
from bot.gateway_client import ProductionGatewayClient
from bot.gateway_health import GatewayHealth, check_gateway, evidence_problems, process_identity

APPLICATION = 789001


def test_ready_requires_real_ack_and_disconnect_invalidates(tmp_path):
    """@description: Distinguish READY from fresh acknowledged connectivity.
    @param tmp_path: Private evidence directory.
    @return: None.
    """
    path = tmp_path / 'health.json'
    with GatewayHealth(APPLICATION, path) as health:
        health.mark('ready')
        assert 'GATEWAY_ACK_STALE' in check_gateway(APPLICATION, path=path, expected_pid=os.getpid())['problems']
        health.acknowledge()
        assert check_gateway(APPLICATION, path=path, expected_pid=os.getpid())['result'] == 'ready'
        health.mark('disconnected')
        health.acknowledge()  # A late ACK cannot re-open READY admission.
        assert check_gateway(APPLICATION, path=path, expected_pid=os.getpid())['result'] == 'not_ready'


@pytest.mark.parametrize('mutation,expected', [
    ({'start_ticks': 'older'}, 'GATEWAY_PROCESS_MISMATCH'),
    ({'boot_id': 'other-boot'}, 'GATEWAY_PROCESS_MISMATCH'),
    ({'pid': -1}, 'GATEWAY_PROCESS_MISMATCH'),
    ({'application_id': 1}, 'GATEWAY_APPLICATION_MISMATCH'),
    ({'sample_at': 0}, 'GATEWAY_SAMPLE_STALE'),
    ({'ack_at': 0}, 'GATEWAY_ACK_STALE'),
    ({'sample_at': float('nan')}, 'GATEWAY_SAMPLE_STALE'),
    ({'sample_at': float('inf')}, 'GATEWAY_SAMPLE_STALE'),
    ({'ack_at': 1001}, 'GATEWAY_ACK_STALE'),
    ({'state': 'starting'}, 'GATEWAY_NOT_READY'),
    ({'state': 'stopping'}, 'GATEWAY_NOT_READY'),
])
def test_bad_evidence_is_rejected(tmp_path, mutation, expected):
    """@description: Refuse old-process, stale, future and non-finite health samples.
    @param tmp_path: Evidence fixture directory.
    @param mutation: One invalid observation.
    @param expected: Required refusal code.
    @return: None.
    """
    health = GatewayHealth(APPLICATION, tmp_path / 'health.json')
    snapshot = {**health.identity, 'application_id': APPLICATION, 'instance_id': health.instance_id,
                'state': 'ready', 'sample_at': 1000, 'ack_at': 1000, **mutation}
    assert expected in evidence_problems(snapshot, health.identity, APPLICATION, 1000)


def test_restart_clears_retained_ready_and_local_singleton(tmp_path):
    """@description: Exclude a second login and invalidate old tmpfs evidence on restart.
    @param tmp_path: Shared local evidence/lease directory.
    @return: None.
    """
    path = tmp_path / 'health.json'
    with GatewayHealth(APPLICATION, path) as first:
        first.mark('ready')
        first.acknowledge()
        with pytest.raises(BlockingIOError):
            with GatewayHealth(APPLICATION, path):
                pytest.fail('second Gateway admitted')
    with GatewayHealth(APPLICATION, path) as second:
        assert second.instance_id != first.instance_id
        assert check_gateway(APPLICATION, path=path, expected_pid=os.getpid())['result'] == 'not_ready'


@pytest.mark.parametrize('content', ['[]', 'not-json', 'x' * 4097])
def test_malformed_or_missing_process_fails(tmp_path, content):
    """@description: Treat invalid evidence and dead main process as unhealthy.
    @param tmp_path: Evidence directory.
    @param content: Invalid or oversized file content.
    @return: None.
    """
    path = tmp_path / 'health.json'
    path.write_text(content)
    assert check_gateway(APPLICATION, path=path)['result'] == 'not_ready'
    assert check_gateway(APPLICATION, path=path, expected_pid=99999999)['result'] == 'not_ready'


def test_db_ready_cannot_mask_gateway_failure(monkeypatch):
    """@description: Fail the combined check when only the database is ready.
    @param monkeypatch: Trap external services and provide fake readiness.
    @return: None.
    """
    monkeypatch.setenv('DISCORD_EXPECTED_APPLICATION_ID', str(APPLICATION))
    monkeypatch.setenv('JUKEBOX_FORMAL_EXCHANGE', 'off')
    monkeypatch.setenv('JUKEBOX_NICKEL_CLAIM', 'off')
    monkeypatch.setattr(healthcheck, 'check_readiness', lambda: {'result': 'ready', 'problems': []})
    monkeypatch.setattr(healthcheck, 'check_gateway', lambda _: {'result': 'not_ready', 'problems': ['GATEWAY_NOT_READY']})
    assert healthcheck.check_health()['result'] == 'not_ready'


def test_pinned_discord_raw_ack_event_and_close(tmp_path, monkeypatch):
    """@description: Use the pinned framework websocket parser to dispatch a real fake ACK.
    @param tmp_path: Evidence directory.
    @param monkeypatch: Replace external connection and application identity.
    @return: None.
    """
    from discord.gateway import DiscordWebSocket

    async def scenario():
        """@description: Drive production hooks without logging into Discord.
        @param: none
        @return: None.
        """
        client = ProductionGatewayClient(MagicMock(nickel_claim_enabled=False))
        with GatewayHealth(APPLICATION, tmp_path / 'health.json') as health:
            client.gateway_health = health
            client._connection.application_id = APPLICATION
            await client._async_setup_hook()
            await client.on_ready()
            websocket = DiscordWebSocket(MagicMock(), loop=asyncio.get_running_loop())
            websocket.shard_id = None
            websocket._dispatch = client.dispatch
            websocket.log_receive = websocket.debug_log_receive
            await websocket.received_message('{"op":11,"d":null}')
            await asyncio.sleep(0)
            assert health.ack_at is not None
            await client.on_disconnect()
            assert health.state == 'disconnected' and health.ack_at is None
            await client.on_resumed()
            assert health.state == 'ready'
            await client.close()
            assert health.state == 'stopping'

    asyncio.run(scenario())


def test_identity_mismatch_never_becomes_ready(tmp_path):
    """@description: A fake foreign Application cannot publish a healthy READY.
    @param tmp_path: Private evidence fixture.
    @return: None.
    """
    client = ProductionGatewayClient(MagicMock(nickel_claim_enabled=False))
    with GatewayHealth(APPLICATION, tmp_path / 'health.json') as health:
        client.gateway_health = health
        client._connection.application_id = 1
        asyncio.run(client.on_ready())
        assert health.state == 'identity_mismatch'


def test_health_file_failure_cannot_prevent_discord_close(monkeypatch):
    """@description: Preserve shutdown even if the evidence filesystem becomes unavailable.
    @param monkeypatch: Replace only the parent close transport.
    @return: None.
    """
    from unittest.mock import AsyncMock
    from bot.discord_app import JukeBoxClient
    from bot.__main__ import ShutdownReport, _invalidate_gateway_health
    client = ProductionGatewayClient(MagicMock(nickel_claim_enabled=False))
    health = MagicMock(task=None)
    health.mark.side_effect = OSError('synthetic tmpfs failure')
    client.gateway_health = health
    report = ShutdownReport()
    _invalidate_gateway_health(client, report)
    assert report.failed_steps == ['invalidate_gateway_health:OSError']
    close = AsyncMock()
    monkeypatch.setattr(JukeBoxClient, 'close', close)
    with pytest.raises(OSError):
        asyncio.run(client.close())
    close.assert_awaited_once()
