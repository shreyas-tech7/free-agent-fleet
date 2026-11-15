#!/usr/bin/env bash
# Phase 2, step 4: deploy the repository to /opt/agent-fleet/repo (root) and give the
# agents a working clone at /var/lib/agent-fleet/work/agent-fleet.
# Safe to run twice. A later run fast-forwards the live copy when it can.
# When the live copy has merged agent changes, use `fleetctl update` instead.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

SRC="$FLEET_REPO_DIR"
LIVE=/opt/agent-fleet/repo
WORK=/var/lib/agent-fleet/work/agent-fleet
HOOKS=/opt/agent-fleet/hooks

[ -d "$SRC/.git" ] || die "$SRC is not a git repository"
branch=$(git -C "$SRC" -c safe.directory='*' symbolic-ref --short HEAD)
[ "$branch" = "main" ] || die "the source repository must be on branch main. It is on $branch"
if [ -n "$(git -C "$SRC" -c safe.directory='*' status --porcelain)" ]; then
  warn "the source repository has uncommitted changes. Only committed work is deployed."
fi

install -d -m 0755 -o root -g root /opt/agent-fleet

# Installer steps may have added rows to the LOCK.md of the live copy. Keep them, then reset the file for the merge.
if [ -d "$LIVE/.git" ] && [ -n "$(git -C "$LIVE" status --porcelain -- vendor/LOCK.md)" ]; then
  python3 "$SRC/scripts/lock.py" merge-from "$LIVE/vendor/LOCK.md" || true
  git -C "$LIVE" checkout --quiet -- vendor/LOCK.md
fi

if [ ! -d "$LIVE/.git" ]; then
  log "clone to $LIVE"
  git -c safe.directory='*' clone --quiet --no-hardlinks --branch main "$SRC" "$LIVE"
  chown -R root:root "$LIVE"
else
  git -C "$LIVE" fetch --quiet --no-tags "$SRC" main 2>/dev/null || git -c safe.directory='*' -C "$LIVE" fetch --quiet --no-tags "$SRC" main
  if git -C "$LIVE" merge-base --is-ancestor HEAD FETCH_HEAD; then
    git -C "$LIVE" merge --quiet --ff-only FETCH_HEAD
    chown -R root:root "$LIVE"
    log "live copy is at $(git -C "$LIVE" rev-parse --short HEAD)"
  else
    warn "the live copy has commits that the source does not have. Not touching it. Use: fleetctl update"
  fi
fi
chmod -R go-w "$LIVE"
# The live copy is a normal git repository that root owns. Others may read it.
git config --system --replace-all safe.directory "$LIVE" "$LIVE"
git config --system --add safe.directory "$LIVE/.git" 2>/dev/null || true

# Hooks that agents read. Root owns them.
install -d -m 0755 -o root -g root "$HOOKS"
install -m 0755 -o root -g root "$LIVE/install/hooks/pre-commit" "$HOOKS/pre-commit"

# The working clone for the agents.
if [ ! -d "$WORK/.git" ]; then
  log "clone to $WORK"
  install -d -m 0755 -o fleet -g fleet "$(dirname "$WORK")"
  runuser -u fleet -- git clone --quiet "$LIVE" "$WORK"
fi
runuser -u fleet -- git -C "$WORK" config user.name  "agent-fleet agents"
runuser -u fleet -- git -C "$WORK" config user.email "agents@agent-fleet.invalid"
runuser -u fleet -- git -C "$WORK" config core.hooksPath "$HOOKS"
runuser -u fleet -- git -C "$WORK" config commit.gpgsign false
runuser -u fleet -- git -C "$WORK" fetch --quiet origin
runuser -u fleet -- git -C "$WORK" merge --quiet --ff-only origin/main 2>/dev/null || true

log "live: $(git -C "$LIVE" log --oneline -1)"
log "work: $(runuser -u fleet -- git -C "$WORK" log --oneline -1)"
log "step 20-repo done"
