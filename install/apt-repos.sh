#!/usr/bin/env bash
# Functions that add the official apt repositories of Docker and Tailscale.
# Source this file. Do not run it.
# shellcheck shell=bash

DOCKER_KEY_FPR="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"

add_docker_repo() {
  local id codename tmp fpr
  id=$(os_id); codename=$(os_codename)
  case "$id" in debian|ubuntu) ;; *) die "Docker repo setup supports Debian and Ubuntu only" ;; esac
  install -m 0755 -d /etc/apt/keyrings
  tmp=$(mktemp)
  fetch "https://download.docker.com/linux/${id}/gpg" "$tmp"
  fpr=$(gpg --show-keys --with-colons "$tmp" 2>/dev/null | awk -F: '/^fpr:/ {print $10; exit}')
  if [ "$fpr" != "$DOCKER_KEY_FPR" ]; then
    rm -f "$tmp"
    die "Docker apt key fingerprint is $fpr. Expected $DOCKER_KEY_FPR. Stopping."
  fi
  install -m 0644 "$tmp" /etc/apt/keyrings/docker.asc
  rm -f "$tmp"
  write_file /etc/apt/sources.list.d/docker.sources 0644 root:root <<SRC || true
Types: deb
URIs: https://download.docker.com/linux/${id}
Suites: ${codename}
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
SRC
  apt-get update -y
}

add_tailscale_repo() {
  local id codename
  id=$(os_id); codename=$(os_codename)
  case "$id" in debian|ubuntu) ;; *) die "Tailscale repo setup supports Debian and Ubuntu only" ;; esac
  install -m 0755 -d /usr/share/keyrings
  fetch "https://pkgs.tailscale.com/stable/${id}/${codename}.noarmor.gpg" /usr/share/keyrings/tailscale-archive-keyring.gpg
  chmod 0644 /usr/share/keyrings/tailscale-archive-keyring.gpg
  write_file /etc/apt/sources.list.d/tailscale.list 0644 root:root <<SRC || true
deb [signed-by=/usr/share/keyrings/tailscale-archive-keyring.gpg] https://pkgs.tailscale.com/stable/${id} ${codename} main
SRC
  apt-get update -y
}
