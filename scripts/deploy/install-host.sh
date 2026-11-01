#!/bin/sh
# Install (or rotate the SSH key of) the JukeBox101 deploy entry point. Safe to re-run.
# It never touches the running Bot, secret contents, bot.env, or an existing compose.cd.yaml.
# It creates the two external Docker networks and the secrets directory if they are missing.
set -eu
cd "$(dirname "$0")/../.."
if [ "${1:-}" != --pubkey ] || [ -z "${2:-}" ] || [ "${3:-}" != --apply ]; then
  printf '%s\n' 'Usage (as root): sh scripts/deploy/install-host.sh --pubkey <file.pub> --apply'
  printf '%s\n' 'Installs: deploy script, sudoers rule, restricted ssh key, state directory. Nothing else.'
  exit 0
fi
pubkey_file=$2
test "$(id -u)" -eq 0
for tool in /usr/bin/docker /usr/bin/flock /usr/bin/sudo /usr/sbin/sshd /usr/sbin/visudo /usr/bin/ssh-keygen; do test -x "$tool"; done
test "$(wc -l < "$pubkey_file")" -le 1
grep -q '^ssh-ed25519 [A-Za-z0-9+/=]* *[^ ]*$' "$pubkey_file"
key_body=$(cut -d' ' -f1,2 "$pubkey_file")
/usr/sbin/visudo -cf deploy/host/jukebox101-deploy.sudoers > /dev/null
id jukebox-deploy > /dev/null 2>&1 || useradd --system --no-create-home --shell /bin/sh jukebox-deploy
passwd -l jukebox-deploy > /dev/null
install -d -o root -g root -m 0755 /usr/local/libexec /etc/jukebox101 /etc/ssh/authorized_keys
install -d -o root -g root -m 0700 /var/lib/jukebox101-deploy
# Secret files themselves are placed by the administrator: owner 10001:10001, mode 0400.
install -d -o 10001 -g 10001 -m 0500 /etc/jukebox101/secrets
for network in jukebox101_egress jukebox101_database; do
  /usr/bin/docker network inspect "$network" > /dev/null 2>&1 || /usr/bin/docker network create "$network" > /dev/null
done
install -o root -g root -m 0755 deploy/host/jukebox101-deploy /usr/local/libexec/jukebox101-deploy
install -o root -g root -m 0440 deploy/host/jukebox101-deploy.sudoers /etc/sudoers.d/jukebox101-deploy
test -e /etc/jukebox101/compose.cd.yaml || install -o root -g root -m 0644 deploy/controlled/compose.yaml /etc/jukebox101/compose.cd.yaml
printf 'restrict,command="/usr/bin/sudo -n /usr/local/libexec/jukebox101-deploy" %s jukebox101-deploy\n' "$key_body" > /etc/ssh/authorized_keys/jukebox-deploy.new
chmod 0644 /etc/ssh/authorized_keys/jukebox-deploy.new
mv /etc/ssh/authorized_keys/jukebox-deploy.new /etc/ssh/authorized_keys/jukebox-deploy
cat > /etc/ssh/sshd_config.d/50-jukebox101-deploy.conf <<'CONF'
Match User jukebox-deploy
    AuthorizedKeysFile /etc/ssh/authorized_keys/jukebox-deploy
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding no
    X11Forwarding no
CONF
/usr/sbin/sshd -t
/usr/sbin/sshd -T -C user=jukebox-deploy,host=localhost,addr=127.0.0.1 | grep -q '^authorizedkeysfile /etc/ssh/authorized_keys/jukebox-deploy$'
systemctl reload ssh 2> /dev/null || systemctl reload sshd
printf 'Installed. Key fingerprint: %s\n' "$(ssh-keygen -lf /etc/ssh/authorized_keys/jukebox-deploy)"
printf '%s\n' 'Next: ensure /etc/jukebox101/bot.env and the secret files exist, then run the first deploy from GitHub.'
