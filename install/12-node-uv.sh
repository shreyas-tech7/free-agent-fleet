#!/usr/bin/env bash
# Phase 1, step 3: Node.js LTS from nodejs.org and uv from astral.sh.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

# ---------- Node.js ----------
NODE_VERSION=$(pin NODE_VERSION)
case "$(uname -m)" in
  x86_64)  NODE_ARCH=x64 ;;
  aarch64) NODE_ARCH=arm64 ;;
  *) die "unsupported CPU: $(uname -m)" ;;
esac
NODE_DIR="/opt/node/node-v${NODE_VERSION}-linux-${NODE_ARCH}"
if [ ! -x "$NODE_DIR/bin/node" ]; then
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  base="https://nodejs.org/dist/v${NODE_VERSION}"
  tarball="node-v${NODE_VERSION}-linux-${NODE_ARCH}.tar.xz"
  log "download Node.js v${NODE_VERSION}"
  fetch "$base/SHASUMS256.txt" "$work/SHASUMS256.txt"
  fetch "$base/$tarball" "$work/$tarball"
  (cd "$work" && grep " ${tarball}\$" SHASUMS256.txt | sha256sum -c -) || die "Node.js checksum mismatch"
  node_sha=$(grep " ${tarball}\$" "$work/SHASUMS256.txt" | cut -d' ' -f1)
  mkdir -p /opt/node
  tar -xJf "$work/$tarball" -C /opt/node --no-same-owner
  lock_add --name node --kind binary --source "$base/$tarball" --version "v${NODE_VERSION}" --sha256 "$node_sha" --license "MIT"
  rm -rf "$work"; trap - EXIT
fi
ln -sfn "$NODE_DIR" /opt/node/current
for b in node npm npx; do ln -sfn "/opt/node/current/bin/$b" "/usr/local/bin/$b"; done
log "node $(/usr/local/bin/node --version), npm $(/usr/local/bin/npm --version)"

# ---------- uv ----------
UV_VERSION=$(pin UV_VERSION)
UV_INSTALLER_SHA256=$(pin UV_INSTALLER_SHA256)
have_uv=$(/usr/local/bin/uv --version 2>/dev/null | awk '{print $2}' || true)
if [ "$have_uv" != "$UV_VERSION" ]; then
  work=$(mktemp -d)
  trap 'rm -rf "$work"' EXIT
  fetch "https://astral.sh/uv/${UV_VERSION}/install.sh" "$work/install.sh"
  got=$(sha256sum "$work/install.sh" | cut -d' ' -f1)
  [ "$got" = "$UV_INSTALLER_SHA256" ] || die "uv installer changed. Expected $UV_INSTALLER_SHA256 and got $got. Read the new file before you pin it."
  UV_UNMANAGED_INSTALL=/usr/local/bin sh "$work/install.sh"
  rm -rf "$work"; trap - EXIT
fi
uv_bin_sha=$(sha256sum /usr/local/bin/uv | cut -d' ' -f1)
lock_add --name uv --kind binary --source "https://astral.sh/uv/${UV_VERSION}/install.sh" --version "${UV_VERSION}" --sha256 "$uv_bin_sha" --license "MIT or Apache-2.0"
log "$(/usr/local/bin/uv --version)"
log "step 12-node-uv done"
