#!/bin/sh
# First installation only. It leaves every CD gate off and never touches running services.
set -eu
cd "$(dirname "$0")/../.."
if [ "${1:-}" != --apply ] || [ "$#" -ne 1 ]; then
  printf '%s\n' 'Plan: install root-owned controller, queue, units and restricted sudo helper.'
  printf '%s\n' 'No Bot restart, secret/account installation, journal bootstrap or CD enablement.'
  printf '%s\n' 'Review docs/deploy/continuous-deployment.md, then run with --apply as root.'
  exit 0
fi
test "$(id -u)" -eq 0
test ! -e /usr/local/lib/jukebox101-cd
test ! -e /etc/jukebox101/cd-policy.json
test ! -e /etc/jukebox101/compose.cd.yaml
test ! -e /var/lib/jukebox101-cd/journal.json
test ! -e /etc/sudoers.d/jukebox101-cd
id jukebox-deploy >/dev/null
test -x /usr/bin/docker
test -x /usr/bin/gh
test -x /usr/bin/python3
/usr/sbin/visudo -cf deploy/host/jukebox101-cd.sudoers >/dev/null
install -d -o root -g root -m 0755 /usr/local/lib/jukebox101-cd/deploy /usr/local/lib/jukebox101-cd/scripts/ci
install -o root -g root -m 0644 deploy/*.py /usr/local/lib/jukebox101-cd/deploy/
install -o root -g root -m 0644 scripts/ci/__init__.py scripts/ci/runtime_smoke.py /usr/local/lib/jukebox101-cd/scripts/ci/
install -d -o root -g root -m 0700 /var/lib/jukebox101-cd /var/lib/jukebox101-cd/requests
install -d -o root -g root -m 0755 /etc/jukebox101 /usr/local/libexec
install -o root -g root -m 0600 deploy/host/policy.json.example /etc/jukebox101/cd-policy.json
install -o root -g root -m 0644 deploy/controlled/compose.yaml /etc/jukebox101/compose.cd.yaml
install -o root -g root -m 0755 deploy/host/jukebox101-cd-ssh deploy/host/jukebox101-cd-ingress /usr/local/libexec/
install -o root -g root -m 0440 deploy/host/jukebox101-cd.sudoers /etc/sudoers.d/jukebox101-cd
install -o root -g root -m 0644 deploy/host/jukebox101-cd.service deploy/host/jukebox101-cd.path deploy/host/jukebox101-cd.timer /etc/systemd/system/
printf '%s\n' 'Installed with CD disabled. Units have not been enabled or started.'
printf '%s\n' 'Complete host inventory, provenance acceptance, compatible baseline and key setup before enabling.'
