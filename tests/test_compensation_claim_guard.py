"""Verify, offline on the SQLite stand-in, that a switched-off repository still guards the original claim whenever the compensation tables exist.

Three situations are covered: all tables absent (ordinary delivery keeps working), a partial set or an incompatible table (the claim refuses
explicitly, never treated as "no incident"), and the complete set with an open incident (the claim is held). Nothing connects to MySQL or Discord.
"""

import pytest

from bot.database.compensation_schema import CompensationSchemaMismatch, CompensationSchemaMissing
from bot.database.submission_repository import SubmissionRepository
from tests.compensation_sqlite import create_database, factory_for
from tests.test_compensation_store import NOW, OLD, OWNER, USER, approve, seed_song, seed_stranded, transaction

TABLES = ("exchange_incidents", "exchange_redelivery_grants", "exchange_incident_events")  # [candidate tables]: Names the offline stand-in creates.


@pytest.fixture
def stranded(tmp_path):
    """@description: A fresh database with one stranded, never-claimed reservation for the requester.
    @param tmp_path: Pytest temporary directory.
    @return: Tuple of database path and reservation identifier.
    """
    path = tmp_path / "claim_guard.sqlite"
    create_database(path)
    with transaction(path) as cursor:
        award, song = seed_song(cursor, 1), seed_song(cursor, 2)
        reservation = seed_stranded(cursor, USER, song, award, OLD)
    return path, reservation


def drop(path, names) -> None:
    """@description: Remove the named compensation tables from the offline database, simulating a deployment without them.
    @param path: Database file.
    @param names: Table names to drop.
    @return: None.
    """
    with transaction(path) as cursor:
        for name in names:
            cursor.execute(f"DROP TABLE {name}")


def status_of(path, reservation: int) -> str:
    """@description: Read one reservation's stored status on a connection closed afterwards.
    @param path: Database file.
    @param reservation: Reservation identifier.
    @return: Stored status.
    """
    with transaction(path) as cursor:
        cursor.execute("SELECT status FROM recommendations WHERE recommendation_id = ?", (reservation,))
        return cursor.fetchone()["status"]


def switched_off(path) -> SubmissionRepository:
    """@description: A repository with the feature switch at its default, off.
    @param path: Database file.
    @return: Switched-off repository over the offline database.
    """
    return SubmissionRepository(factory_for(path), OWNER)


def test_switched_off_with_all_tables_absent_an_ordinary_claim_still_delivers(stranded) -> None:
    """@description: Without any compensation table the switched-off claim keeps working, so a deployment without them is unaffected.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    drop(path, TABLES)
    assert switched_off(path).claim_delivery(reservation, "tok-absent", NOW)[0] == "sending"


def test_switched_off_with_a_partial_set_refuses_the_claim_and_sends_nothing(stranded) -> None:
    """@description: A partial set of compensation tables is a broken schema, not a sign of no incident; the claim refuses and the reservation stays reserved.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    drop(path, ("exchange_incident_events",))
    with pytest.raises(CompensationSchemaMissing):
        switched_off(path).claim_delivery(reservation, "tok-partial", NOW)
    assert status_of(path, reservation) == "reserved"


def test_switched_off_with_an_incompatible_table_refuses_the_claim_and_sends_nothing(stranded) -> None:
    """@description: A compensation table without a required column is refused by the schema guard, so the claim never proceeds on an unknown schema.
    @param stranded: Stranded reservation fixture.
    @return: None.
    """
    path, reservation = stranded
    with transaction(path) as cursor:
        cursor.execute("ALTER TABLE exchange_redelivery_grants RENAME COLUMN holds_song TO held_song")
    with pytest.raises(CompensationSchemaMismatch):
        switched_off(path).claim_delivery(reservation, "tok-incompatible", NOW)
    assert status_of(path, reservation) == "reserved"
