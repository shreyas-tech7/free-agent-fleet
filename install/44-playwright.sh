#!/usr/bin/env bash
# Phase 5 support: the Playwright MCP server for the build agent.
#   - the official npm package @playwright/mcp (https://github.com/microsoft/playwright-mcp)
#   - pinned by vendor/playwright-mcp/package-lock.json, which holds an integrity hash for each package
#   - installed with --ignore-scripts, into a folder that root owns
#   - the browser is the Chromium build that this Playwright version pins. Playwright downloads it from its own CDN.
# The server runs as the user fleet, headless, with a clean profile each time, and may open only local pages.
# Safe to run twice.
set -euo pipefail
# shellcheck source=install/lib.sh
. "$(dirname "$0")/lib.sh"
need_root

REPO=/opt/agent-fleet/repo
SRC="$REPO/vendor/playwright-mcp"
DEST=/opt/agent-fleet/mcp/playwright
BROWSERS=/opt/agent-fleet/mcp/browsers
[ -f "$SRC/package-lock.json" ] || die "run install/20-repo.sh first"
command -v npm >/dev/null || die "run install/12-node-uv.sh first"

install -d -m 0755 -o root -g root /opt/agent-fleet/mcp "$DEST" "$BROWSERS"
changed=0
for f in package.json package-lock.json; do
  if ! cmp -s "$SRC/$f" "$DEST/$f" 2>/dev/null; then install -m 0644 -o root -g root "$SRC/$f" "$DEST/$f"; changed=1; fi
done
if [ "$changed" -eq 1 ] || [ ! -x "$DEST/node_modules/.bin/playwright-mcp" ]; then
  log "npm ci (lock file, no install scripts)"
  (cd "$DEST" && env -i HOME=/root PATH=/usr/local/bin:/usr/bin:/bin npm ci --ignore-scripts --no-audit --no-fund --loglevel=error)
fi
ver=$(node -e "console.log(require('$DEST/node_modules/@playwright/mcp/package.json').version)")
core=$(node -e "console.log(require('$DEST/node_modules/playwright-core/package.json').version)")
log "playwright-mcp $ver with playwright-core $core"

# Operating system libraries that Chromium needs. They come from the Debian or Ubuntu archive.
if ! [ -e "$BROWSERS/.deps-$core" ]; then
  log "installing the system libraries for Chromium (apt)"
  env -i HOME=/root PATH=/usr/local/bin:/usr/bin:/bin DEBIAN_FRONTEND=noninteractive PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" \
    node "$DEST/node_modules/playwright-core/cli.js" install-deps chromium
  touch "$BROWSERS/.deps-$core"
fi
# The browser. The folder name holds the pinned revision, so a new version fetches a new browser.
if ! ls "$BROWSERS" | grep -q '^chromium'; then
  log "downloading the pinned Chromium build (official Playwright CDN)"
  env -i HOME=/root PATH=/usr/local/bin:/usr/bin:/bin PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" \
    node "$DEST/node_modules/playwright-core/cli.js" install chromium
fi
chmod -R go+rX,go-w "$BROWSERS" "$DEST"
lock_add --name playwright-mcp --kind npm --source "https://registry.npmjs.org/@playwright/mcp" --version "$ver (playwright-core $core)" --sha256 "see vendor/playwright-mcp/package-lock.json" --license "Apache-2.0"
log "step 44-playwright done"
