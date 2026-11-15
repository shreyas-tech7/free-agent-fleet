#!/usr/bin/env bash
# Phase 4, step 6: the page reader and Crawl4AI.
#   - a Python venv with Crawl4AI and its dependencies, from a hash-locked requirements file
#   - no browser. The reader fetches pages itself and Crawl4AI only converts HTML to Markdown.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

REPO=/opt/agent-fleet/repo
export FLEET_REPO_DIR="$REPO"
VENV=/opt/agent-fleet/venvs/reader

install -d -m 0755 -o root -g root /opt/agent-fleet/venvs
[ -x "$VENV/bin/python" ] || { log "make the venv"; uv venv --quiet --python /usr/bin/python3 "$VENV"; }
uv pip install --quiet --python "$VENV/bin/python" --require-hashes -r "$REPO/vendor/reader-requirements.txt"
ver=$("$VENV/bin/python" -c "import importlib.metadata as m; print(m.version('crawl4ai'))")
lock_add --name crawl4ai --kind pypi --source "https://pypi.org/project/crawl4ai/" --version "$ver" --sha256 "see vendor/reader-requirements.txt" --license "Apache-2.0"
log "crawl4ai $ver in $VENV"

install -d -m 0750 -o fleetops -g fleetops /var/lib/agent-fleet-ops/reader /var/lib/agent-fleet-ops/reader/quarantine
log "step 53-reader done"
