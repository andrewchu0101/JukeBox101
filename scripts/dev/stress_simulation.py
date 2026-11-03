"""Simulate many Discord users hitting the real repository and pipeline on a DISPOSABLE PostgreSQL, to measure latency, lock waits and rule correctness.

It never touches production: it only runs against the test server whose cluster_name is the disposable marker (see compose.test.yaml), creates its own
database and drops it afterwards. YouTube is replaced by a synthetic provider and Discord by direct repository calls (share, balance, history, exchange).
Use --rtt-ms to add the network round trip you measured (about 120 ms from the GCP VM to the Frankfurt database) to every statement, commit and connection.

Run through scripts/dev/run-linux-tests.sh stress [options]; see --help. It exits 1 when any invariant is violated.
"""

import argparse
import hashlib
import json
import random
import statistics
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import psycopg  # noqa: E402

from bot.auto_pipeline import AutoPipeline  # noqa: E402
from bot.database.connection import open_connection  # noqa: E402
from bot.database.exchange_reservation import ExchangeStatus  # noqa: E402
from bot.database.submission_repository import SubmissionRepository  # noqa: E402
from bot.ledger_integrity import STATUS_PASS, evaluate_ledger_integrity  # noqa: E402
from bot.youtube_metadata import BatchLookup, BatchStatus, LookupStatus, MetadataLookup, VideoMetadata  # noqa: E402
from tests.postgres_support import _environment_cluster, admin_query, create_database, drop_database, prepare_cluster  # noqa: E402

OWNER_ID = 900001  # [synthetic Owner]: Never acts here; review cases simply stay pending.
FIRST_USER_ID = 7_000_000  # [synthetic users]: Far from any real Discord snowflake pattern used elsewhere in the tests.
CONNECT_ROUND_TRIPS = 5  # [handshake cost]: TCP, SSL negotiation, TLS and authentication, as estimated for the real connection.
FETCHED_AT = datetime(2026, 11, 3, tzinfo=timezone.utc)  # [fixed fetch time]: The synthetic provider is deterministic.


class Delay:
    """Hold the simulated one-way round-trip time, changeable at run time so setup and checks run at full speed."""

    seconds = 0.0  # [round trip]: Applied once per statement, commit, rollback, and several times per new connection.


class SlowCursor(psycopg.Cursor):
    """Add one round trip to every statement."""

    def execute(self, *arguments, **options):
        """@description: Wait one simulated round trip before the real statement.
        @param self: Cursor.
        @param arguments: Statement and parameters.
        @param options: Execution options.
        @return: The cursor, as psycopg does.
        """
        time.sleep(Delay.seconds)
        return super().execute(*arguments, **options)


class SlowConnection(psycopg.Connection):
    """Add one round trip to commit and rollback."""

    def commit(self):
        """@description: Wait one simulated round trip before committing.
        @param self: Connection.
        @return: None.
        """
        time.sleep(Delay.seconds)
        return super().commit()

    def rollback(self):
        """@description: Wait one simulated round trip before rolling back.
        @param self: Connection.
        @return: None.
        """
        time.sleep(Delay.seconds)
        return super().rollback()


def install_latency() -> None:
    """@description: Route every new connection of the application code through the slow classes, with the handshake cost added.
    @param: none
    @return: None.
    """
    def slow_connect(*arguments, **options):
        """@description: Pay the handshake round trips, then connect with slow cursors.
        @param arguments: Positional connect arguments.
        @param options: Connect options.
        @return: New slow connection.
        """
        time.sleep(CONNECT_ROUND_TRIPS * Delay.seconds)
        options.setdefault("cursor_factory", SlowCursor)
        return SlowConnection.connect(*arguments, **options)

    psycopg.connect = slow_connect


class SyntheticProvider:
    """Answer YouTube lookups from the video identifier, so every song is unique, public and a plausible music video."""

    def __init__(self, review_fraction: float, seed: int) -> None:
        """@description: Fix the share of videos that fail the category rule and so wait for the Owner.
        @param self: Provider.
        @param review_fraction: Fraction of videos that need the Owner.
        @param seed: Run seed.
        @return: None.
        """
        self._review_fraction, self._seed = review_fraction, seed

    def _digest(self, video_id: str) -> bytes:
        """@description: Derive stable pseudo-random bytes for one video.
        @param self: Provider.
        @param video_id: Video identifier.
        @return: Digest bytes.
        """
        return hashlib.sha256(f"{self._seed}:{video_id}".encode()).digest()

    def needs_owner(self, video_id: str) -> bool:
        """@description: Decide deterministically whether this video is outside the music category.
        @param self: Provider.
        @param video_id: Video identifier.
        @return: True when the video would be sent to the Owner.
        """
        return self._digest(video_id)[0] / 255 < self._review_fraction

    def metadata(self, video_id: str) -> VideoMetadata:
        """@description: Build unique official-looking metadata whose title, channel and length never look like another video.
        @param self: Provider.
        @param video_id: Video identifier.
        @return: Metadata.
        """
        digest = self._digest(video_id)
        words = [hashlib.sha256(digest + bytes([index])).hexdigest()[:9] for index in range(2)]
        channel = "UC" + hashlib.sha256(digest + b"c").hexdigest()[:22]
        category = "24" if self.needs_owner(video_id) else "10"
        return VideoMetadata(video_id, channel, category, " ".join(words), "none", False, "processed", "public", 90 + digest[1] * 2, FETCHED_AT)

    def lookup(self, video_id: str) -> MetadataLookup:
        """@description: Answer one lookup like the real client.
        @param self: Provider.
        @param video_id: Video identifier.
        @return: Found lookup.
        """
        return MetadataLookup(LookupStatus.FOUND, self.metadata(video_id))

    def lookup_many(self, video_ids) -> BatchLookup:
        """@description: Answer the version-comparison batch.
        @param self: Provider.
        @param video_ids: Existing videos to describe.
        @return: Complete batch.
        """
        return BatchLookup(BatchStatus.COMPLETE, tuple(self.metadata(video_id) for video_id in video_ids))


class Recorder:
    """Collect latencies and outcomes from many threads."""

    def __init__(self) -> None:
        """@description: Start empty.
        @param self: Recorder.
        @return: None.
        """
        self._lock = threading.Lock()
        self.latencies: dict[str, list[float]] = defaultdict(list)
        self.errors: dict[str, Counter] = defaultdict(Counter)
        self.outcomes: dict[str, Counter] = defaultdict(Counter)

    def timed(self, operation: str, work) -> object:
        """@description: Run one operation, recording its duration, its result label or its error class.
        @param self: Recorder.
        @param operation: Operation name.
        @param work: Zero-argument callable returning a label or an object with .decision.status.
        @return: The work result, or None after an error.
        """
        started = time.perf_counter()
        try:
            result = work()
        except Exception as error:  # [classified]: A failed command is data here, not a crash.
            with self._lock:
                self.latencies[operation].append(time.perf_counter() - started)
                self.errors[operation][type(error).__name__] += 1
            return None
        with self._lock:
            self.latencies[operation].append(time.perf_counter() - started)
            self.outcomes[operation][_label(result)] += 1
        return result


def _label(result: object) -> str:
    """@description: Name a result for the outcome table.
    @param result: Work result.
    @return: Short label.
    """
    decision = getattr(result, "decision", None)
    if decision is not None:
        return decision.status.value
    status = getattr(result, "status", None)
    return status.value if status is not None else type(result).__name__


class Simulation:
    """Hold the repository, pipeline and recorder shared by every simulated user."""

    def __init__(self, repository: SubmissionRepository, pipeline: AutoPipeline, arguments: argparse.Namespace) -> None:
        """@description: Bind collaborators.
        @param self: Simulation.
        @param repository: Production repository over the disposable database.
        @param pipeline: Production pipeline with the synthetic provider.
        @param arguments: Parsed options.
        @return: None.
        """
        self.repository, self.pipeline, self.arguments = repository, pipeline, arguments
        self.recorder = Recorder()

    def run_user(self, index: int) -> None:
        """@description: Play one user's session: share songs, check balance and history, then exchange.
        @param self: Simulation.
        @param index: User position.
        @return: None.
        """
        user = FIRST_USER_ID + index
        rng = random.Random(self.arguments.seed * 100_003 + index)
        for song in range(self.arguments.songs_per_user):
            self._share(user, index, song)
        self.recorder.timed("balance", lambda: self.repository.user_exchange_balance(user))
        self.recorder.timed("history", lambda: self.repository.history(user))
        for attempt in range(self.arguments.exchanges_per_user):
            self.recorder.timed("exchange", lambda attempt=attempt: self._exchange(user, attempt, rng))

    def _share(self, user: int, index: int, song: int) -> None:
        """@description: Submit one unique song and run the automatic pipeline, as /share does.
        @param self: Simulation.
        @param user: Discord user.
        @param index: User position.
        @param song: Song position for this user.
        @return: None.
        """
        video_id = f"s{index * self.arguments.songs_per_user + song:010d}"
        operation = f"discord:{user}:{song}"

        def work():
            """@description: Accept and process one submission.
            @param: none
            @return: Pipeline outcome.
            """
            self.repository.accept_pending(user, f"https://www.youtube.com/watch?v={video_id}", operation)
            return self.pipeline.process(operation)

        self.recorder.timed("share", work)

    def _exchange(self, user: int, attempt: int, rng: random.Random):
        """@description: Reserve and deliver one recommendation with a delivery that always succeeds, as /exchange does apart from the Discord call.
        @param self: Simulation.
        @param user: Discord user.
        @param attempt: Attempt number.
        @param rng: Draw source.
        @return: The reservation outcome.
        """
        outcome = self.repository.exchange(user, f"x:{user}:{attempt}", rng)
        if outcome.status is ExchangeStatus.RESERVED:
            now = datetime.now(timezone.utc)
            _state, _message, claim = self.repository.claim_delivery(outcome.recommendation_id, uuid.uuid4().hex, now)
            if claim is not None:
                self.repository.record_delivery(claim, "delivered", 123456789, now)
        return outcome


def summarize(recorder: Recorder) -> dict:
    """@description: Turn the raw measurements into per-operation percentiles, outcomes and error classes.
    @param recorder: Filled recorder.
    @return: JSON-serializable summary.
    """
    table = {}
    for operation, values in recorder.latencies.items():
        ordered = sorted(values)
        table[operation] = {"count": len(ordered), "errors": dict(recorder.errors[operation]), "outcomes": dict(recorder.outcomes[operation]),
                            "p50_s": round(statistics.median(ordered), 3), "p95_s": round(ordered[int(len(ordered) * 0.95) - 1], 3),
                            "max_s": round(ordered[-1], 3)}
    return table


def invariant_report(database) -> dict:
    """@description: Check the books and the rules on the final database state, at full speed.
    @param database: Disposable database.
    @return: Named counts; every violation count must be zero.
    """
    users = admin_query(database, "SELECT discord_user_id, credit_balance FROM users")
    ledger = admin_query(database, "SELECT ledger_id, discord_user_id, credit_delta, reason, awarded_song_id FROM credit_ledger")
    claims = admin_query(database, "SELECT version_id, ledger_id, discord_user_id FROM jb2_claims")
    return {
        "ledger_integrity_passed": evaluate_ledger_integrity(users, ledger, claims).status == STATUS_PASS,
        "negative_balances": sum(1 for row in users if row["credit_balance"] < 0),
        "awards": len(claims),
        "conflict_events": admin_query(database, "SELECT count(*) AS n FROM jb2_events WHERE reward_status = 'conflict'")[0]["n"],
        "duplicate_active_recommendations": len(admin_query(
            database, "SELECT 1 FROM recommendations WHERE released_at IS NULL GROUP BY discord_user_id, song_id HAVING count(*) > 1")),
        "blocked_by_another_person": admin_query(
            database, "SELECT count(*) AS n FROM jb2_events e WHERE e.reward_status = 'waiting_predecessor' AND NOT EXISTS ("
                      "SELECT 1 FROM jb2_events p WHERE p.discord_user_id = e.discord_user_id AND p.accepted_sequence < e.accepted_sequence "
                      "AND p.reward_status IN ('waiting_validation', 'waiting_identity', 'waiting_predecessor'))")[0]["n"],
        "automatic_review_reasons": {str(row["code"]): row["n"] for row in admin_query(
            database, "SELECT substring(decision_reason from 'reason=([a-z_]+)') AS code, count(*) AS n FROM jb2_review_history "
                      "WHERE decision_reason LIKE 'auto-review%%' GROUP BY 1")},
        "reward_status_counts": {row["reward_status"]: row["n"] for row in admin_query(
            database, "SELECT reward_status, count(*) AS n FROM jb2_events GROUP BY reward_status")},
    }


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    """@description: Declare the run parameters.
    @param argv: Command-line arguments.
    @return: Parsed options.
    """
    parser = argparse.ArgumentParser(description="Simulated load on a disposable PostgreSQL; never production.")
    parser.add_argument("--users", type=int, default=30)
    parser.add_argument("--songs-per-user", type=int, default=3)
    parser.add_argument("--exchanges-per-user", type=int, default=2)
    parser.add_argument("--review-fraction", type=float, default=0.15, help="share of songs that wait for the Owner (outside the music category)")
    parser.add_argument("--workers", type=int, default=8, help="parallel commands, like the worker threads the Bot uses")
    parser.add_argument("--rtt-ms", type=float, default=0.0, help="simulated network round trip per statement (the real VM to database is about 120)")
    parser.add_argument("--seed", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """@description: Build the disposable world, run all simulated users in parallel, then print the measurements and the invariant report.
    @param argv: Optional argument list.
    @return: 0 when every invariant holds, 1 otherwise, 2 when no disposable server is configured.
    """
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    cluster = _environment_cluster()
    if cluster is None:
        print("JB_PG_TEST_HOST and the admin password are not set; run through scripts/dev/run-linux-tests.sh stress", file=sys.stderr)
        return 2
    prepare_cluster(cluster)
    database = create_database(cluster)
    try:
        repository = SubmissionRepository(partial(open_connection, database.app), OWNER_ID, automatic_settlement=True)
        admin_query(database, "UPDATE jb2_control SET settlement_enabled = TRUE")
        provider = SyntheticProvider(arguments.review_fraction, arguments.seed)
        pipeline = AutoPipeline(repository, provider, frozenset(), settlement_enabled=True, api_retention=True, version_confirm=True,
                                trust_mode="music_category")
        simulation = Simulation(repository, pipeline, arguments)
        install_latency()
        Delay.seconds = arguments.rtt_ms / 1000
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=arguments.workers) as pool:
            list(pool.map(simulation.run_user, range(arguments.users)))
        elapsed = time.perf_counter() - started
        Delay.seconds = 0.0
        report = {"parameters": vars(arguments), "wall_seconds": round(elapsed, 1), "operations": summarize(simulation.recorder),
                  "invariants": invariant_report(database)}
    finally:
        Delay.seconds = 0.0
        drop_database(cluster, database.name)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    invariants = report["invariants"]
    broken = (not invariants["ledger_integrity_passed"] or invariants["negative_balances"] or invariants["duplicate_active_recommendations"]
              or invariants["conflict_events"] or invariants["blocked_by_another_person"])
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
