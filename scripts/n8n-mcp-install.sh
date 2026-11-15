#!/usr/bin/env bash
# Install the pinned n8n-mcp helper for Claude Code. It needs no root and no secret.
# It installs from the vendored lockfile with install scripts off. It leaves out the native optional dependency.
# Fleet agents never use this helper. See docs/N8N.md.
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
dest="${N8N_MCP_HOME:-$HOME/.local/share/n8n-mcp}"
mkdir -p "$dest"
cp "$repo/vendor/n8n-mcp/package.json" "$repo/vendor/n8n-mcp/package-lock.json" "$dest/"
cd "$dest"
npm ci --ignore-scripts --omit=optional --no-audit --no-fund
node -e 'console.log("n8n-mcp", require("./node_modules/n8n-mcp/package.json").version, "installed in", process.cwd())'
