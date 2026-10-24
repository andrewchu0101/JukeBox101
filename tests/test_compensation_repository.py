"""Verify the compensation repository boundary, the free-redelivery delivery path, and the refund sweep, on the offline SQLite stand-in.

Owner-only methods refuse every other identity before any database work. The delivery path sends at most one direct message through a
fake adapter, and a settled refund is never swept a second time. The sweep reports an unexplained row explicitly instead of skipping it.
"""

import logging
from datetime import datetime, timedelta

import pytest

from bot.compensation_delivery import deliver_grant
from bot.database.submission_repository import SubmissionRepository
from bot.exchange_delivery import DmKind, DmResult
from tests.compensation_sqlite import create_database, factory_for
from tests.test_compensation_store import (
    NOW, OLD, OWNER, USER, PEER, approve, balance, count, seed_song, seed_stranded, transaction, assert_reconciled,
)

pytestmark = []


class RecordingSender:
    """Record every direct-message attempt and return a scripted classified result. Never contacts Discord."""

    def __init__(self, result: DmResult) -> None:
        """@description: Script the single result this sender returns.
        @param result: Classified outcome for every attempt.
        @return: None.
        """
        self.result = result
        self.sent: list = []

    async def send(self, discord_user_id: int, text: str) -> DmResult:
        """@description: Record the attempt and return the scripted result.
        @param discord_user_id: Recipient.
        @param text: Message body.
        @return: Scripted result.
        """
        self.sent.append((discord_user_id, text))
        return self.result


@pytest.fixture
def repository(tmp_path):
    """@description: A production repository over the offline SQLite stand-in, with one stranded reservation for the user.
    @param tmp_path: Pytest temporary directory.
    @return: Tuple of database path, repository and reservation identifier.
    """
    path = tmp_path / "repository.sqlite"
    create_database(path)
    with transaction(path) as cursor:
        award, song = seed_song(cursor, 1), seed_song(cursor, 2)
        reservation = seed_stranded(cursor, USER, song, award, OLD)
    return path, SubmissionRepository(factory_for(path), OWNER, compensation_enabled=True), reservation


def test_every_owner_method_refuses_another_identity_before_any_write(repository) -> None:
    """@description: Approval, settlement, decline, disposition, the open list and claim expiry refuse non-Owners without touching the database.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    path, repo, reservation = repository
    calls = [
        lambda: repo.owner_approve_incident(USER, reservation, "system_fault_confirmed", NOW),
        lambda: repo.owner_record_not_system_fault(USER, reservation, "not_system_fault", NOW),
        lambda: repo.owner_settle_incident(USER, 1, True, NOW),
        lambda: repo.owner_close_non_fault_refund(USER, 1, True, NOW),
        lambda: repo.owner_record_disposition(USER, 1, "no_further_action", NOW),
        lambda: repo.owner_open_grants(USER, NOW),
        lambda: repo.owner_expire_grant_claims(USER, NOW),
    ]
    for call in calls:
        with pytest.raises(PermissionError, match="OWNER_ONLY"):
            call()
    assert count(path, "SELECT COUNT(*) FROM exchange_incidents") == 0


def test_the_owner_approves_settles_and_the_sender_delivers_one_free_song(repository) -> None:
    """@description: The Owner's approval and settlement refund one point and grant one redelivery; the approved dispatch sends one direct message and the song stays held.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    import asyncio

    from bot.database.compensation_store import held_grant_song_ids, pending_block
    path, repo, reservation = repository
    incident = repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"]
    settled = repo.owner_settle_incident(OWNER, incident, True, NOW)
    assert settled["outcome"] == "settled" and settled["grant_id"] is not None
    sender = RecordingSender(DmResult.delivered(990001))
    state = asyncio.run(deliver_grant(repo, sender, settled["grant_id"], NOW, "dispatch-1"))
    assert state == "sent" and len(sender.sent) == 1
    assert sender.sent[0][0] == USER and "youtube.com" in sender.sent[0][1]
    assert balance(path, USER) == 1
    with transaction(path) as cursor:
        assert pending_block(cursor, USER, NOW) is False
        assert held_grant_song_ids(cursor, USER)
    assert_reconciled(path, USER)


def test_a_repeated_dispatch_sends_nothing_again(repository) -> None:
    """@description: Dispatching an already sent grant again sends no second direct message.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    import asyncio
    path, repo, reservation = repository
    incident = repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"]
    grant = repo.owner_settle_incident(OWNER, incident, True, NOW)["grant_id"]
    sender = RecordingSender(DmResult.delivered(990001))
    asyncio.run(deliver_grant(repo, sender, grant, NOW, "dispatch-1"))
    again = asyncio.run(deliver_grant(repo, sender, grant, NOW, "dispatch-2"))
    assert len(sender.sent) == 1 and again in ("sent", "claimed", "not_sent", "uncertain")


def test_a_not_sent_dispatch_is_not_retried_automatically(repository) -> None:
    """@description: A proven non-delivery stays pending. A later dispatch attempt sends nothing and the Owner must close it.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    import asyncio
    path, repo, reservation = repository
    incident = repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"]
    grant = repo.owner_settle_incident(OWNER, incident, True, NOW)["grant_id"]
    failing = RecordingSender(DmResult.not_sent("refused"))
    assert asyncio.run(deliver_grant(repo, failing, grant, NOW, "dispatch-1")) == "not_sent"
    later = RecordingSender(DmResult.delivered(990002))
    assert asyncio.run(deliver_grant(repo, later, grant, NOW, "dispatch-2")) == "not_sent"
    assert later.sent == []


def test_an_unknown_dispatch_is_never_resent(repository) -> None:
    """@description: An unknown delivery result is recorded as uncertain and is never sent again automatically.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    import asyncio
    path, repo, reservation = repository
    incident = repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"]
    grant = repo.owner_settle_incident(OWNER, incident, True, NOW)["grant_id"]
    unknown = RecordingSender(DmResult.unknown("timeout"))
    assert asyncio.run(deliver_grant(repo, unknown, grant, NOW, "dispatch-1")) == "uncertain"
    retry = RecordingSender(DmResult.delivered(990003))
    assert asyncio.run(deliver_grant(repo, retry, grant, NOW, "dispatch-2")) == "uncertain"
    assert retry.sent == []


def test_the_refund_sweep_never_refunds_a_settled_reservation_twice(repository) -> None:
    """@description: After settlement the reservation is released, so the periodic refund sweep finds nothing to refund for it.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    path, repo, reservation = repository
    incident = repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"]
    repo.owner_settle_incident(OWNER, incident, True, NOW)
    assert repo.retry_pending_refunds(NOW) == []
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 1


def test_the_refund_sweep_reports_an_unexplained_row_explicitly(tmp_path, caplog) -> None:
    """@description: A failed, unreleased row without its original debit is reported as a contradiction and logged; it is neither refunded nor silently skipped.
    @param tmp_path: Pytest temporary directory.
    @param caplog: Log capture.
    @return: None.
    """
    path = tmp_path / "sweep.sqlite"
    create_database(path)
    with transaction(path) as cursor:
        award, song = seed_song(cursor, 1), seed_song(cursor, 2)
        reservation = seed_stranded(cursor, USER, song, award, OLD)
        cursor.execute("DELETE FROM credit_ledger WHERE recommendation_id = ? AND reason = 'exchange_debit'", (reservation,))
        cursor.execute("UPDATE recommendations SET status = 'failed' WHERE recommendation_id = ?", (reservation,))
    repo = SubmissionRepository(factory_for(path), OWNER)
    with caplog.at_level(logging.ERROR):
        outcomes = repo.retry_pending_refunds(NOW)
    assert [outcome.status for outcome in outcomes] == ["contradiction"]
    assert "contradiction" in caplog.text.lower()
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 0
    assert count(path, "SELECT COUNT(*) FROM recommendations WHERE released_at IS NOT NULL") == 0


def test_the_owner_open_list_is_bounded_and_private(repository) -> None:
    """@description: The Owner's open-grant list contains only the grant identifiers, state and times, and no other identity's rows.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    path, repo, reservation = repository
    incident = repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"]
    repo.owner_settle_incident(OWNER, incident, True, NOW)
    rows = repo.owner_open_grants(OWNER, NOW)
    assert len(rows) == 1 and rows[0]["requester"] == USER
    assert set(rows[0]) == {"grant_id", "requester", "song_id", "state", "granted_at", "overdue"}


def test_the_owner_summary_and_not_system_fault_list_refuse_other_identities(repository) -> None:
    """@description: The per-requester summary and the not-system-fault list are Owner-only and refuse every other identity before any read.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    _, repo, _ = repository
    for call in (lambda: repo.owner_user_incident_summary(USER, USER), lambda: repo.owner_pending_incidents(USER, NOW)):
        with pytest.raises(PermissionError, match="OWNER_ONLY"):
            call()


def test_the_owner_summary_shows_only_the_requested_requesters_incidents(repository) -> None:
    """@description: The Owner's per-requester summary contains that requester's incidents and nothing belonging to anyone else.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    path, repo, reservation = repository
    repo.owner_settle_incident(OWNER, repo.owner_approve_incident(OWNER, reservation, "system_fault_confirmed", NOW)["incident_id"], True, NOW)
    summary = repo.owner_user_incident_summary(OWNER, USER)
    assert [item["recommendation_id"] for item in summary] == [reservation]
    assert repo.owner_user_incident_summary(OWNER, PEER) == []


def test_the_owner_closes_a_confirmed_undelivered_non_fault_order_once_and_the_sweep_adds_nothing(repository) -> None:
    """@description: Through the Owner boundary, a confirmed-undelivered non-fault order is refunded once and closed; a repeat and the periodic refund sweep add nothing, and no grant exists.
    @param repository: Stranded reservation fixture.
    @return: None.
    """
    path, repo, reservation = repository
    incident = repo.owner_record_not_system_fault(OWNER, reservation, "not_system_fault", NOW)["incident_id"]
    assert repo.owner_close_non_fault_refund(OWNER, incident, True, NOW)["outcome"] == "closed_refunded"
    assert repo.owner_close_non_fault_refund(OWNER, incident, True, NOW)["outcome"] == "already_closed"
    assert repo.retry_pending_refunds(NOW) == []
    assert repo.owner_pending_incidents(OWNER, NOW) == []
    assert count(path, "SELECT COUNT(*) FROM credit_ledger WHERE reason = 'delivery_refund'") == 1
    assert count(path, "SELECT COUNT(*) FROM exchange_redelivery_grants") == 0
