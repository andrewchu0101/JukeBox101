"""Real PostgreSQL behaviour of the read-only stuck-exchange view for the Owner (stage B).

Contract: SubmissionRepository.owner_stuck_exchanges(requester_id, now) is for the configured Owner only (PermissionError("OWNER_ONLY") otherwise) and returns one row per
unresolved case with operation_code, requester, delivery_state, reason and waiting_seconds, and no link, token or balance. Reasons: reserved_not_sent (reserved
and never claimed for longer than the sending lease, waiting counted from creation), sending_lease_expired (sending past the lease, counted from the claim),
delivery_result_unknown (uncertain, counted from the claim) and refund_pending (proven unsent, not released, counted from the claim). Delivered, refunded
and still-young reserved or sending cases are not shown. The view writes nothing.
"""

import random
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from bot.database.exchange_delivery_store import DELIVERY_LEASE
from tests.postgres_support import admin_query
from tests.test_postgres_auto_pipeline import ALICE, BOB, OWNER_ID
from tests.test_postgres_exchange_stage_b import NOW, ExchangeWorld
from tests.test_postgres_daily_limit import video

pytestmark = pytest.mark.postgres

CAROL, DAVE = 500003, 500004
SECOND = timedelta(seconds=1)
REQUIRED_KEYS = {"operation_code", "requester", "delivery_state", "reason", "waiting_seconds"}  # [view contract]: Every row carries at least these.
FORBIDDEN_WORDS = ("youtube_url", "claim_token", "credit_balance", "balance", "watch?v=", "token")  # [leak markers]: Never part of an Owner row.
TABLES_THAT_MUST_NOT_CHANGE = ("recommendations", "credit_ledger", "users", "songs")  # [read-only proof]: Compared byte for byte around the view.


class StuckWorld(ExchangeWorld):
    """The two-song world plus Carol and Dave, so four users can each hold one stuck case."""

    def __init__(self, database) -> None:
        """@description: Settle one more song for each extra user.
        @param self: World.
        @param database: Disposable database.
        @return: None.
        """
        super().__init__(database)
        self.share(CAROL, video(1), "discord:carol:1")
        self.share(DAVE, video(2), "discord:dave:2")

    def case(self, user: int, kind: str) -> tuple[int, datetime]:
        """@description: Put one user's exchange into one named state at a known time.
        @param self: World.
        @param user: Requester.
        @param kind: reserved, sending, uncertain, failed (refund pending), delivered or refunded.
        @return: Reservation identifier and the time its waiting is counted from.
        """
        recommendation_id = self.repository.exchange(user, f"stuck-{user}", random.Random(7), NOW).recommendation_id
        if kind == "reserved":
            admin_query(self.database, "UPDATE recommendations SET created_at = %s WHERE recommendation_id = %s", (NOW.replace(tzinfo=timezone.utc), recommendation_id))
            return recommendation_id, NOW  # [pinned creation time]: The database stamps real time; pinning makes the waiting time exact.
        _state, _message, claim = self.repository.claim_delivery(recommendation_id, uuid4().hex, NOW)
        if kind in ("uncertain", "failed", "refunded"):
            self.repository.record_delivery(claim, "uncertain" if kind == "uncertain" else "failed", None, None)
        if kind == "refunded":
            self.repository.refund_undelivered(recommendation_id, NOW)
        if kind == "delivered":
            self.repository.record_delivery(claim, "delivered", 7_000_000_002, NOW)
        return recommendation_id, NOW

    def stuck(self, now: datetime, requester: int = OWNER_ID) -> dict[int, dict]:
        """@description: Ask the Owner view and key the rows by operation code.
        @param self: World.
        @param now: Time the view is taken at.
        @param requester: Identity asking.
        @return: Rows by operation code.
        """
        return {row["operation_code"]: row for row in self.repository.owner_stuck_exchanges(requester, now)}

    def fingerprint(self) -> list[str]:
        """@description: Serialise every table the view must not touch.
        @param self: World.
        @return: One text per table.
        """
        return ["\n".join(row["payload"] for row in admin_query(self.database, f"SELECT row_to_json(t)::text AS payload FROM {table} t ORDER BY 1"))
                for table in TABLES_THAT_MUST_NOT_CHANGE]


@pytest.fixture
def world(pg_database) -> StuckWorld:
    """@description: Provide the four-user world.
    @param pg_database: Disposable database.
    @return: World.
    """
    return StuckWorld(pg_database)


def test_the_four_kinds_of_stuck_case_are_listed_with_reason_and_waiting_time(world) -> None:
    """@description: One case of each kind appears with its reason code and the seconds it has waited.
    @param world: Four-user world.
    @return: None.
    """
    reserved, _ = world.case(ALICE, "reserved")
    sending, _ = world.case(BOB, "sending")
    unknown, _ = world.case(CAROL, "uncertain")
    refund, _ = world.case(DAVE, "failed")
    view_at = NOW + DELIVERY_LEASE + 60 * SECOND
    rows = world.stuck(view_at)
    expected = {reserved: ("reserved", "reserved_not_sent", (view_at - NOW).total_seconds()),
                sending: ("sending", "sending_lease_expired", (view_at - NOW).total_seconds()),
                unknown: ("uncertain", "delivery_result_unknown", (view_at - NOW).total_seconds()),
                refund: ("failed", "refund_pending", (view_at - NOW).total_seconds())}
    assert set(rows) == set(expected)
    assert {code: (row["delivery_state"], row["reason"], row["waiting_seconds"]) for code, row in rows.items()} == expected
    assert {code: row["requester"] for code, row in rows.items()} == {reserved: ALICE, sending: BOB, unknown: CAROL, refund: DAVE}


def test_rows_expose_no_link_token_or_balance(world) -> None:
    """@description: The Owner sees codes, reasons and ages only.
    @param world: Four-user world.
    @return: None.
    """
    world.case(BOB, "sending")
    world.case(CAROL, "uncertain")
    rows = world.repository.owner_stuck_exchanges(OWNER_ID, NOW + DELIVERY_LEASE + 60 * SECOND)
    assert rows and all(REQUIRED_KEYS <= set(row) for row in rows)
    assert not any(word in repr(row).lower() for row in rows for word in FORBIDDEN_WORDS)


@pytest.mark.parametrize("kind", ["delivered", "refunded"])
def test_resolved_cases_are_not_listed(world, kind) -> None:
    """@description: A delivered exchange and a refunded failure need no attention.
    @param world: Four-user world.
    @param kind: Resolved state.
    @return: None.
    """
    world.case(ALICE, kind)
    assert world.stuck(NOW + DELIVERY_LEASE + 3600 * SECOND) == {}


@pytest.mark.parametrize("kind", ["reserved", "sending"])
def test_a_case_younger_than_the_lease_is_not_listed_and_appears_after_it(world, kind) -> None:
    """@description: A live attempt is not a stuck one; the same case is listed once the lease has passed.
    @param world: Four-user world.
    @param kind: Young state.
    @return: None.
    """
    code, since = world.case(ALICE, kind)
    assert world.stuck(since + DELIVERY_LEASE - SECOND) == {}
    assert set(world.stuck(since + DELIVERY_LEASE + SECOND)) == {code}


def test_an_expired_sending_case_turns_into_an_unknown_result_after_the_recovery_sweep(world) -> None:
    """@description: The sweep moves sending to uncertain; the view follows with the new reason and the same waiting basis.
    @param world: Four-user world.
    @return: None.
    """
    code, _ = world.case(ALICE, "sending")
    view_at = NOW + DELIVERY_LEASE + 60 * SECOND
    world.new_repository().recover_interrupted_deliveries(view_at)
    row = world.stuck(view_at)[code]
    assert (row["delivery_state"], row["reason"]) == ("uncertain", "delivery_result_unknown")


def test_a_late_proof_removes_the_case_from_the_view(world) -> None:
    """@description: Proof of delivery or of non-delivery plus refund resolves an uncertain case.
    @param world: Four-user world.
    @return: None.
    """
    first, claim_a = world.reserve_and_claim(user=ALICE, key="proof-a")
    second, claim_b = world.reserve_and_claim(user=BOB, key="proof-b")
    for claim in (claim_a, claim_b):
        world.repository.record_delivery(claim, "uncertain", None, None)
    view_at = NOW + 60 * SECOND
    assert set(world.stuck(view_at)) == {first, second}
    world.repository.record_delivery(claim_a, "delivered", 7_000_000_003, NOW)
    assert set(world.stuck(view_at)) == {second}
    world.repository.record_delivery(claim_b, "failed", None, None)
    assert world.stuck(view_at)[second]["reason"] == "refund_pending"
    world.repository.refund_undelivered(second, NOW)
    assert world.stuck(view_at) == {}


def test_a_refund_that_failed_midway_is_listed_as_refund_pending(world) -> None:
    """@description: The refund transaction fails after a proven non-delivery: the case is visible with its reason and the user is not yet credited.
    @param world: Four-user world.
    @return: None.
    """
    admin_query(world.database, "CREATE FUNCTION jukebox101.fail_release() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                                "IF NEW.released_at IS NOT NULL THEN RAISE EXCEPTION 'injected release failure'; END IF; RETURN NEW; END $$")
    admin_query(world.database, "CREATE TRIGGER fail_release BEFORE UPDATE ON jukebox101.recommendations FOR EACH ROW EXECUTE FUNCTION jukebox101.fail_release()")
    code, claim = world.reserve_and_claim()
    world.repository.record_delivery(claim, "failed", None, None)
    assert [item.status for item in world.repository.retry_pending_refunds(NOW)] == ["pending"]
    assert world.stuck(NOW + 60 * SECOND)[code]["reason"] == "refund_pending" and world.balances()[ALICE] == 0


def test_only_the_configured_owner_may_view_and_everyone_else_is_refused(world) -> None:
    """@description: Ordinary users, including the requester of a stuck case, get PermissionError("OWNER_ONLY") and no data.
    @param world: Four-user world.
    @return: None.
    """
    world.case(ALICE, "uncertain")
    for requester in (ALICE, BOB, 1, 0):
        with pytest.raises(PermissionError, match="OWNER_ONLY"):
            world.repository.owner_stuck_exchanges(requester, NOW + 60 * SECOND)


def test_the_view_writes_nothing_and_changes_no_balance(world) -> None:
    """@description: Opening the view leaves reservations, ledger, balances and songs byte for byte identical.
    @param world: Four-user world.
    @return: None.
    """
    world.case(ALICE, "reserved")
    world.case(BOB, "sending")
    world.case(CAROL, "uncertain")
    world.case(DAVE, "failed")
    before = world.fingerprint()
    for _ in range(2):
        assert len(world.repository.owner_stuck_exchanges(OWNER_ID, NOW + DELIVERY_LEASE + 3600 * SECOND)) == 4
    assert world.fingerprint() == before


def test_an_aware_utc_clock_gives_the_same_answer_as_the_naive_one(world) -> None:
    """@description: The CLI clock is timezone-aware; the repository must not care.
    @param world: Four-user world.
    @return: None.
    """
    code, _ = world.case(CAROL, "uncertain")
    naive = NOW + 120 * SECOND
    assert world.stuck(naive) == world.stuck(naive.replace(tzinfo=timezone.utc)) and code in world.stuck(naive)
