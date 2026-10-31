#!/bin/sh
# Create a source-only administrator review bundle. No secrets or Bot images enter it.
set -eu
cd "$(dirname "$0")/../.."
test "$(git rev-parse --show-toplevel)" = "$(pwd)"
mkdir -p artifacts
bundle="$(mktemp -d "$(pwd)/artifacts/cd-host.XXXXXX")"
mkdir -p "$bundle/deploy" "$bundle/scripts/ci" "$bundle/scripts/deploy" "$bundle/docs/deploy"
cp deploy/*.py "$bundle/deploy/"
cp -R deploy/host deploy/controlled "$bundle/deploy/"
cp scripts/ci/__init__.py scripts/ci/runtime_smoke.py "$bundle/scripts/ci/"
cp scripts/deploy/install-cd-host.sh "$bundle/scripts/deploy/"
cp docs/deploy/continuous-deployment.md "$bundle/docs/deploy/"
python3 - "$bundle" <<'PY'
import json, pathlib, subprocess, sys
paths = ['.github/workflows/ci.yml', '.github/workflows/release.yml', '.github/workflows/deploy.yml',
         '.github/workflows/notify.yml']
paths += sorted(str(path) for path in pathlib.Path('bot/database/postgres').rglob('*') if path.is_file())
approved = {path: subprocess.check_output(['git', 'hash-object', '--', path], text=True).strip() for path in paths}
root = pathlib.Path(sys.argv[1])
policy_path = root / 'deploy/host/policy.json.example'
policy = json.loads(policy_path.read_text())
policy['approved_blobs'] = approved
policy_path.write_text(json.dumps(policy, indent=2) + '\n')
(root / 'SOURCE_COMMIT').write_text(subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True))
PY
tar -czf "$bundle.tar.gz" -C "$bundle" .
shasum -a 256 "$bundle.tar.gz"
printf 'Review bundle: %s\n' "$bundle"
