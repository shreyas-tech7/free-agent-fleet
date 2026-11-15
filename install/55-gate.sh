#!/usr/bin/env bash
# Phase 4, step 12 (part 1): the gate image and the scanner rules for the merger.
#   - the gate image (pinned base, hash-locked Python tools, scanner binaries), built in the rootless Docker of fleetops
#   - the semgrep community rules at a pinned commit, kept outside the repository (Semgrep Rules License v1.0)
#   - the OSV vulnerability databases, so that the scan runs offline
# The merger runs the gates in a container without network. Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

REPO=/opt/agent-fleet/repo
export FLEET_REPO_DIR="$REPO"
FLEETOPS_UID=$(user_uid fleetops)
OPS="runuser -u fleetops -- env HOME=/home/fleetops XDG_RUNTIME_DIR=/run/user/$FLEETOPS_UID DOCKER_HOST=unix:///run/user/$FLEETOPS_UID/docker.sock"

BASE=$(pin GATE_BASE_IMAGE)
[ -n "$BASE" ] || die "vendor/pins.env has no GATE_BASE_IMAGE"

# ---- semgrep rules (outside the repository) ----
RULES=/opt/agent-fleet/vendor/semgrep-rules
RULES_COMMIT=$(pin SEMGREP_RULES_COMMIT)
if [ ! -f "$RULES/.commit" ] || [ "$(cat "$RULES/.commit")" != "$RULES_COMMIT" ]; then
  log "fetch semgrep-rules at $RULES_COMMIT"
  work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
  git clone --quiet https://github.com/semgrep/semgrep-rules.git "$work/r"
  git -C "$work/r" -c advice.detachedHead=false checkout --quiet "$RULES_COMMIT"
  rm -rf "$RULES"; install -d -m 0755 -o root -g root "$RULES"
  # Only rule files. Skip the test fixtures. Security rules for the languages that agents write.
  for d in python/lang/security javascript/lang/security bash/lang/security dockerfile/security generic/secrets; do
    [ -d "$work/r/$d" ] || continue
    (cd "$work/r" && find "$d" -type f \( -name '*.yaml' -o -name '*.yml' \) ! -name '*.test.*' -print0 | tar --null -T - -cf -) | tar -xf - -C "$RULES"
  done
  printf '%s\n' "$RULES_COMMIT" > "$RULES/.commit"
  chown -R root:root "$RULES"; chmod -R go-w "$RULES"
  rm -rf "$work"; trap - EXIT
fi
log "semgrep rules: $(find "$RULES" -name '*.y*ml' | wc -l) files at $RULES_COMMIT"
lock_add --name semgrep-rules --kind git --source "https://github.com/semgrep/semgrep-rules" --version "$RULES_COMMIT" --sha256 "n/a (git commit)" --license "Semgrep Rules License v1.0 (not redistributed in this repository)"

# ---- gate image ----
BUILD_HASH=$(cat "$REPO/config/gate/Dockerfile" "$REPO"/vendor/gate-*-requirements.txt /usr/local/bin/gitleaks /usr/local/bin/osv-scanner \
  "$(readlink -f /usr/local/bin/node)" | sha256sum | cut -c1-12)
TAG="agent-fleet-gate:$BUILD_HASH"
if ! $OPS docker image inspect "$TAG" >/dev/null 2>&1; then
  log "build $TAG (this takes a few minutes)"
  ctx=$(mktemp -d); trap 'rm -rf "$ctx"' EXIT
  mkdir -p "$ctx/bin" "$ctx/req"
  cp "$REPO/config/gate/Dockerfile" "$ctx/Dockerfile"
  cp "$REPO"/vendor/gate-*-requirements.txt "$ctx/req/"
  cp /usr/local/bin/gitleaks /usr/local/bin/osv-scanner "$ctx/bin/"
  cp "$(readlink -f /usr/local/bin/node)" "$ctx/bin/node"
  chown -R fleetops:fleetops "$ctx"
  $OPS docker pull --quiet "$BASE" >/dev/null
  $OPS docker build --quiet --build-arg "BASE=$BASE" -t "$TAG" "$ctx" >/dev/null
  rm -rf "$ctx"; trap - EXIT
fi
IMAGE_ID=$($OPS docker image inspect "$TAG" --format '{{.Id}}')
# The merger reads this root-owned file. It checks that the image still has this ID before each run.
printf '%s %s\n' "$TAG" "$IMAGE_ID" | write_file /opt/agent-fleet/gate-image 0644 root:root || true
log "gate image $TAG $IMAGE_ID"
lock_add --name agent-fleet-gate --kind docker-image --source "built from config/gate/Dockerfile on $BASE" --version "$TAG" --sha256 "$IMAGE_ID" --license "mixed, see the packages inside"

# The scanners inside the image. Each one has its own venv and its own hash-locked requirements file.
req_version() { grep -iE "^$2==" "$REPO/vendor/gate-$1-requirements.txt" | head -1 | sed -E 's/^[^=]+==([^ ;]+).*/\1/'; }
lock_add --name semgrep --kind pypi --source "https://pypi.org/project/semgrep/" --version "$(req_version semgrep semgrep)" --sha256 "hashes in vendor/gate-semgrep-requirements.txt" --license "LGPL-2.1-or-later"
lock_add --name cisco-ai-skill-scanner --kind pypi --source "https://github.com/cisco-ai-defense/skill-scanner" --version "$(req_version skillscan cisco-ai-skill-scanner)" --sha256 "hashes in vendor/gate-skillscan-requirements.txt" --license "Apache-2.0"
lock_add --name cisco-ai-mcp-scanner --kind pypi --source "https://github.com/cisco-ai-defense/mcp-scanner" --version "$(req_version mcpscan cisco-ai-mcp-scanner)" --sha256 "hashes in vendor/gate-mcpscan-requirements.txt" --license "Apache-2.0"
lock_add --name gate-test-packages --kind pypi --source "https://pypi.org" --version "see vendor/gate-test-requirements.txt" --sha256 "hashes in vendor/gate-test-requirements.txt" --license "MIT (pytest, jsonschema, PyYAML)"

# ---- OSV databases (the only step that needs the network at scan time) ----
install -d -m 0750 -o fleetops -g fleetops /var/lib/agent-fleet-ops/merger /var/lib/agent-fleet-ops/merger/osv-cache /var/lib/agent-fleet-ops/merger/runs
log "step 55-gate done"
