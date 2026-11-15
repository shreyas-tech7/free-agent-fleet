#!/usr/bin/env bash
# Root tasks that the build could not run on its own.
#
# Run this file once as root: sudo bash install/root-tasks.sh
# It is safe to run twice. Each task checks its own state first.
#
# The build adds a task here when sudo is missing or when the permission
# system blocks a root step (rule R11). Prompt 2 runs this file.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "root-tasks: run this file as root" >&2
  exit 1
fi

# --- tasks go below this line. One function per task. ---

# No task is left. The build had passwordless sudo and ran every root step itself.
# The only root steps that remain belong to the owner, and they are not scripted on purpose:
#   1. sudo tailscale up                       (the owner logs in)
#   2. sudo rm /etc/sudoers.d/99-fleet-bootstrap   (Prompt 2 removes the bootstrap rule)
#   3. review /etc/sudoers.d/90-hermes-setup    (an older setup left it)
# See docs/HUMAN_STEPS.md and HANDOFF.md.

echo "root-tasks: done (nothing to run)"
