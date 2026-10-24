"""Verify the formal /exchange handler on the explicitly isolated MySQL target, through the production handler, the formal MusicService branch, a real SubmissionRepository and the production delivery program.

Only the external boundaries are replaced: the Discord interaction (defer, followup), the Discord client used by the production direct-message adapter
(fetch_user and user.send, scripted outcomes) and nothing else. The formal switch is set on the test's own MusicService object only; no deployment setting is changed.
The synthetic route is left off. Commit-unknown cases live in test_unknown_commit_mysql.py and are not repeated here.

This is an isolated integration of the formal handler. It is not real Discord acceptance and does not verify real startup, real commands or real direct messages.

Opt-in only: every test is skipped unless JUKEBOX_RUN_EXCHANGE_MYSQL_ISOLATED=1 and the isolated target is configured.
The fixture is the recorded guarded world from test_compensation_mysql.py: the compensation tables must already exist, the switch is off as in production.
"""

import asyncio
import os
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot import discord_app
from bot.database.submission_repository import SubmissionRepository
from bot.exchange_delivery import DeliveryStatus
from bot.services import SYNTHETIC_LABEL, MusicService
from tests.isolated_mysql_target import ISOLATION_SWITCH, open_verified_connection
from tests.test_compensation_mysql import guarded_funded_pair, later, read_row  # noqa: F401  (fixture imported by name)
from tests.test_exchange_formal_chain_mysql import backdate, repository_with_switch_on
from tests.test_exchange_mysql_integration import USER_A, fixture_footprint, isolated  # noqa: F401  (isolated: fixture imported by name)
from tests.test_exchange_repository_mysql import assert_repository_state
from tests.test_identity_service_path import OWNER_ID, submission

pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(os.environ.get(ISOLATION_SWITCH) != "1", reason="Requires explicitly authorized isolated MySQL target"),
]

PEER_LINK = submission(2).source_url  # [synthetic link]: The only candidate for the requester, from the fixture's peer.
MESSAGE_ID = 9100  # [synthetic message identifier]: Returned by the scripted Discord client on a delivered attempt.


def discord_client(behaviour: str) -> MagicMock:
    """@description: A replacement Discord client for the production direct-message adapter: fetch_user is a GET and user.send is the send, each scripted.
    @param behaviour: delivered (accepted), lookup_fails (GET fails before any send), or send_unknown (the send raises after it was attempted).
    @return: Mock client with the recorded calls.
    """
    client = MagicMock()
    user = MagicMock()
    if behaviour == "delivered":
        user.send = AsyncMock(return_value=SimpleNamespace(id=MESSAGE_ID))
    elif behaviour == "send_unknown":
        user.send = AsyncMock(side_effect=RuntimeError("scripted send outcome unknown"))
    else:
        user.send = AsyncMock(return_value=SimpleNamespace(id=MESSAGE_ID))
    client.fetch_user = AsyncMock(side_effect=RuntimeError("scripted lookup refusal")) if behaviour == "lookup_fails" else AsyncMock(return_value=user)
    client.user = user
    return client


def interaction(user_id: int, client: MagicMock, interaction_id: int = 555) -> MagicMock:
    """@description: A replacement Discord interaction with awaitable defer and followup, recording the calls the handler makes.
    @param user_id: Requesting user.
    @param client: Replacement Discord client.
    @param interaction_id: Interaction identifier used by the technical request key.
    @return: Mock interaction.
    """
    value = MagicMock()
    value.user.id = user_id
    value.id = interaction_id
    value.client = client
    value.response.defer = AsyncMock()
    value.followup.send = AsyncMock()
    return value


def formal_handler(repository: SubmissionRepository) -> discord_app.CommandHandlers:
    """@description: The production command handler over the production MusicService, with only the formal switch on; the synthetic route stays off.
    @param repository: Production repository over the isolated target.
    @return: Command handlers whose /exchange takes the formal branch.
    """
    service = MusicService(None, submission_repository=repository, formal_exchange=True)
    assert service.formal_exchange_enabled is True and service.synthetic_exchange_enabled is False
    return discord_app.CommandHandlers(service)


@pytest.fixture
def world(guarded_funded_pair):
    """@description: The recorded guarded world with the requester funded and one peer candidate; the repository keeps the production default, switch off.
    @param guarded_funded_pair: Recorded guarded fixture.
    @return: Tuple of verified target and production repository.
    """
    target, repository, _ = guarded_funded_pair
    return target, repository


def sent_texts(client: MagicMock) -> list:
    """@description: The text of every direct message the production adapter attempted through the replacement client.
    @param client: Replacement Discord client.
    @return: Message bodies, in order.
    """
    return [call.args[0] for call in client.user.send.await_args_list]


def reply_texts(value: MagicMock) -> list:
    """@description: The text of every ephemeral followup the handler sent.
    @param value: Mock interaction.
    @return: Reply bodies, in order.
    """
    return [call.args[0] for call in value.followup.send.await_args_list]


def test_c1_a_normal_request_defers_privately_debits_once_and_sends_one_message_with_no_label_in_the_reply(world):
    """@description: The formal handler defers ephemerally, debits one point, sends one direct message carrying the link, and replies privately without the synthetic label and without the link.
    @param world: Recorded guarded world with the production repository.
    @return: None.
    """
    target, repository = world
    client = discord_client("delivered")
    value = interaction(USER_A, client)
    asyncio.run(formal_handler(repository).exchange(value))
    value.response.defer.assert_awaited_once_with(ephemeral=True)
    assert len(sent_texts(client)) == 1
    assert PEER_LINK in sent_texts(client)[0] and SYNTHETIC_LABEL not in sent_texts(client)[0]
    replies = reply_texts(value)
    assert len(replies) == 1 and SYNTHETIC_LABEL not in replies[0] and PEER_LINK not in replies[0]
    assert value.followup.send.await_args_list[0].kwargs == {"ephemeral": True}
    recommendation = read_row(target, "SELECT recommendation_id, status FROM recommendations WHERE discord_user_id=%s", (USER_A,))
    assert recommendation["status"] == "delivered"
    assert_repository_state((target, None, None), 1, 0)


def test_c2_the_same_key_replay_through_the_handler_neither_debits_nor_sends_a_second_message(world):
    """@description: A second /exchange from the same interaction identity returns the original reservation: no second debit and no second direct message.
    @param world: Recorded guarded world with the production repository.
    @return: None.
    """
    target, repository = world
    handler = formal_handler(repository)
    first_client = discord_client("delivered")
    asyncio.run(handler.exchange(interaction(USER_A, first_client, interaction_id=556)))
    replay_client = discord_client("delivered")
    replay = interaction(USER_A, replay_client, interaction_id=556)
    asyncio.run(handler.exchange(replay))
    assert len(sent_texts(first_client)) == 1
    assert replay_client.user.send.await_count == 0 and replay_client.fetch_user.await_count == 0
    assert SYNTHETIC_LABEL not in reply_texts(replay)[0]
    assert_repository_state((target, None, None), 1, 0)


def test_c3_switched_off_with_the_tables_present_an_open_incident_blocks_the_handler_without_debit_or_message(world):
    """@description: With the compensation switch off and the tables present, an open incident blocks the handler's new request: the reply says so, nothing is debited and no direct message is sent.
    @param world: Recorded guarded world with the production repository.
    @return: None.
    """
    target, repository = world
    setup = repository_with_switch_on(target)
    reservation = setup.exchange(USER_A, "h3-original", random.Random(1), now=later()).recommendation_id
    setup.owner_approve_incident(OWNER_ID, reservation, "h3", later())
    backdate(target, reservation)
    before = fixture_footprint(target)
    client = discord_client("delivered")
    value = interaction(USER_A, client, interaction_id=557)
    asyncio.run(formal_handler(repository).exchange(value))
    assert client.user.send.await_count == 0 and client.fetch_user.await_count == 0
    assert len(reply_texts(value)) == 1 and SYNTHETIC_LABEL not in reply_texts(value)[0]
    assert fixture_footprint(target) == before
    assert_repository_state((target, None, None), 1, 0)


def test_c4_an_explicit_not_sent_refunds_once_releases_the_song_and_a_replay_refunds_nothing_more(world):
    """@description: The adapter reports an explicit not-sent outcome: the point is refunded once and the reservation released; a replay of the same request refunds nothing again and sends nothing.
    @param world: Recorded guarded world with the production repository.
    @return: None.
    """
    target, repository = world
    handler = formal_handler(repository)
    failed_client = discord_client("lookup_fails")
    failed = interaction(USER_A, failed_client, interaction_id=558)
    asyncio.run(handler.exchange(failed))
    assert failed_client.user.send.await_count == 0
    assert "已退回 1 點" in reply_texts(failed)[0]
    refunds = read_row(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE reason='delivery_refund'", ())["total"]
    assert refunds == 1
    assert read_row(target, "SELECT released_at FROM recommendations WHERE discord_user_id=%s", (USER_A,))["released_at"] is not None
    replay_client = discord_client("delivered")
    replay = interaction(USER_A, replay_client, interaction_id=558)
    asyncio.run(handler.exchange(replay))
    assert replay_client.fetch_user.await_count == 0 and replay_client.user.send.await_count == 0
    assert read_row(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE reason='delivery_refund'", ())["total"] == 1
    assert_repository_state((target, None, None), 1, 1)


def test_c5_an_unknown_direct_message_outcome_is_never_refunded_or_resent_and_keeps_the_song(world):
    """@description: The send attempt raises after it was made: the outcome is recorded as uncertain, the point is not refunded, a replay sends nothing, and the song stays held.
    @param world: Recorded guarded world with the production repository.
    @return: None.
    """
    target, repository = world
    handler = formal_handler(repository)
    unknown_client = discord_client("send_unknown")
    unknown = interaction(USER_A, unknown_client, interaction_id=559)
    asyncio.run(handler.exchange(unknown))
    assert unknown_client.user.send.await_count == 1
    row = read_row(target, "SELECT status, released_at FROM recommendations WHERE discord_user_id=%s", (USER_A,))
    assert row["status"] == "uncertain" and row["released_at"] is None
    assert read_row(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE reason='delivery_refund'", ())["total"] == 0
    replay_client = discord_client("delivered")
    asyncio.run(handler.exchange(interaction(USER_A, replay_client, interaction_id=559)))
    assert replay_client.user.send.await_count == 0 and replay_client.fetch_user.await_count == 0
    assert_repository_state((target, None, None), 1, 0)


def test_the_synthetic_route_stays_off_for_every_case(world):
    """@description: The isolated handler cases run with the synthetic route off; the synthetic service attribute is False for the formal service used here.
    @param world: Recorded guarded world with the production repository.
    @return: None.
    """
    _, repository = world
    service = MusicService(None, submission_repository=repository, formal_exchange=True)
    assert service.synthetic_exchange_enabled is False
    assert service.formal_exchange_enabled is True
