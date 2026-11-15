#!/usr/bin/env bash
# Phase 1, step 1: base packages from the Debian or Ubuntu archive.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

PACKAGES_REQUIRED=(
  git gh curl jq tmux sqlite3 python3 python3-venv pipx build-essential
  ca-certificates gnupg ufw unattended-upgrades uidmap dbus-user-session slirp4netns
)
# Extra packages that the fleet code and the installer need.
PACKAGES_EXTRA=(
  python3-yaml python3-jsonschema python3-pytest xz-utils unzip netcat-openbsd rsync file acl openssl
  libatomic1
)

log "apt update"
apt-get update -y
apt_ensure "${PACKAGES_REQUIRED[@]}" "${PACKAGES_EXTRA[@]}"

log "recording apt packages in vendor/LOCK.md"
lock_apt "${PACKAGES_REQUIRED[@]}" "${PACKAGES_EXTRA[@]}"
log "step 10-apt done"
