import json
import urllib.request

import pytest

from ops.fleetlib import httpc, httpd


def make_server(**kw):
    routes = {
        ("GET", "/health"): lambda r: (200, {"ok": True}),
        ("POST", "/echo"): lambda r: (200, {"got": r.json()}),
        ("GET", "/boom"): lambda r: 1 / 0,
    }
    return httpd.JsonServer("127.0.0.1", 0, routes, **kw).start_background()


def call(server, method, path, body=None, token=None):
    url = "http://127.0.0.1:%d%s" % (server.port, path)
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_refuses_non_loopback_bind():
    with pytest.raises(ValueError):
        httpd.JsonServer("0.0.0.0", 0, {})


def test_auth_required_except_health():
    token = "t" * 40
    srv = make_server(auth_hashes=[httpd.token_hash(token)])
    try:
        assert call(srv, "GET", "/health")[0] == 200
        assert call(srv, "POST", "/echo", {"a": 1})[0] == 401
        assert call(srv, "POST", "/echo", {"a": 1}, token="wrong")[0] == 401
        assert call(srv, "POST", "/echo", {"a": 1}, token=token) == (200, {"got": {"a": 1}})
    finally:
        srv.shutdown()


def test_errors_do_not_leak_details():
    srv = make_server()
    try:
        status, body = call(srv, "GET", "/boom")
        assert status == 500 and body == {"error": "internal error"}
        assert call(srv, "GET", "/missing")[0] == 404
        req = urllib.request.Request("http://127.0.0.1:%d/echo" % srv.port, method="POST", data=b"not json")
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        assert err.value.code == 400
    finally:
        srv.shutdown()


def test_body_limit():
    srv = make_server(max_body=100)
    try:
        assert call(srv, "POST", "/echo", {"x": "y" * 500})[0] == 413
    finally:
        srv.shutdown()


def test_httpc_uses_replaceable_transport_and_blocks_other_schemes():
    seen = {}

    def fake(method, url, headers, body, timeout, max_bytes):
        seen.update(method=method, url=url, headers=headers, body=body)
        return httpc.Response(200, {"Content-Type": "application/json"}, b'{"ok": true}')

    httpc.set_transport(fake)
    try:
        r = httpc.post("https://example.invalid/x", json_body={"a": 1})
        assert r.ok and r.json() == {"ok": True}
        assert seen["method"] == "POST" and json.loads(seen["body"]) == {"a": 1}
        assert seen["headers"]["User-Agent"].startswith("agent-fleet")
        with pytest.raises(httpc.TransportError):
            httpc.get("file:///etc/passwd")
    finally:
        httpc.reset_transport()


def test_httpc_does_not_follow_redirects():
    class Redirect(httpd.JsonServer):
        pass
    # A real local server that answers 302.
    import http.server, threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/never")
            self.end_headers()
        def log_message(self, *a):
            pass

    s = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        r = httpc.get("http://127.0.0.1:%d/" % s.server_address[1])
        assert r.status == 302
    finally:
        s.shutdown()


def raw_request(server, head):
    import socket
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
        sock.sendall(head.encode())
        sock.settimeout(5)
        return sock.recv(4096).decode()


def test_a_bad_content_length_is_refused_at_once():
    """A negative length made the server read until the end of the stream, so the request hung. See F-06."""
    s = make_server()
    try:
        for value in ("-1", "-999", "ten"):
            answer = raw_request(s, "POST /echo HTTP/1.1\r\nHost: x\r\nContent-Length: %s\r\n\r\n" % value)
            assert answer.startswith("HTTP/1.0 400") or answer.startswith("HTTP/1.1 400")
    finally:
        s.shutdown()


def test_the_handler_has_a_socket_timeout():
    s = make_server()
    try:
        assert s.httpd.RequestHandlerClass.timeout == httpd.SOCKET_TIMEOUT and 5 <= httpd.SOCKET_TIMEOUT <= 120
    finally:
        s.shutdown()
