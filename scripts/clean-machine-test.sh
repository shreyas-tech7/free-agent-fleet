#!/usr/bin/env bash
# Clean-machine test. Run the installer of a public export in a throwaway debian:trixie container, then run doctor and the tests.
# A container has no systemd and no Docker, so the installer runs in its container test mode. See D-051.
#
# Usage: scripts/clean-machine-test.sh PUBLIC_TREE [--keep]
# PUBLIC_TREE is a folder made by `fleetctl public export`. It must be readable by the user that runs Docker.
# Docker must work for the current user (rootless Docker). The test needs network access for apt, Node.js, and the pinned tools.
set -euo pipefail
tree="${1:?usage: clean-machine-test.sh PUBLIC_TREE [--keep]}"
keep="${2:-}"
image="${CLEAN_TEST_IMAGE:-debian:trixie}"
name="fleet-clean-test-$$"
[ -f "$tree/install.sh" ] || { echo "no install.sh in $tree" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is not available for this user" >&2; exit 2; }

cleanup() { [ "$keep" = "--keep" ] || docker rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "== start $image as $name"
docker run -d --name "$name" --network bridge -v "$tree:/src:ro" "$image" sleep 3600 >/dev/null

inside() { docker exec -e DEBIAN_FRONTEND=noninteractive "$name" bash -c "$1"; }

echo "== copy the tree into the container (a clone, as a user would have)"
inside 'apt-get update -qq && apt-get install -y -qq git >/dev/null && mkdir -p /root/free-agent-fleet && cp -a /src/. /root/free-agent-fleet/ && cd /root/free-agent-fleet && git init -q 2>/dev/null; git config --global safe.directory "*"; echo copied'

echo "== run install.sh in container test mode"
inside 'cd /root/free-agent-fleet && FLEET_INSTALL_MODE=container ./install.sh' 2>&1 | tail -n 45
echo "installer exit: ${PIPESTATUS[0]}"

echo "== fleetctl doctor"
set +e
inside 'fleetctl doctor' 2>&1 | tail -n 70
doctor_rc=${PIPESTATUS[0]}
echo "doctor exit: $doctor_rc"

echo "== fleetctl test (as a normal user, like a person who runs it)"
inside 'id tester >/dev/null 2>&1 || useradd -m -s /bin/bash tester; rm -rf /home/tester/free-agent-fleet; cp -a /root/free-agent-fleet /home/tester/free-agent-fleet; chown -R tester:tester /home/tester/free-agent-fleet; su tester -c "cd /home/tester/free-agent-fleet && FLEET_TESTING=1 /usr/local/bin/fleetctl test"' 2>&1 | tail -n 40
test_rc=${PIPESTATUS[0]}
echo "test exit: $test_rc"
set -e

cat <<'TXT'

== what this test did not cover
- systemd units, timers, and start at boot
- rootless Docker, the model router, SearXNG, n8n, and the gate container
- the firewall, sleep masking, Tailscale, and the sudoers rule
- the Hermes install, the agent profiles, and the injection screen model
- calls to a real model, a real chat app, or any social platform
TXT
[ "$doctor_rc" -eq 0 ] && [ "$test_rc" -eq 0 ]
