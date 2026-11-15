#!/usr/bin/env bash
# Phase 4, step 14: install the systemd units from systemd/*.in and systemd/*.target.
# Variables in a template look like @NAME@. This script fills them in.
# Units under agent-fleet.target start at boot. The control bot unit starts at boot on its own.
# Safe to run twice. Run it again when a unit file changes.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

REPO=/opt/agent-fleet/repo
[ -d "$REPO/systemd" ] || die "run install/20-repo.sh first"

FLEETOPS_UID=$(user_uid fleetops)
FLEET_UID=$(user_uid fleet)
tier=$(cfgget capacity.tier small)
case "$tier" in small) ROUTER_MEMORY=1536m ;; medium) ROUTER_MEMORY=3g ;; *) ROUTER_MEMORY=4g ;; esac

# Pinned values come from vendor/pins.env. A pin that is not there yet is left empty.
pinv() { FLEET_REPO_DIR="$REPO" bash -c ". $REPO/install/lib.sh; pin $1" 2>/dev/null || true; }
ROUTER_IMAGE=$(pinv OMNIROUTE_IMAGE)
SEARXNG_IMAGE=$(pinv SEARXNG_IMAGE)
GATE_IMAGE=$(pinv GATE_IMAGE)
ROUTER_PORT=$(cfgget ports.router 20128)
SEARXNG_PORT=$(cfgget ports.searxng 8888)
case "$tier" in small) GATEWAY_MEMORY=1G ;; medium) GATEWAY_MEMORY=2G ;; *) GATEWAY_MEMORY=3G ;; esac

# The brief time and the owner time zone come from the live config. The repo holds no personal data.
BRIEF_TIME=$(cfgget briefing.time 07:00)
OWNER_TZ=$(cfgget owner.timezone America/Chicago)
printf '%s' "$BRIEF_TIME" | grep -Eq '^([01][0-9]|2[0-3]):[0-5][0-9]$' || die "briefing.time must look like 07:00"
printf '%s' "$OWNER_TZ" | grep -Eq '^[A-Za-z0-9_+/-]+$' || die "owner.timezone must be an IANA zone name"
[ -e "/usr/share/zoneinfo/$OWNER_TZ" ] || die "unknown time zone $OWNER_TZ"
BRIEF_CALENDAR="*-*-* ${BRIEF_TIME}:00 ${OWNER_TZ}"
PORTFOLIO_CALENDAR="Sun *-*-* 18:00:00 ${OWNER_TZ}"
# The weekly log writes here. A unit with a missing ReadWritePaths folder does not start, and `fleetctl update` starts at step 30.
install -d -m 0750 -o fleetops -g fleetops /var/lib/agent-fleet-ops/portfolio

render() {
  sed -e "s|@REPO@|$REPO|g" \
      -e "s|@FLEETOPS_UID@|$FLEETOPS_UID|g" \
      -e "s|@FLEET_UID@|$FLEET_UID|g" \
      -e "s|@ROUTER_IMAGE@|$ROUTER_IMAGE|g" \
      -e "s|@ROUTER_MEMORY@|$ROUTER_MEMORY|g" \
      -e "s|@ROUTER_PORT@|$ROUTER_PORT|g" \
      -e "s|@SEARXNG_PORT@|$SEARXNG_PORT|g" \
      -e "s|@SEARXNG_IMAGE@|$SEARXNG_IMAGE|g" \
      -e "s|@GATEWAY_MEMORY@|$GATEWAY_MEMORY|g" \
      -e "s|@BRIEF_CALENDAR@|$BRIEF_CALENDAR|g" \
      -e "s|@PORTFOLIO_CALENDAR@|$PORTFOLIO_CALENDAR|g" \
      -e "s|@GATE_IMAGE@|$GATE_IMAGE|g" "$1"
}

changed=0
for f in "$REPO"/systemd/*; do
  base=$(basename "$f")
  case "$base" in
    *.in)     name="${base%.in}" ;;
    *.target|*.service|*.timer|*.path) name="$base" ;;
    *) continue ;;
  esac
  if render "$f" | write_file "/etc/systemd/system/$name" 0644 root:root; then changed=1; log "installed $name"; fi
  if grep -q '@[A-Z_]*@' "/etc/systemd/system/$name"; then die "unit $name still has an unfilled @VARIABLE@"; fi
done
[ "$changed" -eq 1 ] && systemctl daemon-reload

systemctl enable agent-fleet.target >/dev/null 2>&1
for f in "$REPO"/systemd/*.in "$REPO"/systemd/*.service "$REPO"/systemd/*.timer "$REPO"/systemd/*.path; do
  [ -e "$f" ] || continue
  base=$(basename "$f"); name="${base%.in}"
  grep -q '^\[Install\]' "/etc/systemd/system/$name" || continue     # a unit that a timer starts has no [Install]
  systemctl enable "$name" >/dev/null 2>&1 || warn "could not enable $name"
done
log "units: $(ls /etc/systemd/system | grep -c '^agent-fleet') agent-fleet files in /etc/systemd/system"
log "step 60-units done"
