"""Helpers that fleetctl uses to run commands and to read the machine.

Every command here takes the runner as an argument, so tests can replace it.
"""
import os
import pwd
import subprocess

from ops.fleetlib import httpc

# short name -> (systemd unit, port key in config or None, health path or None)
SERVICES = {
    "router": ("agent-fleet-router", "router", "/api/health"),
    "guard": ("agent-fleet-guard", "guard", "/health"),
    "searxng": ("agent-fleet-searxng", "searxng", "/healthz"),
    "reader": ("agent-fleet-reader", "reader", "/health"),
    "publisher": ("agent-fleet-publisher", "publisher", "/health"),
    "deployer": ("agent-fleet-deployer", "deployer", "/health"),
    "gateway": ("agent-fleet-gateway", None, None),
    "control": ("agent-fleet-control", "control", "/health"),
    "n8n": ("agent-fleet-n8n", "n8n", "/healthz"),
    "board": ("agent-fleet-board", None, None),
    "slack": ("agent-fleet-slack", None, None),
}
TIMERS = {
    "quota": "agent-fleet-quota.timer",
    "briefing": "agent-fleet-briefing.timer",
    "merger": "agent-fleet-merger.timer",
    "export": "agent-fleet-export.timer",
    "backup": "agent-fleet-backup.timer",
    "redteam": "agent-fleet-redteam.timer",
    "n8n_backup": "agent-fleet-n8n-backup.timer",
    "n8n_releases": "agent-fleet-n8n-releases.timer",
    "n8n_migration": "agent-fleet-n8n-migration.timer",
    "portfolio": "agent-fleet-portfolio.timer",
}
# Names that `fleetctl logs` accepts, besides the services.
LOG_UNITS = dict({k: v[0] + ".service" for k, v in SERVICES.items()},
                 quota="agent-fleet-quota.service", briefing="agent-fleet-briefing.service",
                 merger="agent-fleet-merger.service", export="agent-fleet-export.service",
                 backup="agent-fleet-backup.service", redteam="agent-fleet-redteam.service",
                 n8n_backup="agent-fleet-n8n-backup.service", n8n_releases="agent-fleet-n8n-releases.service",
                 n8n_migration="agent-fleet-n8n-migration.service", portfolio="agent-fleet-portfolio.service", events="agent-fleet-events.service", cards="agent-fleet-cards.service")


def is_root():
    return os.geteuid() == 0


def run(argv, timeout=60, user=None, extra_env=None, input_text=None):
    """Run a command. Returns (exit code, combined output). Never raises for a missing program or a timeout.

    With `user` and root, the command runs as that user with a working user session and rootless Docker.
    `input_text` goes to the standard input of the command. Use it for a secret, so that it never sits in an argument.
    """
    cmd = list(argv)
    env = None
    if user and is_root():
        try:
            info = pwd.getpwnam(user)
        except KeyError:
            return 127, "no such user: %s" % user
        pairs = {
            "HOME": info.pw_dir,
            "PATH": "%s/.local/bin:/usr/local/bin:/usr/bin:/bin" % info.pw_dir,
            "LANG": "C.UTF-8",
            "XDG_RUNTIME_DIR": "/run/user/%d" % info.pw_uid,
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/%d/bus" % info.pw_uid,
            "DOCKER_HOST": "unix:///run/user/%d/docker.sock" % info.pw_uid,
            "HERMES_MANAGED_DIR": "/etc/hermes",
        }
        pairs.update(extra_env or {})
        cmd = ["runuser", "-u", user, "--", "env", "-i"] + ["%s=%s" % kv for kv in pairs.items()] + cmd
    elif extra_env:
        env = dict(os.environ, **extra_env)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd="/", input=input_text)  # nosemgrep: dangerous-subprocess-use-audit
    except FileNotFoundError:
        return 127, "command not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "timeout after %ds" % timeout
    except OSError as exc:
        return 126, type(exc).__name__
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def unit_state(unit, runner=run):
    """The answer of `systemctl is-active`: active, inactive, failed, activating, unknown."""
    rc, out = runner(["systemctl", "is-active", unit if "." in unit else unit + ".service"], timeout=15)
    first = out.splitlines()[0].strip() if out else ""
    return first or ("active" if rc == 0 else "unknown")


def unit_enabled(unit, runner=run):
    rc, out = runner(["systemctl", "is-enabled", unit], timeout=15)
    return (out.splitlines()[0].strip() if out else "") or ("enabled" if rc == 0 else "unknown")


def http_json(url, timeout=4, headers=None):
    """GET a loopback URL. Returns (status, parsed JSON or None). Status 0 means unreachable."""
    try:
        resp = httpc.get(url, timeout=timeout, headers=headers or {})
    except httpc.TransportError:
        return 0, None
    try:
        return resp.status, resp.json()
    except ValueError:
        return resp.status, None


def listening_sockets(runner=run):
    """Return [(address, port)] for every TCP listener, from `ss -ltnH`."""
    rc, out = runner(["ss", "-ltnH"], timeout=15)
    found = []
    if rc != 0:
        return found
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        addr, _, port = local.rpartition(":")
        if port.isdigit():
            found.append((addr.strip("[]"), int(port)))
    return found
