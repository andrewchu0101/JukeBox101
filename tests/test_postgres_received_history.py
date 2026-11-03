"""Real PostgreSQL behaviour of the caller's received-recommendation history: only delivered songs, only the caller's own, newest first."""

import pytest

from bot.exchange_delivery import DmResult
from tests.test_postgres_auto_pipeline import ALICE, BOB
from tests.test_postgres_exchange_stage_b import ExchangeWorld, ScriptedSender

pytestmark = pytest.mark.postgres


@pytest.fixture
def world(pg_database) -> ExchangeWorld:
    """@description: Provide the two-song exchange world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return ExchangeWorld(pg_database)


def test_a_delivered_song_appears_for_its_recipient_only(world) -> None:
    """@description: Alice received Bob's song; Bob received nothing, so Bob's list is empty.
    @param world: Two-song world.
    @return: None.
    """
    world.run(ScriptedSender())
    received = world.repository.received_history(ALICE)
    assert len(received) == 1 and set(received[0]) == {"youtube_url", "delivered_at"} and received[0]["delivered_at"] is not None
    assert world.repository.received_history(BOB) == []


def test_failed_and_uncertain_deliveries_are_not_listed_as_received(world) -> None:
    """@description: Only an accepted direct message counts as received.
    @param world: Two-song world.
    @return: None.
    """
    world.run(ScriptedSender(DmResult.not_sent("dm_closed")), key="recv-failed")
    world.run(ScriptedSender(DmResult.unknown("timeout")), user=BOB, key="recv-unknown")
    assert world.repository.received_history(ALICE) == [] and world.repository.received_history(BOB) == []
