#!/usr/bin/env bash
# Prompt 3, Phase 1: n8n, the workflow engine that does the work that needs no model.
#   - folders for fleetops, the secrets that the installer owns, the owner password hash, the compose .env
#   - the pinned image, pulled into the rootless Docker of fleetops
# The unit agent-fleet-n8n.service is installed by step 60 and starts in step 70. Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

REPO=/opt/agent-fleet/repo
export FLEET_REPO_DIR="$REPO"
if [ "$(cfgget n8n.enabled true)" != "true" ]; then
  log "n8n is off in the config. Skipping."
  exit 0
fi
command -v docker >/dev/null || die "docker is missing. Run step 11 first."
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is missing. Run step 11 first."

image=$(pin N8N_IMAGE)
version=$(pin N8N_VERSION)
(cd "$REPO" && python3 -m ops.n8n.prepare) | python3 -c "
import json, sys
r = json.load(sys.stdin)
if r.get('status') != 'ok':
    print('[install] ERROR: n8n prepare failed: %s' % r.get('reason', 'unknown'), file=sys.stderr); sys.exit(1)
print('[install] n8n prepare: image %s, owner hash %s, env %s, secrets made: %s' % (r['image'], r['owner_hash'], r['env'], ', '.join(r['secrets']) or 'none'))
for w in r.get('warnings', []): print('[install] WARN: %s' % w)
"
lock_add --name n8n --kind docker --source "docker.n8n.io/n8nio/n8n (official image of github.com/n8n-io/n8n)" --version "$version" --sha256 "${image##*@sha256:}" --license "Sustainable Use License"
log "step 56-n8n done"
