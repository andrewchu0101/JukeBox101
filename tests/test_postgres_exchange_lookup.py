"""Real PostgreSQL behaviour of the Owner's exchange lookup by the operation code a user reported.

The lookup is read-only and shows no link, token or balance; only the configured Owner may use it, and an unknown code is reported as not found.
"""

import pytest

from bot.exchange_delivery import DmResult
from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID
from tests.test_postgres_exchange_stage_b import NOW, ExchangeWorld, ScriptedSender

pytestmark = pytest.mark.postgres


@pytest.fixture
def world(pg_database) -> ExchangeWorld:
    """@description: Provide the two-song exchange world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return ExchangeWorld(pg_database)


def test_a_delivered_exchange_is_found_by_its_code_without_secrets(world) -> None:
    """@description: The code from a reply finds the reservation, and the answer carries no link, token or balance.
    @param world: Two-song world.
    @return: None.
    """
    flow = world.run(ScriptedSender())
    found = world.repository.owner_exchange_lookup(OWNER_ID, flow.recommendation_id, NOW)
    assert found["operation_code"] == flow.recommendation_id and found["requester"] == ALICE
    assert found["delivery_state"] == "delivered" and found["refund_state"] == "not_needed" and found["reason"] is None
    assert set(found) == {"operation_code", "requester", "delivery_state", "refund_state", "reason", "created_at", "waiting_seconds"}


def test_a_refunded_failure_and_an_uncertain_outcome_are_told_apart(world) -> None:
    """@description: The Owner can see a refund that landed versus an unknown result that needs attention.
    @param world: Two-song world.
    @return: None.
    """
    failed = world.run(ScriptedSender(DmResult.not_sent("dm_closed")), key="lookup-failed")
    uncertain = world.run(ScriptedSender(DmResult.unknown("timeout")), user=BOB, key="lookup-unknown")
    assert world.repository.owner_exchange_lookup(OWNER_ID, failed.recommendation_id, NOW)["refund_state"] == "refunded"
    unknown = world.repository.owner_exchange_lookup(OWNER_ID, uncertain.recommendation_id, NOW)
    assert unknown["delivery_state"] == "uncertain" and unknown["reason"] == "delivery_result_unknown"


def test_an_unknown_code_is_not_found_and_only_the_owner_may_ask(world) -> None:
    """@description: A code that never existed returns None, and anyone else is refused before any database read.
    @param world: Two-song world.
    @return: None.
    """
    assert world.repository.owner_exchange_lookup(OWNER_ID, 987654, NOW) is None
    with pytest.raises(PermissionError):
        world.repository.owner_exchange_lookup(ALICE, 1, NOW)
