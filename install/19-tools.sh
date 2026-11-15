#!/usr/bin/env bash
# Phase 1 support: scanner binaries from their official GitHub releases.
# Each download must match the SHA-256 that vendor/pins.env pins.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

case "$(uname -m)" in
  x86_64)  GL_ARCH=x64;   OSV_ARCH=amd64; PIN_ARCH=X64 ;;
  aarch64) GL_ARCH=arm64; OSV_ARCH=arm64; PIN_ARCH=ARM64 ;;
  *) die "unsupported CPU: $(uname -m)" ;;
esac

# ---- gitleaks ----
GL_VER=$(pin GITLEAKS_VERSION)
GL_SHA=$(pin "GITLEAKS_SHA256_${PIN_ARCH}")
have=$(/usr/local/bin/gitleaks version 2>/dev/null | tr -d 'v' || true)
if [ "$have" != "$GL_VER" ]; then
  work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
  f="gitleaks_${GL_VER}_linux_${GL_ARCH}.tar.gz"
  url="https://github.com/gitleaks/gitleaks/releases/download/v${GL_VER}/${f}"
  fetch "$url" "$work/$f"
  echo "${GL_SHA}  $work/$f" | sha256sum -c - >/dev/null || die "gitleaks checksum mismatch"
  mkdir "$work/x"; tar -xzf "$work/$f" -C "$work/x" --no-same-owner gitleaks
  install -m 0755 -o root -g root "$work/x/gitleaks" /usr/local/bin/gitleaks
  rm -rf "$work"; trap - EXIT
fi
lock_add --name gitleaks --kind binary --source "https://github.com/gitleaks/gitleaks/releases/download/v${GL_VER}/gitleaks_${GL_VER}_linux_${GL_ARCH}.tar.gz" --version "v${GL_VER}" --sha256 "$GL_SHA" --license "MIT"
log "$(gitleaks version)"

# ---- osv-scanner ----
OSV_VER=$(pin OSV_SCANNER_VERSION)
OSV_SHA=$(pin "OSV_SCANNER_SHA256_${PIN_ARCH}")
have=$(/usr/local/bin/osv-scanner --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if [ "$have" != "$OSV_VER" ]; then
  work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
  url="https://github.com/google/osv-scanner/releases/download/v${OSV_VER}/osv-scanner_linux_${OSV_ARCH}"
  fetch "$url" "$work/osv-scanner"
  echo "${OSV_SHA}  $work/osv-scanner" | sha256sum -c - >/dev/null || die "osv-scanner checksum mismatch"
  install -m 0755 -o root -g root "$work/osv-scanner" /usr/local/bin/osv-scanner
  rm -rf "$work"; trap - EXIT
fi
lock_add --name osv-scanner --kind binary --source "https://github.com/google/osv-scanner/releases/download/v${OSV_VER}/osv-scanner_linux_${OSV_ARCH}" --version "v${OSV_VER}" --sha256 "$OSV_SHA" --license "Apache-2.0"
log "osv-scanner $(osv-scanner --version 2>&1 | head -1)"
log "step 19-tools done"
