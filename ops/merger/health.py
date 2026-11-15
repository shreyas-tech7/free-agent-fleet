"""Health check after a deploy. It must pass, or the merger rolls the change back."""
import json
import pathlib
import re
import subprocess
import time
import urllib.error
import urllib.request

import yaml

from ops.fleetlib import config

# changed path prefix -> units to restart
RESTART_MAP = [
    ("agents/", ["agent-fleet-gateway"]),
    ("skills/", ["agent-fleet-gateway"]),
    ("ops/guard/", ["agent-fleet-guard"]),
    ("ops/reader/", ["agent-fleet-reader"]),
    ("ops/publisher/", ["agent-fleet-publisher"]),
    ("ops/deployer/", ["agent-fleet-deployer"]),
    ("ops/control/", ["agent-fleet-control", "agent-fleet-slack"]),
    ("ops/board/", ["agent-fleet-board"]),
    ("ops/mcp/", ["agent-fleet-gateway"]),
    ("ops/fleetlib/", ["agent-fleet-guard", "agent-fleet-reader", "agent-fleet-publisher", "agent-fleet-deployer", "agent-fleet-control", "agent-fleet-slack", "agent-fleet-board", "agent-fleet-gateway"]),
    ("systemd/", []),
]
HTTP_UNITS = {"agent-fleet-guard": "guard", "agent-fleet-reader": "reader", "agent-fleet-publisher": "publisher",
              "agent-fleet-deployer": "deployer", "agent-fleet-control": "control"}


def units_to_restart(changed_paths, exists=None):
    """The units to restart for a change. With `exists`, a unit that is not installed yet is left out. See D-049.

    A restart of a unit that does not exist fails the health check and rolls back a good change. The first deploy that
    adds a unit to this map always meets that case, because the installer makes the unit after the deploy.
    """
    units = []
    for path in changed_paths:
        for prefix, targets in RESTART_MAP:
            if path.startswith(prefix):
                units += [u for u in targets if u not in units]
    return [u for u in units if exists(u)] if exists else units


def unit_installed(unit):
    return pathlib.Path("/etc/systemd/system/%s.service" % unit).exists()


def selfcheck(repo):
    """Static checks of the deployed tree: every agent has its files, the files parse, and the Rule of Two holds."""
    repo = pathlib.Path(repo)
    problems = []
    agents = sorted(p for p in (repo / "agents").glob("*") if p.is_dir()) if (repo / "agents").is_dir() else []
    for a in agents:
        for need in ("SOUL.md", "agent.yaml"):
            if not (a / need).is_file():
                problems.append("%s/%s is missing" % (a.name, need))
        try:
            meta = yaml.safe_load((a / "agent.yaml").read_text()) or {}
        except (OSError, yaml.YAMLError):
            problems.append("%s/agent.yaml does not parse" % a.name)
            continue
        flags = (meta.get("rule_of_two") or {})
        if all(flags.get(k) is True for k in ("untrusted_input", "sensitive_access", "external_actions")):
            problems.append("%s breaks the Rule of Two" % a.name)
    for skill in (repo / "skills").rglob("SKILL.md") if (repo / "skills").is_dir() else []:
        text = skill.read_text(errors="replace")
        if not re.match(r"\A---\n.*?\n---\n", text, re.S):
            problems.append("%s has no front matter" % skill.relative_to(repo))
    return problems


def _http_ok(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:      # nosec: loopback only
            return resp.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _unit_active(unit, runner=subprocess.run):
    proc = runner(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=30)
    return proc.stdout.strip() == "active"


def _wait_for(test, sleep, tries, step):
    """Call test() until it is true or the tries are used up. It sleeps between tries only. See D-049."""
    for attempt in range(tries):
        if test():
            return True
        if attempt < tries - 1:
            sleep(step)
    return False


def check(repo, restarted, cfg=None, runner=subprocess.run, http_ok=_http_ok, sleep=time.sleep, settle=8, force_failure=False, wait=90, step=3):
    """Return (ok, details).

    A restarted service gets time to come up. The injection screen loads a model, and that takes 3 to 15 seconds,
    more when the machine is busy. The first deploy to meet a slow start was rolled back by a check that tried once.
    """
    cfg = cfg or config.load()
    if force_failure:
        return False, ["forced failure for the rollback test"]
    details = []
    problems = selfcheck(repo)
    details += problems
    sleep(settle)                                       # let the restarted services come up
    tries = max(1, int(wait // step) + 1)
    for unit in restarted:
        if not _wait_for(lambda: _unit_active(unit, runner), sleep, min(tries, 11), step):
            details.append("%s is not active" % unit)
        elif unit in HTTP_UNITS:
            port = config.port(cfg, HTTP_UNITS[unit])
            url = "http://127.0.0.1:%d/health" % port
            if not _wait_for(lambda: http_ok(url), sleep, tries, step):
                details.append("%s does not answer on port %d" % (unit, port))
    return not details, details
