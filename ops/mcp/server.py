"""A small MCP server over stdio. Standard library only.

Hermes starts it as a subprocess of the profile that uses it. It exposes the fleet's local
services as tools. Each profile lists the tool names it may use in its config (`--tools a,b`),
so an agent gets only the tools its job needs.

Protocol: JSON-RPC 2.0, one message per line. Methods: initialize, tools/list, tools/call, ping.
The server holds no secret. A tool that needs a token reads it from the environment that
Hermes passes in from the profile's .env.
"""
import json
import os
import sys

from ops.mcp import tools as toolset

PROTOCOL = "2024-11-05"


def handle(message, enabled):
    """Handle one JSON-RPC message. Returns a response dict, or None for a notification."""
    method = message.get("method")
    mid = message.get("id")
    params = message.get("params") or {}
    if mid is None:
        return None                       # notifications get no answer
    if method == "initialize":
        version = params.get("protocolVersion") or PROTOCOL
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": version, "capabilities": {"tools": {}},
            "serverInfo": {"name": "agent-fleet-tools", "version": "0.1"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        listing = [{"name": n, "description": t["description"], "inputSchema": t["schema"]}
                   for n, t in toolset.TOOLS.items() if n in enabled]
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": listing}}
    if method == "tools/call":
        name = params.get("name")
        if name not in enabled or name not in toolset.TOOLS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool: %s" % name}}
        try:
            result = toolset.TOOLS[name]["run"](params.get("arguments") or {})
            text = json.dumps(result, sort_keys=True)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": False}}
        except toolset.ToolError as exc:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(exc)}], "isError": True}}
        except Exception as exc:            # never leak a stack trace or a secret
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "tool failed: %s" % type(exc).__name__}], "isError": True}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}


def serve(enabled, stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        response = handle(message, enabled)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main(argv):
    names = []
    for i, arg in enumerate(argv):
        if arg == "--tools" and i + 1 < len(argv):
            names = [n for n in argv[i + 1].split(",") if n]
    unknown = [n for n in names if n not in toolset.TOOLS]
    if unknown:
        print("unknown tools: %s" % ", ".join(unknown), file=sys.stderr)
        return 2
    serve(set(names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
