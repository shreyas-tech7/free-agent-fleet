#!/usr/bin/env bash
# Phase 4, step 5b: the Slack adapter.
#   - a Python venv with Bolt and the Slack SDK, from a hash-locked requirements file
#   - the folder where the board exporter writes its snapshot
# Bolt is only for the Socket Mode listener. The kill-switch bot uses no package.
# Safe to run twice. No key is needed. The adapter idles until the Fleet HQ tokens exist.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

REPO=/opt/agent-fleet/repo
export FLEET_REPO_DIR="$REPO"
VENV=/opt/agent-fleet/venvs/control

install -d -m 0755 -o root -g root /opt/agent-fleet/venvs
if [ ! -x "$VENV/bin/python" ]; then
  log "make the venv"
  uv venv --quiet --system-site-packages --python /usr/bin/python3 "$VENV"
fi
# The adapter imports ops.fleetlib, which needs PyYAML. Only Bolt and the Slack SDK come from the lock file. The rest comes from the system packages. See D-062.
sed -i 's/^include-system-site-packages *=.*/include-system-site-packages = true/' "$VENV/pyvenv.cfg"
uv pip install --quiet --python "$VENV/bin/python" --require-hashes -r "$REPO/vendor/control-requirements.txt"
# The version is in the package metadata. slack_bolt has no __version__ attribute. See D-058.
"$VENV/bin/python" -c "import importlib.metadata as m, slack_bolt, slack_sdk; print('control venv: slack-bolt', m.version('slack-bolt'), 'slack-sdk', m.version('slack-sdk'))"
# The same import chain as the unit. A missing module stops the installer here and not in a crash loop later.
(cd "$REPO" && "$VENV/bin/python" -c "import ops.control.slackbot") || die "the Slack adapter cannot import its modules"
for pkg in slack-bolt slack-sdk; do
  v=$("$VENV/bin/python" -c "import importlib.metadata as m; print(m.version('$pkg'))")
  lock_add --name "$pkg" --kind pypi --source "https://pypi.org/project/$pkg/" --version "$v" --sha256 "see vendor/control-requirements.txt" --license "MIT"
done

# The exporter runs as fleet and writes here. The control bot (fleetops, in the group fleetops) reads it.
install -d -m 2755 -o fleet -g fleetops /var/lib/agent-fleet/board
log "board folder ready"
log "step 54-slack done"
