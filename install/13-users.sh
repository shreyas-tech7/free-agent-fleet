#!/usr/bin/env bash
# Phase 1, step 4: system users and directories.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

make_user() {
  local name=$1 comment=$2
  if ! user_exists "$name"; then
    log "create user $name"
    useradd --create-home --shell /bin/bash --user-group --comment "$comment" "$name"
  fi
  usermod --lock "$name"                      # no password login
  chmod 0700 "$(user_home "$name")"
  # Rootless Docker needs a range of sub-UIDs and sub-GIDs.
  grep -q "^${name}:" /etc/subuid || usermod --add-subuids 200000-265535 "$name"
  grep -q "^${name}:" /etc/subgid || usermod --add-subgids 200000-265535 "$name"
}

make_user fleet    "agent-fleet agent runtime"
make_user fleetops "agent-fleet operations services"

# fleetops writes some files in the shared state directory (budget.json and others).
id -nG fleetops | tr ' ' '\n' | grep -qx fleet || usermod --append --groups fleet fleetops

# Neither user may use sudo. Stop here if that is wrong.
for u in fleet fleetops; do
  if id -nG "$u" | tr ' ' '\n' | grep -qxE 'sudo|wheel|admin|docker'; then
    die "$u is in a privileged group"
  fi
done

# No SSH login for the service users.
if [ -d /etc/ssh/sshd_config.d ]; then
  if write_file /etc/ssh/sshd_config.d/60-agent-fleet.conf 0644 root:root <<'CONF'
# agent-fleet: the service users never log in over SSH.
DenyUsers fleet fleetops
CONF
  then
    sshd -t && systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
  fi
fi

# Lingering lets their user services (rootless Docker) run without a login.
if [ "${FLEET_INSTALL_MODE:-}" != "container" ]; then
  loginctl enable-linger fleet fleetops
fi

# Directories.
install -d -m 0755 -o root    -g root     /opt/agent-fleet
install -d -m 0700 -o root    -g root     /var/lib/agent-fleet-merger
install -d -m 3775 -o root    -g fleet    /var/lib/agent-fleet
install -d -m 0750 -o fleetops -g fleetops /var/lib/agent-fleet-ops
install -d -m 0755 -o root    -g root     /etc/agent-fleet
install -d -m 0711 -o root    -g root     /etc/agent-fleet/secrets

# Agent-owned work area.
install -d -m 0755 -o fleet -g fleet /var/lib/agent-fleet/work
# Output of ops services. Agents may read some of it.
install -d -m 0755 -o fleetops -g fleet /var/lib/agent-fleet/briefs
install -d -m 0750 -o fleetops -g fleet /var/lib/agent-fleet/inbox
install -d -m 0750 -o fleetops -g fleetops /var/lib/agent-fleet/outbox
install -d -m 0775 -o fleetops -g fleet /var/lib/agent-fleet/tickets
install -d -m 0755 -o fleetops -g fleet /var/lib/agent-fleet/status
# The nightly export runs as fleet. The backup (fleetops) reads it.
install -d -m 0755 -o fleet -g fleet /var/lib/agent-fleet/exports
# Ops-only state.
for d in backups logs merger router guard publisher deployer control briefing quota portfolio; do
  install -d -m 0750 -o fleetops -g fleetops "/var/lib/agent-fleet-ops/$d"
done

log "users: $(id fleet) | $(id fleetops)"
log "step 13-users done"
