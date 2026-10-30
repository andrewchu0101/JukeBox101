"""Real PostgreSQL integration tests for the open commands (/share, /history, /balance) and their safety boundaries.

Runs the production MusicService and repositories with the least-privilege app role on a disposable PostgreSQL 18
database: first-use initialization, cross-guild sharing of one account, privacy, ordering, idempotent replay,
concurrency, failures before/after COMMIT, connection loss, readiness, and no accounting writes from closed features.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import psycopg
import pytest

from bot.database.connection import open_connection
from bot.discord_app import _service_reply
from bot.database.repository import MusicRepository
from bot.database.submission_repository import SubmissionRepository
from bot.database.submission_transaction import CommitOutcomeUnknown
from bot.ledger_integrity import STATUS_PASS, evaluate_ledger_integrity
from bot.production_config import MAX_STORABLE_SNOWFLAKE
from bot.services import MusicService
from tests.commit_fault_injector import FaultInjectingConnection
from tests.postgres_support import admin_query

pytestmark = pytest.mark.postgres

OWNER_ID = 900000000000000001  # [synthetic Owner]
ALICE, BOB = 910000000000000002, 920000000000000003  # [synthetic members]
VIDEO = "https://www.youtube.com/watch?v=Abc_123-Xyz"
OTHER_VIDEO = "https://youtu.be/Zz9_8Yy7Xx6"


def _service(database, factory=None) -> MusicService:
    """@description: Build the production service exactly as the entry does, with the app role.
    @param database: Disposable test database.
    @param factory: Optional connection factory replacement for fault injection.
    @return: Service with the Beta submission repository.
    """
    connection_factory = factory or partial(open_connection, database.app)
    return MusicService(MusicRepository(database.app), SubmissionRepository(connection_factory, OWNER_ID),
                        nickel_connection_factory=partial(open_connection, database.app))


def _count(database, table: str) -> int:
    """@description: Count rows of one constant table through the admin role.
    @param database: Disposable test database.
    @param table: Constant table name.
    @return: Row count.
    """
    return admin_query(database, f"SELECT count(*) AS n FROM {table}")[0]["n"]


def _accounting_rows(database) -> dict:
    """@description: Snapshot every table that can carry money or grants.
    @param database: Disposable test database.
    @return: Table to row count.
    """
    return {table: _count(database, table) for table in ("credit_ledger", "jb2_claims", "nickel_grants", "nickel_claim_requests",
                                                         "recommendations", "jb2_settlements")}


def test_first_use_initializes_the_account_automatically_and_balance_reads_write_nothing(pg_database):
    """@description: A brand-new member needs no manual SQL: /balance reads 0 without writing; /share creates the account once.
    @param pg_database: Disposable database.
    @return: None.
    """
    service = _service(pg_database)
    assert asyncio.run(service.balance(ALICE)) == "目前可用 Nickel：0。"
    assert _count(pg_database, "users") == 0  # [read only]: Balance never inserts.
    reply = asyncio.run(service.share(ALICE, VIDEO, "discord:1001"))
    assert reply.startswith("已儲存投稿；審核狀態：pending") and "本次不發放 Nickel" in reply
    assert admin_query(pg_database, "SELECT discord_user_id, credit_balance FROM users") == [{"discord_user_id": ALICE, "credit_balance": 0}]
    assert set(_accounting_rows(pg_database).values()) == {0}


def test_one_account_across_guilds_with_private_history_and_shared_balance(pg_database):
    """@description: Interactions from two guilds by the same user land in one history; another user's rows never show.
    @param pg_database: Disposable database.
    @return: None.
    """
    service = _service(pg_database)
    asyncio.run(service.share(ALICE, VIDEO, "discord:2001"))  # [guild A interaction]
    asyncio.run(service.share(ALICE, OTHER_VIDEO, "discord:2002"))  # [guild B interaction]
    asyncio.run(service.share(BOB, OTHER_VIDEO, "discord:2003"))
    alice_history = asyncio.run(service.history(ALICE))
    assert alice_history.splitlines()[1].startswith("discord:2002") and alice_history.splitlines()[2].startswith("discord:2001")
    assert "discord:2003" not in alice_history
    bob_history = asyncio.run(service.history(BOB))
    assert "discord:2003" in bob_history and "discord:2001" not in bob_history and "discord:2002" not in bob_history
    assert asyncio.run(service.balance(ALICE)) == asyncio.run(service.balance(BOB)) == "目前可用 Nickel：0。"
    assert _count(pg_database, "users") == 2 and _count(pg_database, "songs") == 2


def test_replaying_the_same_operation_returns_the_original_receipt_once(pg_database):
    """@description: A retried interaction key replays its receipt; a different URL under the same key adds nothing.
    @param pg_database: Disposable database.
    @return: None.
    """
    service = _service(pg_database)
    asyncio.run(service.share(ALICE, VIDEO, "discord:3001"))
    replay = asyncio.run(service.share(ALICE, VIDEO, "discord:3001"))
    assert replay.startswith("重複操作，已取回原投稿回執")
    repository = SubmissionRepository(partial(open_connection, pg_database.app), OWNER_ID)
    try:
        conflicting = repository.accept_pending(ALICE, OTHER_VIDEO, "discord:3001")
        assert conflicting.create_share_event is False  # [original wins]: The stored receipt is returned unchanged.
        assert conflicting.video.video_id == "Abc_123-Xyz"
    except ValueError:
        pass  # [refused]: A conflicting payload may also be refused; either way nothing new is written.
    assert _count(pg_database, "jb2_events") == 1 and _count(pg_database, "songs") == 1
    assert admin_query(pg_database, "SELECT accepted_sequence FROM jb2_control") == [{"accepted_sequence": 1}]


def test_concurrent_shares_from_different_guilds_get_unique_contiguous_order_and_one_song(pg_database):
    """@description: Parallel intakes of one video by many members serialize on the control row: one song, N events, order 1..N.
    @param pg_database: Disposable database.
    @return: None.
    """
    members = [930000000000000000 + index for index in range(8)]

    def share(member: int) -> bool:
        """@description: Accept one share on its own worker connection.
        @param member: Member ID.
        @return: Whether a new event was created.
        """
        repository = SubmissionRepository(partial(open_connection, pg_database.app), OWNER_ID)
        return repository.accept_pending(member, VIDEO, f"discord:{member}").create_share_event

    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(share, members))
    assert created == [True] * 8
    sequences = [row["accepted_sequence"] for row in admin_query(pg_database, "SELECT accepted_sequence FROM jb2_events ORDER BY accepted_sequence")]
    assert sequences == list(range(1, 9))
    assert _count(pg_database, "songs") == 1 and _count(pg_database, "jb2_videos") == 1 and _count(pg_database, "users") == 8
    assert set(_accounting_rows(pg_database).values()) == {0}


def test_the_same_interaction_delivered_twice_concurrently_creates_one_event(pg_database):
    """@description: Two workers racing on one operation key and one brand-new user produce one user row and one event.
    @param pg_database: Disposable database.
    @return: None.
    """
    barrier = threading.Barrier(2)

    def share(_: int) -> bool:
        """@description: Start both attempts together.
        @param _: Unused index.
        @return: Whether this attempt created the event.
        """
        barrier.wait()
        repository = SubmissionRepository(partial(open_connection, pg_database.app), OWNER_ID)
        return repository.accept_pending(BOB, VIDEO, "discord:4001").create_share_event

    with ThreadPoolExecutor(max_workers=2) as pool:
        created = sorted(pool.map(share, range(2)))
    assert created == [False, True]
    assert _count(pg_database, "jb2_events") == 1 and _count(pg_database, "users") == 1


def test_a_statement_failure_rolls_back_everything_and_the_retry_succeeds_once(pg_database):
    """@description: A deadlock-victim failure inside the transaction leaves no row and no consumed sequence.
    @param pg_database: Disposable database.
    @return: None.
    """
    failing = lambda: FaultInjectingConnection(open_connection(pg_database.app), "fail_statement", statement_marker="jb2_receipts")
    reply = asyncio.run(_service_reply(_service(pg_database, failing).share, ALICE, VIDEO, "discord:5001"))  # [user-visible path]
    assert reply == "資料服務暫時無法使用，請稍後再試。"
    assert _count(pg_database, "jb2_events") == 0 and _count(pg_database, "users") == 0 and _count(pg_database, "songs") == 0
    assert admin_query(pg_database, "SELECT accepted_sequence, revision FROM jb2_control") == [{"accepted_sequence": 0, "revision": 0}]
    assert asyncio.run(_service(pg_database).share(ALICE, VIDEO, "discord:5001")).startswith("已儲存投稿")
    assert _count(pg_database, "jb2_events") == 1


def test_lost_acknowledgement_after_commit_is_reported_unknown_and_replay_finds_the_original(pg_database):
    """@description: A durable commit whose acknowledgement is lost is never retried silently; replay identifies it.
    @param pg_database: Disposable database.
    @return: None.
    """
    lost_ack = lambda: FaultInjectingConnection(open_connection(pg_database.app), "lost_ack_after_commit")
    reply = asyncio.run(_service(pg_database, lost_ack).share(ALICE, VIDEO, "discord:6001"))
    assert reply.startswith("投稿提交結果未知；操作 discord:6001") and "勿重新投稿" in reply
    assert _count(pg_database, "jb2_events") == 1  # [durable]: The server did commit.
    assert asyncio.run(_service(pg_database).share(ALICE, VIDEO, "discord:6001")).startswith("重複操作")
    assert _count(pg_database, "jb2_events") == 1


def test_failure_before_commit_is_unknown_to_the_caller_but_nothing_is_durable(pg_database):
    """@description: A connection lost before COMMIT leaves nothing; the replay creates the event exactly once.
    @param pg_database: Disposable database.
    @return: None.
    """
    lost_before = lambda: FaultInjectingConnection(open_connection(pg_database.app), "fail_before_commit")
    repository = SubmissionRepository(lost_before, OWNER_ID)
    with pytest.raises(CommitOutcomeUnknown):
        repository.accept_pending(ALICE, VIDEO, "discord:7001")
    assert _count(pg_database, "jb2_events") == 0
    assert asyncio.run(_service(pg_database).share(ALICE, VIDEO, "discord:7001")).startswith("已儲存投稿")
    assert _count(pg_database, "jb2_events") == 1


class _PausingConnection:
    """Pause a real connection just before a chosen statement so the test can kill the server session mid-transaction."""

    def __init__(self, connection, marker: str, reached: threading.Event, release: threading.Event) -> None:
        """@description: Wrap a real psycopg connection.
        @param self: Wrapper.
        @param connection: Real connection.
        @param marker: Statement prefix that triggers the pause.
        @param reached: Set when the pause point is reached.
        @param release: Awaited before executing the statement.
        @return: None.
        """
        self._connection, self._marker, self._reached, self._release = connection, marker, reached, release

    def cursor(self, *args, **kwargs):
        """@description: Return a pausing cursor.
        @param self: Wrapper.
        @param args: Cursor arguments.
        @param kwargs: Cursor keyword arguments.
        @return: Cursor wrapper.
        """
        connection = self

        class _Cursor:
            """Forward to the real cursor, pausing once before the marker statement."""

            def __init__(self, inner) -> None:
                """@description: Wrap the real cursor.
                @param self: Cursor wrapper.
                @param inner: Real cursor.
                @return: None.
                """
                self._inner = inner

            def __enter__(self):
                """@description: Enter the real cursor context.
                @param self: Cursor wrapper.
                @return: Wrapper.
                """
                self._inner.__enter__()
                return self

            def __exit__(self, *details):
                """@description: Exit the real cursor context.
                @param self: Cursor wrapper.
                @param details: Exception details.
                @return: Real cursor's result.
                """
                return self._inner.__exit__(*details)

            def execute(self, statement, parameters=None):
                """@description: Pause before the marker statement, then forward.
                @param self: Cursor wrapper.
                @param statement: SQL text.
                @param parameters: Bound values.
                @return: Real cursor result.
                """
                if statement.startswith(connection._marker) and not connection._reached.is_set():
                    connection._reached.set()
                    connection._release.wait(timeout=10)
                return self._inner.execute(statement, parameters)

            def __getattr__(self, name):
                """@description: Forward everything else.
                @param self: Cursor wrapper.
                @param name: Attribute.
                @return: Real attribute.
                """
                return getattr(self._inner, name)

        return _Cursor(self._connection.cursor(*args, **kwargs))

    def __getattr__(self, name):
        """@description: Forward connection methods.
        @param self: Wrapper.
        @param name: Attribute.
        @return: Real attribute.
        """
        return getattr(self._connection, name)


def test_server_side_connection_loss_mid_transaction_rolls_back_and_releases_the_intake_lock(pg_database):
    """@description: Killing the app session while it holds the control-row lock leaves no partial rows and no stuck lock.
    @param pg_database: Disposable database.
    @return: None.
    """
    reached, release = threading.Event(), threading.Event()
    factory = lambda: _PausingConnection(open_connection(pg_database.app), "INSERT INTO jb2_events", reached, release)
    repository = SubmissionRepository(factory, OWNER_ID)
    with ThreadPoolExecutor(max_workers=1) as pool:
        attempt = pool.submit(repository.accept_pending, ALICE, VIDEO, "discord:8001")
        assert reached.wait(timeout=10)
        killed = admin_query(pg_database, "SELECT pg_terminate_backend(pid) AS killed FROM pg_stat_activity "
                                          "WHERE usename = 'jukebox101_app' AND datname = current_database() AND state LIKE 'idle in transaction%%'")
        release.set()
        with pytest.raises((psycopg.OperationalError, CommitOutcomeUnknown)):
            attempt.result(timeout=20)
    assert killed and all(row["killed"] for row in killed)
    assert _count(pg_database, "jb2_events") == 0 and _count(pg_database, "users") == 0
    assert asyncio.run(_service(pg_database).share(ALICE, VIDEO, "discord:8002")).startswith("已儲存投稿")
    lingering = admin_query(pg_database, "SELECT count(*) AS n FROM pg_stat_activity WHERE usename = 'jukebox101_app' AND datname = current_database()")
    assert lingering == [{"n": 0}]  # [released]: Every worker connection was closed.


def test_unready_control_row_refuses_intake_without_writing(pg_database):
    """@description: Settlement enabled or a wrong schema version stops /share with a clear reply and no row.
    @param pg_database: Disposable database.
    @return: None.
    """
    admin_query(pg_database, "UPDATE jb2_control SET settlement_enabled = TRUE")
    reply = asyncio.run(_service_reply(_service(pg_database).share, ALICE, VIDEO, "discord:9001"))  # [user-visible path]
    assert reply == "投稿資料庫尚未通過啟用檢查；本次未受理，請聯絡 Owner。"
    admin_query(pg_database, "UPDATE jb2_control SET settlement_enabled = FALSE, schema_version = 'other'")
    assert asyncio.run(_service_reply(_service(pg_database).share, ALICE, VIDEO, "discord:9002")) == reply
    assert _count(pg_database, "jb2_events") == 0 and _count(pg_database, "users") == 0


def test_closed_features_write_no_accounting_rows(pg_database):
    """@description: /claim, /exchange and /daily stay closed and touch no money, grant or reservation table.
    @param pg_database: Disposable database.
    @return: None.
    """
    service = _service(pg_database)
    asyncio.run(service.share(ALICE, VIDEO, "discord:9101"))
    before = _accounting_rows(pg_database)
    assert asyncio.run(service.claim(ALICE, "discord:9102")) == "Nickel 領取功能尚未開放，本次未發放。"
    assert "本次未扣除 Nickel" in asyncio.run(service.exchange(ALICE))
    assert "未保存偏好" in asyncio.run(service.daily(ALICE, True))
    assert _accounting_rows(pg_database) == before == {table: 0 for table in before}
    assert admin_query(pg_database, "SELECT daily_enabled FROM users WHERE discord_user_id = %s", (ALICE,)) == [{"daily_enabled": False}]


def test_ledger_and_balances_stay_consistent(pg_database):
    """@description: After intake the ledger integrity rules pass: every balance equals its ledger sum (zero here).
    @param pg_database: Disposable database.
    @return: None.
    """
    service = _service(pg_database)
    for index, member in enumerate((ALICE, BOB)):
        asyncio.run(service.share(member, VIDEO, f"discord:{9200 + index}"))
    users = admin_query(pg_database, "SELECT discord_user_id, credit_balance FROM users")
    ledger = admin_query(pg_database, "SELECT ledger_id, discord_user_id, credit_delta, reason, awarded_song_id FROM credit_ledger")
    claims = admin_query(pg_database, "SELECT version_id, ledger_id, discord_user_id FROM jb2_claims")
    assert evaluate_ledger_integrity(users, ledger, claims).status == STATUS_PASS


def test_stored_json_is_verbatim_and_ids_are_exact(pg_database):
    """@description: Receipts keep their exact encoded text, video IDs stay case-sensitive, and the largest storable ID round-trips.
    @param pg_database: Disposable database.
    @return: None.
    """
    repository = SubmissionRepository(partial(open_connection, pg_database.app), OWNER_ID)
    repository.accept_pending(MAX_STORABLE_SNOWFLAKE, "https://youtu.be/abcdefghijk", "discord:9301")
    repository.accept_pending(MAX_STORABLE_SNOWFLAKE, "https://youtu.be/ABCDEFGHIJK", "discord:9302")
    assert _count(pg_database, "songs") == 2
    row = admin_query(pg_database, "SELECT receipt_json::text AS text FROM jb2_receipts WHERE operation_id = 'discord:9301'")[0]
    assert row["text"].startswith('{"codec":1,"value":')  # [verbatim json]: Compact codec text, not re-normalized.
    assert repository.history(MAX_STORABLE_SNOWFLAKE)[0]["operation_id"] == "discord:9302"
