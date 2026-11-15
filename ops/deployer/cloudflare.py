"""Cloudflare REST API calls for the Deployer. Workers and D1 on the free plan only.

No wrangler and no build step. The Deployer sends one Worker module and, when asked, a D1 binding.
That is all the product can reach. The token never leaves this module.
"""
import json
import uuid

from ops.fleetlib import httpc

API = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    pass


class Cloudflare:
    def __init__(self, token, account_id, http=None):
        self._token = token
        self.account = account_id
        self.http = http or httpc

    def _call(self, method, path, json_body=None, data=None, content_type=None, timeout=60):
        headers = {"Authorization": "Bearer " + self._token}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            resp = self.http.request(method, API + path, headers=headers, json_body=json_body, data=data, timeout=timeout)
        except httpc.TransportError as exc:
            raise CloudflareError("Cloudflare is not reachable: %s" % exc) from None
        try:
            body = resp.json()
        except ValueError:
            raise CloudflareError("Cloudflare gave a bad answer (status %d)" % resp.status) from None
        if resp.status >= 300 or not body.get("success", True):
            errors = "; ".join(str(e.get("message", e))[:120] for e in (body.get("errors") or [])[:3])
            raise CloudflareError("Cloudflare refused the call (status %d): %s" % (resp.status, errors))
        return body.get("result")

    def verify_token(self):
        return (self._call("GET", "/user/tokens/verify") or {}).get("status") == "active"

    def subscriptions(self):
        return self._call("GET", "/accounts/%s/subscriptions" % self.account) or []

    def subdomain(self):
        return (self._call("GET", "/accounts/%s/workers/subdomain" % self.account) or {}).get("subdomain", "")

    def d1_ensure(self, name):
        found = self._call("GET", "/accounts/%s/d1/database?name=%s" % (self.account, name)) or []
        for db in found:
            if db.get("name") == name:
                return db["uuid"]
        return self._call("POST", "/accounts/%s/d1/database" % self.account, json_body={"name": name})["uuid"]

    def d1_query(self, database_id, sql):
        return self._call("POST", "/accounts/%s/d1/database/%s/query" % (self.account, database_id), json_body={"sql": sql})

    def upload_worker(self, script, module_source, compatibility_date, d1_id=None):
        meta = {"main_module": "worker.js", "compatibility_date": compatibility_date, "bindings": []}
        if d1_id:
            meta["bindings"].append({"type": "d1", "name": "DB", "id": d1_id})
        boundary = "fleet-" + uuid.uuid4().hex
        parts = [
            ("metadata", None, "application/json", json.dumps(meta).encode()),
            ("worker.js", "worker.js", "application/javascript+module", module_source),
        ]
        body = b""
        for name, filename, ctype, content in parts:
            disp = 'form-data; name="%s"' % name + ('; filename="%s"' % filename if filename else "")
            body += ("--%s\r\nContent-Disposition: %s\r\nContent-Type: %s\r\n\r\n" % (boundary, disp, ctype)).encode() + content + b"\r\n"
        body += ("--%s--\r\n" % boundary).encode()
        self._call("PUT", "/accounts/%s/workers/scripts/%s" % (self.account, script), data=body,
                   content_type="multipart/form-data; boundary=" + boundary, timeout=120)
        self._call("POST", "/accounts/%s/workers/scripts/%s/subdomain" % (self.account, script), json_body={"enabled": True})
        return True
