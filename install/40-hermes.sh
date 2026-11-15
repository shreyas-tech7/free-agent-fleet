#!/usr/bin/env bash
# Phase 4, step 1: Hermes Agent for the user fleet, from the official installer.
#   - the installer is the copy tagged in vendor/pins.env. Its SHA-256 must match the pin.
#   - the checkout is pinned by commit.
#   - no browser and no computer-use driver. Agents run shell commands in the sandbox.
#   - the hooks folder becomes root-owned (mode 755), so no agent can add a hook.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

FLEET_UID=$(user_uid fleet)
FHOME=/home/fleet
run_fleet() {
  runuser -u fleet -- env -i HOME="$FHOME" USER=fleet LOGNAME=fleet SHELL=/bin/bash LANG=C.UTF-8 \
    PATH="$FHOME/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    XDG_RUNTIME_DIR="/run/user/$FLEET_UID" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$FLEET_UID/bus" \
    DOCKER_HOST="unix:///run/user/$FLEET_UID/docker.sock" "$@"
}

URL=$(pin HERMES_INSTALLER_URL)
SHA=$(pin HERMES_INSTALLER_SHA256)
COMMIT=$(pin HERMES_COMMIT)
TAG=$(pin HERMES_VERSION_TAG)
IMAGE=$(pin HERMES_SANDBOX_IMAGE)

# ---- install Hermes ----
if ! run_fleet bash -c 'command -v hermes >/dev/null 2>&1'; then
  work=$(run_fleet mktemp -d /home/fleet/.cache-install.XXXXXX)
  run_fleet curl -fsSL --retry 3 -o "$work/install.sh" "$URL"
  got=$(run_fleet sha256sum "$work/install.sh" | cut -d' ' -f1)
  [ "$got" = "$SHA" ] || { run_fleet rm -rf "$work"; die "Hermes installer changed. Expected $SHA and got $got. Read the new file before you pin it."; }
  log "running the pinned Hermes installer ($TAG, commit ${COMMIT:0:10})"
  run_fleet bash "$work/install.sh" --non-interactive --skip-browser --skip-computer-use --commit "$COMMIT" --verbose
  run_fleet rm -rf "$work"
fi
ver=$(run_fleet hermes --version 2>&1 | head -1)
log "installed: $ver"

lock_add --name hermes-agent --kind git --source "https://github.com/NousResearch/hermes-agent.git" --version "$TAG ($COMMIT)" --sha256 "$SHA" --license "MIT (installer script sha256 shown)"

# ---- updates off. The managed config pins this too ----
run_fleet hermes config set updates.check false >/dev/null 2>&1 || true

# ---- sandbox image, pinned by digest ----
if ! run_fleet docker image inspect "$IMAGE" >/dev/null 2>&1; then
  log "pulling the sandbox image (about 1.2 GB)"
  run_fleet docker pull "$IMAGE"
fi
lock_add --name hermes-sandbox --kind docker --source "docker.io/nousresearch/hermes-sandbox" --version "$IMAGE" --sha256 "${IMAGE##*@sha256:}" --license "see image"

# ---- the hooks folder: root owns it so no agent can add a gateway hook ----
install -d -m 0755 -o root -g root "$FHOME/.hermes/hooks"
chown root:root "$FHOME/.hermes/hooks"
chmod 0755 "$FHOME/.hermes/hooks"
# Check that Hermes still starts with the locked folder.
if ! run_fleet hermes doctor >/tmp/hermes-doctor.$$ 2>&1; then
  warn "hermes doctor reported problems. See docs/DECISIONS.md. Output follows."
  tail -20 /tmp/hermes-doctor.$$ || true
fi
rm -f /tmp/hermes-doctor.$$
log "step 40-hermes done"
