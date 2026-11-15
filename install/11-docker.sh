#!/usr/bin/env bash
# Phase 1, step 2 (part 1): Docker Engine from the official Docker apt repository.
# Rootless setup for the fleet users is in 14-rootless.sh.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
# shellcheck source=install/apt-repos.sh
. "$(dirname "$0")/apt-repos.sh"
need_root

add_docker_repo

ver=$(pin DOCKER_VERSION)
full=$(apt-cache madison docker-ce | awk -F'|' -v v="$ver" '{gsub(/ /,"",$2); if ($2 ~ "^5:" v "-") {print $2; exit}}')
[ -n "$full" ] || die "docker-ce $ver is not in the repository"
log "docker-ce pinned at $full"

pinned_pkgs=(docker-ce docker-ce-cli docker-ce-rootless-extras)
other_pkgs=(containerd.io docker-buildx-plugin)

need=0
for p in "${pinned_pkgs[@]}"; do
  cur=$(dpkg-query -W -f='${Version}' "$p" 2>/dev/null || true)
  [ "$cur" = "$full" ] || need=1
done
for p in "${other_pkgs[@]}"; do pkg_installed "$p" || need=1; done

if [ "$need" -eq 1 ]; then
  args=()
  for p in "${pinned_pkgs[@]}"; do args+=("$p=$full"); done
  apt-mark unhold "${pinned_pkgs[@]}" "${other_pkgs[@]}" >/dev/null 2>&1 || true
  apt_install "${args[@]}" "${other_pkgs[@]}"
fi
# Hold the packages. Docker updates go through the merger, not through unattended upgrades.
apt-mark hold "${pinned_pkgs[@]}" "${other_pkgs[@]}" >/dev/null

# The Compose plugin runs the n8n unit. It comes from the same repository and is held at its own pinned version.
cver=$(pin DOCKER_COMPOSE_VERSION)
cfull=$(apt-cache madison docker-compose-plugin | awk -F'|' -v v="$cver" '{gsub(/ /,"",$2); if ($2 ~ "^" v "-") {print $2; exit}}')
[ -n "$cfull" ] || die "docker-compose-plugin $cver is not in the repository"
if [ "$(dpkg-query -W -f='${Version}' docker-compose-plugin 2>/dev/null || true)" != "$cfull" ]; then
  apt-mark unhold docker-compose-plugin >/dev/null 2>&1 || true
  apt_install "docker-compose-plugin=$cfull"
fi
apt-mark hold docker-compose-plugin >/dev/null

# Only rootless daemons run on this machine. Turn off the system-wide root daemon.
# Nobody is in the docker group. Undo: systemctl enable --now docker
for u in docker.service docker.socket containerd.service; do
  systemctl disable --now "$u" >/dev/null 2>&1 || true
done
rm -f /var/run/docker.sock

lock_apt "${pinned_pkgs[@]}" "${other_pkgs[@]}" docker-compose-plugin
docker --version
log "step 11-docker done"
