#!/bin/bash
# Run the test suite in a Linux container from a Windows (or any) checkout without touching the repository.
#
#   scripts/dev/run-linux-tests.sh offline  [pytest args]    # -m "not postgres", --network none
#   scripts/dev/run-linux-tests.sh postgres [pytest args]    # -m postgres, against the disposable PostgreSQL 18.6 of compose.test.yaml
#
# Why a copy: git's core.autocrlf turns shell scripts into CRLF on Windows, which bash cannot run, so the container must be built from an
# LF-normalised copy (CI checks out LF and does not need this). The copy lives in a fresh temporary directory that this script removes.
# It only reads the repository, starts nothing but its own randomly named containers, and removes only the test project's volume.
# PostgreSQL 17 is not available here; it runs only in CI (python3 scripts/dev/verify-fresh-postgres.py --postgres-major 17).
set -euo pipefail

mode=${1:-}
case "$mode" in offline | postgres | stress) shift ;; *) echo "usage: $0 offline|postgres [pytest args] | stress [stress_simulation.py options]" >&2; exit 2 ;; esac

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
copy="$(mktemp -d)"
trap '[ "$mode" = offline ] || (cd "$copy" 2> /dev/null && docker compose -f compose.test.yaml down -v > /dev/null 2>&1) || true; rm -rf "$copy"' EXIT

cd "$repo_root"
tar --exclude=.git --exclude=.venv --exclude=.pytest_tmp --exclude=.pytest_cache --exclude='__pycache__' --exclude='*.pdf' \
    --exclude='jukebox101-ssh-*' --exclude=ci-evidence -cf - . | tar -xf - -C "$copy"
find "$copy" -type f \( -name '*.py' -o -name '*.sh' -o -name '*.yaml' -o -name '*.yml' -o -name '*.toml' -o -name '*.txt' -o -name '*.sql' \
    -o -name '*.json' -o -name '*.md' -o -name 'jukebox101-deploy*' -o -name 'Dockerfile*' -o -name '*.dockerignore' -o -name '*.template' \) \
    -exec sed -i 's/\r$//' {} +

cd "$copy"
if [ "$mode" = offline ]; then
  docker build -q -f test.Dockerfile -t jukebox101-test:v7-local . > /dev/null
  MSYS_NO_PATHCONV=1 docker run --rm --network none jukebox101-test:v7-local -q -p no:cacheprovider -m "not postgres" "$@"
elif [ "$mode" = stress ]; then
  JB_LOCALTEST_PG_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(16))' 2> /dev/null || python -c 'import secrets; print(secrets.token_hex(16))')"
  export JB_LOCALTEST_PG_PASSWORD
  docker compose -f compose.test.yaml up -d postgres > /dev/null 2>&1
  docker compose -f compose.test.yaml --profile tests run --build --rm --entrypoint python tests scripts/dev/stress_simulation.py "$@"
else
  JB_LOCALTEST_PG_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(16))' 2> /dev/null || python -c 'import secrets; print(secrets.token_hex(16))')"
  export JB_LOCALTEST_PG_PASSWORD
  docker compose -f compose.test.yaml up -d postgres > /dev/null 2>&1
  docker compose -f compose.test.yaml --profile tests run --build --rm tests -q -p no:cacheprovider -m postgres "$@"
fi
