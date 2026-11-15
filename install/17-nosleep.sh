#!/usr/bin/env bash
# Phase 1, step 7: keep the laptop awake. Ignore the lid. Mask sleep targets.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

changed=0
write_file /etc/systemd/logind.conf.d/50-agent-fleet.conf 0644 root:root <<'CONF' && changed=1 || true
[Login]
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
IdleAction=ignore
CONF
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1
# A HUP makes logind read its config again. A restart could end login sessions.
if [ "$changed" -eq 1 ]; then systemctl kill -s HUP systemd-logind || true; fi
for t in sleep suspend hibernate hybrid-sleep; do
  [ "$(systemctl is-enabled ${t}.target 2>&1)" = "masked" ] || die "${t}.target is not masked"
done
log "lid switch setting: $(busctl get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager HandleLidSwitch 2>&1)"
log "step 17-nosleep done"
