"""A small fake of the OmniRoute management API, for tests. It keeps its state in memory."""
import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOmniRoute:
    def __init__(self, password="pw-test-123", provider_models=None):
        self.password = password
        self.settings = {"freeAccessPolicy": "off", "excludeTosAvoid": True, "hidePaidModels": False,
                         "proxyEnabled": True, "cloudEnabled": False, "tailscaleEnabled": False}
        self.providers = []
        self.combos = []
        self.keys = []
        self.nodes = []
        self.provider_models = provider_models or {}
        self.log = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def _send(self, status, obj=None, headers=None):
                body = json.dumps(obj).encode() if obj is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                path = self.path.split("?")[0]
                outer.log.append((self.command, path))
                if path == "/api/monitoring/health":
                    return self._send(200, {"ok": True})
                if path == "/api/auth/login":
                    if body and body.get("password") == outer.password:
                        return self._send(200, {"success": True}, {"Set-Cookie": "auth_token=abc123; Path=/; HttpOnly"})
                    return self._send(401, {"error": "bad password"})
                if path == "/v1/models":
                    token = self.headers.get("Authorization", "")[7:]
                    if any(k["key"] == token for k in outer.keys):
                        return self._send(200, {"data": [{"id": c["name"]} for c in outer.combos]})
                    return self._send(401, {"error": "bad key"})
                if "auth_token=abc123" not in (self.headers.get("Cookie") or ""):
                    return self._send(401, {"error": "login first"})
                if path == "/api/settings":
                    if self.command == "PATCH":
                        outer.settings.update(body)
                    return self._send(200, outer.settings)
                if path == "/api/providers":
                    if self.command == "POST":
                        conn = {"id": str(uuid.uuid4()), "provider": body["provider"], "name": body.get("name"), "apiKey": body.get("apiKey")}
                        outer.providers.append(conn)
                        return self._send(201, {"id": conn["id"]})
                    return self._send(200, {"connections": [{k: v for k, v in c.items() if k != "apiKey"} for c in outer.providers], "total": len(outer.providers)})
                m = re.fullmatch(r"/api/providers/([^/]+)(/models)?", path)
                if m:
                    conn = next((c for c in outer.providers if c["id"] == m.group(1)), None)
                    if conn is None:
                        return self._send(404, {"error": "no such provider"})
                    if m.group(2):
                        return self._send(200, {"models": [{"id": x} for x in outer.provider_models.get(conn["provider"], [])]})
                    if self.command == "PUT":
                        conn.update({"apiKey": body.get("apiKey"), "name": body.get("name")})
                        return self._send(200, {"id": conn["id"]})
                    if self.command == "DELETE":
                        outer.providers.remove(conn)
                        return self._send(200, {"deleted": True})
                if path == "/api/provider-nodes":
                    if self.command == "POST":
                        node = {"id": "openai-compatible-%s-%s" % (body.get("apiType"), body.get("providerIdSuffix") or uuid.uuid4().hex[:6]),
                                "name": body["name"], "prefix": body["prefix"], "baseUrl": body["baseUrl"]}
                        outer.nodes.append(node)
                        return self._send(201, {"node": node})
                    return self._send(200, {"nodes": outer.nodes})
                m = re.fullmatch(r"/api/provider-nodes/([^/]+)", path)
                if m and self.command == "DELETE":
                    outer.nodes[:] = [n for n in outer.nodes if n["id"] != m.group(1)]
                    return self._send(200, {"deleted": True})
                if path == "/api/combos":
                    if self.command == "POST":
                        combo = {"id": str(uuid.uuid4()), "name": body["name"], "strategy": body["strategy"],
                                 "models": [{"model": t} for t in body["models"]]}
                        outer.combos.append(combo)
                        return self._send(201, combo)
                    return self._send(200, {"combos": outer.combos, "total": len(outer.combos)})
                m = re.fullmatch(r"/api/combos/([^/]+)", path)
                if m and self.command == "DELETE":
                    outer.combos[:] = [c for c in outer.combos if c["id"] != m.group(1)]
                    return self._send(200, {"deleted": True})
                if m and self.command == "PUT":
                    combo = next((c for c in outer.combos if c["id"] == m.group(1)), None)
                    combo["models"] = [{"model": t} for t in body["models"]]
                    return self._send(200, combo)
                if path == "/api/keys" and self.command == "POST":
                    key = {"id": str(uuid.uuid4()), "name": body.get("name"), "key": "sk-fake-" + uuid.uuid4().hex, "perm": {}}
                    outer.keys.append(key)
                    return self._send(201, {"id": key["id"], "key": key["key"], "name": key["name"]})
                m = re.fullmatch(r"/api/keys/([^/]+)", path)
                if m:
                    key = next((k for k in outer.keys if k["id"] == m.group(1)), None)
                    if key is None:
                        return self._send(404, {"error": "no such key"})
                    if self.command == "PATCH":
                        key["perm"].update(body)
                        return self._send(200, {"id": key["id"]})
                    if self.command == "DELETE":
                        outer.keys.remove(key)
                        return self._send(200, {"deleted": True})
                self._send(404, {"error": "not found"})

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
