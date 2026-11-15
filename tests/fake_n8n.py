"""A small fake of the n8n editor API and public API, for tests. It keeps its state in memory.

It checks what a real n8n checks for the calls that fleetctl makes: the owner login, the session cookie for the key calls,
the API key for the public API, and the shape of a workflow. It does not run a workflow.
"""
import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeN8n:
    def __init__(self, email="owner@example.org", password="pw-test-0123456789", version="2.47.4", ready_after=0, event_token="evt-token", open_webhook=False,
                 publish_lag=0):
        self.email, self.password, self.version = email, password, version
        self.event_token = event_token          # the header token that /webhook/fleet-event wants
        self.open_webhook = open_webhook        # True makes a broken webhook that takes any token
        self.ready_after = ready_after          # the number of readiness calls that answer 503 first
        self.ready_calls = 0
        self.publish_lag = publish_lag          # reads that still see a new publication as missing. A real n8n shows this after a first publish.
        self.lag = {}                           # id -> reads left until the published version shows
        self.api_keys = []                      # {"id", "label", "raw", "scopes"}
        self.workflows = {}                     # id -> workflow dict (with "active")
        self.credentials = {}                   # id -> {"id", "name", "type", "data"}
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

            def _page(self, items, query):
                m = re.search(r"cursor=(\d+)", query)
                limit = int(re.search(r"limit=(\d+)", query).group(1)) if "limit=" in query else 100
                start = int(m.group(1)) if m else 0
                chunk = items[start:start + limit]
                return {"data": chunk, "nextCursor": str(start + limit) if start + limit < len(items) else None}

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                path, _, query = self.path.partition("?")
                outer.log.append((self.command, path))
                if path == "/healthz":
                    return self._send(200, {"status": "ok"})
                if path == "/webhook/fleet-event":
                    if outer.open_webhook or self.headers.get("X-Fleet-Token") == outer.event_token:
                        return self._send(200, {"message": "Workflow was started"})
                    return self._send(403, {"message": "Authorization data is wrong!"})
                if path == "/healthz/readiness":
                    outer.ready_calls += 1
                    return self._send(503 if outer.ready_calls <= outer.ready_after else 200, {"status": "ok"})
                if path == "/rest/login":
                    if body and body.get("emailOrLdapLoginId") == outer.email and body.get("password") == outer.password:
                        return self._send(200, {"data": {"email": outer.email, "isOwner": True}},
                                          {"Set-Cookie": "n8n-auth=session-abc; Path=/; Max-Age=604800; HttpOnly; Secure; SameSite=Lax"})
                    return self._send(401, {"message": "Wrong username or password"})
                if path.startswith("/rest/"):
                    if "n8n-auth=session-abc" not in (self.headers.get("Cookie") or ""):
                        return self._send(401, {"message": "Unauthorized"})
                    if path == "/rest/settings":
                        return self._send(200, {"data": {"versionCli": outer.version}})
                    if path == "/rest/api-keys":
                        if self.command == "POST":
                            key = {"id": str(uuid.uuid4())[:8], "label": body["label"], "scopes": body["scopes"], "raw": "eyJ" + uuid.uuid4().hex + "." + uuid.uuid4().hex + ".sig"}
                            outer.api_keys.append(key)
                            return self._send(200, {"data": {"id": key["id"], "label": key["label"], "scopes": key["scopes"], "rawApiKey": key["raw"]}})
                        return self._send(200, {"data": {"items": [{"id": k["id"], "label": k["label"]} for k in outer.api_keys]}})
                    m = re.fullmatch(r"/rest/api-keys/(\w+)", path)
                    if m and self.command == "DELETE":
                        outer.api_keys = [k for k in outer.api_keys if k["id"] != m.group(1)]
                        return self._send(200, {"data": True})
                    return self._send(404, {"message": "not found"})
                if path.startswith("/api/v1/"):
                    key = self.headers.get("X-N8N-API-KEY", "")
                    found = next((k for k in outer.api_keys if k["raw"] == key), None)
                    if not found:
                        return self._send(401, {"message": "unauthorized"})
                    scopes = found["scopes"]
                    if path == "/api/v1/workflows":
                        if self.command == "GET":
                            return self._send(200, outer_page(list(outer.workflows.values()), query, self))
                        return self._send(*outer.save_workflow(body, None))
                    m = re.fullmatch(r"/api/v1/workflows/(\w+)", path)
                    if m:
                        wid = m.group(1)
                        if wid not in outer.workflows:
                            return self._send(404, {"message": "not found"})
                        if self.command == "GET":
                            return self._send(200, outer.view(wid))
                        if self.command == "PUT":
                            return self._send(*outer.save_workflow(body, wid))
                    m = re.fullmatch(r"/api/v1/workflows/(\w+)/(activate|deactivate)", path)
                    if m and m.group(1) in outer.workflows:
                        outer.workflows[m.group(1)]["active"] = m.group(2) == "activate"
                        outer.lag[m.group(1)] = outer.publish_lag if m.group(2) == "activate" else 0
                        return self._send(200, outer.view(m.group(1)))
                    if path == "/api/v1/credentials":
                        if self.command == "GET":
                            return self._send(200, outer_page([{"id": c["id"], "name": c["name"], "type": c["type"]} for c in outer.credentials.values()], query, self))
                        cid = uuid.uuid4().hex[:16]
                        outer.credentials[cid] = {"id": cid, "name": body["name"], "type": body["type"], "data": body["data"]}
                        return self._send(200, {"id": cid, "name": body["name"], "type": body["type"]})
                    m = re.fullmatch(r"/api/v1/credentials/(\w+)", path)
                    if m and self.command == "PATCH" and m.group(1) in outer.credentials:
                        outer.credentials[m.group(1)].update(name=body["name"], type=body["type"], data=body["data"])
                        return self._send(200, {"id": m.group(1), "name": body["name"], "type": body["type"]})
                return self._send(404, {"message": "not found"})
            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

        def outer_page(items, query, handler):
            return handler._page(items, query)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.url = "http://127.0.0.1:%d" % self.port

    def save_workflow(self, body, wid):
        """What a real n8n checks first: only the fields that the public API accepts, and a node list that is not empty."""
        extra = set(body) - {"name", "nodes", "connections", "settings"}
        if extra:
            return 400, {"message": "request/body must NOT have additional properties: %s" % ", ".join(sorted(extra))}
        if not body.get("nodes"):
            return 400, {"message": "nodes are required"}
        bad = set(body.get("settings") or {}) - {"saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution", "saveDataSuccessExecution",
                                                  "executionTimeout", "errorWorkflow", "timezone", "executionOrder", "callerPolicy", "callerIds",
                                                  "timeSavedPerExecution", "availableInMCP"}
        if bad:
            return 400, {"message": "request/body/settings must NOT have additional properties: %s" % ", ".join(sorted(bad))}
        target = (body.get("settings") or {}).get("errorWorkflow")
        if target and target != "DEFAULT":
            if target not in self.workflows:
                return 400, {"message": "The selected error workflow does not exist or you do not have access to it."}
            if not self.view(target).get("activeVersionId"):
                return 400, {"message": "The selected error workflow has no published version."}
        wid = wid or uuid.uuid4().hex[:16]
        was_active = self.workflows.get(wid, {}).get("active", False)
        self.workflows[wid] = dict(body, id=wid, active=was_active)
        return 200, self.workflows[wid]

    def view(self, wid):
        """A workflow as the public API shows it. A published workflow shows its version after the lag. Each read uses up one lag."""
        wf = dict(self.workflows[wid])
        left = self.lag.get(wid, 0)
        if wf.get("active") and left > 0:
            self.lag[wid] = left - 1
            wf["activeVersionId"] = None
        else:
            wf["activeVersionId"] = ("version-" + wid) if wf.get("active") else None
        return wf

    def __enter__(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
