#!/usr/bin/env bash
# Phase 1, step 2 (part 2): rootless Docker for the users fleet and fleetops.
# Safe to run twice. Needs 11-docker.sh and 13-users.sh first.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

setup_rootless() {
  local user=$1 uid i sock
  uid=$(user_uid "$user")
  sock="/run/user/$uid/docker.sock"
  loginctl enable-linger "$user"
  # Wait for the user service manager that lingering starts.
  for i in $(seq 1 30); do
    [ -S "/run/user/$uid/bus" ] && break
    sleep 1
  done
  [ -S "/run/user/$uid/bus" ] || die "no user bus for $user after 30 seconds"

  if [ ! -f "$(user_home "$user")/.config/systemd/user/docker.service" ]; then
    log "dockerd-rootless-setuptool for $user"
    run_as_user "$user" dockerd-rootless-setuptool.sh install
  fi
  run_as_user "$user" systemctl --user enable --now docker.service

  for i in $(seq 1 30); do
    [ -S "$sock" ] && break
    sleep 1
  done
  [ -S "$sock" ] || die "rootless Docker socket missing for $user"
  run_as_user "$user" env DOCKER_HOST="unix://$sock" docker info --format '{{.ServerVersion}} rootless={{.ClientInfo.Context}} security={{.SecurityOptions}}'
}

setup_rootless fleet
setup_rootless fleetops
log "step 14-rootless done"
