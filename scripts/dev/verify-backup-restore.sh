#!/usr/bin/env bash
# Verify PostgreSQL backup and restore on the DISPOSABLE test server only (never a production database).
# Seeds a migrated database through the production service, dumps it as the schema-owner migrator with pg_dump 18,
# restores it into a fresh database with pg_restore, then compares every table digest, readiness, pending migrations
# and continued intake. Every failure exits non-zero. Only the databases and dump created here are removed.
#
# Requires: a running disposable server container (cluster_name=jukebox101-disposable-test) on a private network,
# the dev test image (test.Dockerfile), and an env file with JB_PG_TEST_* settings (see docs/deploy/v7-postgresql-deployment.md).
set -euo pipefail

DOCKER="${DOCKER:-docker}"                       # docker CLI (override for a wrapper)
PG_CONTAINER="${PG_CONTAINER:-jb-v7-pg}"         # disposable PostgreSQL 18 container
TEST_NETWORK="${TEST_NETWORK:-jb-v7-test-net}"   # private network shared with the test image
TEST_IMAGE="${TEST_IMAGE:-jukebox101-test:v7-dev}"
TEST_ENV_FILE="${TEST_ENV_FILE:?set TEST_ENV_FILE to the JB_PG_TEST_* env file}"

run_check() {
  "$DOCKER" run --rm --network "$TEST_NETWORK" --env-file "$TEST_ENV_FILE" --entrypoint python "$TEST_IMAGE" -m tests.backup_restore_check "$@" | tr -d '\r'
}

marker="$("$DOCKER" exec -u postgres "$PG_CONTAINER" psql -U postgres -tAc "SHOW cluster_name" | tr -d '\r')"
[ "$marker" = "jukebox101-disposable-test" ] || { echo "refusing: $PG_CONTAINER is not the disposable test server" >&2; exit 2; }

seed_json="$(run_check seed)"
source_db="$(printf '%s' "$seed_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["database"])')"
restored_db="jb_t_r$(python3 -c 'import secrets; print(secrets.token_hex(5))')"
dump_path="/tmp/${source_db}.dump"
cleanup() {
  "$DOCKER" exec -u postgres "$PG_CONTAINER" rm -f "$dump_path" >/dev/null 2>&1 || true
  run_check drop "$source_db" "$restored_db" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"$DOCKER" exec -u postgres "$PG_CONTAINER" pg_dump -U jukebox101_migrator -Fc -d "$source_db" -f "$dump_path"
"$DOCKER" exec -u postgres "$PG_CONTAINER" createdb -U postgres -T template0 -E UTF8 "$restored_db"
"$DOCKER" exec -u postgres "$PG_CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 -q -d "$restored_db" \
  -c "REVOKE ALL ON DATABASE \"$restored_db\" FROM PUBLIC" \
  -c "GRANT CONNECT ON DATABASE \"$restored_db\" TO jukebox101_migrator, jukebox101_app, jukebox101_readonly" \
  -c "REVOKE ALL ON SCHEMA public FROM PUBLIC"
"$DOCKER" exec -u postgres "$PG_CONTAINER" pg_restore -U postgres --exit-on-error --no-password -d "$restored_db" "$dump_path"

verify_json="$(run_check verify "$source_db" "$restored_db")"
echo "source=$source_db restored=$restored_db dump_bytes=$("$DOCKER" exec "$PG_CONTAINER" stat -c %s "$dump_path" | tr -d '\r')"
echo "$verify_json"
printf '%s' "$verify_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["digests_identical"] and not d["pending_migrations"] and d["new_share_accepted"] else 1)'
echo "BACKUP/RESTORE VERIFIED"
