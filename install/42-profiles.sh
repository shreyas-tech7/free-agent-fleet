#!/usr/bin/env bash
# Phase 4, step 3: one Hermes profile for each agent, made from agents/<name>/ in the live repository.
#   - SOUL.md, model, toolsets, sandbox mounts, MCP servers, skills, and cron jobs
#   - a root-owned copy of each agent's skills in /opt/agent-fleet/skills-view
#   - the router key and the local tokens in the profile .env files
#   - the hooks folder of each profile becomes root-owned, so no agent can add a hook
# The Bot Chat sessions come in step 70, while the fleet is paused. A first message would call a model.
# Safe to run twice. A run with no change makes no change.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

LIVE=/opt/agent-fleet/repo
[ -d "$LIVE/agents" ] || die "run install/20-repo.sh first"
command -v /home/fleet/.local/bin/hermes >/dev/null 2>&1 || [ -x /home/fleet/.local/bin/hermes ] || die "run install/40-hermes.sh first"

install -d -m 0755 -o root -g root /opt/agent-fleet/skills-view
# Folders that agents write to. The sandbox of an agent mounts some of them.
install -d -m 0750 -o fleet -g fleet /var/lib/agent-fleet/requests /var/lib/agent-fleet/feedback

report=$(cd "$LIVE" && python3 -m ops.profiles.sync --no-botmode) || { printf '%s\n' "$report" | tail -40; die "profile sync failed"; }
printf '%s\n' "$report" | python3 -c "
import json, sys
r = json.load(sys.stdin)
for name, steps in sorted(r['agents'].items()):
    bad = [k for k, v in steps.items() if isinstance(v, str) and v.startswith('FAILED')]
    print('[install] profile %-13s %s' % (name, 'FAILED ' + ','.join(bad) if bad else 'ok'))
print('[install] work clone: %s' % r['work_clone'])
"
log "step 42-profiles done"
