#!/usr/bin/env bash
# Phase 4, step 14: bring the system up and leave it idle.
#   1. The fleet starts paused with the reason waiting_for_keys. Services run. Agents wait. (Definition of Done D5)
#   2. The router gets its restricted client keys (for Hermes and the briefing).
#   3. The profiles are synced again while the fleet is paused. The first message of each Bot Chat is then blocked by the
#      pause hook, so no model is called.
#   4. Every unit of agent-fleet.target starts. Units that run already are not restarted.
#   5. `fleetctl doctor` prints the state of every component.
# The owner runs `fleetctl setup` later. It adds keys and ends the pause.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

LIVE=/opt/agent-fleet/repo
FLEETCTL=/usr/local/bin/fleetctl
[ -x "$FLEETCTL" ] || die "run install/62-fleetctl.sh first"

# 1. paused until keys exist
if [ ! -e /var/lib/agent-fleet/PAUSED ]; then
  ready=$(cd "$LIVE" && python3 -c "from core.killswitch import killswitch as k; print('yes' if k.ready_to_run()[0] else 'no')")
  if [ "$ready" = "no" ]; then
    "$FLEETCTL" pause --reason waiting_for_keys --by installer
  else
    log "a provider key exists, so the fleet is not paused"
  fi
else
  log "the fleet is paused already: $(cat /var/lib/agent-fleet/PAUSED | tr -d '\n ' | cut -c1-80)"
fi

# 2. router client keys
if "$FLEETCTL" router apply; then :; else warn "router apply failed. When the router is up, run: fleetctl router apply"; fi

# 3. profiles with the Bot Chat sessions
(cd "$LIVE" && python3 -m ops.profiles.sync) > /tmp/fleet-profile-sync.$$ 2>&1 || { tail -30 /tmp/fleet-profile-sync.$$; rm -f /tmp/fleet-profile-sync.$$; die "profile sync failed"; }
python3 -c "
import json, sys
r = json.load(open('/tmp/fleet-profile-sync.$$'))
for name, steps in sorted(r['agents'].items()):
    print('[install] %-13s botmode: %s' % (name, '; '.join(steps.get('botmode', ['skipped']))))
"
rm -f /tmp/fleet-profile-sync.$$

# 4. start what is not running
systemctl daemon-reload
systemctl start agent-fleet.target
systemctl start agent-fleet-control.service 2>/dev/null || true
systemctl start agent-fleet-slack.service 2>/dev/null || true       # outside the target, like the control bot. It idles until the Slack tokens exist. See D-052.

# 5. n8n: the API key, the credentials, and the workflows. It waits for n8n to be ready. A failure is not fatal.
if [ "$(cfgget n8n.enabled true)" = "true" ]; then
  if "$FLEETCTL" n8n bootstrap; then :; else warn "n8n bootstrap failed. When n8n is ready, run: fleetctl n8n bootstrap"; fi
fi

# 6. the state of the machine
"$FLEETCTL" doctor || warn "doctor reports problems. Read the lines above."
log "step 70-bringup done"
