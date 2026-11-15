#!/usr/bin/env bash
# Phase 4: prepare the services that run in containers or need secrets.
#   - secrets that the installer makes (router session secrets and similar)
#   - pinned container images, pulled by digest as the user that runs them
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

REPO=/opt/agent-fleet/repo
[ -d "$REPO/.git" ] || die "run install/20-repo.sh first"
export FLEET_REPO_DIR="$REPO"

# ---- secret files must exist, so each unit can bind its own file. They start empty. ----
for f in router router-admin searxng publisher deployer control briefing backup quota; do
  [ -e "/etc/agent-fleet/secrets/$f.env" ] || install -m 0600 -o fleetops -g fleetops /dev/null "/etc/agent-fleet/secrets/$f.env"
done

# ---- move the router password into its own file (older installs had it in router.env) ----
if grep -q '^INITIAL_PASSWORD=' /etc/agent-fleet/secrets/router.env 2>/dev/null && ! grep -q '^INITIAL_PASSWORD=' /etc/agent-fleet/secrets/router-admin.env; then
  grep '^INITIAL_PASSWORD=' /etc/agent-fleet/secrets/router.env >> /etc/agent-fleet/secrets/router-admin.env
  sed -i '/^INITIAL_PASSWORD=/d' /etc/agent-fleet/secrets/router.env
  log "moved INITIAL_PASSWORD to router-admin.env"
fi

# ---- secrets the installer makes ----
made=$(PYTHONPATH="$REPO" python3 -c "
from ops.fleetlib import autosecrets
print(','.join(autosecrets.ensure(['JWT_SECRET','API_KEY_SECRET','STORAGE_ENCRYPTION_KEY','INITIAL_PASSWORD','SEARXNG_SECRET'])))")
log "secrets made: ${made:-none (they exist)}"
chown fleetops:fleetops /etc/agent-fleet/secrets/router.env
chmod 600 /etc/agent-fleet/secrets/router.env /etc/agent-fleet/secrets/router-admin.env
chown fleetops:fleetops /etc/agent-fleet/secrets/router-admin.env
chown fleetops:fleetops /etc/agent-fleet/secrets/searxng.env
chmod 600 /etc/agent-fleet/secrets/searxng.env

# ---- images ----
pull_image() {   # pull_image NAME DIGEST_REF LICENSE
  local name=$1 ref=$2 license=$3
  [ -n "$ref" ] || { warn "no pin for $name yet. Skipping."; return 0; }
  if ! run_ops docker image inspect "$ref" >/dev/null 2>&1; then
    log "pulling $name"
    run_ops docker pull "$ref" >/dev/null
  fi
  lock_add --name "$name" --kind docker --source "docker.io/${ref%@*}" --version "$ref" --sha256 "${ref##*@sha256:}" --license "$license"
}
pull_image omniroute "$(pin OMNIROUTE_IMAGE)" "MIT"
pull_image searxng "$(pin SEARXNG_IMAGE 2>/dev/null || true)" "AGPL-3.0"
pull_image gate-base "$(pin GATE_BASE_IMAGE 2>/dev/null || true)" "PSF-2.0 and others (Python official image)"

log "step 50-prepare done"
