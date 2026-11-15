#!/usr/bin/env bash
# Start n8n-mcp for Claude Code over stdio. Fleet agents never use this helper. See docs/N8N.md.
# The API key comes from a file with mode 600. It is never in an argument and never in a log.
# The tools that can delete anything are off. See D-042.
set -euo pipefail
dest="${N8N_MCP_HOME:-$HOME/.local/share/n8n-mcp}"
env_file="${N8N_MCP_ENV_FILE:-$HOME/.config/n8n-mcp/env}"
if [ ! -f "$env_file" ]; then
  echo "n8n-mcp: there is no env file yet. Run: sudo fleetctl n8n mcp-env" >&2
  exit 1
fi
if [ "$(stat -c %a "$env_file")" != "600" ]; then
  echo "n8n-mcp: $env_file must have mode 600" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
. "$env_file"
set +a
export N8N_API_URL="${N8N_API_URL:-http://127.0.0.1:5678}"
export MCP_MODE=stdio LOG_LEVEL=error DISABLE_CONSOLE_OUTPUT=true WEBHOOK_SECURITY_MODE=moderate N8N_MCP_TELEMETRY_DISABLED=true
export DISABLED_TOOLS="n8n_delete_workflow,n8n_executions,n8n_workflow_versions,n8n_manage_datatable,n8n_manage_folders,n8n_manage_credentials,n8n_manage_agents,n8n_audit_instance"
exec node "$dest/node_modules/n8n-mcp/dist/mcp/stdio-wrapper.js"
