"""Exercise the compensation state machine (M1-M8, M10, M11, M12) on the explicitly isolated MySQL target, through the production repository.

Opt-in only: every test is skipped unless JUKEBOX_RUN_EXCHANGE_MYSQL_ISOLATED=1 and the isolated target is configured. Each run requires the three
candidate tables from docs/compensation-candidate-004.sql to exist; the fixture refuses otherwise instead of creating them.
Concurrent cases run on threads, each with its own connection, inside this process. They are not separate operating-system processes, and no
lock wait is observed directly; the writer gate is a database row lock, so separate connections exercise it. M9 (missing table or constraint)
is intentionally absent: it runs only on the offline SQLite stand-in, never against the isolated MySQL target.
Fixture rows of the synthetic identities are removed after each test, as the other isolated tests do, before the shared fixture clears the rest.
"""

import json
import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymysql
import pytest

from bot.database.compensation_schema import require_schema
from bot.database.compensation_store import ApprovalRefused
from bot.database.exchange_reservation import ExchangeStatus
from bot.database.submission_codec import decode_contract
from bot.database.submission_repository import SubmissionRepository
from bot.owner_review import OwnerReviewService
from bot.review import ReviewSnapshot
from tests.isolated_mysql_target import ISOLATION_SWITCH, open_verified_connection
from tests.test_exchange_mysql_integration import (
    USER_A, USER_HIGH, USER_LOW, count_rows, isolated, verified_transaction,
)
from tests.test_exchange_repository_mysql import (  # noqa: F401  (fixtures and helpers imported by name)
    OPERATIONS, assert_repository_state, require_empty_baseline, seed_repository_candidate,
)
from tests.test_identity_service_path import OWNER_ID, START, video_of

pytestmark = [pytest.mark.mysql, pytest.mark.skipif(
    os.environ.get(ISOLATION_SWITCH) != "1", reason="Requires explicitly authorized isolated MySQL target")]

COMPENSATION_TABLES = ("exchange_incident_events", "exchange_redelivery_grants", "exchange_incidents")  # [candidate tables]: Cleared children first.
FOOTPRINT_QUERIES = (
    "SELECT COUNT(*) AS total FROM exchange_incidents",
    "SELECT COUNT(*) AS total FROM exchange_redelivery_grants",
    "SELECT COUNT(*) AS total FROM exchange_incident_events",
    "SELECT COUNT(*) AS total FROM credit_ledger WHERE reason = 'delivery_refund'",
    "SELECT COUNT(*) AS total FROM credit_ledger WHERE credit_delta = 0",
)  # [compensation footprint]: Any change here after a refused or replayed request is a failure.


def later() -> datetime:
    """@description: Give the Owner a clock far enough after reservation creation that the reservation counts as stranded, without editing stored rows.
    @return: Naive UTC time one day ahead of the wall clock.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1)


def read_row(target, sql: str, params: tuple) -> dict:
    """@description: Read one row on a verified connection after the statement, so tests see committed state only.
    @param target: Verified isolated destination.
    @param sql: Read-only statement.
    @param params: Bound values.
    @return: The first row as a dictionary.
    """
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()


def compensation_footprint(target) -> tuple:
    """@description: Count the compensation rows and the refund and zero-amount ledger rows of the whole isolated fixture.
    @param target: Verified isolated destination.
    @return: Tuple of counts in FOOTPRINT_QUERIES order.
    """
    return tuple(count_rows(target, sql, ()) for sql in FOOTPRINT_QUERIES)


EVIDENCE_ENV = "COMPENSATION_EVIDENCE_DIR"  # [evidence folder]: Set by the launcher to a new folder; the fixture refuses to clean without it.
USER_RANGE = (USER_LOW, USER_HIGH)  # [fixture users]: The only identity range any cleanup may touch.
VIDEO_IDS = tuple(video_of(number).video_id for number in (1, 2))  # [fixture videos]: Exact synthetic identifiers.
ROW_STEPS = (  # [cleanup order]: Compensation children first, then the shared fixture order; each entry is (table, WHERE clause, parameters).
    ("exchange_incident_events", "incident_id IN (SELECT incident_id FROM exchange_incidents WHERE discord_user_id BETWEEN %s AND %s)", USER_RANGE),
    ("exchange_redelivery_grants", "discord_user_id BETWEEN %s AND %s", USER_RANGE),
    ("exchange_incidents", "discord_user_id BETWEEN %s AND %s", USER_RANGE),
    ("jb2_claims", "discord_user_id BETWEEN %s AND %s", USER_RANGE),
    ("credit_ledger", "discord_user_id BETWEEN %s AND %s", USER_RANGE),
    ("recommendations", "discord_user_id BETWEEN %s AND %s", USER_RANGE),
    ("submissions", "discord_user_id BETWEEN %s AND %s", USER_RANGE),
    ("jb2_settlements", "operation_id IN (%s,%s)", OPERATIONS),
    ("jb2_receipts", "operation_id IN (%s,%s)", OPERATIONS),
    ("jb2_events", "operation_id IN (%s,%s)", OPERATIONS),
    ("jb2_reminders", "video_id IN (%s,%s)", VIDEO_IDS),
    ("jb2_review_history", "video_id IN (%s,%s)", VIDEO_IDS),
    ("jb2_evidence", "video_id IN (%s,%s)", VIDEO_IDS),
    ("jb2_videos", "video_id IN (%s,%s)", VIDEO_IDS),
    None,  # [jb2_decisions]: Matched by decoding each row; see decision_step.
    ("users", "discord_user_id BETWEEN %s AND %s", USER_RANGE),
    ("songs", "platform='youtube' AND platform_track_id IN (%s,%s)", VIDEO_IDS),
    ("jb2_control", "singleton_id=1 AND schema_version='2b2-b1'", ()),
)


def evidence_folder() -> Path:
    """@description: Name the evidence folder the launcher created for this run; no row is written unless it exists.
    @return: Existing evidence folder.
    """
    value = os.environ.get(EVIDENCE_ENV)
    if not value or not Path(value).is_dir():
        raise RuntimeError("COMPENSATION_EVIDENCE_DIR must name an existing evidence folder before any fixture row is written")
    return Path(value)


def row_step(cursor, table: str, where: str, params: tuple) -> dict:
    """@description: Read the rows one cleanup statement will delete, so the plan records them before anything is removed.
    @param cursor: Cursor inside the cleanup transaction.
    @param table: Table name from the fixed cleanup order.
    @param where: Fixed WHERE clause for this table.
    @param params: Bound values of the clause, from the fixture scope.
    @return: Plan entry with its planned rows, the delete statement and the post-delete check.
    """
    cursor.execute(f"SELECT * FROM {table} WHERE {where}", params)
    rows = cursor.fetchall()
    return {"table": table, "where": where, "planned_rows": len(rows), "rows": rows,
            "deletes": [[f"DELETE FROM {table} WHERE {where}", list(params)]],
            "post_check": [f"SELECT COUNT(*) AS total FROM {table} WHERE {where}", list(params)]}


def decision_step(cursor) -> dict:
    """@description: Plan the removal of only those audited decisions whose subject is a fixture video and whose actor is the Owner; any other decision refuses the whole cleanup.
    @param cursor: Cursor inside the cleanup transaction.
    @return: Plan entry with one delete per matched decision.
    """
    cursor.execute("SELECT decision_id, actor_id, result_json FROM jb2_decisions")
    matched = []
    for row in cursor.fetchall():
        review = decode_contract(row["result_json"], ReviewSnapshot)
        assert row["actor_id"] == OWNER_ID and review.video.video_id in VIDEO_IDS, "unrelated jb2_decisions row: cleanup refused"
        matched.append(row)
    identifiers = [row["decision_id"] for row in matched]
    if identifiers:
        post_check = [f"SELECT COUNT(*) AS total FROM jb2_decisions WHERE decision_id IN ({','.join(['%s'] * len(identifiers))})", identifiers]
    else:
        post_check = ["SELECT 0 AS total", []]
    return {"table": "jb2_decisions", "where": "decision_id IN (matched)", "planned_rows": len(matched), "rows": matched,
            "deletes": [["DELETE FROM jb2_decisions WHERE decision_id=%s", [identifier]] for identifier in identifiers],
            "post_check": post_check}


def plan_cleanup(cursor, identity: dict) -> dict:
    """@description: Build the complete cleanup plan from read-only selects, so every row to be removed is known before the first delete.
    @param cursor: Cursor inside the cleanup transaction.
    @param identity: Test identity and fixture scope recorded with the plan.
    @return: Plan with identity and ordered steps.
    """
    steps = [decision_step(cursor) if entry is None else row_step(cursor, *entry) for entry in ROW_STEPS]
    return {"identity": identity, "steps": steps}


def execute_cleanup(cursor, plan: dict) -> list:
    """@description: Run the planned deletes in order, asserting each count matches the plan and nothing planned remains afterwards.
    @param cursor: Cursor inside the cleanup transaction.
    @param plan: Plan returned by plan_cleanup.
    @return: Per-table planned, deleted and remaining counts.
    """
    results = []
    for step in plan["steps"]:
        deleted = 0
        for sql, params in step["deletes"]:
            cursor.execute(sql, params)
            deleted += cursor.rowcount
        assert deleted == step["planned_rows"], f"cleanup of {step['table']} deleted {deleted}, planned {step['planned_rows']}"
        cursor.execute(step["post_check"][0], step["post_check"][1])
        remaining = cursor.fetchone()["total"]
        assert remaining == 0, f"{step['table']} still holds {remaining} planned rows"
        results.append({"table": step["table"], "planned_rows": step["planned_rows"], "deleted_rows": deleted, "remaining_rows": remaining})
    return results


def write_json_new(path: Path, payload: dict) -> None:
    """@description: Create one evidence file that must not exist yet, flush it to disk and read it back before the caller may continue.
    @param path: New file path inside the evidence folder.
    @param payload: JSON-compatible evidence.
    @return: None; a missing folder, an existing file or a read-back difference raises.
    """
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(text), "evidence read-back differs from what was written"


def guarded_cleanup(connection, evidence_dir: Path, test_name: str, identity: dict) -> list:
    """@description: Remove the fixture's rows only after their plan is saved and read back; any failure before the commit rolls everything back, and the result is recorded after the commit.
    @param connection: Verified connection; the transaction is owned here.
    @param evidence_dir: Existing evidence folder for this run.
    @param test_name: Name of the test whose rows are removed, used in the file names.
    @param identity: Test identity and fixture scope recorded with the plan.
    @return: Per-table results of the committed cleanup.
    """
    plan_path = evidence_dir / f"cleanup-plan-{test_name}.json"
    result_path = evidence_dir / f"cleanup-result-{test_name}.json"
    connection.begin()
    try:
        with connection.cursor() as cursor:
            plan = plan_cleanup(cursor, identity)
            write_json_new(plan_path, plan)  # [before any delete]: A failed save raises here, so nothing below runs.
            results = execute_cleanup(cursor, plan)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    write_json_new(result_path, {"identity": identity, "committed": True, "steps": results})
    return results


def require_no_compensation_rows(target) -> None:
    """@description: Fail closed when the candidate tables are absent or already hold rows, so a test never inherits unknown state.
    @param target: Verified isolated destination.
    @return: None.
    """
    with verified_transaction(target) as connection:
        with connection.cursor() as cursor:
            require_schema(cursor)
    assert compensation_footprint(target)[:3] == (0, 0, 0), "compensation tables must be empty before the test"


def guarded_steps(isolated, request, compensation_enabled: bool, seed_candidates: bool):
    """@description: Build a verified repository over the isolated target with the evidence folder and an empty baseline proven first; the rows of this test are removed through the guarded cleanup.
    @param isolated: Verified isolated target.
    @param request: Pytest request, used for the test name.
    @param compensation_enabled: The repository's feature switch; the guard applies with the tables present either way.
    @param seed_candidates: Whether to seed the funded pair before the test runs.
    @return: Generator yielding the verified target, the repository and the Owner review service.
    """
    evidence_dir = evidence_folder()
    require_empty_baseline(isolated)
    require_no_compensation_rows(isolated)
    settings, pin = isolated
    repository = SubmissionRepository(lambda: open_verified_connection(settings, pin), OWNER_ID, compensation_enabled=compensation_enabled)
    service = OwnerReviewService(repository, OWNER_ID, lambda: START, token_ttl=timedelta(minutes=5))
    with verified_transaction(isolated) as connection:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO jb2_control (singleton_id,schema_version,snapshot_ready,"
                           "legacy_writers_quiesced,settlement_enabled) VALUES (1,'2b2-b1',1,1,1)")
    identity = {"test": request.node.name, "server_uuid": pin.server_uuid, "database": "jukebox101_test",
                "user_range": list(USER_RANGE), "operations": list(OPERATIONS), "videos": list(VIDEO_IDS)}
    try:
        world = (isolated, repository, service)
        if seed_candidates:
            for number in (1, 2):
                seed_repository_candidate(world, number)
        yield world
    finally:
        connection = open_verified_connection(settings, pin)
        try:
            guarded_cleanup(connection, evidence_dir, request.node.name, identity)
        finally:
            connection.close()
        require_empty_baseline(isolated)


@pytest.fixture
def guarded_world(isolated, request):
    """@description: A switched-on guarded world without seeded candidates, for cases that seed their own state.
    @param isolated: Verified isolated target.
    @param request: Pytest request.
    @return: Tuple of verified target, switched-on repository and Owner review service.
    """
    yield from guarded_steps(isolated, request, compensation_enabled=True, seed_candidates=False)


@pytest.fixture
def guarded_funded_pair(isolated, request):
    """@description: A switched-off guarded world with the funded synthetic pair, used by the unknown-commit cases to run with the compensation tables present, as in production.
    @param isolated: Verified isolated target.
    @param request: Pytest request.
    @return: Tuple of verified target, switched-off repository and Owner review service.
    """
    yield from guarded_steps(isolated, request, compensation_enabled=False, seed_candidates=True)


@pytest.fixture
def compensation_world(guarded_world):
    """@description: The guarded world with the requester funded and one peer candidate seeded through production boundaries.
    @param guarded_world: Guarded isolated world.
    @return: Tuple of verified target and switched-on repository.
    """
    for number in (1, 2):
        seed_repository_candidate(guarded_world, number)
    return guarded_world[0], guarded_world[1]


def reserve(repository, request_key: str) -> int:
    """@description: Reserve one peer song for the requester through the production exchange, which must succeed here.
    @param repository: Repository under test.
    @param request_key: Durable technical request identity.
    @return: Reservation identifier.
    """
    outcome = repository.exchange(USER_A, request_key, random.Random(1))
    assert outcome.status is ExchangeStatus.RESERVED
    return outcome.recommendation_id


def approve_and_settle(repository, reservation: int, redelivery_approved: bool) -> dict:
    """@description: Confirm a system fault for one stranded reservation and settle it with the given redelivery decision.
    @param repository: Switched-on repository.
    @param reservation: Reservation to compensate.
    @param redelivery_approved: The Owner's separate redelivery decision.
    @return: Settlement outcome.
    """
    incident = repository.owner_approve_incident(OWNER_ID, reservation, "m-case", later())["incident_id"]
    return repository.owner_settle_incident(OWNER_ID, incident, redelivery_approved, later())


def run_concurrently(attempts: list) -> list:
    """@description: Start each attempt on its own thread and connection at the same moment, and return every result in submission order or raise the first error.
    @param attempts: Callables taking no arguments, one per simultaneous request.
    @return: Results in the order of attempts.
    """
    barrier = threading.Barrier(len(attempts))

    def released(attempt):
        """@description: Wait until every attempt is ready, then run this one.
        @param attempt: The callable to run.
        @return: The attempt's result.
        """
        barrier.wait(timeout=10)
        return attempt()

    with ThreadPoolExecutor(max_workers=len(attempts)) as workers:
        return [future.result(timeout=30) for future in [workers.submit(released, attempt) for attempt in attempts]]


def test_m1_two_concurrent_settlements_refund_once_and_issue_one_grant(compensation_world):
    """@description: Two simultaneous settlements of one incident refund the original debit once and issue one free grant.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    reservation = reserve(repository, "m1-reserve")
    incident = repository.owner_approve_incident(OWNER_ID, reservation, "m1", later())["incident_id"]
    results = run_concurrently([lambda: repository.owner_settle_incident(OWNER_ID, incident, True, later())] * 2)
    assert sorted(result["outcome"] for result in results) == ["already_settled", "settled"]
    assert count_rows(target, "SELECT COUNT(*) AS total FROM credit_ledger WHERE reason='delivery_refund'", ()) == 1
    assert count_rows(target, "SELECT COUNT(*) AS total FROM exchange_redelivery_grants WHERE incident_id=%s", (incident,)) == 1
    assert_repository_state((target, None, None), 1, 1)


def test_m2_two_concurrent_claims_of_one_grant_send_at_most_once(compensation_world):
    """@description: Two simultaneous claims of one open grant produce exactly one claimed result.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    _, repository = compensation_world
    grant = approve_and_settle(repository, reserve(repository, "m2-reserve"), True)["grant_id"]
    results = run_concurrently([
        lambda: repository.claim_grant(grant, "m2-token-a", later()),
        lambda: repository.claim_grant(grant, "m2-token-b", later()),
    ])
    assert [state for state, _ in results] == ["claimed", "claimed"]  # [second caller]: The row already reads claimed and sends nothing.
    assert sum(1 for _, payload in results if payload is not None) == 1


def test_m3_refund_only_closes_without_a_grant_and_releases_the_block(compensation_world):
    """@description: Declining redelivery still refunds once, closes the incident, issues no grant, and lets the requester exchange again.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    outcome = approve_and_settle(repository, reserve(repository, "m3-reserve"), False)
    assert outcome["outcome"] == "settled" and outcome["grant_id"] is None
    closed = read_row(target, "SELECT closure, closed_at FROM exchange_incidents WHERE discord_user_id=%s", (USER_A,))
    assert closed["closure"] == "refund_only" and closed["closed_at"] is not None
    assert compensation_footprint(target)[1] == 0
    again = repository.exchange(USER_A, "m3-after", random.Random(1))
    assert again.status is ExchangeStatus.RESERVED
    assert_repository_state((target, None, None), 2, 0)


def test_m4_a_repeated_settlement_refunds_and_grants_nothing_more(compensation_world):
    """@description: Settling an incident again with the other redelivery decision changes neither the refund nor the grant, and approving again returns the existing incident.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    reservation = reserve(repository, "m4-reserve")
    incident = repository.owner_approve_incident(OWNER_ID, reservation, "m4", later())["incident_id"]
    repository.owner_settle_incident(OWNER_ID, incident, False, later())
    again = repository.owner_settle_incident(OWNER_ID, incident, True, later())
    assert again["outcome"] == "already_settled" and again["grant_id"] is None
    reapproved = repository.owner_approve_incident(OWNER_ID, reservation, "m4", later())
    assert reapproved["created"] is False
    assert compensation_footprint(target) == (1, 0, 2, 1, 0)  # [events]: One approval and one settlement; the repeats wrote nothing.
    assert_repository_state((target, None, None), 1, 1)


def test_m5_a_claim_that_commits_first_refuses_a_later_approval(compensation_world):
    """@description: When the send claim commits first, the Owner's later approval is refused and nothing is compensated.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    reservation = reserve(repository, "m5a-reserve")
    assert repository.claim_delivery(reservation, "m5a-claim", later())[0] == "sending"
    with pytest.raises(ApprovalRefused, match="NOT_STRANDED"):
        repository.owner_approve_incident(OWNER_ID, reservation, "m5a", later())
    assert compensation_footprint(target)[:3] == (0, 0, 0)


def test_m5_an_approval_that_commits_first_holds_the_claim_and_is_audited(compensation_world):
    """@description: When the approval commits first, the later claim is held for review, sends nothing, and leaves a claim_held event.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    reservation = reserve(repository, "m5b-reserve")
    repository.owner_approve_incident(OWNER_ID, reservation, "m5b", later())
    status, message_id, claim = repository.claim_delivery(reservation, "m5b-claim", later())
    assert (status, message_id, claim) == ("held_for_review", None, None)
    assert read_row(target, "SELECT status FROM recommendations WHERE recommendation_id=%s", (reservation,))["status"] == "reserved"
    assert count_rows(target, "SELECT COUNT(*) AS total FROM exchange_incident_events WHERE action='claim_held'", ()) == 1


def test_m6_a_settlement_first_refuses_the_claim_without_sending(compensation_world):
    """@description: A settled original is released as failed, so a claim after settlement reports failed and sends nothing.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    _, repository = compensation_world
    reservation = reserve(repository, "m6-reserve")
    approve_and_settle(repository, reservation, True)
    assert repository.claim_delivery(reservation, "m6-late", later()) == ("failed", None, None)


def test_m6_a_claim_first_leaves_no_incident_for_the_owner_to_void(compensation_world):
    """@description: Once the claim has committed, no approval can attach to the reservation, so no approved incident exists that a void could act on.
    The void branch of settlement is therefore unreachable through the production claim path; this test records that reachability rather than exercising it.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    reservation = reserve(repository, "m6b-reserve")
    repository.claim_delivery(reservation, "m6b-claim", later())
    with pytest.raises(ApprovalRefused):
        repository.owner_approve_incident(OWNER_ID, reservation, "m6b", later())
    assert compensation_footprint(target)[0] == 0


def test_m7_a_not_sent_grant_blocks_until_the_owner_closes_it_and_then_releases_the_song(compensation_world):
    """@description: An open not-sent grant blocks new exchanges; closing it with no further action releases the song for a new reservation.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    grant = approve_and_settle(repository, reserve(repository, "m7a-reserve"), True)["grant_id"]
    assert repository.claim_grant(grant, "m7a-claim", later())[0] == "claimed"
    assert repository.record_grant_delivery(grant, "m7a-claim", "not_sent", None, later()) == "not_sent"
    assert repository.exchange(USER_A, "m7a-blocked", random.Random(1)).status is ExchangeStatus.PENDING_REVIEW
    assert repository.owner_record_disposition(OWNER_ID, grant, "no_further_action", later()) == {"grant_id": grant, "holds_song": False}
    assert repository.exchange(USER_A, "m7a-after", random.Random(1)).status is ExchangeStatus.RESERVED


def test_m7_an_uncertain_grant_keeps_its_song_after_the_owner_closes_it(compensation_world):
    """@description: An unknown delivery stays held after the Owner closes it as not delivered, so the same song cannot be redrawn.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    grant = approve_and_settle(repository, reserve(repository, "m7b-reserve"), True)["grant_id"]
    repository.claim_grant(grant, "m7b-claim", later())
    assert repository.record_grant_delivery(grant, "m7b-claim", "unknown", None, later()) == "uncertain"
    assert repository.exchange(USER_A, "m7b-blocked", random.Random(1)).status is ExchangeStatus.PENDING_REVIEW
    assert repository.owner_record_disposition(OWNER_ID, grant, "confirmed_not_delivered", later())["holds_song"] is True
    assert repository.exchange(USER_A, "m7b-after", random.Random(1)).status is ExchangeStatus.NO_CANDIDATE
    assert count_rows(target, "SELECT COUNT(*) AS total FROM exchange_redelivery_grants WHERE holds_song=1 AND owner_disposition='confirmed_not_delivered'", ()) == 1


def test_m8_with_the_switch_off_and_the_tables_present_an_ordinary_exchange_and_claim_still_work(compensation_world):
    """@description: With the switch off, an ordinary reservation and claim succeed and write no compensation row, even with the tables present.
    @param compensation_world: Switched-on repository, whose settings are reused for a switched-off one.
    @return: None.
    """
    target, _ = compensation_world
    settings, pin = target
    ordinary = SubmissionRepository(lambda: open_verified_connection(settings, pin), OWNER_ID)
    reservation = reserve(ordinary, "m8-reserve")
    assert ordinary.claim_delivery(reservation, "m8-claim", later())[0] == "sending"
    assert compensation_footprint(target)[:3] == (0, 0, 0)


def test_m8_a_switched_off_claim_still_holds_an_open_incident(compensation_world):
    """@description: An original order with an open incident stays held after the switch is turned off, while the compensation tables exist (D-2).
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    settings, pin = target
    reservation = reserve(repository, "m8b-reserve")
    repository.owner_record_not_system_fault(OWNER_ID, reservation, "m8b", later())
    ordinary = SubmissionRepository(lambda: open_verified_connection(settings, pin), OWNER_ID)
    assert ordinary.claim_delivery(reservation, "m8b-claim", later())[0] == "held_for_review"


def test_m10_engine_refuses_a_duplicate_reservation_and_an_invalid_closure(compensation_world):
    """@description: The engine itself rejects a second incident for one reservation (unique key, 1062) and an inconsistent closure (named CHECK, 3819), and a rollback leaves no partial row.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    incident = repository.owner_record_not_system_fault(OWNER_ID, reserve(repository, "m10-reserve"), "m10", later())["incident_id"]
    settings, pin = target
    connection = open_verified_connection(settings, pin)
    try:
        connection.begin()
        with connection.cursor() as cursor:
            with pytest.raises(pymysql.MySQLError) as duplicate:
                cursor.execute("INSERT INTO exchange_incidents (recommendation_id, debit_ledger_id, discord_user_id, song_id, reason_code, approved_by, "
                               "approved_at, state) SELECT recommendation_id, debit_ledger_id, discord_user_id, song_id, 'm10', approved_by, approved_at, "
                               "'approved' FROM exchange_incidents WHERE incident_id = %s", (incident,))
            assert duplicate.value.args[0] == 1062
            with pytest.raises(pymysql.MySQLError) as invalid:
                cursor.execute("UPDATE exchange_incidents SET state = 'non_fault_refunded' WHERE incident_id = %s", (incident,))
            assert invalid.value.args[0] == 3819
    finally:
        connection.rollback()
        connection.close()
    assert read_row(target, "SELECT state FROM exchange_incidents WHERE incident_id=%s", (incident,))["state"] == "not_system_fault"
    assert compensation_footprint(target)[0] == 1


def test_m10_engine_refuses_a_second_incident_with_the_same_refund_ledger(compensation_world):
    """@description: A second reservation cannot be marked as refunded with the ledger row of another refund (unique key on the refund ledger).
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    first = approve_and_settle(repository, reserve(repository, "m10b-first"), False)
    second = repository.owner_approve_incident(OWNER_ID, reserve(repository, "m10b-second"), "m10b", later())["incident_id"]
    settings, pin = target
    connection = open_verified_connection(settings, pin)
    try:
        connection.begin()
        with connection.cursor() as cursor:
            with pytest.raises(pymysql.MySQLError) as duplicate:
                cursor.execute("UPDATE exchange_incidents SET refund_status='refunded_once', refund_ledger_id=%s WHERE incident_id=%s",
                               (first["refund_ledger_id"], second))
            assert duplicate.value.args[0] == 1062
    finally:
        connection.rollback()
        connection.close()
    assert read_row(target, "SELECT refund_status FROM exchange_incidents WHERE incident_id=%s", (second,))["refund_status"] == "none"


def test_m11_concurrent_non_fault_closures_refund_once_with_no_grant_and_no_zero_row(compensation_world):
    """@description: Two simultaneous D1 closures of one not-system-fault incident refund once; the second reports already closed; no grant or zero-amount row exists.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    reservation = reserve(repository, "m11-reserve")
    incident = repository.owner_record_not_system_fault(OWNER_ID, reservation, "m11", later())["incident_id"]
    results = run_concurrently([lambda: repository.owner_close_non_fault_refund(OWNER_ID, incident, True, later())] * 2)
    assert sorted(result["outcome"] for result in results) == ["already_closed", "closed_refunded"]
    assert compensation_footprint(target) == (1, 0, 2, 1, 0)
    closed = read_row(target, "SELECT state, refund_status, redelivery_decision, closure FROM exchange_incidents WHERE incident_id=%s", (incident,))
    assert closed == {"state": "non_fault_refunded", "refund_status": "refunded_once", "redelivery_decision": "not_approved", "closure": "non_fault_refund"}
    assert read_row(target, "SELECT status, released_at FROM recommendations WHERE recommendation_id=%s", (reservation,))["status"] == "failed"
    assert_repository_state((target, None, None), 1, 1)


def test_m11_a_later_closure_repeats_nothing(compensation_world):
    """@description: A sequential repeat of the closure, after the first succeeded, returns already closed and refunds nothing.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    incident = repository.owner_record_not_system_fault(OWNER_ID, reserve(repository, "m11b-reserve"), "m11b", later())["incident_id"]
    repository.owner_close_non_fault_refund(OWNER_ID, incident, True, later())
    assert repository.owner_close_non_fault_refund(OWNER_ID, incident, True, later())["outcome"] == "already_closed"
    assert compensation_footprint(target)[3] == 1


def test_d1_refuses_closure_without_confirmed_non_delivery_and_writes_nothing(compensation_world):
    """@description: Without the Owner's explicit confirmation of non-delivery, the D1 closure refuses before any write.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    incident = repository.owner_record_not_system_fault(OWNER_ID, reserve(repository, "d1-reserve"), "d1", later())["incident_id"]
    before = compensation_footprint(target)
    with pytest.raises(ApprovalRefused, match="NOT_CONFIRMED_UNDELIVERED"):
        repository.owner_close_non_fault_refund(OWNER_ID, incident, False, later())
    assert compensation_footprint(target) == before


def test_m12_a_recorded_non_fault_holds_a_later_claim_and_the_closure_refunds_once(compensation_world):
    """@description: After the non-fault decision is recorded, a claim is held for review and sends nothing; the closure then refunds exactly once.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    reservation = reserve(repository, "m12a-reserve")
    incident = repository.owner_record_not_system_fault(OWNER_ID, reservation, "m12a", later())["incident_id"]
    assert repository.claim_delivery(reservation, "m12a-claim", later()) == ("held_for_review", None, None)
    assert repository.owner_close_non_fault_refund(OWNER_ID, incident, True, later())["outcome"] == "closed_refunded"
    assert compensation_footprint(target)[3] == 1


def test_m12_a_claim_that_commits_first_leaves_no_refund_and_no_incident(compensation_world):
    """@description: When the claim commits first, the non-fault decision is refused, nothing is refunded, and the one send is the claimed attempt.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    reservation = reserve(repository, "m12b-reserve")
    assert repository.claim_delivery(reservation, "m12b-claim", later())[0] == "sending"
    with pytest.raises(ApprovalRefused, match="NOT_STRANDED"):
        repository.owner_record_not_system_fault(OWNER_ID, reservation, "m12b", later())
    assert compensation_footprint(target)[:4] == (0, 0, 0, 0)


def test_m12_concurrent_closure_and_claim_never_refund_and_send_together(compensation_world):
    """@description: Racing the D1 decision against the send claim, a refund and a sending claim never both happen; either order is acceptable and neither is assumed.
    @param compensation_world: Switched-on repository over real MySQL.
    @return: None.
    """
    target, repository = compensation_world
    reservation = reserve(repository, "m12c-reserve")

    def decide_and_close():
        """@description: Record the non-fault decision and close it, reporting a refusal as data.
        @return: Pair of kind and outcome or refusal code.
        """
        try:
            incident = repository.owner_record_not_system_fault(OWNER_ID, reservation, "m12c", later())["incident_id"]
        except ApprovalRefused as refusal:
            return ("refused", str(refusal))
        return ("closed", repository.owner_close_non_fault_refund(OWNER_ID, incident, True, later())["outcome"])

    def claim():
        """@description: Attempt the send claim and report its status.
        @return: Pair of kind and status.
        """
        return ("claim", repository.claim_delivery(reservation, "m12c-claim", later())[0])

    results = run_concurrently([decide_and_close, claim])
    claim_status = results[1][1]
    footprint = compensation_footprint(target)
    assert footprint[3] <= 1, "a reservation is refunded at most once"
    assert not (footprint[3] == 1 and claim_status == "sending"), "a refund and a sending claim must never both happen"
    assert not (claim_status == "sending" and footprint[0] == 1), "a claimed original must not have an incident"


def switched_off_repository(target) -> SubmissionRepository:
    """@description: A production repository with the feature switch off, over the same isolated target; the guard still applies while the tables exist.
    @param target: Verified isolated destination.
    @return: Switched-off repository.
    """
    settings, pin = target
    return SubmissionRepository(lambda: open_verified_connection(settings, pin), OWNER_ID)


def test_m13_switched_off_a_stranded_reservation_blocks_a_new_request_and_still_identifies_its_key(compensation_world):
    """@description: With the switch off and the tables present, a stranded reservation blocks the requester's new request before any debit, while a same-key retry of the original is still identified and not blocked (D-2).
    @param compensation_world: Switched-on repository over real MySQL, used for the setup.
    @return: None.
    """
    target, repository = compensation_world
    off = switched_off_repository(target)
    reservation = reserve(repository, "m13-original")
    replay = off.exchange(USER_A, "m13-original", random.Random(1), now=later())
    assert replay.status is ExchangeStatus.REPLAYED and replay.recommendation_id == reservation
    assert off.exchange(USER_A, "m13-new", random.Random(1), now=later()).status is ExchangeStatus.PENDING_REVIEW
    assert_repository_state((target, None, None), 1, 0)


def test_m14_switched_off_an_uncertain_grant_blocks_and_keeps_its_song_after_closure(compensation_world):
    """@description: With the switch off, an open uncertain grant blocks new requests; once closed as not delivered, its song stays held, so the redraw reports no candidate.
    @param compensation_world: Switched-on repository over real MySQL, used for the setup.
    @return: None.
    """
    target, repository = compensation_world
    off = switched_off_repository(target)
    grant = approve_and_settle(repository, reserve(repository, "m14-reserve"), True)["grant_id"]
    repository.claim_grant(grant, "m14-claim", later())
    assert repository.record_grant_delivery(grant, "m14-claim", "unknown", None, later()) == "uncertain"
    assert off.exchange(USER_A, "m14-blocked", random.Random(1), now=later()).status is ExchangeStatus.PENDING_REVIEW
    assert repository.owner_record_disposition(OWNER_ID, grant, "confirmed_not_delivered", later())["holds_song"] is True
    assert off.exchange(USER_A, "m14-after", random.Random(1), now=later()).status is ExchangeStatus.NO_CANDIDATE


def test_m15_switched_off_a_not_sent_grant_releases_its_song_once_closed(compensation_world):
    """@description: With the switch off, an open not-sent grant blocks; closed with no further action, its song is released and the requester can reserve it again.
    @param compensation_world: Switched-on repository over real MySQL, used for the setup.
    @return: None.
    """
    target, repository = compensation_world
    off = switched_off_repository(target)
    grant = approve_and_settle(repository, reserve(repository, "m15-reserve"), True)["grant_id"]
    repository.claim_grant(grant, "m15-claim", later())
    assert repository.record_grant_delivery(grant, "m15-claim", "not_sent", None, later()) == "not_sent"
    assert off.exchange(USER_A, "m15-blocked", random.Random(1), now=later()).status is ExchangeStatus.PENDING_REVIEW
    assert repository.owner_record_disposition(OWNER_ID, grant, "no_further_action", later()) == {"grant_id": grant, "holds_song": False}
    assert off.exchange(USER_A, "m15-after", random.Random(1), now=later()).status is ExchangeStatus.RESERVED
    assert_repository_state((target, None, None), 2, 0)
