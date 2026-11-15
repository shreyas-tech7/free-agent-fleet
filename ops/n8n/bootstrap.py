"""fleetctl n8n bootstrap. The first-run steps after n8n is up. It is safe to run twice. Run it as root.

 1. Wait until n8n is ready.
 2. Log in as the owner. The password is N8N_OWNER_PASSWORD. No person clicks through a setup page.
 3. Make the public API key with the smallest scopes, unless the stored key still works. Store it as N8N_API_KEY.
 4. Make or update the n8n credentials from the fleet secrets. A credential whose secret did not change is left alone.
 5. Import the workflows from n8n/workflows. Put the real ids in place of the FLEET_CRED and FLEET_WF names.
    Publish each one. A workflow that did not change is left alone.
It never prints or logs a password, a key, or a token. See D-040.
"""
import hashlib
import json
import pathlib
import sys

from ops.fleetlib import config, log, paths, secrets
from ops.n8n import client as nclient
from ops.n8n import prepare

LOG = log.get("n8n-bootstrap")
KEY_LABEL = "fleetctl"
# Workflows that others point at come first: the error workflow, then the sub-workflow of fetch_feed.
ORDER = ["fleet-error", "fleet-tool-fetch-feed", "fleet-event", "fleet-tools", "health-watch", "token-ledger"]


def credential_specs():
    """The credentials that the workflows use. Each value comes from a fleet secret."""
    return [
        ("fleet-event-token", "httpHeaderAuth", lambda: {"name": "X-Fleet-Token", "value": secrets.value_of("FLEET_EVENT_TOKEN")}),
        ("fleet-action-token", "httpBearerAuth", lambda: {"token": secrets.value_of("FLEET_ACTION_TOKEN")}),
        ("fleet-tools-token", "httpBearerAuth", lambda: {"token": secrets.value_of("N8N_TOOLS_TOKEN")}),
    ]


def state_path():
    return prepare.run_dir() / "bootstrap-state.json"


def load_state():
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    from ops.fleetlib import jsonio
    import os
    owner = "fleetops" if os.geteuid() == 0 else None
    jsonio.write_json(state_path(), state, mode=0o600, owner=owner, group=owner)


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def resolve(text, cred_ids, wf_ids):
    """Replace the placeholder names with real ids. A name that has no id is an error."""
    for name, value in cred_ids.items():
        text = text.replace('"FLEET_CRED:%s"' % name, json.dumps(value))
    for name, value in wf_ids.items():
        text = text.replace('"FLEET_WF:%s"' % name, json.dumps(value))
    if "FLEET_CRED:" in text or "FLEET_WF:" in text:
        left = sorted({w.strip('",') for w in text.split() if "FLEET_CRED:" in w or "FLEET_WF:" in w})
        raise nclient.N8nError("a placeholder has no id yet: %s" % ", ".join(left[:3]))
    return text


def load_workflows(folder=None):
    folder = pathlib.Path(folder or (paths.repo_root() / "n8n" / "workflows"))
    found = {}
    for name in ORDER:
        path = folder / (name + ".json")
        if path.exists():
            found[name] = path.read_text()
    extra = sorted(p.stem for p in folder.glob("*.json") if p.stem not in ORDER)
    if extra:
        raise nclient.N8nError("a workflow file is not in the bootstrap order: %s" % ", ".join(extra))
    return found


def ensure_api_key(c, email, password, current_key):
    """Returns (key, 'kept' or 'made'). A key is kept only when a harmless call with it works."""
    if current_key:
        try:
            c.workflows(current_key)
            return current_key, "kept"
        except nclient.N8nError:
            pass
    c.login(email, password)
    for item in c.list_api_keys():
        if item.get("label") == KEY_LABEL:
            c.delete_api_key(item["id"])
    return c.create_api_key(KEY_LABEL), "made"


def sync_credentials(c, key, state):
    existing = {x["name"]: x["id"] for x in c.credentials(key)}
    seen = state.setdefault("credentials", {})
    ids, report = {}, {}
    for name, type_, data_fn in credential_specs():
        data = data_fn()
        if not all(data.values()):
            raise nclient.N8nError("the secret for credential %s is not set. Run: fleetctl n8n prepare" % name)
        mark = digest(data)
        if name in existing and seen.get(name) == mark:
            ids[name], report[name] = existing[name], "kept"
            continue
        ids[name] = c.save_credential(key, name, type_, data, existing.get(name))
        seen[name] = mark
        report[name] = "updated" if name in existing else "created"
    return ids, report


def sync_workflows(c, key, cred_ids, state, folder=None, publish=True):
    existing = {w["name"]: w for w in c.workflows(key)}
    wf_ids = {name: w["id"] for name, w in existing.items()}
    seen = state.setdefault("workflows", {})
    report, active = {}, []
    sources = load_workflows(folder)
    # First give every workflow an id, so that a placeholder for a later one can be resolved.
    for name in sources:
        if name not in wf_ids:
            stub = {"name": name, "nodes": [{"id": "00000000-0000-4000-8000-000000000001", "name": "Start", "type": "n8n-nodes-base.manualTrigger",
                                              "typeVersion": 1, "position": [0, 0], "parameters": {}}], "connections": {}, "settings": {}}
            wf_ids[name] = c.save_workflow(key, stub)
            report[name] = "created"
    for name, raw in sources.items():
        wf = json.loads(resolve(raw, cred_ids, wf_ids))
        mark = digest(wf)
        is_active = bool(existing.get(name, {}).get("active"))
        if seen.get(name) == mark and report.get(name) != "created" and (is_active or not publish):
            report[name] = "kept"
            continue
        if is_active:
            c.set_active(key, wf_ids[name], False)
        c.save_workflow(key, wf, existing_id=wf_ids[name])
        if publish:
            c.set_active(key, wf_ids[name], True)
            active.append(name)
        seen[name] = mark
        report[name] = report.get(name) or "updated"
    return wf_ids, report, active


def bootstrap(base=None, folder=None, c=None, publish=True):
    cfg = config.load()
    env = prepare.read_env_file(prepare.run_dir() / ".env")
    email = env.get("N8N_OWNER_EMAIL")
    password = secrets.value_of("N8N_OWNER_PASSWORD")
    if not email or not password:
        raise nclient.N8nError("run fleetctl n8n prepare first. The owner email or password is missing.")
    c = c or nclient.Client(base or "http://127.0.0.1:%d" % config.port(cfg, "n8n"))
    if not c.wait_ready(180):
        raise nclient.N8nError("n8n is not ready. Check: fleetctl logs n8n")
    key, key_state = ensure_api_key(c, email, password, secrets.value_of("N8N_API_KEY"))
    if key_state == "made":
        secrets.set_value("N8N_API_KEY", key)
    state = load_state()
    cred_ids, cred_report = sync_credentials(c, key, state)
    wf_ids, wf_report, active = sync_workflows(c, key, cred_ids, state, folder, publish)
    save_state(state)
    return {"api_key": key_state, "credentials": cred_report, "workflows": wf_report, "published": active}


def main(argv=None):
    try:
        report = bootstrap()
    except (nclient.N8nError, secrets.SecretError, prepare.PrepareError) as exc:
        print(json.dumps({"status": "failed", "reason": nclient.secret_free(exc)}))
        return 1
    report["status"] = "ok"
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
