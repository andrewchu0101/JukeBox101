"""Verify the first-stage protection for stranded reserved exchanges, offline over the SQL-protocol fake.

A reservation is stranded when it is still reserved, was never claimed for delivery, is not released, and was created before the
threshold now - STRANDED_AFTER. While one exists, the same requester cannot start a new exchange. A technical replay of an existing key is
never blocked, because it identifies the original order first. The check runs inside the writer-gate transaction, and the Owner list is
readable only by the configured Owner and exposes only stranded rows.
"""

from datetime import datetime, timedelta, timezone

import pytest

from bot.database.exchange_reservation import ExchangeStatus
from bot.database.pending_review import STRANDED_AFTER
from tests.test_exchange_reservation import FirstPicks, RNG_SEED, balance, two_submitters  # noqa: F401  (fixture imported by name)
from tests.test_identity_service_path import OWNER_ID, world  # noqa: F401  (world is the pytest fixture behind two_submitters)


@pytest.fixture
def enabled_submitters(two_submitters):
    """@description: The shared two-submitter world with the compensation switch on, because the stranded block is part of that feature.
    @param enabled_submitters: Shared offline world.
    @return: Database, repository with the switch on, and service.
    """
    database, repository, service = two_submitters
    repository._compensation_enabled = True  # [test switch]: The production default stays off.
    return database, repository, service

NOW = datetime(2026, 10, 24, 12, 0, 0)  # [fixed clock]: Naive UTC, as the repository stores it.
USER, PEER = 101, 102  # [synthetic users]: The two submitters created by the shared fixture.


def request(repository, user_id: int, key: str, now=NOW):
    """@description: Call the production exchange with the deterministic draw and an explicit clock.
    @param repository: Real repository over the fake.
    @param user_id: Requesting synthetic user.
    @param key: Technical request identity.
    @param now: Clock used by the stranded-reservation check.
    @return: Exchange outcome.
    """
    return repository.exchange(user_id, key, FirstPicks(RNG_SEED), now=now)


def age(database, recommendation_id: int, created_at: datetime) -> None:
    """@description: Move one synthetic reservation to a chosen creation time, as a stored row would have it.
    @param database: Offline fake database.
    @param recommendation_id: Reservation to age.
    @param created_at: Creation time to store.
    @return: None.
    """
    database.state["recommendations"][recommendation_id]["created_at"] = created_at


def test_a_stranded_reservation_blocks_a_new_request_by_the_same_requester(enabled_submitters) -> None:
    """@description: An old, never-claimed reservation stops the requester's next new exchange, which then debits and creates nothing.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = enabled_submitters
    first = request(repository, USER, "k-stranded-1")
    assert first.status is ExchangeStatus.RESERVED
    age(database, first.recommendation_id, NOW - STRANDED_AFTER - timedelta(minutes=1))
    rows_before = len(database.state["recommendations"])
    outcome = request(repository, USER, "k-stranded-2")
    assert outcome.status is ExchangeStatus.PENDING_REVIEW
    assert outcome.recommendation_id is None and outcome.youtube_url is None
    assert len(database.state["recommendations"]) == rows_before
    assert balance(database, USER) == 0


def test_a_reservation_younger_than_the_threshold_does_not_block(enabled_submitters) -> None:
    """@description: A reservation still inside the threshold is a live request; it must not block the requester.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = enabled_submitters
    first = request(repository, USER, "k-young-1")
    age(database, first.recommendation_id, NOW - STRANDED_AFTER + timedelta(minutes=1))
    outcome = request(repository, USER, "k-young-2")
    assert outcome.status is ExchangeStatus.INSUFFICIENT_CREDITS, "the normal path must decide, not the block"


def test_exactly_at_the_threshold_is_not_yet_stranded(enabled_submitters) -> None:
    """@description: The rule is strict: a reservation created exactly at the threshold is not yet classified as stranded.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = enabled_submitters
    first = request(repository, USER, "k-edge-1")
    age(database, first.recommendation_id, NOW - STRANDED_AFTER)
    assert request(repository, USER, "k-edge-2").status is ExchangeStatus.INSUFFICIENT_CREDITS


@pytest.mark.parametrize("state_change", [
    {"status": "sending", "claim_token": "token-a", "claimed_at": NOW},
    {"status": "delivered", "claim_token": "token-b", "claimed_at": NOW, "delivered_at": NOW},
    {"status": "failed", "released_at": NOW},
])
def test_a_claimed_or_released_reservation_never_blocks(enabled_submitters, state_change) -> None:
    """@description: Only a never-claimed, unreleased reservation is stranded; sending, delivered and refunded rows never block.
    @param enabled_submitters: Two settled submitters with verified songs.
    @param state_change: Row fields that represent the claimed or released state.
    @return: None.
    """
    database, repository, _ = enabled_submitters
    first = request(repository, USER, "k-state-1")
    age(database, first.recommendation_id, NOW - STRANDED_AFTER - timedelta(minutes=5))
    database.state["recommendations"][first.recommendation_id].update(state_change)
    assert request(repository, USER, "k-state-2").status is ExchangeStatus.INSUFFICIENT_CREDITS


def test_a_technical_replay_of_the_stranded_key_is_not_blocked(enabled_submitters) -> None:
    """@description: The original order is identified first, so the same key replays it instead of being refused.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = enabled_submitters
    first = request(repository, USER, "k-replay-stranded")
    age(database, first.recommendation_id, NOW - STRANDED_AFTER - timedelta(minutes=1))
    replay = request(repository, USER, "k-replay-stranded")
    assert replay.status is ExchangeStatus.REPLAYED
    assert replay.recommendation_id == first.recommendation_id
    assert balance(database, USER) == 0


def test_another_requester_is_not_blocked(enabled_submitters) -> None:
    """@description: The block is per requester; a different user with a point can still exchange.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = enabled_submitters
    first = request(repository, USER, "k-other-1")
    age(database, first.recommendation_id, NOW - STRANDED_AFTER - timedelta(minutes=1))
    outcome = request(repository, PEER, "k-other-peer")
    assert outcome.status is ExchangeStatus.RESERVED


def test_the_block_is_evaluated_inside_the_writer_gate(enabled_submitters) -> None:
    """@description: The stranded check runs after the writer-gate lock is taken, so it cannot interleave with a claim or a new reservation.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = enabled_submitters
    first = request(repository, USER, "k-gate-1")
    age(database, first.recommendation_id, NOW - STRANDED_AFTER - timedelta(minutes=1))
    database.statements.clear()
    request(repository, USER, "k-gate-2")
    statements = [text for text, _ in database.statements]
    gate = next(index for index, text in enumerate(statements) if "jb2_control" in text and "FOR UPDATE" in text)
    check = next(index for index, text in enumerate(statements) if "AND status='reserved' AND claim_token IS NULL" in text)
    assert gate < check


def test_owner_list_shows_only_stranded_rows_to_the_owner(enabled_submitters) -> None:
    """@description: The Owner sees exactly the stranded reservations, with the minimum fields needed to act, and no other row.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    database, repository, _ = enabled_submitters
    stranded = request(repository, USER, "k-list-1")
    age(database, stranded.recommendation_id, NOW - STRANDED_AFTER - timedelta(minutes=1))
    young = request(repository, PEER, "k-list-2")
    age(database, young.recommendation_id, NOW - STRANDED_AFTER + timedelta(minutes=1))
    rows = repository.owner_pending_reservations(OWNER_ID, NOW)
    assert [row["operation_code"] for row in rows] == [stranded.recommendation_id]
    assert set(rows[0]) == {"operation_code", "requester", "song_id", "reserved_at"}
    assert rows[0]["requester"] == USER


def test_owner_list_refuses_every_other_requester(enabled_submitters) -> None:
    """@description: The list is for the Owner only; any other identity is refused without data.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    _, repository, _ = enabled_submitters
    with pytest.raises(PermissionError, match="OWNER_ONLY"):
        repository.owner_pending_reservations(USER, NOW)


def test_history_labels_a_stranded_reservation_instead_of_failing(enabled_submitters) -> None:
    """@description: The synthetic history shows a reserved row as awaiting confirmation, and never raises a key error for it.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    import asyncio

    import tests.exchange_synthetic_acceptance as entry
    database, repository, _ = enabled_submitters
    first = request(repository, USER, "k-history-1")
    age(database, first.recommendation_id, NOW - STRANDED_AFTER - timedelta(minutes=1))
    accounts = entry.SyntheticAccounts(owner=USER, partner=PEER, zero=None)
    service = entry.SyntheticExchangeService(None, database, repository, "success", accounts)
    text = asyncio.run(service.history(USER))
    assert "待確認" in text and "尚未派送" in text


def test_caller_reports_a_pending_review_without_any_direct_message() -> None:
    """@description: A requester blocked by a stranded reservation receives the awaiting-confirmation reply and no direct message is attempted.
    @return: None.
    """
    from bot.database.exchange_reservation import ExchangeOutcome
    from tests.test_exchange_unknown_commit import FakeRepository, FakeReplier, FakeSender, run_flow
    repository = FakeRepository([ExchangeOutcome(ExchangeStatus.PENDING_REVIEW, None, None)])
    sender, replier = FakeSender(), FakeReplier()
    flow = run_flow(repository, sender, replier)
    assert sender.sent == []
    assert any("尚待確認" in text and "Owner" in text for text in replier.texts)
    assert flow.recommendation_id is None


def test_synthetic_exchange_reports_the_block_with_the_label_and_no_link(enabled_submitters) -> None:
    """@description: The labelled synthetic route tells a blocked requester to wait for the Owner, labels the reply, and never shows a link.
    @param enabled_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    import asyncio
    from unittest.mock import MagicMock

    from bot.services import MusicService
    from tests.exchange_synthetic_acceptance import SYNTHETIC_LABEL
    from tests.test_exchange_unknown_commit import FakeReplier, FakeSender
    database, repository, _ = enabled_submitters
    first = request(repository, USER, "k-synthetic-1")
    real_now = datetime.now(timezone.utc).replace(tzinfo=None)  # [real clock]: The synthetic route has no injected clock, so the aged row must be relative to it.
    age(database, first.recommendation_id, real_now - STRANDED_AFTER - timedelta(minutes=1))
    service = MusicService(MagicMock(), submission_repository=repository, synthetic_exchange=True)
    flow = asyncio.run(service.synthetic_exchange(USER, "k-synthetic-2", FakeSender(), FakeReplier()))
    assert flow.reply_text.startswith(SYNTHETIC_LABEL) and "尚待確認" in flow.reply_text and "Owner" in flow.reply_text
    assert "youtube.com" not in flow.reply_text


def test_production_sql_text_runs_on_a_second_engine() -> None:
    """@description: The production stranded queries are valid and select the same rows on SQLite, a separate engine, so the predicate is not an artefact of the fake.
    @return: None.
    """
    import sqlite3

    from bot.database.pending_review import has_stranded_reservation, owner_stranded_rows
    from tests.test_financial_invariants import SqliteCursor
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE recommendations (recommendation_id INTEGER PRIMARY KEY, discord_user_id INTEGER, song_id INTEGER,
            status TEXT, claim_token TEXT, claimed_at TEXT, released_at TEXT, created_at TEXT);
        CREATE TABLE exchange_incidents (recommendation_id INTEGER, state TEXT);
    """)
    old, young = NOW - STRANDED_AFTER - timedelta(minutes=1), NOW - STRANDED_AFTER + timedelta(minutes=1)
    rows = [(1, USER, 10, "reserved", None, None, None, old), (2, USER, 11, "reserved", None, None, None, young),
            (3, PEER, 12, "sending", "t", old, None, old), (4, PEER, 13, "reserved", None, None, "x", old)]
    connection.executemany("INSERT INTO recommendations VALUES (?,?,?,?,?,?,?,?)",
                           [tuple(str(value) if index == 7 else value for index, value in enumerate(row)) for row in rows])
    cursor = SqliteCursor(connection)
    assert has_stranded_reservation(cursor, USER, NOW) is True
    assert has_stranded_reservation(cursor, PEER, NOW) is False
    assert [row["operation_code"] for row in owner_stranded_rows(cursor, NOW)] == [1]
