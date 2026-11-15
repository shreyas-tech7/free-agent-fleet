"""The everyday commands: status, start, stop, pause, resume, brief, logs, backup, revoke-checklist."""
import json
import os
import pathlib
import sys

from core.killswitch import killswitch
from ops.fleetctl import projects, system
from ops.fleetlib import config, jsonio, paths, pause, secrets


# ------------------------------------------------------------------ status
def gather_status(cfg, runner=system.run):
    info = pause.paused()
    data = {
        "fleet": "PAUSED" if info else "RUNNING",
        "pause": info or {},
        "publisher_mode": config.get(cfg, "publisher.mode"),
        "deployer_mode": config.get(cfg, "deployer.mode"),
        "services": {name: system.unit_state(unit, runner) for name, (unit, _p, _h) in system.SERVICES.items()},
        "timers": {name: system.unit_state(unit, runner) for name, unit in system.TIMERS.items()},
        "keys": {},
        "budget": jsonio.read_json(paths.budget_file(), {}) or {},
        "projects": [],
    }
    for group in secrets.registry()["groups"]:
        if group == "internal":
            continue
        rows = secrets.status_rows(group)
        data["keys"][group] = "%d of %d" % (sum(1 for r in rows if r["set"]), len(rows))
    live = paths.live_repo() if paths.live_repo().is_dir() else paths.repo_root()
    data["projects"] = [{"slug": p["slug"], "stage": p["stage"], "missed_gates": p["state"].get("missed_gates", 0)}
                        for p in projects.active_projects(live)]
    briefs = paths.state_dir() / "briefs"
    try:
        data["last_brief"] = max(p.name for p in briefs.glob("*.md") if p.name != "latest.md")
    except ValueError:
        data["last_brief"] = None
    try:
        data["outbox"] = len(list((paths.state_dir() / "outbox").glob("*.json")))
    except OSError:
        data["outbox"] = None
    return data


def cmd_status(args, console, runner=system.run):
    cfg = config.load()
    data = gather_status(cfg, runner)
    if args.json:
        console.say(json.dumps(data, indent=2, sort_keys=True))
        return 0
    if data["pause"]:
        p = data["pause"]
        console.say("Fleet: PAUSED (%s) since %s" % (p.get("reason", "?"), p.get("since", "?")))
    else:
        console.say("Fleet: RUNNING")
    console.say("Publisher: %s. Deployer: %s." % (data["publisher_mode"], data["deployer_mode"]))
    console.say("Services: " + ", ".join("%s %s" % (k, v) for k, v in data["services"].items()))
    console.say("Timers:   " + ", ".join("%s %s" % (k, v) for k, v in data["timers"].items()))
    console.say("Keys set: " + ", ".join("%s %s" % (k, v) for k, v in data["keys"].items()))
    b = data["budget"]
    console.say("Budget:   %s" % (("slowing down (%s)" % ", ".join(b.get("hot", []))) if b.get("slowdown") else "no free limit is near"))
    if data["projects"]:
        for p in data["projects"]:
            console.say("Project:  %s, stage %s, missed gates %s" % (p["slug"], p["stage"], p["missed_gates"]))
    else:
        console.say("Project:  none yet")
    console.say("Last brief: %s. Dry-run posts in the outbox: %s." % (data["last_brief"] or "none", data["outbox"]))
    return 0


# ------------------------------------------------------------------ kill switch
def _show(console, steps):
    for k, v in steps.items():
        console.say("  %s: %s" % (k, v))


def _who(by):
    """The control bot reaches this command through sudo as the user fleetops. Say so in the flag."""
    if by == "fleetctl" and os.environ.get("SUDO_USER") == "fleetops":
        return "control bot"
    return by


def cmd_pause(args, console, runner=system.run):
    steps = killswitch.pause_fleet(reason=args.reason, by=_who(args.by), runner=_user_runner(runner))
    console.say("Fleet paused (reason: %s)." % args.reason)
    _show(console, steps)
    return 0


def cmd_resume(args, console, runner=system.run):
    try:
        steps = killswitch.resume_fleet(force=args.force, runner=_user_runner(runner))
    except killswitch.NotReady as exc:
        console.say(str(exc))
        return 1
    if steps.get("flag") == "not paused":
        console.say("The fleet is not paused.")
        return 0
    _show(console, steps)
    console.say("Fleet resumed." if steps.get("flag") == "removed" else "The fleet is still paused.")
    return 0 if steps.get("flag") == "removed" else 1


def _user_runner(runner):
    """The real command uses the kill switch's own runner. A test passes a fake one."""
    return None if runner is system.run else runner


def cmd_stop(args, console, runner=system.run):
    steps = killswitch.stop_fleet(runner=_user_runner(runner))
    _show(console, steps)
    console.say("Stopped agent-fleet.target. The control bot keeps running. Start again with: fleetctl start")
    return 0 if steps["systemctl_stop"] == "ok" else 1


def cmd_start(args, console, runner=system.run):
    steps = killswitch.start_fleet(runner=_user_runner(runner))
    _show(console, steps)
    info = pause.paused()
    if info:
        console.say("Note: the fleet is still paused (%s). Services run. Agents wait. Run: fleetctl resume" % info.get("reason", "?"))
    return 0 if steps["systemctl_start"] == "ok" else 1


def cmd_revoke(args, console):
    console.say(killswitch.revoke_checklist())
    return 0


# ------------------------------------------------------------------ brief
def cmd_brief(args, console, runner=system.run):
    if getattr(args, "note", None):
        from ops.briefing import note
        try:
            stored = note.write(args.note)
        except note.NoteError as exc:
            console.say("Not saved: %s" % exc)
            return 2
        console.say("Saved. The next brief shows it first: %s" % stored)
        if not args.now:
            return 0
    if args.now:
        console.say("Making a new brief. This can take a minute.")
        rc, out = runner(["systemctl", "start", "agent-fleet-briefing.service"], timeout=600)
        if rc != 0:
            console.say("The brief service failed: %s" % out[-200:])
            return 1
    latest = paths.state_dir() / "briefs" / "latest.md"
    try:
        console.say(latest.read_text())
    except OSError:
        console.say("No brief yet. The next one is at %s (%s)." % (config.get(config.load(), "briefing.time"), config.get(config.load(), "owner.timezone")))
    return 0


# ------------------------------------------------------------------ logs and backup
def cmd_logs(args, console, runner=system.run):
    unit = system.LOG_UNITS.get(args.service)
    if not unit:
        console.say("Unknown service. Choose one of: %s" % ", ".join(sorted(system.LOG_UNITS)))
        return 2
    argv = ["journalctl", "-u", unit, "-n", str(args.lines), "--no-pager", "-o", "cat"]
    if args.follow:
        argv.append("-f")
        sys.stdout.flush()
        os.execvp("journalctl", argv)
    rc, out = runner(argv, timeout=60)
    console.say(out)
    return 0 if rc == 0 else 1


def cmd_backup(args, console, runner=system.run):
    for unit in ("agent-fleet-export.service", "agent-fleet-backup.service"):
        rc, out = runner(["systemctl", "start", unit], timeout=1800)
        if rc != 0:
            console.say("%s failed: %s" % (unit, out[-200:]))
            return 1
    folder = paths.ops_state_dir() / "backups"
    try:
        newest = max(folder.glob("agent-fleet-*.tar.gz"), key=lambda p: p.stat().st_mtime)
        console.say("Backup done: %s (%.1f MB). It holds no secrets." % (newest.name, newest.stat().st_size / 1e6))
    except (OSError, ValueError):
        console.say("The backup ran but no archive was found in %s." % folder)
        return 1
    return 0
