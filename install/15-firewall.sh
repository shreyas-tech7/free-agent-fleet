#!/usr/bin/env bash
# Phase 1, step 5: ufw. Deny incoming, allow outgoing, allow SSH.
# The SSH rule goes in before ufw turns on, so SSH keeps working.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

apt_ensure ufw
ports=$(sshd -T 2>/dev/null | awk '/^port / {print $2}' | sort -u)
[ -n "$ports" ] || ports=22

ufw default deny incoming
ufw default allow outgoing
for p in $ports; do
  ufw allow "${p}/tcp" comment 'SSH'
done

# Check the rules before we turn the firewall on.
for p in $ports; do
  ufw show added | grep -qE "allow ${p}/tcp" || die "SSH rule for port $p is missing. ufw stays off."
done
if ! ufw status | grep -q '^Status: active'; then
  ufw --force enable
fi
ufw status verbose | sed -n 1,12p

# Check that the rule is live and that sshd still listens.
for p in $ports; do
  ufw status | grep -qE "^${p}/tcp +ALLOW" || die "SSH rule for port $p is not active"
  ss -tln | grep -qE ":${p}\b" || die "sshd is not listening on port $p"
done
log "step 15-firewall done"
