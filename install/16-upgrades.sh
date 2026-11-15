#!/usr/bin/env bash
# Phase 1, step 6: unattended security upgrades.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

apt_ensure unattended-upgrades
write_file /etc/apt/apt.conf.d/20auto-upgrades 0644 root:root <<'CONF' || true
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
# Never reboot on its own. The owner decides when to reboot.
write_file /etc/apt/apt.conf.d/52agent-fleet-upgrades 0644 root:root <<'CONF' || true
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
CONF
systemctl enable --now apt-daily.timer apt-daily-upgrade.timer >/dev/null 2>&1 || true
unattended-upgrade --dry-run 2>&1 | tail -3 || true
log "timers: $(systemctl is-enabled apt-daily-upgrade.timer 2>&1)"
log "step 16-upgrades done"
