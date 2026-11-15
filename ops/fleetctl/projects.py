"""Projects, stage gates, and pivots.

A project is a folder projects/<slug>/ in the live repository. STATE.md holds its stage and numbers
in a YAML block at the top. Each project has its own Kanban board. Only one project is active at a time
(`limits.max_active_projects` in config).

A pivot archives the active project and its board, writes a lessons file, and starts a new project
at the Discover stage. Nothing is deleted (rule R8): the folder moves to projects/_graveyard/ and the
board moves to the Hermes board archive.
"""
import datetime
import json
import os
import pathlib
import re
import subprocess

import yaml

from ops.fleetctl import system
from ops.fleetlib import config, jsonio, paths

SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")
STAGES = ("discover", "score", "validate", "build_mvp", "launch", "first_customer", "iterate")
FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
LESSONS_MAX = 4000


class ProjectError(Exception):
    pass


# ------------------------------------------------------------------ STATE.md
def default_state(slug, today, stage="discover"):
    return {
        "slug": slug, "stage": stage, "active": True, "created": today, "stage_started": today, "missed_gates": 0,
        "gates": {s: "pending" for s in STAGES},
        "metrics": {"candidates": 0, "min_complaints": 0, "paid_proof": False, "waitlist_signups": 0, "written_yes_replies": 0,
                    "tests_pass": False, "stranger_value_minutes": 0, "active_users": 0, "payments": 0,
                    "tickets_open": 0, "ticket_fix_hours": 0},
    }


BODY = """
# {slug}

This file is the record of the project. The orchestrator keeps the numbers above up to date.
The lines above the first `---` are data. Keep each value short and plain.

## Stage log

- {today}: project started at the Discover stage.

## Decisions

Write each business decision here with its date and its reason.

## Gate changes

The orchestrator may tune the gate numbers in config/stages.yaml. Log each change here.
"""


def render_state(front, body):
    return "---\n" + yaml.safe_dump(front, sort_keys=False).rstrip() + "\n---\n" + body.lstrip("\n")


def read_state(path):
    """Return (front matter dict, body text). A missing or broken file gives ({}, '')."""
    try:
        text = pathlib.Path(path).read_text()
    except OSError:
        return {}, ""
    m = FRONT_RE.match(text)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    return (data if isinstance(data, dict) else {}), m.group(2)


def _deep_update(base, updates):
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
    return base


def update_state(path, updates):
    front, body = read_state(path)
    front = _deep_update(front, updates)
    jsonio.atomic_write(path, render_state(front, body), mode=0o644)
    return front


# ------------------------------------------------------------------ listing
def projects_dir(live):
    return pathlib.Path(live) / "projects"


def list_projects(live):
    base = projects_dir(live)
    out = []
    try:
        for d in sorted(base.iterdir()):
            if d.is_dir() and not d.name.startswith("_") and (d / "STATE.md").is_file():
                front, _ = read_state(d / "STATE.md")
                out.append({"slug": d.name, "stage": front.get("stage", "?"), "active": bool(front.get("active")), "state": front})
    except OSError:
        pass
    return out


def active_projects(live):
    return [p for p in list_projects(live) if p["active"]]


def next_slug(live):
    """A neutral working name for a project whose niche is not known yet."""
    seen = {p["slug"] for p in list_projects(live)}
    graveyard = projects_dir(live) / "_graveyard"
    try:
        seen |= {d.name for d in graveyard.iterdir()}
    except OSError:
        pass
    n = 1
    while "project-%03d" % n in seen or any(s.startswith("project-%03d-" % n) for s in seen):
        n += 1
    return "project-%03d" % n


# ------------------------------------------------------------------ gates
def gate_status(stage, state, stages_cfg, now=None):
    """Compare the numbers in STATE.md with the gate. Returns {met, missed, detail}."""
    metrics = state.get("metrics", {}) or {}
    spec = (stages_cfg.get("stages", {}) or {}).get(stage, {})
    now = now or datetime.datetime.now(datetime.timezone.utc)
    started = _parse_date(state.get("stage_started")) or now
    days_in = (now - started).days
    met, missed, detail = False, False, ""
    if stage == "discover":
        need, per = spec.get("candidates", 3), spec.get("min_separate_complaints", 10)
        met = metrics.get("candidates", 0) >= need and metrics.get("min_complaints", 0) >= per and \
            (metrics.get("paid_proof") is True or not spec.get("require_paid_workaround_proof", True))
        detail = "%s of %s candidates, fewest complaints %s of %s, paid proof %s" % (
            metrics.get("candidates", 0), need, metrics.get("min_complaints", 0), per, "yes" if metrics.get("paid_proof") else "no")
    elif stage == "score":
        met = state.get("gates", {}).get("score") == "met" or bool(metrics.get("scored"))
        detail = "highest total score wins"
    elif stage == "validate":
        window = spec.get("window_days", 14)
        signups, yes = metrics.get("waitlist_signups", 0), metrics.get("written_yes_replies", 0)
        met = signups >= spec.get("waitlist_signups", 20) or yes >= spec.get("written_yes_replies", 5)
        missed = (not met) and days_in >= window
        detail = "%s of %s signups, %s of %s yes replies, day %d of %d" % (
            signups, spec.get("waitlist_signups", 20), yes, spec.get("written_yes_replies", 5), days_in, window)
    elif stage == "build_mvp":
        met = metrics.get("tests_pass") is True and 0 < metrics.get("stranger_value_minutes", 0) <= spec.get("stranger_value_minutes", 5)
        detail = "tests pass %s, a stranger gets value in %s of %s minutes" % (
            "yes" if metrics.get("tests_pass") else "no", metrics.get("stranger_value_minutes", 0), spec.get("stranger_value_minutes", 5))
    elif stage == "launch":
        met = metrics.get("active_users", 0) >= spec.get("active_users", 10)
        detail = "%s of %s active users" % (metrics.get("active_users", 0), spec.get("active_users", 10))
    elif stage == "first_customer":
        met = metrics.get("payments", 0) >= spec.get("payments", 1)
        detail = "%s of %s payments. Needs the owner to set up payments." % (metrics.get("payments", 0), spec.get("payments", 1))
    elif stage == "iterate":
        limit = spec.get("ticket_fix_hours", 72)
        met = 0 < metrics.get("ticket_fix_hours", 0) <= limit or metrics.get("tickets_open", 0) == 0
        detail = "tickets are fixed in %s of %s hours" % (metrics.get("ticket_fix_hours", 0), limit)
    return {"met": bool(met), "missed": bool(missed), "detail": detail}


def _parse_date(text):
    try:
        return datetime.datetime.strptime(str(text), "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def load_stages(live=None):
    path = (pathlib.Path(live) / "config" / "stages.yaml") if live else paths.stages_file()
    try:
        return yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}


def cmd_status(args, console, runner=system.run):
    live = paths.live_repo() if paths.live_repo().is_dir() else paths.repo_root()
    stages_cfg = load_stages(live)
    items = list_projects(live)
    if args.slug:
        items = [p for p in items if p["slug"] == args.slug]
        if not items:
            console.say("No project named %s." % args.slug)
            return 1
    if not items:
        console.say("No project yet. The orchestrator starts one at the Discover stage when the fleet runs.")
        return 0
    limit = stages_cfg.get("missed_gate_limit", 2)
    for p in items:
        st = p["state"]
        console.say("Project %s (%s)" % (p["slug"], "active" if p["active"] else "archived"))
        console.say("  stage: %s since %s. Missed gates: %s of %s." % (st.get("stage"), st.get("stage_started", "?"), st.get("missed_gates", 0), limit))
        gate = gate_status(st.get("stage", ""), st, stages_cfg)
        console.say("  gate: %s. %s" % ("met" if gate["met"] else ("MISSED" if gate["missed"] else "not met yet"), gate["detail"]))
        if int(st.get("missed_gates", 0) or 0) >= limit:
            console.say("  The gate was missed %d times. This project should pivot: fleetctl pivot" % limit)
    return 0


# ------------------------------------------------------------------ pivot
def clean_text(text, limit=LESSONS_MAX):
    """Plain text only. No control characters. Bounded length. The text came from an agent."""
    text = "".join(ch for ch in str(text or "") if ch == "\n" or ch == "\t" or ch >= " ")
    return text[:limit].strip()


def _git(live, *args, check=True):
    from ops.merger import gitops
    return gitops.git(live, *args, check=check, identity=True)


def lessons_text(slug, front, reason, lessons, today):
    metrics = front.get("metrics", {}) or {}
    lines = ["# Lessons from %s" % slug, "", "Archived: %s" % today, "Last stage: %s" % front.get("stage", "?"),
             "Missed gates: %s" % front.get("missed_gates", 0), "", "## Reason for the pivot", "", clean_text(reason, 600) or "No reason given.", "",
             "## Final numbers", ""]
    lines += ["- %s: %s" % (k, v) for k, v in sorted(metrics.items())]
    lines += ["", "## What we learned", "", clean_text(lessons) or "No lessons were written. The orchestrator should add them.", ""]
    return "\n".join(lines)


def pivot(new_slug, reason="", lessons="", archive_only=False, live=None, runner=system.run, now=None, cfg=None):
    """Archive the active project and start a new one at Discover. Returns a report dict."""
    live = pathlib.Path(live or paths.live_repo())
    cfg = cfg or config.load()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    today = now.strftime("%Y-%m-%d")
    report = {"archived": [], "started": None, "board": {}, "commit": None, "warnings": []}
    if not (live / "projects").is_dir():
        raise ProjectError("the repository has no projects folder")
    if not archive_only:
        new_slug = new_slug or next_slug(live)
        if not SLUG_RE.match(new_slug):
            raise ProjectError("the project name must be 3 to 40 characters: lowercase letters, digits, and dashes. It starts with a letter.")
        taken = {p["slug"] for p in list_projects(live)}
        graveyard = projects_dir(live) / "_graveyard"
        taken |= {d.name for d in graveyard.iterdir()} if graveyard.is_dir() else set()
        if new_slug in taken or any(t.startswith(new_slug + "-") for t in taken):
            raise ProjectError("a project with that name already exists or was archived")
    maxp = int(config.get(cfg, "limits.max_active_projects", 1))
    active = active_projects(live)
    to_archive = active if (archive_only or len(active) >= maxp) else []
    if archive_only and not active:
        raise ProjectError("there is no active project to archive")
    for p in to_archive:
        old = p["slug"]
        dest = projects_dir(live) / "_graveyard" / ("%s-%s" % (old, now.strftime("%Y%m%d")))
        n = 1
        while dest.exists():
            n += 1
            dest = projects_dir(live) / "_graveyard" / ("%s-%s-%d" % (old, now.strftime("%Y%m%d"), n))
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git(live, "mv", "projects/%s" % old, str(dest.relative_to(live)))
        update_state(dest / "STATE.md", {"active": False, "archived": today})
        (dest / "LESSONS.md").write_text(lessons_text(old, p["state"], reason, lessons, today))
        report["archived"].append(str(dest.relative_to(live)))
        rc, out = runner(["hermes", "kanban", "boards", "rm", old], user="fleet", timeout=90)
        report["board"][old] = "archived" if rc == 0 else "not archived: %s" % out[-120:]
        if rc != 0:
            report["warnings"].append("the board of %s was not archived. Run: hermes kanban boards rm %s" % (old, old))
    if not archive_only:
        folder = projects_dir(live) / new_slug
        folder.mkdir(parents=True)
        (folder / "STATE.md").write_text(render_state(default_state(new_slug, today), BODY.format(slug=new_slug, today=today)))
        (folder / "app").mkdir()
        (folder / "app" / ".gitkeep").write_text("")
        rc, out = runner(["hermes", "kanban", "boards", "create", new_slug, "--name", new_slug,
                          "--description", "Business project %s" % new_slug, "--switch"], user="fleet", timeout=90)
        report["board"][new_slug] = "created" if rc == 0 else "not created: %s" % out[-120:]
        if rc != 0:
            report["warnings"].append("the board was not created. Run: hermes kanban boards create %s --switch" % new_slug)
        report["started"] = new_slug
    _git(live, "add", "-A", "projects")
    message = ("pivot: %s -> %s" % (",".join(p["slug"] for p in to_archive) or "none", new_slug or "none"))
    _git(live, "commit", "--quiet", "-m", message, check=False)
    try:
        report["commit"] = _git(live, "rev-parse", "--short", "HEAD").strip()
    except Exception:
        report["commit"] = None
    if os.geteuid() == 0:
        subprocess.run(["chown", "-R", "root:root", str(live / "projects")], check=False, timeout=60)
        subprocess.run(["chmod", "-R", "go-w", str(live / "projects")], check=False, timeout=60)
    return report


def cmd_pivot(args, console, runner=system.run):
    if args.from_request:
        return process_request(console, runner)
    try:
        report = pivot(args.slug, reason=args.reason or "", lessons=_read_lessons(args.lessons), archive_only=args.archive_only, runner=runner)
    except ProjectError as exc:
        console.say("Not done: %s" % exc)
        return 2
    for a in report["archived"]:
        console.say("Archived to %s" % a)
    if report["started"]:
        console.say("Started %s at the Discover stage." % report["started"])
    for k, v in report["board"].items():
        console.say("Board %s: %s" % (k, v))
    for w in report["warnings"]:
        console.say("Warning: %s" % w)
    console.say("Committed %s. The agents see it after they run: git fetch origin" % report["commit"])
    return 0


def _read_lessons(path):
    if not path:
        return ""
    try:
        return pathlib.Path(path).read_text()
    except OSError:
        raise ProjectError("cannot read the lessons file")


# ------------------------------------------------------------------ pivot requests from the orchestrator
REQUEST_DIR = "requests"
REQUEST_NAME = "pivot.json"
REQUEST_COOLDOWN_HOURS = 24


def request_path():
    return paths.state_dir() / REQUEST_DIR / REQUEST_NAME


def read_request(path):
    """Read a request that an agent wrote. The folder belongs to the agents, so trust nothing in it."""
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat_is_regular(st) or st.st_size > 20000:
            raise ProjectError("the request is not a small regular file")
        data = os.read(fd, 20001).decode("utf-8", "replace")
    finally:
        os.close(fd)
    try:
        req = json.loads(data)
    except ValueError:
        raise ProjectError("the request is not JSON")
    if not isinstance(req, dict):
        raise ProjectError("the request must be a JSON object")
    return req


def stat_is_regular(st):
    import stat
    return stat.S_ISREG(st.st_mode)


def process_request(console, runner=system.run, now=None):
    """Handle a pivot request from the orchestrator. It is rate limited and strictly checked."""
    path = request_path()
    if not os.path.lexists(str(path)):
        console.say("No pivot request.")
        return 0
    now = now or datetime.datetime.now(datetime.timezone.utc)
    marker = paths.state_dir() / "status" / "last_pivot.json"
    last = jsonio.read_json(marker, {}) or {}
    try:
        req = read_request(path)
        slug = req.get("slug") or None
        if slug is not None and not SLUG_RE.match(str(slug)):
            raise ProjectError("the project name in the request is not valid")
        if now.timestamp() - float(last.get("at", 0)) < REQUEST_COOLDOWN_HOURS * 3600:
            raise ProjectError("the last pivot was less than %d hours ago" % REQUEST_COOLDOWN_HOURS)
        report = pivot(slug, reason=clean_text(req.get("reason"), 600), lessons=clean_text(req.get("lessons")), runner=runner, now=now)
    except (ProjectError, OSError) as exc:
        console.say("Pivot request refused: %s" % exc)
        _consume(path, "refused")
        return 1
    except Exception as exc:                          # an unexpected failure must not leave the file behind, or the path unit would run again and again
        console.say("Pivot request failed: %s" % type(exc).__name__)
        _consume(path, "failed")
        return 1
    jsonio.write_json(marker, {"at": now.timestamp(), "started": report["started"]}, mode=0o644,
                      owner="root" if os.geteuid() == 0 else None, group="fleet" if os.geteuid() == 0 else None)
    _consume(path, "done")
    console.say("Pivot done: archived %s, started %s." % (", ".join(report["archived"]) or "nothing", report["started"]))
    return 0


def _consume(path, outcome):
    """Move the request away, so it is handled once. The result stays next to it for the orchestrator to read."""
    try:
        done = pathlib.Path(path).with_name("pivot.%s.json" % outcome)
        os.replace(str(path), str(done))
    except OSError:
        try:
            os.unlink(str(path))
        except OSError:
            pass
