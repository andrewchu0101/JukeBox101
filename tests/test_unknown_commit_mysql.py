"""Verify the unknown-commit contract on the explicitly isolated MySQL target, with reviewed commit-boundary fault injection.

Opt-in only: every test is skipped unless JUKEBOX_RUN_EXCHANGE_MYSQL_ISOLATED=1 and the isolated target is configured.
Faults are injected only into the first connection of a scenario, through a test-only wrapper. The observation connections used for
checks are created separately and are never wrapped. Each scenario names the stage it targets; the injector's `fired` count proves
that the stage was reached. The direct-message adapter is a fake: no real DM is sent.

What each case can and cannot prove:
- lost_ack_after_commit: the real COMMIT succeeded and the client was told it failed. This is fault injection, not a real network cut.
- fail_before_commit: the client failed before COMMIT was sent. The production transaction rolls back, and the outcome is still reported as unknown.
- fail_statement: a deadlock-style error before the debit insert. This is a definite rollback, so the caller gets the database error, not an unknown outcome.
"""

import asyncio
import os
import random
from datetime import datetime, timezone

import pymysql
import pytest

from bot.database.exchange_reservation import ExchangeStatus
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.exchange_delivery import DeliveryStatus, exchange_and_deliver
from tests.commit_fault_injector import FaultInjectingConnection
from tests.financial_invariants import attribution_violations
from tests.isolated_mysql_target import ISOLATION_SWITCH, open_verified_connection
from tests.test_exchange_mysql_integration import USER_A, fixture_footprint, fixture_snapshot, isolated
from tests.test_exchange_repository_mysql import assert_footprint_unchanged, assert_repository_state, funded_pair, repository_world
from tests.test_exchange_unknown_commit import FakeReplier, FakeSender
from tests.test_identity_service_path import OWNER_ID

pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(os.environ.get(ISOLATION_SWITCH) != "1", reason="Requires explicitly authorized isolated MySQL target"),
]

INJECTED_STATEMENT = "INSERT INTO credit_ledger"  # [statement stage]: Matches the debit insert only; the replay SELECT also names credit_ledger.


def faulted_repository(target, mode: str, statement_marker: str = ""):
    """@description: Build a production repository whose first connection carries one reviewed fault and whose later connections are clean.
    @param target: Tuple of settings and identity pin for the isolated target.
    @param mode: Fault mode from the injector.
    @param statement_marker: Statement substring for the statement mode.
    @return: Tuple of repository and the injector that owns the first connection.
    """
    settings, pin = target
    created: list = []

    def factory():
        """@description: Return the faulted connection once, then plain verified connections.
        @return: Connection for the next worker.
        """
        connection = open_verified_connection(settings, pin)
        if not created:
            wrapped = FaultInjectingConnection(connection, mode, statement_marker)
            created.append(wrapped)
            return wrapped
        return connection

    return SubmissionRepository(factory, OWNER_ID), created


def clean_repository(target) -> SubmissionRepository:
    """@description: Build a production repository over plain verified connections, used for the retry and every check.
    @param target: Tuple of settings and identity pin.
    @return: Repository without any fault.
    """
    settings, pin = target
    return SubmissionRepository(lambda: open_verified_connection(settings, pin), OWNER_ID)


def test_lost_acknowledgement_after_commit_is_replayed_by_key_without_a_second_debit(funded_pair) -> None:
    """@description: After a real commit whose acknowledgement is lost, the same key on a fresh connection returns the original reservation.
    @param funded_pair: Two funded submitters and one candidate over real MySQL.
    @return: None.
    """
    target, _, _ = funded_pair
    before = fixture_snapshot(target)
    repository, injected = faulted_repository(target, "lost_ack_after_commit")
    with pytest.raises(CommitOutcomeUnknown):
        repository.exchange(USER_A, "s-unknown-ack", random.Random(1))
    assert injected[0].fired == 1
    retry = clean_repository(target).exchange(USER_A, "s-unknown-ack", random.Random(1))
    assert retry.status is ExchangeStatus.REPLAYED and retry.recommendation_id is not None
    assert attribution_violations("s-unknown-ack", retry.recommendation_id, before, fixture_snapshot(target)) == ()
    assert_repository_state(funded_pair, 1, 0)


def test_failure_before_commit_is_unknown_but_rolled_back_and_the_retry_reserves_once(funded_pair) -> None:
    """@description: A failure before COMMIT is reported as unknown; the production rollback leaves nothing, so the same-key retry reserves once.
    @param funded_pair: Two funded submitters and one candidate over real MySQL.
    @return: None.
    """
    target, _, _ = funded_pair
    before_footprint = fixture_footprint(target)
    repository, injected = faulted_repository(target, "fail_before_commit")
    with pytest.raises(CommitOutcomeUnknown):
        repository.exchange(USER_A, "s-unknown-precommit", random.Random(1))
    assert injected[0].fired == 1
    assert_footprint_unchanged(target, before_footprint)  # [rollback proof]: The real rollback left no reservation or ledger row.
    assert_repository_state(funded_pair, 0, 1)
    before = fixture_snapshot(target)
    retry = clean_repository(target).exchange(USER_A, "s-unknown-precommit", random.Random(1))
    assert retry.status is ExchangeStatus.RESERVED
    assert attribution_violations("s-unknown-precommit", retry.recommendation_id, before, fixture_snapshot(target)) == ()
    assert_repository_state(funded_pair, 1, 0)


def test_definite_statement_failure_rolls_back_the_partial_reservation(funded_pair) -> None:
    """@description: A database error before the debit insert is definite: the database error is raised, the partial reservation is rolled back, and the retry reserves once.
    @param funded_pair: Two funded submitters and one candidate over real MySQL.
    @return: None.
    """
    target, _, _ = funded_pair
    before_footprint = fixture_footprint(target)
    repository, injected = faulted_repository(target, "fail_statement", INJECTED_STATEMENT)
    with pytest.raises(pymysql.MySQLError) as error:
        repository.exchange(USER_A, "s-definite", random.Random(1))
    assert not isinstance(error.value, CommitOutcomeUnknown)
    assert injected[0].fired == 1 and error.value.args[0] == 1213
    assert_footprint_unchanged(target, before_footprint)
    assert_repository_state(funded_pair, 0, 1)
    retry = clean_repository(target).exchange(USER_A, "s-definite", random.Random(1))
    assert retry.status is ExchangeStatus.RESERVED
    assert_repository_state(funded_pair, 1, 0)


def test_caller_replays_once_and_delivers_the_original_through_a_fake_adapter(funded_pair) -> None:
    """@description: The production caller, with a lost acknowledgement on its first attempt, replays the same key once and sends one direct message through a fake adapter.
    @param funded_pair: Two funded submitters and one candidate over real MySQL.
    @return: None.
    """
    target, _, _ = funded_pair
    settings, pin = target
    created: list = []

    def factory():
        """@description: Fault only the first connection with a lost acknowledgement after commit.
        @return: Connection for the next worker.
        """
        connection = open_verified_connection(settings, pin)
        if not created:
            wrapped = FaultInjectingConnection(connection, "lost_ack_after_commit")
            created.append(wrapped)
            return wrapped
        return connection

    repository = SubmissionRepository(factory, OWNER_ID)
    sender, replier = FakeSender(), FakeReplier()
    flow = asyncio.run(exchange_and_deliver(repository, sender, replier, USER_A, "s-caller-replay",
                                                          random.Random(1), datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
                                                          new_token=lambda: "s-caller-token"))
    assert created[0].fired == 1
    assert len(sender.sent) == 1, "exactly one fake direct message for the original reservation"
    assert flow.delivery_status is DeliveryStatus.DELIVERED
    assert_repository_state(funded_pair, 1, 0)
