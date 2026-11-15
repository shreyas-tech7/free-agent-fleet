"""A small MCP client over streamable HTTP. The selftest uses it to list and call the tools of the n8n MCP server.

The bearer token goes in a header. It is never in an error message and never printed.
The server may answer with plain JSON or with server-sent events. This client reads both.
"""
import json

from ops.fleetlib import httpc

PROTOCOL = "2025-03-26"


class McpError(Exception):
    """A clear one-line reason. It never holds the token."""


class McpClient:
    def __init__(self, url, token, timeout=60, request=None):
        self.url = url
        self.token = token
        self.timeout = timeout
        self._request = request or httpc.request
        self.session = None
        self._id = 0

    def _headers(self):
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        return headers

    def _post(self, payload):
        try:
            return self._request("POST", self.url, headers=self._headers(), json_body=payload, timeout=self.timeout)
        except httpc.TransportError as exc:
            raise McpError("no answer from the server (%s)" % type(exc).__name__) from None

    @staticmethod
    def _message(resp, wanted):
        """The JSON-RPC message with the id `wanted`. The body is JSON or server-sent events."""
        text = resp.text()
        found = []
        if "text/event-stream" in resp.headers.get("content-type", ""):
            for line in text.splitlines():
                if line.startswith("data:"):
                    try:
                        found.append(json.loads(line[5:].strip()))
                    except ValueError:
                        continue
        else:
            try:
                body = json.loads(text)
            except ValueError:
                raise McpError("the server did not answer with JSON") from None
            found = body if isinstance(body, list) else [body]
        for message in found:
            if isinstance(message, dict) and message.get("id") == wanted:
                return message
        raise McpError("the server gave no answer for request %s" % wanted)

    def rpc(self, method, params=None):
        self._id += 1
        resp = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        if resp.status in (401, 403):
            raise McpError("the server refused the token (status %d)" % resp.status)
        if resp.status >= 400:
            raise McpError("%s failed with status %d" % (method, resp.status))
        if method == "initialize":
            self.session = resp.headers.get("mcp-session-id") or self.session
        message = self._message(resp, self._id)
        if "error" in message:
            raise McpError("%s: %s" % (method, str((message["error"] or {}).get("message", "error"))[:160]))
        return message.get("result") or {}

    def notify(self, method):
        resp = self._post({"jsonrpc": "2.0", "method": method})
        if resp.status >= 400:
            raise McpError("%s failed with status %d" % (method, resp.status))

    def connect(self):
        """Start a session. Returns the server info that the server sent."""
        result = self.rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "fleetctl-selftest", "version": "0"}})
        self.notify("notifications/initialized")
        return result.get("serverInfo", {})

    def tool_names(self):
        return sorted(t.get("name", "") for t in self.rpc("tools/list").get("tools", []))

    def call(self, name, arguments):
        """Call one tool. Returns (is_error, text). The text is what the tool returned."""
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        text = "\n".join(part.get("text", "") for part in result.get("content", []) if isinstance(part, dict) and part.get("type") == "text")
        return bool(result.get("isError")), text
