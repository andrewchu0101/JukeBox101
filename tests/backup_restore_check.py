"""Seed, compare and clean disposable databases for the backup/restore verification (scripts/dev/verify-backup-restore.sh).

`seed` creates a migrated database with real intake rows; `verify` compares every table's content digest between the
source and the restored database, checks readiness and pending migrations, and proves intake continues; `drop` removes
one harness database. Runs inside the test image on the disposable network; prints JSON without secrets.
"""

import asyncio
import json
import sys
from functools import partial

from bot.database.connection import open_connection
from bot.database.migration_runner import run_migrations
from bot.database.repository import MusicRepository
from bot.database.submission_repository import SubmissionRepository
from bot.services import MusicService
from tests.postgres_support import (PostgresDatabase, _environment_cluster, _settings, admin_query, create_database, drop_database,
                                    prepare_cluster)

TABLES = ("users", "songs", "jb2_control", "jb2_videos", "jb2_events", "jb2_receipts", "jb2_review_history",
          "jb2_evidence", "jb2_reminders", "credit_ledger", "jb_schema_migrations")
OWNER_ID = 900000000000000001


def table_digests(database) -> dict:
    """@description: Fingerprint each table's full content in a stable order.
    @param database: Harness database description.
    @return: Table name to md5 of its ordered rows and the row count.
    """
    digests = {}
    for table in TABLES:
        row = admin_query(database, f"SELECT count(*) AS n, md5(coalesce(string_agg(t::text, '|' ORDER BY t::text), '')) AS digest "
                                    f"FROM {table} t")[0]
        digests[table] = [row["n"], row["digest"]]
    return digests


def seed(cluster) -> dict:
    """@description: Create a migrated database and write intake rows through the production service.
    @param cluster: Disposable server.
    @return: Database name and digests.
    """
    database = create_database(cluster)
    service = MusicService(MusicRepository(database.app), SubmissionRepository(partial(open_connection, database.app), OWNER_ID))
    for index, url in enumerate(("https://youtu.be/Abc_123-Xyz", "https://youtu.be/Zz9_8Yy7Xx6")):
        asyncio.run(service.share(910000000000000000 + index, url, f"discord:{100 + index}"))
    return {"database": database.name, "digests": table_digests(database)}


def verify(cluster, source_name: str, restored_name: str) -> dict:
    """@description: Compare digests, then prove the restored database is ready, fully migrated and accepts new intake.
    @param cluster: Disposable server.
    @param source_name: Seeded database.
    @param restored_name: Restored database.
    @return: Verification summary.
    """
    source = _database(cluster, source_name)
    restored = _database(cluster, restored_name)
    same = table_digests(source) == table_digests(restored)
    connection = open_connection(restored.migrator)
    try:
        pending = run_migrations(connection, apply=False)["pending_before"]
    finally:
        connection.close()
    service = MusicService(MusicRepository(restored.app), SubmissionRepository(partial(open_connection, restored.app), OWNER_ID))
    reply = asyncio.run(service.share(920000000000000000, "https://youtu.be/Qq1_2Ww3Ee4", "discord:300"))
    sequence = admin_query(restored, "SELECT accepted_sequence FROM jb2_control")[0]["accepted_sequence"]
    return {"digests_identical": same, "pending_migrations": pending, "new_share_accepted": reply.startswith("已儲存投稿"),
            "accepted_sequence_after": sequence}


def _database(cluster, name: str) -> PostgresDatabase:
    """@description: Describe an existing harness database with this process's role passwords.
    @param cluster: Disposable server.
    @param name: Database name.
    @return: Database description.
    """
    roles = [_settings(cluster, name, role, cluster.role_passwords[role]) for role in ("jukebox101_app", "jukebox101_migrator", "jukebox101_readonly")]
    return PostgresDatabase(name, *roles, _settings(cluster, name, cluster.admin_user, cluster.admin_password))


def main(argv: list[str]) -> int:
    """@description: Dispatch seed, verify or drop and print a JSON result.
    @param argv: Command arguments.
    @return: 0 on success, 1 when verification fails.
    """
    cluster = _environment_cluster()
    prepare_cluster(cluster)
    if argv[0] == "seed":
        print(json.dumps(seed(cluster)))
        return 0
    if argv[0] == "verify":
        summary = verify(cluster, argv[1], argv[2])
        print(json.dumps(summary))
        return 0 if summary["digests_identical"] and summary["pending_migrations"] == [] and summary["new_share_accepted"] else 1
    for name in argv[1:]:
        drop_database(cluster, name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
