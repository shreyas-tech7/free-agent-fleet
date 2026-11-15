"""fleetctl n8n serve on|off. Owner access to the n8n editor over Tailscale, to the tailnet only.

`tailscale serve` puts HTTPS in front of 127.0.0.1:5678 on its own port. It is never `tailscale funnel`.
Funnel would open n8n to the whole internet. This module has no code path that can build a funnel command.
n8n then needs its public address, so the compose .env gets N8N_EDITOR_BASE_URL, WEBHOOK_URL, and N8N_PROXY_HOPS=1.
The fallback is an SSH tunnel. See docs/N8N.md.
"""
import json
import os
import re

from ops.fleetctl import system
from ops.fleetlib import config, jsonio
from ops.n8n import prepare

DEFAULT_HTTPS_PORT = 8443
SERVE_KEYS = ("N8N_EDITOR_BASE_URL", "WEBHOOK_URL", "N8N_PROXY_HOPS")


class ServeError(Exception):
    pass


def tailnet_name(runner=system.run):
    rc, out = runner(["tailscale", "status", "--json"], timeout=30)
    try:
        data = json.loads(out)
    except ValueError:
        raise ServeError("cannot read the Tailscale status") from None
    if rc != 0 or data.get("BackendState") != "Running":
        raise ServeError("Tailscale is not connected. Run: sudo tailscale up")
    name = (data.get("Self") or {}).get("DNSName", "").rstrip(".")
    if not name or not re.fullmatch(r"[A-Za-z0-9.-]+", name):
        raise ServeError("Tailscale gave no machine name")
    return name


def _write_env(updates, remove=()):
    path = prepare.run_dir() / ".env"
    values = prepare.read_env_file(path)
    text = path.read_text()
    for key in remove:
        text = re.sub(r"(?m)^%s=.*\n?" % key, "", text)
    for key, value in updates.items():
        if re.search(r"(?m)^%s=" % key, text):
            text = re.sub(r"(?m)^%s=.*$" % key, "%s=%s" % (key, value), text)
        else:
            text = text.rstrip("\n") + "\n%s=%s\n" % (key, value)
    owner = "fleetops" if os.geteuid() == 0 else None
    jsonio.atomic_write(path, text, mode=0o600, owner=owner, group=owner)


def serve_on(runner=system.run, port=DEFAULT_HTTPS_PORT, cfg=None):
    cfg = cfg or config.load()
    name = tailnet_name(runner)
    target = "http://127.0.0.1:%d" % config.port(cfg, "n8n")
    argv = ["tailscale", "serve", "--bg", "--https=%d" % port, target]
    assert "funnel" not in " ".join(argv)
    rc, out = runner(argv, timeout=60)
    if rc != 0:
        raise ServeError("tailscale serve failed: %s" % out[-120:])
    url = "https://%s:%d" % (name, port)
    _write_env({"N8N_EDITOR_BASE_URL": url, "WEBHOOK_URL": url + "/", "N8N_PROXY_HOPS": "1"})
    return url


def serve_off(runner=system.run, port=DEFAULT_HTTPS_PORT):
    argv = ["tailscale", "serve", "--https=%d" % port, "off"]
    assert "funnel" not in " ".join(argv)
    rc, out = runner(argv, timeout=60)
    _write_env({"N8N_PROXY_HOPS": "0"}, remove=("N8N_EDITOR_BASE_URL", "WEBHOOK_URL"))
    return rc == 0
