"""Apply the fleet's router settings to OmniRoute.

What it does, in order:
 1. Log in to the router management API with the router's own password.
 2. Turn on the zero-cost and no-proxy settings (rules R1 and R4).
 3. For each approved provider that has all its keys, make or update the provider connection.
    Remove the connection of a provider that is no longer approved or has lost its keys.
 4. Make or update the four combos from config/router/combos.yaml. Keep only targets that the
    provider offers. A combo with no valid target keeps its first target, so the name stays defined.
 5. Make one restricted client key for each client (Hermes, briefing, dev). A client key can call
    its own combos and nothing else. Only the dev key can call fleet-dev. It holds the trial models.

Run it as root: `fleetctl router apply`. It is safe to run twice.
The module never prints or logs a key.
"""
import hashlib
import json
import os
import pathlib

import yaml

from ops.fleetlib import config as cfglib
from ops.fleetlib import httpc, jsonio, log, paths, secrets
from ops.router import freeonly

LOG = log.get("router-apply")

POLICY_SETTINGS = {
    "freeAccessPolicy": "strict",
    "excludeTosAvoid": True,
    "hidePaidModels": True,
    "proxyEnabled": False,
    "cloudEnabled": False,
    "tailscaleEnabled": False,
}

# client label -> (secret name, combos that the key may call)
# The "dev" key is for tests only. It may call only dev_only combos (fleet-dev).
# No fleet client may call a dev_only combo. check_scopes enforces both rules. See D-031.
CLIENTS = {
    "hermes": ("ROUTER_KEY_HERMES", ["fleet-smart", "fleet-worker", "fleet-coder", "fleet-local"]),
    "briefing": ("ROUTER_KEY_BRIEFING", ["fleet-worker"]),
    "dev": ("ROUTER_KEY_DEV", ["fleet-dev"]),
}


class RouterError(Exception):
    pass


def check_scopes(combos_cfg, clients=None):
    """Fail closed before anything changes in the router.

    - A provider with trial terms (commercial_use: false) may appear only in a dev_only combo.
    - The dev client may call only dev_only combos. Every other client may call none of them.
    """
    clients = CLIENTS if clients is None else clients
    providers = combos_cfg["providers"]
    combos = combos_cfg["combos"]
    dev = {name for name, combo in combos.items() if combo.get("dev_only")}
    for name, combo in combos.items():
        if name in dev:
            continue
        for target in combo["targets"]:
            provider = target.partition("/")[0]
            if providers.get(provider, {}).get("commercial_use") is False:
                raise RouterError("combo %s holds %s, a provider with trial terms. Only a dev_only combo may." % (name, provider))
    for label, (_secret, allowed) in clients.items():
        for name in allowed:
            if label == "dev" and name not in dev:
                raise RouterError("the dev key may call only dev_only combos, not %s" % name)
            if label != "dev" and name in dev:
                raise RouterError("client %s may not call the dev_only combo %s" % (label, name))


def load_test_combo():
    return yaml.safe_load((paths.repo_root() / "config" / "router" / "test-combo.yaml").read_text())


def load_combos():
    return yaml.safe_load((paths.repo_root() / "config" / "router" / "combos.yaml").read_text())


OPENROUTER_PUBLIC_MODELS = "https://openrouter.ai/api/v1/models"


def openrouter_free_models(request=None):
    """Names of the free chat models in OpenRouter's public list. No key is sent. None when the list cannot be read.

    The router lists only a local catalog of 24 non-chat models for OpenRouter ("Auto-fetch disabled"). So the router
    list cannot tell which `:free` model exists. OpenRouter's own public list can. See D-037."""
    request = request or httpc.request
    try:
        resp = request("GET", OPENROUTER_PUBLIC_MODELS, timeout=20, max_bytes=8_000_000)
        data = resp.json() if resp.status == 200 else None
    except (httpc.TransportError, ValueError):
        return None
    ids = set()
    for model in (data or {}).get("data", []) if isinstance(data, dict) else []:
        price = model.get("pricing") or {}
        try:
            free = float(price.get("prompt", 1)) == 0 and float(price.get("completion", 1)) == 0
        except (TypeError, ValueError):
            free = False
        if free and str(model.get("id", "")).endswith(":free"):
            ids.add(model["id"])
    return ids or None


class Router:
    """A small client for the OmniRoute management API."""

    def __init__(self, base, password, transport=None):
        self.base = base.rstrip("/")
        self.password = password
        self.cookie = ""
        self._request = transport or httpc.request

    def req(self, method, path, body=None, bearer=None, timeout=30):
        headers = {}
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        elif self.cookie:
            headers["Cookie"] = self.cookie
        try:
            resp = self._request(method, self.base + path, headers=headers, json_body=body, timeout=timeout)
        except httpc.TransportError as exc:
            raise RouterError("the router is not reachable: %s" % exc) from None
        try:
            data = resp.json() if resp.body else None
        except ValueError:
            data = None
        return resp.status, data, resp

    def login(self):
        status, _data, resp = self.req("POST", "/api/auth/login", {"password": self.password})
        if status != 200:
            raise RouterError("router login failed with status %d" % status)
        cookie = resp.headers.get("set-cookie", "")
        pair = cookie.split(";", 1)[0]
        if not pair.startswith("auth_token="):
            raise RouterError("router login gave no session cookie")
        self.cookie = pair

    def healthy(self):
        try:
            status, _d, _r = self.req("GET", "/api/monitoring/health", timeout=5)
        except RouterError:
            return False
        return status == 200


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _norm_model(provider, model):
    """Drop a leading provider prefix, so 'groq/llama' and 'llama' compare equal."""
    for prefix in (provider + "/",):
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _offered(data):
    """Pull model ids from a provider models answer. Tolerates a few shapes."""
    items = []
    if isinstance(data, dict):
        for key in ("models", "data", "items"):
            if isinstance(data.get(key), list):
                items = data[key]
                break
    elif isinstance(data, list):
        items = data
    out = set()
    for item in items:
        if isinstance(item, str):
            out.add(item)
        elif isinstance(item, dict):
            for key in ("id", "model", "name"):
                if item.get(key):
                    out.add(str(item[key]))
                    break
    return out


def select_targets(combo, connected, offered):
    """Targets to keep, in order. `connected` is the set of provider ids. `offered` maps provider to model ids or None."""
    keep = []
    for target in combo["targets"]:
        provider, _, model = target.partition("/")
        if combo.get("local_only") and provider != "ollama":
            continue
        if provider not in connected:
            continue
        models = offered.get(provider)
        if models is not None and _norm_model(provider, model) not in {_norm_model(provider, m) for m in models}:
            continue
        if not freeonly.is_free(target):
            continue                                   # rule R1: free models only. See ops/router/freeonly.py.
        keep.append(target)
    return keep


def plan_providers(cfg, combos_cfg):
    """Which providers should have a connection. Returns {provider: key value} for those that should."""
    approved = set(cfglib.get(cfg, "router.approved_providers", []) or [])
    wanted = {}
    for pid, spec in combos_cfg["providers"].items():
        if pid not in approved:
            continue
        if spec.get("commercial_use") is False and not cfglib.get(cfg, "router.allow_noncommercial", False):
            continue                                   # trial terms forbid business use. The owner opts in. See D-029.
        values = {name: secrets.value_of(name) for name in spec["secrets"]}
        if all(values.values()):
            wanted[pid] = values
    return wanted


def apply_test_combo(router, on, spec, state):
    """Make or remove the provider node and the combo of the mock model. Returns a short word for the report."""
    prov, combo = spec["provider"], spec["combo"]
    status, data, _ = router.req("GET", "/api/provider-nodes")
    nodes = [n for n in ((data or {}).get("nodes") or (data if isinstance(data, list) else [])) if isinstance(n, dict)] if status == 200 else []
    node = next((n for n in nodes if n.get("prefix") == prov["prefix"]), None)
    status, data, _ = router.req("GET", "/api/combos")
    have = next((c for c in ((data or {}).get("combos", []) if status == 200 else []) if c.get("name") == combo["name"]), None)
    if not on:
        gone = []
        if have:
            router.req("DELETE", "/api/combos/%s" % have["id"])
            gone.append("combo")
        if node:
            router.req("DELETE", "/api/provider-nodes/%s" % node["id"])
            gone.append("node")
        state.pop("mock", None)
        return "removed " + "+".join(gone) if gone else "off"
    if not node:
        status, created, _ = router.req("POST", "/api/provider-nodes", {"name": prov["name"], "prefix": prov["prefix"], "apiType": prov["api_type"],
                                                                         "baseUrl": prov["base_url"], "providerIdSuffix": "fleet-mock"})
        if status not in (200, 201) or not isinstance(created, dict):
            return "failed to make the provider node (status %d)" % status
        node = created.get("node", created)
        router.req("POST", "/api/providers", {"provider": node.get("id"), "apiKey": prov["api_key"], "name": prov["name"]})
    body = {"name": combo["name"], "strategy": combo["strategy"], "enabled": True, "models": list(combo["targets"]), "config": {}}
    if have is None:
        status, _d, _ = router.req("POST", "/api/combos", body)
        result = "created" if status in (200, 201) else "failed to make the combo (status %d)" % status
    else:
        result = "kept"
    state["mock"] = True
    return result


def apply(router, cfg=None, combos_cfg=None, state_path=None, write_secret=None, openrouter_models=None):
    """Apply everything. Returns a report dict. `write_secret(name, value)` stores a new client key."""
    cfg = cfg or cfglib.load()
    combos_cfg = combos_cfg or load_combos()
    state_path = pathlib.Path(state_path or (paths.ops_state_dir() / "router" / "applied.json"))
    write_secret = write_secret or secrets.set_value
    state = jsonio.read_json(state_path, {}) or {}
    report = {"settings": "unchanged", "providers": {}, "combos": {}, "keys": {}, "warnings": [], "needs_restart": False}

    check_scopes(combos_cfg)
    router.login()

    # 1. policy settings
    status, current, _ = router.req("GET", "/api/settings")
    if status == 200 and isinstance(current, dict):
        diff = {k: v for k, v in POLICY_SETTINGS.items() if current.get(k) != v}
    else:
        diff = dict(POLICY_SETTINGS)
    if diff:
        status, _d, _ = router.req("PATCH", "/api/settings", diff)
        if status != 200:
            raise RouterError("could not apply the policy settings (status %d)" % status)
        report["settings"] = "applied: " + ", ".join(sorted(diff))

    # 2. provider connections
    wanted = plan_providers(cfg, combos_cfg)
    status, data, _ = router.req("GET", "/api/providers")
    existing = {}
    for conn in (data or {}).get("connections", []) if status == 200 else []:
        existing.setdefault(conn.get("provider"), []).append(conn)
    applied = state.get("providers", {})
    for pid, values in wanted.items():
        spec = combos_cfg["providers"][pid]
        rid = spec["router_id"]
        key_name = spec["secrets"][0]
        digest = _hash(json.dumps(values, sort_keys=True))
        conns = existing.get(rid, [])
        if not conns:
            status, _d, _ = router.req("POST", "/api/providers", {"provider": rid, "apiKey": values[key_name], "name": "fleet-" + pid})
            report["providers"][pid] = "created" if status in (200, 201) else "failed (status %d)" % status
        elif applied.get(pid) != digest:
            status, _d, _ = router.req("PUT", "/api/providers/%s" % conns[0]["id"], {"apiKey": values[key_name], "name": "fleet-" + pid})
            report["providers"][pid] = "updated" if status == 200 else "failed (status %d)" % status
        else:
            report["providers"][pid] = "kept"
        if report["providers"][pid] in ("created", "updated"):
            applied[pid] = digest
        # The Cloudflare account id reaches the router through its environment. A change needs a restart.
        for name, value in values.items():
            if name == "CLOUDFLARE_ACCOUNT_ID" and state.get("env", {}).get(name) != _hash(value):
                report["needs_restart"] = True
                state.setdefault("env", {})[name] = _hash(value)
    for rid, conns in existing.items():
        pid = next((p for p, s in combos_cfg["providers"].items() if s["router_id"] == rid), None)
        if pid is not None and pid not in wanted:
            for conn in conns:
                router.req("DELETE", "/api/providers/%s" % conn["id"])
            report["providers"][pid] = "removed"
            applied.pop(pid, None)
    state["providers"] = applied

    # 3. what each connected provider offers
    connected = set(wanted)
    offered = {}
    status, data, _ = router.req("GET", "/api/providers")
    conn_ids = {c.get("provider"): c.get("id") for c in (data or {}).get("connections", [])} if status == 200 else {}
    for pid in wanted:
        rid = combos_cfg["providers"][pid]["router_id"]
        if conn_ids.get(rid):
            st, models, _ = router.req("GET", "/api/providers/%s/models" % conn_ids[rid])
            offered[pid] = _offered(models) if st == 200 and _offered(models) else None
        else:
            offered[pid] = None
    if "openrouter" in wanted:
        free = (openrouter_models or openrouter_free_models)()
        offered["openrouter"] = free                     # None keeps the :free targets unchecked
        if free is None:
            report["warnings"].append("openrouter: the public model list could not be read, so its :free targets are not checked")

    # 4. combos
    status, data, _ = router.req("GET", "/api/combos")
    have = {c.get("name"): c for c in (data or {}).get("combos", [])} if status == 200 else {}
    for name, combo in combos_cfg["combos"].items():
        targets = select_targets(combo, connected, offered)
        placeholder = False
        if not targets:
            targets = combo["targets"][:1]
            placeholder = True
        body = {"name": name, "strategy": combo.get("strategy", "priority"), "enabled": True, "models": targets, "config": {}}
        current = have.get(name)
        if current is None:
            status, _d, _ = router.req("POST", "/api/combos", body)
            result = "created" if status in (200, 201) else "failed (status %d)" % status
        else:
            old = [m.get("model") if isinstance(m, dict) else m for m in current.get("models", [])]
            if old == targets:
                result = "kept"
            else:
                status, _d, _ = router.req("PUT", "/api/combos/%s" % current["id"], body)
                result = "updated" if status == 200 else "failed (status %d)" % status
        report["combos"][name] = result + (" (placeholder, no provider yet)" if placeholder else " (%d targets)" % len(targets))

    # 4b. the test combo for the mock model. It exists only while features.mock_llm is true.
    mock_on = bool(cfglib.get(cfg, "features.mock_llm", False))
    report["mock"] = apply_test_combo(router, mock_on, load_test_combo(), state)

    # 5. restricted client keys
    keys = state.get("client_keys", {})
    for label, (secret_name, allowed) in CLIENTS.items():
        allowed = list(allowed) + (["fleet-test"] if mock_on and label == "hermes" else [])
        raw = secrets.value_of(secret_name)
        ok = False
        if raw:
            st, _d, _ = router.req("GET", "/v1/models", bearer=raw)
            ok = st == 200
        if not ok:
            old_id = keys.get(label)
            status, created, _ = router.req("POST", "/api/keys", {"name": "fleet-" + label})
            if status not in (200, 201) or not isinstance(created, dict) or not created.get("key"):
                report["keys"][label] = "failed (status %d)" % status
                continue
            keys[label] = created["id"]
            write_secret(secret_name, created["key"], enforce_format=False)
            if old_id:
                router.req("DELETE", "/api/keys/%s" % old_id)
            report["keys"][label] = "created"
        else:
            report["keys"][label] = "kept"
        perm = {"allowedCombos": allowed, "allowAutoCombos": False, "catalogScope": "combos",
                "modelAccessMode": "restricted", "allowedModels": []}
        if keys.get(label):
            router.req("PATCH", "/api/keys/%s" % keys[label], perm)
    state["client_keys"] = keys

    owner = "fleetops" if os.geteuid() == 0 else None
    jsonio.write_json(state_path, state, mode=0o600, owner=owner, group=owner)
    return report


def main(argv=None):
    cfg = cfglib.load()
    base = "http://127.0.0.1:%d" % cfglib.port(cfg, "router")
    router = Router(base, secrets.value_of("INITIAL_PASSWORD"))
    report = apply(router, cfg)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
