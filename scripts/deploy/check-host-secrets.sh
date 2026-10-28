#!/usr/bin/env bash
# Deployment pre-check for host secret source files; reads only ownership and permission bits, never the content.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "用法：$0 <秘密檔案路徑>" >&2
  echo "預期擁有者與群組由環境變數 SECRET_EXPECTED_UID、SECRET_EXPECTED_GID 提供（數字 uid、gid）。" >&2
  exit 2
fi

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repository_root"
exec "${PYTHON:-python3}" -m bot.secret_file_policy "$1" \
  --expected-owner-uid "${SECRET_EXPECTED_UID-}" \
  --expected-group-gid "${SECRET_EXPECTED_GID-}"
