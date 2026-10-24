"""Verify the claim boundary for compensated reservations, the feature switch, and the schema-missing refusal, offline on the SQLite stand-in.

The original order and its free redelivery are never delivered together: a reservation with any incident is held for the Owner at the claim
itself, inside the writer-gate transaction, so a direct call, a same-key replay and a race all meet the same rule. With the switch off the
general exchange and claim paths touch none of the compensation tables. With the switch on and a missing table, every compensation path
refuses explicitly instead of continuing with money.
"""

import asyncio
import random
import sqlite3
from datetime import timedelta

import pytest

from bot.compensation_delivery import deliver_grant
from bot.database import compensation_store as store
from bot.database.compensation_schema import CompensationSchemaMissing, CompensationDisabled
from bot.database.exchange_reservation import ExchangeOutcome, ExchangeStatus
from bot.database.submission_repository import SubmissionRepository
from bot.exchange_delivery import DeliveryStatus, DmResult, deliver_recommendation, exchange_and_deliver
from tests.compensation_sqlite import create_database, factory_for
from tests.test_compensation_repository import RecordingSender
from tests.test_compensation_store import NOW, OLD, OWNER, USER, approve, balance, count, seed_song, seed_stranded, settle, transaction
from tests.test_exchange_unknown_commit import FakeReplier
from tests.test_exchange_reservation import two_submitters  # noqa: F401  (fixture imported by name)
from tests.test_identity_service_path import world  # noqa: F401  (world is the pytest fixture behind two_submitters)

URL = "https://www.youtube.com/watch?v=JBX00000002"  # [synthetic link]: The fixture song's fixed pattern.


class ReplayingRepository:
    """Answer the exchange with the original reservation, as a same-key technical replay does; every other operation goes to the real repository."""

    def __init__(self, real, reservation: int) -> None:
        """@description: Bind the real repository and the reservation the replay returns.
        @param real: Real repository over the offline stand-in.
        @param reservation: Original reservation identifier.
        @return: None.
        """
        self._real = real
        self._reservation = reservation

    def exchange(self, discord_user_id: int, request_key: str, rng, now=None) -> ExchangeOutcome:
        """@description: Return the replayed original outcome, without any debit.
        @param discord_user_id: Requester.
        @param request_key: Technical request identity.
        @param rng: Unused.
        @param now: Unused clock.
        @return: Replayed outcome.
        """
        return ExchangeOutcome(ExchangeStatus.REPLAYED, self._reservation, URL)

    def __getattr__(self, name: str):
        """@description: Delegate every other operation to the real repository.
        @param name: Operation name.
        @return: The real repository's attribute.
        """
        return getattr(self._real, name)


@pytest.fixture
def gate_world(tmp_path):
    """@description: A switched-on repository over one stranded reservation for the requester, with its song.
    @param tmp_path: Pytest temporary directory.
    @return: Tuple of database path, enabled repository, reservation and song identifiers.
    """
    path = tmp_path / "gate.sqlite"
    create_database(path)
    with transaction(path) as cursor:
        award, song = seed_song(cursor, 1), seed_song(cursor, 2)
        reservation = seed_stranded(cursor, USER, song, award, OLD)
    return path, SubmissionRepository(factory_for(path), OWNER, compensation_enabled=True), reservation, song


def status_of(path, reservation: int) -> str:
    """@description: Read one reservation's status on a connection that is closed afterwards.
    @param path: Database file.
    @param reservation: Reservation identifier.
    @return: Stored status.
    """
    with transaction(path) as cursor:
        cursor.execute("SELECT status FROM recommendations WHERE recommendation_id = ?", (reservation,))
        return cursor.fetchone()["status"]


def replay(repo, path, reservation: int, sender) -> object:
    """@description: Run the production caller for a same-key replay of the original reservation.
    @param repo: Enabled repository.
    @param path: Database file (unused, kept for readability at call sites).
    @param reservation: Original reservation.
    @param sender: Direct-message adapter.
    @return: Flow result.
    """
    return asyncio.run(exchange_and_deliver(ReplayingRepository(repo, reservation), sender, FakeReplier(), USER, "k-replay",
                                            random.Random(1), NOW, new_token=lambda: "tok-replay"))


def test_a_not_system_fault_original_is_not_claimed_or_sent_by_a_same_key_replay(gate_world) -> None:
    """@description: After the Owner declines, a same-key replay of the original order sends nothing, and the order is shown as held, never as delivered.
    @param gate_world: Enabled repository and one stranded reservation.
    @return: None.
    """
    path, repo, reservation, _ = gate_world
    with transaction(path) as cursor:
        store.record_not_system_fault(cursor, reservation, OWNER, "not_system_fault", NOW)
    sender = RecordingSender(DmResult.delivered(990001))
    flow = replay(repo, path, reservation, sender)
    assert sender.sent == [] and flow.delivery_status is DeliveryStatus.HELD
    assert "待 Owner" in flow.reply_text and status_of(path, reservation) == "reserved"


def test_an_approved_original_is_held_at_the_claim_until_the_owner_settles(gate_world) -> None:
    """@description: While the incident is only approved, the original order is held at the claim: no direct message and no status change.
    @param gate_world: Enabled repository and one stranded reservation.
    @return: None.
    """
    path, repo, reservation, _ = gate_world
    with transaction(path) as cursor:
        approve_result = store.approve_incident(cursor, reservation, OWNER, "system_fault_confirmed", NOW)
    assert approve_result["state"] == "approved"
    sender = RecordingSender(DmResult.delivered(990001))
    assert repo.claim_delivery(reservation, "tok-direct", NOW)[0] == "held_for_review"
    assert asyncio.run(deliver_recommendation(repo, sender, reservation, NOW, "tok-direct")).status is DeliveryStatus.HELD
    assert sender.sent == [] and status_of(path, reservation) == "reserved"


def test_an_ordinary_reservation_without_an_incident_is_still_claimed_and_delivered_once(gate_world) -> None:
    """@description: The normal path is unchanged: a reservation with no incident is claimed and delivered by a same-key replay exactly once.
    @param gate_world: Enabled repository and one stranded reservation.
    @return: None.
    """
    path, repo, reservation, _ = gate_world
    sender = RecordingSender(DmResult.delivered(990001))
    flow = replay(repo, path, reservation, sender)
    assert flow.delivery_status is DeliveryStatus.DELIVERED and len(sender.sent) == 1
    assert status_of(path, reservation) == "delivered"


def test_a_settled_original_is_refused_at_the_claim_and_the_grant_is_the_only_delivery(gate_world) -> None:
    """@description: After settlement the original order cannot be claimed; the free redelivery is the one and only message for that incident.
    @param gate_world: Enabled repository and one stranded reservation.
    @return: None.
    """
    path, repo, reservation, _ = gate_world
    incident = repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"]
    grant = repo.owner_settle_incident(OWNER, incident, True, NOW)["grant_id"]
    sender = RecordingSender(DmResult.delivered(990001))
    flow = replay(repo, path, reservation, sender)
    assert sender.sent == [] and flow.delivery_status is not DeliveryStatus.DELIVERED
    assert asyncio.run(deliver_grant(repo, sender, grant, NOW, "tok-grant")) == "sent"
    assert len(sender.sent) == 1 and "免費補送" in sender.sent[0][1]


def test_a_claim_that_commits_first_wins_and_the_later_approval_is_refused(gate_world) -> None:
    """@description: When the original claim commits first, the Owner's later approval is refused as not stranded; nothing is refunded and the order is sent once.
    @param gate_world: Enabled repository and one stranded reservation.
    @return: None.
    """
    from bot.database.compensation_store import ApprovalRefused
    path, repo, reservation, _ = gate_world
    sender = RecordingSender(DmResult.delivered(990001))
    assert asyncio.run(deliver_recommendation(repo, sender, reservation, NOW, "tok-first")).status is DeliveryStatus.DELIVERED
    with pytest.raises(ApprovalRefused, match="NOT_STRANDED"):
        repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)
    assert len(sender.sent) == 1 and balance(path, USER) == 0
    assert count(path, "SELECT COUNT(*) FROM exchange_incidents") == 0
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 0


def test_an_approval_that_commits_first_holds_the_claim_and_leaves_an_auditable_event(gate_world) -> None:
    """@description: When the approval commits first, a later claim is held and the hold is recorded as an auditable event; the Owner's settlement then decides.
    @param gate_world: Enabled repository and one stranded reservation.
    @return: None.
    """
    path, repo, reservation, _ = gate_world
    incident = repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"]
    sender = RecordingSender(DmResult.delivered(990001))
    assert asyncio.run(deliver_recommendation(repo, sender, reservation, NOW, "tok-held")).status is DeliveryStatus.HELD
    assert sender.sent == []
    assert count(path, f"SELECT COUNT(*) FROM exchange_incident_events WHERE incident_id = {incident} AND action = 'claim_held'") == 1
    assert repo.owner_settle_incident(OWNER, incident, True, NOW)["outcome"] == "settled"


def test_a_settlement_before_the_claim_wins_and_the_claim_is_refused(gate_world) -> None:
    """@description: When the settlement commits first, the original claim is refused by its status, and the requester gets exactly the refund and the grant.
    @param gate_world: Enabled repository and one stranded reservation.
    @return: None.
    """
    path, repo, reservation, _ = gate_world
    incident = repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"]
    repo.owner_settle_incident(OWNER, incident, True, NOW)
    sender = RecordingSender(DmResult.delivered(990001))
    assert repo.claim_delivery(reservation, "tok-late", NOW)[0] == "failed"
    assert sender.sent == [] and balance(path, USER) == 1


def test_the_switch_is_off_by_default_and_owner_paths_refuse_explicitly(tmp_path) -> None:
    """@description: With the switch off, every compensation entry point refuses with an explicit disabled error before any database work.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "off.sqlite"
    create_database(path)
    repo = SubmissionRepository(factory_for(path), OWNER)
    with pytest.raises(CompensationDisabled):
        repo.owner_open_grants(OWNER, NOW)
    with pytest.raises(CompensationDisabled):
        repo.owner_approve_incident(OWNER, 1, "system_fault_confirmed", NOW)


def test_with_the_switch_on_and_a_missing_table_the_claim_refuses_without_sending(tmp_path) -> None:
    """@description: Enabling the feature on a schema without the compensation tables fails loudly at the claim; nothing is sent and nothing changes.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "missing.sqlite"
    create_database(path)
    with transaction(path) as cursor:
        award, song = seed_song(cursor, 1), seed_song(cursor, 2)
        reservation = seed_stranded(cursor, USER, song, award, OLD)
        cursor.execute("DROP TABLE exchange_incidents")
    repo = SubmissionRepository(factory_for(path), OWNER, compensation_enabled=True)
    sender = RecordingSender(DmResult.delivered(990001))
    with pytest.raises(CompensationSchemaMissing):
        asyncio.run(deliver_recommendation(repo, sender, reservation, NOW, "tok-missing"))
    assert sender.sent == [] and status_of(path, reservation) == "reserved"


def test_with_the_switch_on_and_a_missing_table_owner_reads_refuse_explicitly(tmp_path) -> None:
    """@description: Owner reads on a schema without the compensation tables refuse with the explicit schema error, not an unrelated database error.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "missing_owner.sqlite"
    create_database(path)
    with transaction(path) as cursor:
        cursor.execute("DROP TABLE exchange_redelivery_grants")
    repo = SubmissionRepository(factory_for(path), OWNER, compensation_enabled=True)
    with pytest.raises(CompensationSchemaMissing):
        repo.owner_open_grants(OWNER, NOW)


def test_the_general_exchange_path_issues_no_compensation_statement_when_the_switch_is_off(two_submitters) -> None:
    """@description: With the switch off, an ordinary exchange and its claim never touch the compensation tables, so a deployment without them keeps working.
    @param two_submitters: Two settled submitters with verified songs.
    @return: None.
    """
    from tests.test_exchange_reservation import exchange
    database, repository, _ = two_submitters
    database.statements.clear()
    outcome = exchange(repository, 101, "k-off-switch")
    repository.claim_delivery(outcome.recommendation_id, "tok-off", NOW)
    texts = " ".join(statement for statement, _ in database.statements)
    assert "exchange_incidents" not in texts and "exchange_redelivery_grants" not in texts




def test_with_the_switch_off_and_no_compensation_tables_an_ordinary_claim_still_delivers(tmp_path) -> None:
    """@description: A deployment without the compensation tables and with the switch off claims and delivers an ordinary reservation exactly as before.
    @param tmp_path: Pytest temporary directory.
    @return: None.
    """
    path = tmp_path / "compat.sqlite"
    create_database(path)
    with transaction(path) as cursor:
        award, song = seed_song(cursor, 1), seed_song(cursor, 2)
        reservation = seed_stranded(cursor, USER, song, award, OLD)
        for table in ("exchange_incidents", "exchange_redelivery_grants", "exchange_incident_events"):
            cursor.execute(f"DROP TABLE {table}")
    repo = SubmissionRepository(factory_for(path), OWNER)  # [switch off by default]: No compensation table is read.
    sender = RecordingSender(DmResult.delivered(990001))
    assert asyncio.run(deliver_recommendation(repo, sender, reservation, NOW, "tok-compat")).status is DeliveryStatus.DELIVERED
    assert len(sender.sent) == 1 and status_of(path, reservation) == "delivered"
