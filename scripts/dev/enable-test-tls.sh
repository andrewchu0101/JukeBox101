#!/usr/bin/env bash
# Enable TLS only on the marked disposable server. Private keys stay inside its disposable volume.
# Usage: PG_CONTAINER=jukebox101-localtest-postgres-1 scripts/dev/enable-test-tls.sh
set -euo pipefail
DOCKER="${DOCKER:-docker}"
PG_CONTAINER="${PG_CONTAINER:?set PG_CONTAINER to the disposable PostgreSQL container}"
marker="$("$DOCKER" exec -u postgres "$PG_CONTAINER" psql -U postgres -tAc 'SHOW cluster_name' | tr -d '\r')"
[ "$marker" = jukebox101-disposable-test ] || { echo 'refusing unmarked server' >&2; exit 2; }
"$DOCKER" exec -i -u postgres "$PG_CONTAINER" bash -se <<'TLS'
set -euo pipefail
umask 077
directory=/var/lib/postgresql/jb-test-tls
if [ ! -e "$directory/ca.crt" ]; then
    mkdir -p "$directory"
    openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj /CN=JukeBox101-disposable-test-CA \
        -keyout "$directory/ca.key" -out "$directory/ca.crt" >/dev/null 2>&1
    openssl req -newkey rsa:2048 -nodes -subj /CN=postgres \
        -keyout "$directory/server.key" -out "$directory/server.csr" >/dev/null 2>&1
    printf '%s\n' 'subjectAltName=DNS:postgres,DNS:jb-v7-pg' 'extendedKeyUsage=serverAuth' > "$directory/server.ext"
    openssl x509 -req -days 30 -in "$directory/server.csr" -CA "$directory/ca.crt" -CAkey "$directory/ca.key" \
        -CAcreateserial -extfile "$directory/server.ext" -out "$directory/server.crt" >/dev/null 2>&1
    chmod 0600 "$directory"/*.key
fi
psql -U postgres -v ON_ERROR_STOP=1 -q \
    -c "ALTER SYSTEM SET ssl_cert_file = '/var/lib/postgresql/jb-test-tls/server.crt'" \
    -c "ALTER SYSTEM SET ssl_key_file = '/var/lib/postgresql/jb-test-tls/server.key'" \
    -c "ALTER SYSTEM SET ssl = 'on'" -c 'SELECT pg_reload_conf()'
TLS
echo 'Disposable TLS configured; run the postgres tests and require zero skips.'
