"""Smoke checks for n8n. `fleetctl n8n smoke` runs them. `fleetctl n8n upgrade` runs them before it keeps a new version.

Each check returns (name, ok, detail). A detail never holds a secret.
"""
import time

from ops.fleetctl import system
from ops.fleetlib import config, httpc, secrets
from ops.n8n import bootstrap, prepare
from ops.n8n import client as nclient

LOOPBACK = ("127.0.0.1", "::1")


def check_health(c):
    return ("healthz", c.healthy(), "answers ok" if c.healthy() else "does not answer ok on /healthz")


def check_login(c, email, password):
    try:
        c.login(email, password)
    except nclient.N8nError as exc:
        return ("owner login", False, nclient.secret_free(exc))
    return ("owner login", True, "the owner can log in")


def check_version(c, pinned):
    try:
        running = c.version()
    except nclient.N8nError as exc:
        return ("version", False, nclient.secret_free(exc))
    ok = bool(running) and running == pinned
    return ("version", ok, "n8n %s runs and %s is pinned" % (running or "?", pinned))


def check_loopback(runner=system.run, ports=(5678, 5679)):
    exposed = sorted({p for a, p in system.listening_sockets(runner) if p in ports and a not in LOOPBACK})
    seen = sorted({p for a, p in system.listening_sockets(runner) if p in ports})
    if exposed:
        return ("loopback only", False, "port %s listens on more than 127.0.0.1" % ", ".join(map(str, exposed)))
    return ("loopback only", bool(seen), "ports %s listen on 127.0.0.1 only" % ", ".join(map(str, seen)) if seen else "no n8n port listens")


def check_workflows(c, key, expected=None):
    expected = list(expected or bootstrap.ORDER)
    try:
        have = {w["name"]: bool(w.get("active")) for w in c.workflows(key)}
    except nclient.N8nError as exc:
        return ("workflows published", False, nclient.secret_free(exc))
    missing = [n for n in expected if n not in have]
    inactive = [n for n in expected if n in have and not have[n]]
    if missing or inactive:
        return ("workflows published", False, "missing: %s. not published: %s" % (", ".join(missing) or "none", ", ".join(inactive) or "none"))
    return ("workflows published", True, "all %d are published" % len(expected))


def check_webhook(c, token, path="/webhook/fleet-event"):
    """A bad event type: the workflow does nothing with it. A 200 shows that the webhook and its header token work."""
    try:
        resp = httpc.post(c.base + path, json_body={"type": "BAD TYPE", "severity": "info", "text": "smoke"}, headers={"X-Fleet-Token": token}, timeout=15)
        bad = httpc.post(c.base + path, json_body={"type": "n8n.smoke"}, headers={"X-Fleet-Token": "wrong"}, timeout=15)
    except httpc.TransportError as exc:
        return ("webhook", False, str(exc))
    ok = resp.status == 200 and bad.status in (401, 403)
    return ("webhook", ok, "the right token gives %d and a wrong token gives %d" % (resp.status, bad.status))


def run_all(cfg=None, runner=system.run, restart=False, now=time.time, sleep=time.sleep):
    """The Phase 1 checks. Returns a list of (name, ok, detail)."""
    cfg = cfg or config.load()
    base = "http://127.0.0.1:%d" % config.port(cfg, "n8n")
    c = nclient.Client(base)
    env = prepare.read_env_file(prepare.run_dir() / ".env")
    pins = prepare.read_pins()
    results = [check_health(c), check_loopback(runner)]
    results.append(check_login(c, env.get("N8N_OWNER_EMAIL", ""), secrets.value_of("N8N_OWNER_PASSWORD")))
    results.append(check_version(c, pins["N8N_VERSION"]))
    key = secrets.value_of("N8N_API_KEY")
    results.append(check_workflows(c, key) if key else ("workflows published", False, "no API key yet. Run: fleetctl n8n bootstrap"))
    results.append(check_webhook(c, secrets.value_of("FLEET_EVENT_TOKEN")))
    if restart:
        rc, out = runner(["systemctl", "restart", "agent-fleet-n8n.service"], timeout=240)
        ready = rc == 0 and c.wait_ready(240)
        results.append(("restart", ready, "n8n came back" if ready else "n8n did not come back after the restart"))
        if ready and key:
            name, ok, detail = check_workflows(c, key)
            results.append(("workflows stay published after a restart", ok, detail))
    return results
