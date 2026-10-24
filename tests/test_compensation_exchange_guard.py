"""Verify offline that the exchange path keeps the compensation protections when the switch is off, as long as the compensation tables exist.

Three situations are covered: the complete set (the stranded block, the open-grant block, the grant-held songs and the same-key replay apply
even with the switch off), all tables absent (an ordinary exchange is unaffected and reads no compensation table), and a partial set, an
incompatible schema or a failing catalogue probe (the exchange refuses explicitly and reserves nothing). Owner compensation operations stay
refused while the switch is off. Runs on the SQL-protocol fake; nothing connects to MySQL or Discord.
"""

from datetime import datetime, timedelta

import pytest

from bot.database.compensation_schema import CompensationDisabled, CompensationSchemaMismatch, CompensationSchemaMissing
from bot.database.exchange_reservation import ExchangeStatus
from bot.database.pending_review import STRANDED_AFTER
from tests.test_exchange_reservation import FirstPicks, RNG_SEED, balance, two_submitters  # noqa: F401  (fixture imported by name)
from tests.test_identity_service_path import OWNER_ID, world  # noqa: F401  (world is the pytest fixture behind two_submitters)

NOW = datetime(2026, 10, 24, 12, 0, 0)  # [fixed clock]: Naive UTC, as the repository stores it.
USER, PEER = 101, 102  # [synthetic users]: The two submitters created by the shared fixture.
NAMES = ("exchange_incidents", "exchange_redelivery_grants", "exchange_incident_events")  # [compensation names]: Substrings the statements must not carry.


def request(repository, user_id: int, key: str, now=NOW):
    """@description: Call the production exchange with the deterministic draw and an explicit clock.
    @param repository: Switched-off repository over the fake.
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


def strand(database, repository) -> int:
    """@description: Create one reservation for the requester and age it past the stranded threshold, so it blocks the requester's next exchange.
    @param database: Offline fake database.
    @param repository: Switched-off repository over the fake.
    @return: Reservation identifier.
    """
    first = request(repository, USER, "guard-first")
    assert first.status is ExchangeStatus.RESERVED
    age(database, first.recommendation_id, NOW - STRANDED_AFTER - timedelta(minutes=1))
    return first.recommendation_id


def compensation_statements(database) -> list:
    """@description: List the recorded statements that name a compensation table.
    @param database: Offline fake database.
    @return: Statement texts naming a compensation table.
    """
    return [statement for statement, _ in database.statements if any(name in statement for name in NAMES)]


def test_switched_off_a_stranded_reservation_still_blocks_a_new_request_when_the_tables_exist(two_submitters) -> None:
    """@description: With the complete table set, an old unclaimed reservation stops the requester's next new exchange even though the switch is off.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    database, repository, _ = two_submitters
    strand(database, repository)
    rows_before = len(database.state["recommendations"])
    outcome = request(repository, USER, "guard-second")
    assert outcome.status is ExchangeStatus.PENDING_REVIEW
    assert len(database.state["recommendations"]) == rows_before


def test_switched_off_a_technical_replay_of_the_stranded_key_is_still_identified_not_blocked(two_submitters) -> None:
    """@description: A same-key retry of the original order returns that order even while the block applies, and debits nothing again.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    database, repository, _ = two_submitters
    original = request(repository, USER, "guard-replayed")
    age(database, original.recommendation_id, NOW - STRANDED_AFTER - timedelta(minutes=1))
    replay = request(repository, USER, "guard-replayed")
    assert replay.status is ExchangeStatus.REPLAYED
    assert replay.recommendation_id == original.recommendation_id
    assert balance(database, USER) == 0


def test_switched_off_an_open_not_sent_grant_blocks_a_new_request(two_submitters) -> None:
    """@description: An open redelivery grant, not yet closed by the Owner, blocks the requester's next new exchange with the switch off.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    database, repository, _ = two_submitters
    database.grants.append({"discord_user_id": USER, "song_id": 2, "holds_song": 1, "closed_at": None})
    assert request(repository, USER, "guard-grant-open").status is ExchangeStatus.PENDING_REVIEW


def test_switched_off_an_uncertain_grant_keeps_its_song_out_of_the_redraw(two_submitters) -> None:
    """@description: A grant closed as not delivered keeps its song held, so the only candidate is excluded and the redraw reports no candidate.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    database, repository, _ = two_submitters
    database.grants.append({"discord_user_id": USER, "song_id": 2, "holds_song": 1, "closed_at": NOW})
    assert request(repository, USER, "guard-held").status is ExchangeStatus.NO_CANDIDATE


def test_switched_off_a_released_grant_frees_its_song_for_a_new_reservation(two_submitters) -> None:
    """@description: A not-sent grant closed with no further action releases its song, so the requester can reserve it again.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    database, repository, _ = two_submitters
    database.grants.append({"discord_user_id": USER, "song_id": 2, "holds_song": 0, "closed_at": NOW})
    assert request(repository, USER, "guard-released").status is ExchangeStatus.RESERVED


def test_switched_off_with_all_tables_absent_an_ordinary_exchange_is_unaffected_and_reads_no_compensation_table(two_submitters) -> None:
    """@description: Without the compensation tables the exchange works normally, and no statement names a compensation table.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    database, repository, _ = two_submitters
    database.compensation_tables = set()
    database.statements.clear()
    assert request(repository, USER, "guard-absent").status is ExchangeStatus.RESERVED
    assert compensation_statements(database) == []


def test_switched_off_a_partial_table_set_refuses_the_exchange_and_reserves_nothing(two_submitters) -> None:
    """@description: A partial set of compensation tables is a broken schema; the exchange refuses explicitly and writes no reservation.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    database, repository, _ = two_submitters
    database.compensation_tables = {"exchange_incidents"}
    rows_before = len(database.state["recommendations"])
    with pytest.raises(CompensationSchemaMissing):
        request(repository, USER, "guard-partial")
    assert len(database.state["recommendations"]) == rows_before


def test_switched_off_an_incompatible_schema_refuses_the_exchange_and_reserves_nothing(two_submitters) -> None:
    """@description: A compensation table without a required column is refused by the schema guard before any reservation is written.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    database, repository, _ = two_submitters
    database.compensation_hidden_columns = {"exchange_redelivery_grants": {"holds_song"}}
    rows_before = len(database.state["recommendations"])
    with pytest.raises(CompensationSchemaMismatch):
        request(repository, USER, "guard-incompatible")
    assert len(database.state["recommendations"]) == rows_before


def test_switched_off_a_failing_catalogue_probe_refuses_the_exchange_and_is_never_read_as_absent(two_submitters) -> None:
    """@description: A failed presence probe propagates; the exchange does not proceed as if no compensation table existed.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    database, repository, _ = two_submitters
    database.compensation_probe_fault = True
    with pytest.raises(RuntimeError, match="synthetic catalogue failure"):
        request(repository, USER, "guard-probe")


def test_switched_off_owner_compensation_operations_stay_refused_even_with_the_tables_present(two_submitters) -> None:
    """@description: The switch still governs new compensation business: with the switch off, Owner operations refuse before any work, even though the guard is active.
    @param two_submitters: Two settled submitters over the fake, switch off by default.
    @return: None.
    """
    _, repository, _ = two_submitters
    with pytest.raises(CompensationDisabled):
        repository.owner_pending_incidents(OWNER_ID, NOW)
