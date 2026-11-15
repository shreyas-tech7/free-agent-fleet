"""The kill switch. Three levels: PAUSE, STOP, REVOKE.

Root runs this code through `fleetctl`. The control bot reaches it through one sudo rule.

PAUSE  Create the flag /var/lib/agent-fleet/PAUSED, pause Hermes cron, and stop the
       Kanban dispatcher. The Hermes shell hook then blocks every tool call and model
       turn while the flag exists. Work in progress ends at its next step.
STOP   Run `systemctl stop agent-fleet.target`. The control bot is outside the target.
REVOKE Print a checklist of every key and grant with its revoke page. No secret values.
"""
import datetime
import json
import os
import pathlib
import pwd
import subprocess

from ops.fleetlib import config, jsonio, paths, pause, secrets

FLEET_USER = "fleet"
TARGET = "agent-fleet.target"
PAUSED_JOBS = "paused_cron_jobs.json"


class NotReady(Exception):
    pass


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_runner(argv, user=None, timeout=90):
    """Run a command. With `user`, run it as that user with a working user session."""
    cmd = list(argv)
    if user and os.geteuid() == 0:
        info = pwd.getpwnam(user)
        env = {
            "HOME": info.pw_dir,
            "PATH": "%s/.local/bin:/usr/local/bin:/usr/bin:/bin" % info.pw_dir,
            "HERMES_HOME": str(paths.hermes_home()),
            "HERMES_MANAGED_DIR": "/etc/hermes",
            "XDG_RUNTIME_DIR": "/run/user/%d" % info.pw_uid,
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/%d/bus" % info.pw_uid,
        }
        cmd = ["runuser", "-u", user, "--", "env"] + ["%s=%s" % kv for kv in env.items()] + cmd
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _hermes(runner, args, profile=None):
    argv = ["hermes"] + (["-p", profile] if profile else []) + list(args)
    try:
        rc, out = runner(argv, user=FLEET_USER)
    except FileNotFoundError:
        return "skipped: hermes is not installed"
    except subprocess.TimeoutExpired:
        return "failed: timeout"
    if rc == 127:
        return "skipped: hermes is not installed"
    return "ok" if rc == 0 else "failed: " + out[-200:]


def _profiles():
    """The default profile (None) and every named profile."""
    base = paths.hermes_home() / "profiles"
    try:
        named = sorted(p.name for p in base.iterdir() if p.is_dir())
    except OSError:
        named = []
    return [None] + named


def _profile_key(profile):
    return profile or "default"


def _cron_jobs(runner, profile):
    """Return [(id, enabled)] for a profile, or [] when the list cannot be read."""
    argv = ["hermes"] + (["-p", profile] if profile else []) + ["cron", "list", "--json"]
    try:
        rc, out = runner(argv, user=FLEET_USER)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if rc != 0:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    jobs = data.get("jobs", data) if isinstance(data, dict) else data
    result = []
    for job in jobs if isinstance(jobs, list) else []:
        if isinstance(job, dict) and job.get("id"):
            result.append((str(job["id"]), bool(job.get("enabled", True)) and job.get("state") != "paused"))
    return result


def _write_flag(reason, by):
    flag = paths.paused_flag()
    owner = group = "root" if os.geteuid() == 0 else None
    jsonio.write_json(flag, {"reason": reason, "by": by, "since": _now()}, mode=0o644, owner=owner, group=group)


def pause_fleet(reason="owner", by="fleetctl", runner=None):
    """PAUSE. The flag comes first, because the hook enforces it even if Hermes steps fail."""
    runner = runner or default_runner
    steps = {}
    _write_flag(reason, by)
    steps["flag"] = "created"
    steps["hermes_global_pause"] = _hermes(runner, ["pause", "--reason", reason])
    paused_ids = {}
    for profile in _profiles():
        for job_id, active in _cron_jobs(runner, profile):
            if not active:
                continue
            result = _hermes(runner, ["cron", "pause", job_id], profile=profile)
            if result == "ok":
                paused_ids.setdefault(_profile_key(profile), []).append(job_id)
    steps["cron_jobs_paused"] = sum(len(v) for v in paused_ids.values())
    jsonio.write_json(paths.state_dir() / "status" / PAUSED_JOBS, paused_ids, mode=0o644,
                      owner="root" if os.geteuid() == 0 else None, group="fleet" if os.geteuid() == 0 else None)
    # An empty list makes the dispatcher claim nothing (fail closed).
    steps["kanban_dispatcher"] = _hermes(runner, ["config", "set", "kanban.dispatch_profiles", "[]"])
    return steps


def ready_to_run():
    """True when at least one router provider key exists, or the mock model is on."""
    cfg = config.load()
    if config.get(cfg, "features.mock_llm", False):
        return True, "mock model is on"
    for row in secrets.status_rows("router"):
        if row["set"]:
            return True, "a provider key exists"
    return False, "no router provider key exists yet"


def resume_fleet(force=False, runner=None):
    """Undo PAUSE. Refuses while the fleet waits for keys, unless force is true."""
    runner = runner or default_runner
    info = pause.paused()
    if info is None:
        return {"flag": "not paused"}
    if info.get("reason") == "waiting_for_keys" and not force:
        ok, why = ready_to_run()
        if not ok:
            raise NotReady("The fleet is waiting for keys: %s. Run: fleetctl setup" % why)
    steps = {}
    steps["hermes_global_resume"] = _hermes(runner, ["resume"])
    saved = jsonio.read_json(paths.state_dir() / "status" / PAUSED_JOBS, {}) or {}
    resumed = 0
    for key, ids in saved.items():
        profile = None if key == "default" else key
        for job_id in ids:
            if _hermes(runner, ["cron", "resume", job_id], profile=profile) == "ok":
                resumed += 1
    steps["cron_jobs_resumed"] = resumed
    steps["kanban_dispatcher"] = _hermes(runner, ["config", "unset", "kanban.dispatch_profiles"])
    failed = [k for k, v in steps.items() if isinstance(v, str) and v.startswith("failed")]
    if failed and not force:
        steps["flag"] = "kept (a step failed: %s). Fix it or use --force." % ", ".join(failed)
        return steps
    try:
        paths.paused_flag().unlink()
    except FileNotFoundError:
        pass
    steps["flag"] = "removed"
    return steps


def stop_fleet(runner=None):
    runner = runner or default_runner
    rc, out = runner(["systemctl", "stop", TARGET])
    return {"systemctl_stop": "ok" if rc == 0 else "failed: " + out[-200:]}


def start_fleet(runner=None):
    runner = runner or default_runner
    rc, out = runner(["systemctl", "start", TARGET])
    return {"systemctl_start": "ok" if rc == 0 else "failed: " + out[-200:]}


def revoke_checklist(cfg=None):
    """A text checklist of every key and grant. It never holds a secret value."""
    cfg = cfg or config.load()
    lines = ["REVOKE CHECKLIST", "", "Work from top to bottom. Tick each line when done.", ""]
    for entry in secrets.all_secrets():
        if entry.get("auto"):
            continue
        state = secrets.is_set(entry["name"])
        mark = {True: "set", False: "not set", None: "unknown"}[state]
        lines.append("[ ] %s (%s, %s)" % (entry["name"], entry["service"], mark))
        lines.append("    Revoke at: %s" % entry["revoke"])
    lines += ["", "Other grants:"]
    lines.append("[ ] Tailscale: remove this machine at https://login.tailscale.com/admin/machines")
    remote = config.get(cfg, "backup.github_remote", "")
    if remote:
        lines.append("[ ] GitHub backup remote (%s): remove access at https://github.com/settings/applications" % remote)
    lines.append("[ ] Cloudflare: delete the Workers and Pages projects of the fleet at https://dash.cloudflare.com")
    lines.append("[ ] Telegram and Discord bots: delete the bots, or reset their tokens")
    lines.append("[ ] Local tokens: run `fleetctl secret rotate` for each internal token, or stop the fleet")
    lines.append("")
    lines.append("This list holds no secret values. After the list is done, run: fleetctl stop")
    return "\n".join(lines)
