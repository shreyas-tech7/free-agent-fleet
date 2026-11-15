"""A small client for n8n. It uses only the standard library through ops.fleetlib.httpc.

Two kinds of call:
  - the editor API, with a login cookie. It makes the API key. The cookie is flagged Secure, so it is sent by hand.
  - the public API, with the API key. It manages workflows and credentials.
No message, log line, or exception holds a password, a cookie, or a key. See D-040.
"""
import json
import re
import time

from ops.fleetlib import httpc

# The smallest scope list that fleetctl needs. A key with these scopes cannot delete a workflow, read a secret,
# or change a user. Activating a workflow is `workflow:activate`.
API_SCOPES = ["workflow:create", "workflow:read", "workflow:update", "workflow:list", "workflow:activate", "workflow:deactivate",
              "credential:create", "credential:update", "credential:list", "execution:list", "execution:read"]
# Fields that the public API accepts when it makes or changes a workflow.
WORKFLOW_FIELDS = ("name", "nodes", "connections", "settings")
SETTINGS_FIELDS = ("saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution", "saveDataSuccessExecution", "executionTimeout",
                   "errorWorkflow", "timezone", "executionOrder", "callerPolicy", "callerIds", "timeSavedPerExecution", "availableInMCP")


# The wait for a new publication to show up. 12 tries, 1 second apart.
PUBLISH_WAIT_TRIES = 12
PUBLISH_WAIT_STEP = 1
NOT_PUBLISHED_YET = "has no published version"


class N8nError(Exception):
    pass


class Client:
    def __init__(self, base, transport=None, sleep=time.sleep):
        self.base = base.rstrip("/")
        self._request = transport or httpc.request
        self._sleep = sleep
        self.cookie = ""

    # ------------------------------------------------------------ plumbing
    def call(self, method, path, body=None, key=None, timeout=30):
        """Returns (status, parsed JSON or None). Raises N8nError when n8n is unreachable."""
        headers = {}
        if key:
            headers["X-N8N-API-KEY"] = key
        elif self.cookie:
            headers["Cookie"] = self.cookie
        try:
            resp = self._request(method, self.base + path, headers=headers, json_body=body, timeout=timeout, max_bytes=30_000_000)
        except httpc.TransportError as exc:
            raise N8nError("n8n is not reachable: %s" % exc) from None
        try:
            data = resp.json() if resp.body else None
        except ValueError:
            data = None
        self._last_headers = resp.headers
        return resp.status, data

    def healthy(self):
        try:
            status, data = self.call("GET", "/healthz", timeout=5)
        except N8nError:
            return False
        return status == 200 and (data or {}).get("status") == "ok"

    def ready(self):
        try:
            status, _ = self.call("GET", "/healthz/readiness", timeout=5)
        except N8nError:
            return False
        return status == 200

    def wait_ready(self, seconds=180, step=3):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self.ready():
                return True
            self._sleep(step)
        return False

    # ------------------------------------------------------------ the editor API
    def login(self, email, password):
        status, data = self.call("POST", "/rest/login", {"emailOrLdapLoginId": email, "password": password})
        if status != 200:
            raise N8nError("the n8n login failed with status %d" % status)
        cookie = self._last_headers.get("set-cookie", "")
        pair = cookie.split(";", 1)[0]
        if not pair.startswith("n8n-auth="):
            raise N8nError("the n8n login gave no session cookie")
        self.cookie = pair
        return data

    def version(self):
        """The version of the running n8n. It needs a login."""
        status, data = self.call("GET", "/rest/settings")
        return ((data or {}).get("data") or {}).get("versionCli") if status == 200 else None

    def create_api_key(self, label, scopes=None, expires_at=None):
        """A new public API key. The raw key is returned once. The caller must store it."""
        status, data = self.call("POST", "/rest/api-keys", {"label": label, "scopes": list(scopes or API_SCOPES), "expiresAt": expires_at})
        raw = ((data or {}).get("data") or {}).get("rawApiKey")
        if status != 200 or not raw:
            raise N8nError("n8n did not make an API key (status %d)" % status)
        return raw

    def list_api_keys(self):
        status, data = self.call("GET", "/rest/api-keys")
        return ((data or {}).get("data") or {}).get("items", []) if status == 200 else []

    def delete_api_key(self, key_id):
        status, _ = self.call("DELETE", "/rest/api-keys/%s" % key_id)
        return status in (200, 204)

    # ------------------------------------------------------------ the public API
    def pages(self, path, key, limit=100):
        out, cursor = [], ""
        for _ in range(50):
            sep = "&" if "?" in path else "?"
            status, data = self.call("GET", "%s%slimit=%d%s" % (path, sep, limit, ("&cursor=" + cursor) if cursor else ""), key=key)
            if status != 200 or not isinstance(data, dict):
                raise N8nError("n8n answered %d for %s" % (status, path.split("?")[0]))
            out += data.get("data", [])
            cursor = data.get("nextCursor") or ""
            if not cursor:
                break
        return out

    def workflows(self, key):
        return self.pages("/api/v1/workflows", key)

    def workflow(self, key, workflow_id):
        status, data = self.call("GET", "/api/v1/workflows/%s" % workflow_id, key=key)
        if status != 200:
            raise N8nError("workflow %s: status %d" % (workflow_id, status))
        return data

    def _clean(self, wf):
        body = {k: wf[k] for k in WORKFLOW_FIELDS if k in wf}
        body["settings"] = {k: v for k, v in (body.get("settings") or {}).items() if k in SETTINGS_FIELDS}
        return body

    def save_workflow(self, key, wf, existing_id=None):
        """Make or replace one workflow. Returns its id. The workflow keeps its published state.

        After the first publication of the error workflow, n8n may need a moment before it shows the published version.
        A workflow that points at it then gets a 400. That one answer is tried again. See D-053.
        """
        body = self._clean(wf)
        for attempt in range(PUBLISH_WAIT_TRIES):
            if existing_id:
                status, data = self.call("PUT", "/api/v1/workflows/%s" % existing_id, body, key=key)
            else:
                status, data = self.call("POST", "/api/v1/workflows", body, key=key)
            message = str(data.get("message", "")) if isinstance(data, dict) else ""
            if status == 400 and NOT_PUBLISHED_YET in message and attempt < PUBLISH_WAIT_TRIES - 1:
                self._sleep(PUBLISH_WAIT_STEP)
                continue
            break
        if status != 200 or not isinstance(data, dict) or not data.get("id"):
            raise N8nError("n8n refused workflow %s (status %d%s)" % (wf.get("name"), status, ": " + message[:160] if message else ""))
        return data["id"]

    def set_active(self, key, workflow_id, on=True):
        status, data = self.call("POST", "/api/v1/workflows/%s/%s" % (workflow_id, "activate" if on else "deactivate"), {}, key=key)
        if status != 200:
            raise N8nError("n8n could not %s workflow %s (status %d%s)" % ("publish" if on else "unpublish", workflow_id, status,
                                                                       ": " + str(data.get("message", ""))[:160] if isinstance(data, dict) else ""))
        if on:
            self.wait_published(key, workflow_id)
        return True

    def wait_published(self, key, workflow_id):
        """Wait until n8n shows the published version of a workflow. A reply without the field means an n8n that does not report it. Do not wait then."""
        for attempt in range(PUBLISH_WAIT_TRIES):
            try:
                shown = self.workflow(key, workflow_id)
            except N8nError:
                shown = {}
            if "activeVersionId" not in shown or shown["activeVersionId"]:
                return True
            if attempt < PUBLISH_WAIT_TRIES - 1:
                self._sleep(PUBLISH_WAIT_STEP)
        raise N8nError("n8n did not show a published version of workflow %s after %d seconds" % (workflow_id, PUBLISH_WAIT_TRIES * PUBLISH_WAIT_STEP))

    def credentials(self, key):
        return self.pages("/api/v1/credentials", key)

    def save_credential(self, key, name, type_, data, existing_id=None):
        body = {"name": name, "type": type_, "data": data}
        if existing_id:
            status, out = self.call("PATCH", "/api/v1/credentials/%s" % existing_id, body, key=key)
        else:
            status, out = self.call("POST", "/api/v1/credentials", body, key=key)
        if status not in (200, 201) or not isinstance(out, dict) or not out.get("id"):
            raise N8nError("n8n refused credential %s (status %d)" % (name, status))
        return out["id"]

    def executions(self, key, workflow_id=None, status=None, limit=20):
        path = "/api/v1/executions?limit=%d" % limit
        if workflow_id:
            path += "&workflowId=%s" % workflow_id
        if status:
            path += "&status=%s" % status
        code, data = self.call("GET", path, key=key)
        return (data or {}).get("data", []) if code == 200 else []


def secret_free(text):
    """For messages. Cut anything that looks like a JWT (an n8n API key) or a long token."""
    text = re.sub(r"eyJ[A-Za-z0-9_-]{5,}(\.[A-Za-z0-9_-]+){0,2}", "<hidden>", str(text))
    return re.sub(r"[A-Za-z0-9_-]{24,}", "<hidden>", text)
