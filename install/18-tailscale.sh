#!/usr/bin/env bash
# Phase 1, step 8: Tailscale from the official apt repository.
# This script never runs `tailscale up`. Prompt 2 does that with the owner.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
# shellcheck source=install/apt-repos.sh
. "$(dirname "$0")/apt-repos.sh"
need_root

add_tailscale_repo
ver=$(pin TAILSCALE_VERSION)
cur=$(dpkg-query -W -f='${Version}' tailscale 2>/dev/null || true)
if [ "$cur" != "$ver" ]; then
  apt-mark unhold tailscale >/dev/null 2>&1 || true
  apt_install "tailscale=$ver"
fi
apt-mark hold tailscale >/dev/null
lock_apt tailscale
tailscale version | head -1
log "tailscale is installed and logged out: $(tailscale status 2>&1 | head -1)"
log "step 18-tailscale done"
