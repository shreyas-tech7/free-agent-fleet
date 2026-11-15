#!/usr/bin/env bash
# Phase 4, step 2: Kanban and the base settings of the default Hermes profile.
# The default profile hosts the one gateway. It holds the dispatcher and the cron ticker.
# Settings go in through `hermes config set`. The managed config pins the safety keys.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

command -v /home/fleet/.local/bin/hermes >/dev/null 2>&1 || [ -x /home/fleet/.local/bin/hermes ] || die "run install/40-hermes.sh first"

tz=$(cfgget owner.timezone America/Chicago)
workers=$(cfgget limits.max_concurrent_workers 1)

run_fleet hermes kanban init >/dev/null 2>&1 || true
set_cfg() { run_fleet hermes config set "$1" "$2" >/dev/null; log "hermes config: $1 = $2"; }

set_cfg kanban.dispatch_in_gateway true
set_cfg kanban.orchestrator_profile orchestrator
set_cfg kanban.auto_decompose false
set_cfg kanban.max_in_progress "$workers"
set_cfg kanban.max_in_progress_per_profile 1
set_cfg kanban.failure_limit 2
set_cfg model.default fleet-worker
set_cfg timezone "$tz"

# A gateway that started by itself for a one-shot chat exits when idle. Stop it so the service owns the role.
run_fleet hermes gateway stop >/dev/null 2>&1 || true
log "kanban boards: $(run_fleet hermes kanban boards list 2>&1 | head -3 | tr '\n' ' ')"
log "step 41-kanban done"
