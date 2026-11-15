#!/usr/bin/env bash
# Phase 4, step 9 (part 2): install fleetctl at /usr/local/bin/fleetctl.
# Root owns the file and only root can write it, because fleetops runs `fleetctl pause` and
# `fleetctl resume` through sudo. The file is a small launcher. It runs the code of
# /opt/agent-fleet/repo, which root owns too.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

LIVE=/opt/agent-fleet/repo
[ -f "$LIVE/fleetctl" ] || die "run install/20-repo.sh first"
if install -m 0755 -o root -g root "$LIVE/fleetctl" /usr/local/bin/fleetctl.new; then
  if [ -f /usr/local/bin/fleetctl ] && cmp -s /usr/local/bin/fleetctl.new /usr/local/bin/fleetctl; then
    rm -f /usr/local/bin/fleetctl.new
    log "fleetctl is up to date"
  else
    mv -f /usr/local/bin/fleetctl.new /usr/local/bin/fleetctl
    log "installed /usr/local/bin/fleetctl"
  fi
fi
stat -c '%U:%G %a' /usr/local/bin/fleetctl | grep -qx 'root:root 755' || die "fleetctl must be root:root mode 755"
/usr/local/bin/fleetctl --version
log "step 62-fleetctl done"
