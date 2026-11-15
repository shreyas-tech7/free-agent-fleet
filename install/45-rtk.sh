#!/usr/bin/env bash
# Phase 4 support: RTK, a command line proxy that shortens long command output for agents.
#   - official release binary from https://github.com/rtk-ai/rtk, the Apache-2.0 only edition (rtk-oss)
#   - the SHA-256 of the archive must match the pin in vendor/pins.env and the checksums.txt of the release
#   - installed at /opt/agent-fleet/bin/rtk (root, mode 755). The sandboxes of the terminal agents mount it read only.
#   - no hook and no plugin is installed. Agents use `rtk <command>` on purpose (docs/DECISIONS.md).
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

case "$(uname -m)" in
  x86_64)  TARGET=x86_64-unknown-linux-musl;  PIN_ARCH=X64 ;;
  aarch64) TARGET=aarch64-unknown-linux-musl; PIN_ARCH=ARM64 ;;
  *) die "unsupported CPU: $(uname -m)" ;;
esac
VER=$(pin RTK_VERSION)
SHA=$(pin "RTK_SHA256_${PIN_ARCH}")
have=$(/opt/agent-fleet/bin/rtk --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if [ "v$have" != "$VER" ]; then
  work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
  asset="rtk-oss-${TARGET}.tar.gz"
  base="https://github.com/rtk-ai/rtk/releases/download/${VER}"
  fetch "$base/$asset" "$work/$asset"
  fetch "$base/checksums.txt" "$work/checksums.txt"
  got=$(sha256sum "$work/$asset" | cut -d' ' -f1)
  [ "$got" = "$SHA" ] || die "RTK archive checksum differs from the pin. Expected $SHA and got $got."
  grep -q "^${SHA}[[:space:]]\+${asset}\$" "$work/checksums.txt" || die "the pin is not in checksums.txt of the release"
  mkdir "$work/x"; tar -xzf "$work/$asset" -C "$work/x" --no-same-owner
  bin=$(find "$work/x" -type f -name rtk | head -1)
  [ -n "$bin" ] || die "no rtk binary in the archive"
  install -d -m 0755 -o root -g root /opt/agent-fleet/bin
  install -m 0755 -o root -g root "$bin" /opt/agent-fleet/bin/rtk
  rm -rf "$work"; trap - EXIT
fi
lock_add --name rtk --kind binary --source "https://github.com/rtk-ai/rtk/releases/download/${VER}/rtk-oss-${TARGET}.tar.gz" --version "$VER" --sha256 "$SHA" --license "Apache-2.0 (rtk-oss edition)"
log "$(/opt/agent-fleet/bin/rtk --version 2>&1 | head -1)"
log "step 45-rtk done"
