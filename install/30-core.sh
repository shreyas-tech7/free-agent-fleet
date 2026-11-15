#!/usr/bin/env bash
# Phase 3: the locked core on the installed system.
#   - /etc/hermes/config.yaml  the Hermes managed config (root, 0644)
#   - hidden tests             /opt/agent-fleet/core/tests/hidden (root, 0700)
#   - /etc/agent-fleet/fleet.yaml  minimal live config with the capacity tier
# Safe to run twice. Run after 20-repo.sh.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

LIVE=/opt/agent-fleet/repo
[ -d "$LIVE/.git" ] || die "run install/20-repo.sh first"

# ---- Hermes managed config ----
image=$(FLEET_REPO_DIR="$LIVE" bash -c ". $LIVE/install/lib.sh; pin HERMES_SANDBOX_IMAGE")
install -d -m 0755 -o root -g root /etc/hermes
rendered=$(sed "s|@@HERMES_SANDBOX_IMAGE@@|${image}|g" "$LIVE/config/hermes.managed.yaml")
if printf '%s\n' "$rendered" | write_file /etc/hermes/config.yaml 0644 root:root; then
  log "installed /etc/hermes/config.yaml"
else
  log "/etc/hermes/config.yaml is up to date"
fi
# Fixed non-secret environment values. The file is world-readable by design.
sed "s|127.0.0.1:8888|127.0.0.1:$(cfgget ports.searxng 8888)|" "$LIVE/config/hermes.managed.env" | write_file /etc/hermes/.env 0644 root:root || true
grep -q '@@' /etc/hermes/config.yaml && die "a placeholder is left in /etc/hermes/config.yaml"
python3 -c "import yaml,sys; yaml.safe_load(open('/etc/hermes/config.yaml'))" || die "managed config is not valid YAML"

# ---- hidden tests ----
install -d -m 0755 -o root -g root /opt/agent-fleet/core /opt/agent-fleet/core/tests
install -d -m 0700 -o root -g root /opt/agent-fleet/core/tests/hidden
python3 "$LIVE/install/make_hidden_tests.py" --out /opt/agent-fleet/core/tests/hidden
chown -R root:root /opt/agent-fleet/core/tests/hidden
chmod 0700 /opt/agent-fleet/core/tests/hidden

# ---- minimal live config ----
install -d -m 0755 -o root -g root /etc/agent-fleet
if [ ! -f /etc/agent-fleet/fleet.yaml ]; then
  mem_kb=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
  if   [ "$mem_kb" -lt 8388608 ];  then tier=small
  elif [ "$mem_kb" -lt 16777216 ]; then tier=medium
  else tier=large; fi
  cat > /etc/agent-fleet/fleet.yaml <<YAML
# Live config. Only the keys that differ from config/fleet.example.yaml are here.
# Change it with: fleetctl config set KEY VALUE
schema_version: 1
capacity:
  tier: ${tier}
YAML
  log "wrote /etc/agent-fleet/fleet.yaml with tier ${tier}"
fi
chown root:fleetops /etc/agent-fleet/fleet.yaml
chmod 0640 /etc/agent-fleet/fleet.yaml

log "step 30-core done"
