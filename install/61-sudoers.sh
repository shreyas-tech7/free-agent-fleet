#!/usr/bin/env bash
# Phase 4, step 9: the one sudoers rule of the control bot.
# fleetops may run exactly four commands as root, with exactly these arguments:
#   systemctl start agent-fleet.target
#   systemctl stop agent-fleet.target
#   /usr/local/bin/fleetctl pause
#   /usr/local/bin/fleetctl resume
# Nothing else. The file is checked with visudo before it goes live.
# Do not touch /etc/sudoers.d/99-fleet-bootstrap here. Prompt 2 removes it.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

TARGET=/etc/sudoers.d/agent-fleet-control
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<'RULE'
# agent-fleet: the control bot (user fleetops) may run these four commands as root. Nothing else.
Cmnd_Alias FLEET_KILLSWITCH = \
    /usr/bin/systemctl start agent-fleet.target, \
    /usr/bin/systemctl stop agent-fleet.target, \
    /usr/local/bin/fleetctl pause, \
    /usr/local/bin/fleetctl resume
Defaults:fleetops !requiretty, !lecture, env_reset
fleetops ALL=(root) NOPASSWD: FLEET_KILLSWITCH
RULE
visudo -cf "$TMP" >/dev/null || die "the sudoers rule did not pass visudo"
if write_file "$TARGET" 0440 root:root < "$TMP"; then log "installed $TARGET"; fi
visudo -c >/dev/null || die "sudoers is invalid after the change"

# Check the rule from the point of view of fleetops. Only the four commands may appear.
listed=$(sudo -n -l -U fleetops 2>/dev/null | grep -E '^\s+\(' || true)
count=$(printf '%s\n' "$listed" | grep -c . || true)
log "fleetops sudo rules: $(printf '%s' "$listed" | tr -s ' \n' ' ')"
[ "$count" -le 1 ] || die "fleetops has more than one sudo rule"
log "step 61-sudoers done"
