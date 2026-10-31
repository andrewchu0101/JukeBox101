"""Real PostgreSQL tests for Owner review through the Discord Owner group and for the ported settlement path.

Reuses the offline end-to-end helpers (command tree, buttons, fixed clock) with the repository on a disposable
PostgreSQL 18 database. Settlement is production-disabled; the award tests enable it on the test database only, to
prove the ported ledger/balance/claim atomicity and the one-award-per-version rule.
"""

from functools import partial

import pytest

from bot.database.connection import open_connection
from bot.database.submission_repository import SubmissionRepository
from bot.ledger_integrity import STATUS_PASS, evaluate_ledger_integrity
from bot.owner_review import OwnerReviewService
from bot.review import ReviewStatus, StaleReviewVersion
from bot.reward_candidates import RewardCandidateStatus
from bot.version_identity import VersionIdentityStatus
from tests.postgres_support import admin_query
from tests.test_owner_e2e_acceptance import (CITATION_A, OWNER_ID, SOURCE_INTERACTION_ID, TOKEN_TTL, Clock, approve_through_discord,
                                             command_group, confirm_version_through_discord, owner_command, press_confirm, seed,
                                             seed_metadata_unavailable, submitter_of)

pytestmark = pytest.mark.postgres


@pytest.fixture
def pg_world(pg_database):
    """@description: Build the real repository and review service over the PostgreSQL app role.
    @param pg_database: Disposable database.
    @return: Tuple of database, repository and service.
    """
    repository = SubmissionRepository(partial(open_connection, pg_database.app), OWNER_ID)
    return pg_database, repository, OwnerReviewService(repository, OWNER_ID, Clock(), token_ttl=TOKEN_TTL)


def money(database) -> dict:
    """@description: Read every table a reward could change.
    @param database: Disposable database.
    @return: Ledger rows, balances, claims and settlements.
    """
    return {"ledger": admin_query(database, "SELECT discord_user_id, credit_delta, reason FROM credit_ledger ORDER BY ledger_id"),
            "balances": admin_query(database, "SELECT discord_user_id, credit_balance FROM users ORDER BY discord_user_id"),
            "claims": admin_query(database, "SELECT version_id, operation_id, discord_user_id FROM jb2_claims ORDER BY version_id"),
            "settlements": admin_query(database, "SELECT operation_id FROM jb2_settlements ORDER BY operation_id")}


def test_approve_and_reject_through_discord_persist_review_and_move_no_money(pg_world):
    """@description: Owner approve/reject via the guild-scoped group update review state only; settlement does not pay.
    @param pg_world: PostgreSQL repository and service.
    @return: None.
    """
    database, repository, service = pg_world
    seed(repository, 1)
    seed(repository, 2)
    group = command_group(lambda: service)
    before = money(database)
    approve_through_discord(group, 1)
    press_confirm(owner_command(group, "reject", operation="op-2", citation=CITATION_A, reason="不是歌曲"))
    assert service.view(OWNER_ID, "op-1").review.status is ReviewStatus.APPROVED
    assert service.view(OWNER_ID, "op-2").review.status is ReviewStatus.REJECTED
    assert repository.settle("op-1").status is RewardCandidateStatus.WAITING_IDENTITY
    assert repository.settle("op-2").status is RewardCandidateStatus.INELIGIBLE
    assert money(database) == before
    statuses = admin_query(database, "SELECT video_id, review_status FROM jb2_videos ORDER BY video_id")
    assert [row["review_status"] for row in statuses] == ["approved", "rejected"]


def test_version_confirmation_with_payouts_off_blocks_settlement(pg_world):
    """@description: A confirmed version makes the operation READY, but the disabled switch refuses any payout.
    @param pg_world: PostgreSQL repository and service.
    @return: None.
    """
    database, repository, service = pg_world
    seed(repository, 1)
    group = command_group(lambda: service)
    approve_through_discord(group, 1)
    confirm_version_through_discord(group, "orig-A", CITATION_A, SOURCE_INTERACTION_ID)
    assert service.view(OWNER_ID, "op-1").identity.status is VersionIdentityStatus.CONFIRMED
    before = money(database)
    with pytest.raises(PermissionError):
        repository.settle("op-1")
    assert money(database) == before and before["ledger"] == []


def test_settlement_awards_one_nickel_atomically_once_per_version(pg_world):
    """@description: With payouts enabled on this test database only, the first READY submission of a version gets exactly
    one ledger row, one claim and a +1 balance; repeating settle or settling a later submission of the same version pays nothing.
    @param pg_world: PostgreSQL repository and service.
    @return: None.
    """
    database, repository, service = pg_world
    seed(repository, 1)
    seed(repository, 2)
    group = command_group(lambda: service)
    for number in (1, 2):
        approve_through_discord(group, number)
        confirm_version_through_discord(group, "orig-A", CITATION_A, SOURCE_INTERACTION_ID + number, number=number)
    admin_query(database, "UPDATE jb2_control SET settlement_enabled = TRUE")
    assert repository.settle("op-1").status is RewardCandidateStatus.AWARDED
    repository.settle("op-1")
    repository.settle("op-2")
    state = money(database)
    assert state["ledger"] == [{"discord_user_id": submitter_of(1), "credit_delta": 1, "reason": "submission_award"}]
    assert {"discord_user_id": submitter_of(1), "credit_balance": 1} in state["balances"]
    assert {"discord_user_id": submitter_of(2), "credit_balance": 0} in state["balances"]
    assert [claim["operation_id"] for claim in state["claims"]] == ["op-1"]
    users = admin_query(database, "SELECT discord_user_id, credit_balance FROM users")
    ledger = admin_query(database, "SELECT ledger_id, discord_user_id, credit_delta, reason, awarded_song_id FROM credit_ledger")
    claims = admin_query(database, "SELECT version_id, ledger_id, discord_user_id FROM jb2_claims")
    assert evaluate_ledger_integrity(users, ledger, claims).status == STATUS_PASS


def test_owner_reads_pending_operations_through_the_app_role_and_a_non_owner_is_refused_first(pg_world):
    """@description: Replaces the MySQL container tests: the Owner lists pending intake via the real service and app role;
    a non-Owner is refused before any repository read.
    @param pg_world: PostgreSQL repository and service.
    @return: None.
    """
    database, repository, service = pg_world
    seed(repository, 1)
    seed(repository, 2)
    page = service.list_pending(OWNER_ID)
    assert [item.operation_id for item in page.items] == ["op-1", "op-2"]
    with pytest.raises(PermissionError):
        service.list_pending(OWNER_ID + 1)


def test_pending_rows_carry_the_submitter_and_the_known_duration_for_the_case_card(pg_world):
    """@description: The Owner queue shows who submitted each case and its full-video duration, or none when the duration is unknown.
    @param pg_world: PostgreSQL repository and service.
    @return: None.
    """
    database, repository, service = pg_world
    seed(repository, 1, 180)
    seed_metadata_unavailable(repository, 2)
    first, second = service.list_pending(OWNER_ID).items
    assert (first.submitter_id, first.duration_seconds) == (submitter_of(1), 180)
    assert (second.submitter_id, second.duration_seconds) == (submitter_of(2), None)


def test_a_duration_confirmation_is_checked_against_its_revision_inside_the_transaction(pg_world):
    """@description: Two duration previews bound to the same revision cannot both apply: the second confirmation meets the advanced revision in the database transaction.
    @param pg_world: PostgreSQL repository and service.
    @return: None.
    """
    database, repository, service = pg_world
    seed_metadata_unavailable(repository, 1)
    item = service.list_pending(OWNER_ID).items[0]
    first = service.preview_duration(OWNER_ID, item.operation_id, 120, "https://example.test/a", "first", expected_revision=item.revision)
    second = service.preview_duration(OWNER_ID, item.operation_id, 130, "https://example.test/b", "second", expected_revision=item.revision)
    service.confirm(OWNER_ID, first.token_id)
    with pytest.raises(StaleReviewVersion):
        service.confirm(OWNER_ID, second.token_id)


def test_a_duration_preview_for_the_displayed_revision_is_refused_once_the_case_moved_on(pg_world):
    """@description: A card that displayed an older revision cannot obtain a duration preview, because the service binds the preview to the revision it is given.
    @param pg_world: PostgreSQL repository and service.
    @return: None.
    """
    database, repository, service = pg_world
    seed_metadata_unavailable(repository, 1)
    item = service.list_pending(OWNER_ID).items[0]
    done = service.preview_duration(OWNER_ID, item.operation_id, 120, "https://example.test/a", "first", expected_revision=item.revision)
    service.confirm(OWNER_ID, done.token_id)
    with pytest.raises(StaleReviewVersion):
        service.preview_duration(OWNER_ID, item.operation_id, 130, "https://example.test/b", "late", expected_revision=item.revision)


def test_the_pending_page_reads_only_the_rows_it_returns(pg_world, monkeypatch):
    """@description: A one-row page transfers at most one row more than it shows, however many cases wait, and keeps acceptance order and cursors.
    @param pg_world: PostgreSQL repository and service.
    @param monkeypatch: Spy on the queue query.
    @return: None.
    """
    from bot.database import review_persistence
    database, repository, service = pg_world
    for number in range(1, 41):
        seed(repository, number)
    transferred = []
    original = review_persistence.fetch_all

    def spy(cursor, sql, parameters=()):
        """@description: Record how many rows the queue query hands back.
        @param cursor: Transaction cursor.
        @param sql: Statement.
        @param parameters: Bound values.
        @return: The rows, unchanged.
        """
        rows = original(cursor, sql, parameters)
        if "FROM jb2_events e JOIN jb2_videos" in sql:
            transferred.append(len(rows))
        return rows

    monkeypatch.setattr(review_persistence, "fetch_all", spy)
    first = service.list_pending(OWNER_ID, limit=1)
    second = service.list_pending(OWNER_ID, after_sequence=first.next_after_sequence, limit=1)
    assert [item.operation_id for item in (*first.items, *second.items)] == ["op-1", "op-2"]
    assert first.next_after_sequence == first.items[0].accepted_sequence
    assert max(transferred) <= 2
