#!/usr/bin/env bash
# Shared helpers for the installer. Source this file. Do not run it.
# shellcheck shell=bash

set -euo pipefail

FLEET_REPO_DIR="${FLEET_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

log()  { printf '[install] %s\n' "$*"; }
warn() { printf '[install] WARN: %s\n' "$*" >&2; }
die()  { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

need_root() { [ "$(id -u)" -eq 0 ] || die "run this script as root"; }

os_id()       { . /etc/os-release; printf '%s' "$ID"; }
os_codename() { . /etc/os-release; printf '%s' "${UBUNTU_CODENAME:-$VERSION_CODENAME}"; }

pkg_installed() { dpkg -s "$1" >/dev/null 2>&1; }

apt_install() {
  apt-get install -y --no-install-recommends "$@"
}

# Install only the packages that are missing. Prints what it does.
apt_ensure() {
  local missing=() p
  for p in "$@"; do
    pkg_installed "$p" || missing+=("$p")
  done
  if [ "${#missing[@]}" -gt 0 ]; then
    log "apt install: ${missing[*]}"
    apt_install "${missing[@]}"
  else
    log "apt: already installed: $*"
  fi
}

# write_file PATH MODE OWNER:GROUP  (content on stdin)
# Returns 0 when the file changed and 1 when it was already correct.
write_file() {
  local path=$1 mode=$2 owner=$3 tmp
  tmp=$(mktemp)
  cat > "$tmp"
  if [ -f "$path" ] && cmp -s "$tmp" "$path"; then
    rm -f "$tmp"
    chmod "$mode" "$path"; chown "$owner" "$path"
    return 1
  fi
  install -D -m "$mode" -o "${owner%%:*}" -g "${owner##*:}" "$tmp" "$path"
  rm -f "$tmp"
  return 0
}

user_exists() { getent passwd "$1" >/dev/null 2>&1; }
user_uid()    { id -u "$1"; }
user_home()   { getent passwd "$1" | cut -d: -f6; }

# Run a command as another user with a working user systemd and rootless Docker.
run_as_user() {
  local user=$1; shift
  local uid
  uid=$(user_uid "$user")
  runuser -u "$user" -- env \
    HOME="$(user_home "$user")" \
    XDG_RUNTIME_DIR="/run/user/$uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
    "$@"
}

# Download a URL to a file. Fail on HTTP errors. Print the SHA-256.
fetch() {
  local url=$1 dest=$2
  curl -fsSL --retry 3 --retry-delay 2 -o "$dest" "$url"
}

# Rows go to the repository where the owner started the installer (FLEET_SRC_DIR). Else to FLEET_REPO_DIR.
lock_add() { python3 "${FLEET_SRC_DIR:-$FLEET_REPO_DIR}/scripts/lock.py" add "$@"; }
lock_apt() { python3 "${FLEET_SRC_DIR:-$FLEET_REPO_DIR}/scripts/lock.py" apt "$@"; }

# Read a pinned value from vendor/pins.env
pin() {
  local key=$1 val
  val=$({ grep -E "^${key}=" "$FLEET_REPO_DIR/vendor/pins.env" 2>/dev/null || true; } | head -1 | cut -d= -f2- | tr -d '"')
  [ -n "$val" ] || die "missing pin: $key in $FLEET_REPO_DIR/vendor/pins.env"
  printf '%s' "$val"
}

# Read one value from the live config. Example: cfgget limits.max_concurrent_workers
cfgget() { PYTHONPATH=/opt/agent-fleet/repo python3 -m ops.fleetlib.cfgget "$@"; }

# Run a command as the user fleet with a clean environment and a working user session.
run_fleet() {
  local uid; uid=$(user_uid fleet)
  runuser -u fleet -- bash -c 'cd /home/fleet && exec env -i HOME=/home/fleet USER=fleet LOGNAME=fleet SHELL=/bin/bash LANG=C.UTF-8 \
    PATH=/home/fleet/.local/bin:/usr/local/bin:/usr/bin:/bin XDG_RUNTIME_DIR=/run/user/'"$uid"' \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'"$uid"'/bus DOCKER_HOST=unix:///run/user/'"$uid"'/docker.sock "$@"' _ "$@"
}

# Same for the user fleetops.
run_ops() {
  local uid; uid=$(user_uid fleetops)
  runuser -u fleetops -- bash -c 'cd /home/fleetops && exec env -i HOME=/home/fleetops USER=fleetops LOGNAME=fleetops SHELL=/bin/bash LANG=C.UTF-8 \
    PATH=/usr/local/bin:/usr/bin:/bin XDG_RUNTIME_DIR=/run/user/'"$uid"' \
    DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'"$uid"'/bus DOCKER_HOST=unix:///run/user/'"$uid"'/docker.sock "$@"' _ "$@"
}
