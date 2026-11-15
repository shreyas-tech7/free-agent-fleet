#!/usr/bin/env bash
# agent-fleet installer.
# It installs everything on a fresh Debian or Ubuntu machine. Safe to run twice.
#
# Usage:
#   ./install.sh                 run every step in order
#   ./install.sh --from 14       start at step 14
#   ./install.sh --only 13       run only step 13
#   ./install.sh --list          list the steps
#
# The installer asks for sudo once. It never asks for a key or a password of a service.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

FROM=""; ONLY=""; LIST=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --list) LIST=1; shift ;;
    -h|--help) sed -n 2,13p "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

steps=()
for f in install/[0-9][0-9]-*.sh; do steps+=("$f"); done

if [ "$LIST" -eq 1 ]; then
  printf '%s\n' "${steps[@]}"
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "The installer needs root. Asking for sudo."
  exec sudo -E env FLEET_REPO_DIR="$HERE" bash "$HERE/install.sh" "$@"
fi
export FLEET_REPO_DIR="$HERE"
export FLEET_SRC_DIR="$HERE"

# ---- container test mode ----
# The clean-machine test runs this installer in a throwaway container. A container has no systemd and no Docker.
# FLEET_INSTALL_MODE=container runs only the steps that need neither. It writes /etc/agent-fleet/test-mode, and `fleetctl doctor`
# then reports every other part as SKIPPED. Never use this mode on a real machine.
MODE="${FLEET_INSTALL_MODE:-}"
CONTAINER_STEPS=" 10 12 13 19 20 30 62 "
if [ "$MODE" = "container" ]; then
  export FLEET_INSTALL_MODE
  install -d -m 0755 /etc/agent-fleet
  printf 'container\n' > /etc/agent-fleet/test-mode
fi

# ---- preflight ----
. /etc/os-release
case "$ID" in
  debian) [ "${VERSION_ID%%.*}" -ge 12 ] || { echo "Debian 12 or newer is required." >&2; exit 1; } ;;
  ubuntu) [ "${VERSION_ID%%.*}" -ge 22 ] || { echo "Ubuntu 22.04 or newer is required." >&2; exit 1; } ;;
  *) echo "Only Debian and Ubuntu are supported. Found: $ID" >&2; exit 1 ;;
esac
case "$(uname -m)" in x86_64|aarch64) ;; *) echo "Only x86_64 and aarch64 are supported." >&2; exit 1 ;; esac
[ "$MODE" = "container" ] || [ "$(ps -p 1 -o comm=)" = "systemd" ] || { echo "systemd must be the init system." >&2; exit 1; }
# A minimal image has no curl. The checks below and the later steps need it.
command -v curl >/dev/null 2>&1 || { apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl ca-certificates; }
curl -fsS -m 15 -o /dev/null https://deb.debian.org || curl -fsS -m 15 -o /dev/null https://archive.ubuntu.com \
  || { echo "No internet access to the package mirrors." >&2; exit 1; }

echo "agent-fleet installer on $PRETTY_NAME"
for s in "${steps[@]}"; do
  n="$(basename "$s" | cut -c1-2)"
  if [ -n "$ONLY" ] && [ "$n" != "$ONLY" ]; then continue; fi
  if [ -n "$FROM" ] && [ "$n" -lt "$FROM" ]; then continue; fi
  if [ "$MODE" = "container" ] && [ "${CONTAINER_STEPS#* $n }" = "$CONTAINER_STEPS" ]; then
    echo "=== $s: skipped in container test mode ==="
    continue
  fi
  echo
  echo "=== $s ==="
  bash "$s"
done
echo
echo "Install finished. Next step: fleetctl setup"
