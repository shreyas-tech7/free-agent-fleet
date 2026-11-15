#!/usr/bin/env bash
# Phase 5 support: the GEPA runner of the improver (NousResearch/hermes-agent-self-evolution).
#   - the source is the archive of one pinned commit. Its SHA-256 must match vendor/pins.env.
#   - the Python packages come from vendor/evolve-requirements.txt. Every package has a hash.
#   - everything sits in root-owned folders. The weekly job runs as the user fleet and only reads them.
#   - `--dry-run` of the tool checks the setup without a model.
# The weekly job skips itself until a model works. Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

SRC_DIR=/opt/agent-fleet/vendor/hermes-agent-self-evolution
VENV=/opt/agent-fleet/venvs/evolve
[ -f "$FLEET_REPO_DIR/vendor/evolve-requirements.txt" ] || die "vendor/evolve-requirements.txt is missing"

COMMIT=$(pin EVOLVE_COMMIT)
SHA=$(pin EVOLVE_SHA256)
if [ ! -f "$SRC_DIR/.pinned-$COMMIT" ]; then
  work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
  fetch "https://github.com/NousResearch/hermes-agent-self-evolution/archive/${COMMIT}.tar.gz" "$work/src.tar.gz"
  got=$(sha256sum "$work/src.tar.gz" | cut -d' ' -f1)
  [ "$got" = "$SHA" ] || die "the self-evolution archive differs from the pin. Expected $SHA and got $got."
  mkdir "$work/x"; tar -xzf "$work/src.tar.gz" -C "$work/x" --no-same-owner
  top=$(find "$work/x" -mindepth 1 -maxdepth 1 -type d | head -1)
  [ -f "$top/pyproject.toml" ] || die "the archive has no pyproject.toml"
  install -d -m 0755 -o root -g root /opt/agent-fleet/vendor
  [ -d "$SRC_DIR" ] && mv "$SRC_DIR" "${SRC_DIR}.old-$(date +%s)"
  cp -a "$top" "$SRC_DIR"
  chown -R root:root "$SRC_DIR"; chmod -R go-w "$SRC_DIR"
  touch "$SRC_DIR/.pinned-$COMMIT"
  rm -rf "$work"; trap - EXIT
fi

install -d -m 0755 -o root -g root /opt/agent-fleet/venvs
[ -x "$VENV/bin/python" ] || { log "make the venv"; uv venv --quiet --python /usr/bin/python3 "$VENV"; }
uv pip install --quiet --python "$VENV/bin/python" --require-hashes -r "$FLEET_REPO_DIR/vendor/evolve-requirements.txt"
uv pip install --quiet --python "$VENV/bin/python" --no-deps --no-build-isolation "$SRC_DIR"
chmod -R go+rX,go-w "$VENV"

# The tool checks its own setup. It needs no model for this.
out=$(cd /tmp && env -i HOME=/tmp PATH=/usr/bin:/bin HERMES_AGENT_REPO="$FLEET_REPO_DIR" "$VENV/bin/python" -m evolution.skills.evolve_skill --skill copywriting --hermes-repo "$FLEET_REPO_DIR" --dry-run 2>&1) \
  || { printf '%s\n' "$out" | tail -8; die "the self-evolution tool failed its own dry run"; }
printf '%s\n' "$out" | tail -3 | sed 's/^/[install] /'

lock_add --name hermes-agent-self-evolution --kind git --source "https://github.com/NousResearch/hermes-agent-self-evolution" --version "$COMMIT" --sha256 "$SHA (source archive)" --license "MIT"
lock_add --name evolve-python-packages --kind pypi --source "https://pypi.org" --version "see vendor/evolve-requirements.txt" --sha256 "a hash for each package in that file" --license "various, see each package"
log "step 43-evolve done"
