"""The MCP client of the selftest. A fake transport stands in for the server. No network and no real token."""
import json

import pytest

from ops.fleetlib import httpc
from ops.n8n import mcpclient

TOKEN = "placeholder-" + "t" * 20


class Server:
    """Records each request. Answers from a script."""

    def __init__(self, sse=False, session="s-123", refuse=False):
        self.sse, self.session, self.refuse, self.seen = sse, session, refuse, []

    def __call__(self, method, url, headers=None, json_body=None, timeout=0, **kw):
        self.seen.append((method, url, dict(headers or {}), json_body))
        if self.refuse:
            return httpc.Response(401, {}, b'{"message":"Unauthorized"}')
        if "id" not in json_body:
            return httpc.Response(202, {}, b"")
        method_name, rid = json_body["method"], json_body["id"]
        results = {"initialize": {"serverInfo": {"name": "fleet-tools"}},
                   "tools/list": {"tools": [{"name": "log_event"}, {"name": "notify_owner"}]},
                   "tools/call": {"content": [{"type": "text", "text": "line one"}, {"type": "image"}, {"type": "text", "text": "line two"}], "isError": False}}
        if json_body.get("params", {}).get("name") == "broken":
            body = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "the tool failed " + "x" * 400}}
        else:
            body = {"jsonrpc": "2.0", "id": rid, "result": results[method_name]}
        headers = {"mcp-session-id": self.session} if method_name == "initialize" else {}
        if self.sse:
            payload = ("event: message\ndata: %s\n\n" % json.dumps({"jsonrpc": "2.0", "method": "notifications/ping"}) + "event: message\ndata: %s\n\n" % json.dumps(body)).encode()
            return httpc.Response(200, dict(headers, **{"content-type": "text/event-stream"}), payload)
        return httpc.Response(200, dict(headers, **{"content-type": "application/json"}), json.dumps(body).encode())


def client(server):
    return mcpclient.McpClient("http://127.0.0.1:5678/mcp/fleet-tools", TOKEN, request=server)


@pytest.mark.parametrize("sse", [False, True])
def test_connect_lists_the_tools_and_calls_one(sse):
    server = Server(sse=sse)
    c = client(server)
    assert c.connect() == {"name": "fleet-tools"}
    assert c.tool_names() == ["log_event", "notify_owner"]
    is_error, text = c.call("log_event", {"text": "hello"})
    assert is_error is False and text == "line one\nline two"


def test_the_token_goes_in_the_header_and_the_session_id_is_carried_on():
    server = Server()
    c = client(server)
    c.connect()
    c.tool_names()
    assert all(h["Authorization"] == "Bearer " + TOKEN for _m, _u, h, _b in server.seen)
    assert "Mcp-Session-Id" not in server.seen[0][2]
    assert all(h.get("Mcp-Session-Id") == "s-123" for _m, _u, h, _b in server.seen[1:])
    assert [b.get("method") for _m, _u, _h, b in server.seen] == ["initialize", "notifications/initialized", "tools/list"]


def test_a_refused_token_is_one_clear_line_without_the_token():
    with pytest.raises(mcpclient.McpError) as err:
        client(Server(refuse=True)).connect()
    assert "refused the token" in str(err.value) and TOKEN not in str(err.value)


def test_a_tool_error_is_clipped_and_has_no_token():
    c = client(Server())
    c.connect()
    with pytest.raises(mcpclient.McpError) as err:
        c.call("broken", {})
    assert "the tool failed" in str(err.value) and len(str(err.value)) < 220 and TOKEN not in str(err.value)


def test_a_transport_failure_is_a_clear_error():
    def down(*a, **k):
        raise httpc.TransportError("connection refused")
    with pytest.raises(mcpclient.McpError) as err:
        client(down).connect()
    assert "no answer from the server" in str(err.value)


def test_an_answer_that_is_not_json_is_refused():
    def odd(method, url, headers=None, json_body=None, **k):
        return httpc.Response(200, {"content-type": "text/html"}, b"<html>")
    with pytest.raises(mcpclient.McpError):
        client(odd).connect()


def test_an_answer_for_another_request_is_not_taken():
    def other(method, url, headers=None, json_body=None, **k):
        return httpc.Response(200, {"content-type": "application/json"}, json.dumps({"jsonrpc": "2.0", "id": 999, "result": {}}).encode())
    with pytest.raises(mcpclient.McpError):
        client(other).connect()
