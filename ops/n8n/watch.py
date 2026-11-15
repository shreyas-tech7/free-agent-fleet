"""Release and advisory checks for n8n. Plain code. It uses no model. It never upgrades.

  releases    A weekly timer. It reads the n8n 2.x releases and the published security advisories from GitHub, with no key.
              It sends one fleet event: the newest stable release that is 3 days old, the releases that still wait,
              and any critical or high advisory whose fix is newer than the version that runs.
  migration   A monthly timer. It asks n8n for its Migration Report and sends the counts as a fleet event.
"""
import datetime
import re
import sys
import time

from ops.fleetlib import config, events, httpc, secrets
from ops.n8n import client as nclient
from ops.n8n import prepare
from ops.n8n.upgrade import GITHUB, MIN_AGE_DAYS, VERSION_RE, get_json


def vkey(version):
    m = VERSION_RE.match(version)
    return (2, int(m.group(1)), int(m.group(2))) if m else None


def stable_2x(releases, now):
    out = []
    for r in releases or []:
        tag = str(r.get("tag_name", ""))
        version = tag[len("n8n@"):] if tag.startswith("n8n@") else ""
        if r.get("prerelease") or r.get("draft") or not vkey(version) or not r.get("published_at"):
            continue
        published = datetime.datetime.fromisoformat(r["published_at"].replace("Z", "+00:00")).timestamp()
        out.append((vkey(version), version, (now - published) / 86400.0))
    return sorted(out, reverse=True)


def patched_2x(advisory):
    """The 2.x versions in the patched_versions text of an advisory."""
    found = []
    for v in advisory.get("vulnerabilities") or []:
        found += [vkey(x) for x in re.findall(r"2\.[0-9]+\.[0-9]+", v.get("patched_versions") or "") if vkey(x)]
    return found


def open_advisories(advisories, running):
    """Critical and high advisories that the running version still has. Newest first.

    If the running minor line (2.47 for 2.47.4) has a fix, the advisory is open only when that fix is newer than the running version.
    If the line has no fix, the advisory is open only when a later release has one."""
    me = vkey(running)
    rows = []
    for a in advisories or []:
        if a.get("severity") not in ("critical", "high") or not me:
            continue
        fixes = patched_2x(a)
        same = [p for p in fixes if p[1] == me[1]]
        if same:
            fix = min(same)
            if fix <= me:
                continue
        else:
            later = [p for p in fixes if p > me]
            if not later:
                continue
            fix = min(later)
        rows.append((a.get("published_at", ""), a["ghsa_id"], a["severity"], ".".join(map(str, fix))))
    return sorted(rows, reverse=True)


def check_releases(running, now=None, request=httpc.request, emit=events.emit):
    now = now if now is not None else time.time()
    releases = get_json("%s/releases?per_page=40" % GITHUB, request)
    advisories = get_json("%s/security-advisories?state=published&per_page=40" % GITHUB, request)
    if releases is None:
        emit("n8n.update_check_failed", "The n8n release check could not read GitHub.", severity="warn", source="n8n-watch")
        return {"status": "unreadable"}
    ranked = stable_2x(releases, now)
    me = vkey(running)
    newer = [r for r in ranked if me and r[0] > me]
    ready = [r for r in newer if r[2] >= MIN_AGE_DAYS]
    waiting = [r for r in newer if r[2] < MIN_AGE_DAYS]
    adv = open_advisories(advisories, running)
    critical = [a for a in adv if a[2] == "critical"]
    parts = ["n8n %s runs." % running]
    parts.append("Newest 2.x release that is %d days old: %s." % (MIN_AGE_DAYS, ready[0][1]) if ready else "No newer release is %d days old yet." % MIN_AGE_DAYS)
    if waiting:
        parts.append("%d newer release%s wait%s for age." % (len(waiting), "" if len(waiting) == 1 else "s", "s" if len(waiting) == 1 else ""))
    if adv:
        parts.append("Open advisories: %s." % ", ".join("%s (%s, fixed in %s)" % (a[1], a[2], a[3]) for a in adv[:4]))
    if ready:
        parts.append("To move: fleetctl n8n upgrade %s." % ready[0][1])
    emit("n8n.update_check", " ".join(parts), severity="warn" if critical else "info", source="n8n-watch",
         title="n8n update check", fields={"running": running, "ready": ready[0][1] if ready else "", "advisories": len(adv)})
    return {"status": "ok", "ready": ready[0][1] if ready else "", "waiting": [w[1] for w in waiting], "advisories": [a[1] for a in adv]}


def check_migration(c, email, password, emit=events.emit):
    c.login(email, password)
    c.call("POST", "/rest/breaking-changes/report/refresh", {}, timeout=120)
    status, data = c.call("GET", "/rest/breaking-changes/report", timeout=120)
    report = ((data or {}).get("data") or {}).get("report") if status == 200 else None
    if not report:
        emit("n8n.migration_report", "The n8n Migration Report could not be read.", severity="warn", source="n8n-watch")
        return {"status": "unreadable"}
    instance = report.get("instanceResults") or []
    workflows = report.get("workflowResults") or []
    levels = {}
    for r in instance:
        for issue in r.get("instanceIssues") or []:
            levels[issue.get("level", "info")] = levels.get(issue.get("level", "info"), 0) + 1
    text = "n8n %s, report against %s: %d instance rules, %d workflow findings (%s)." % (
        report.get("currentVersion", "?"), report.get("targetVersion", "?"), len(instance), len(workflows),
        ", ".join("%s %d" % kv for kv in sorted(levels.items())) or "no issues")
    if report.get("targetVersion") != "v3":
        text += " This version still reports against %s. Check again when the 3.x line is stable." % report.get("targetVersion", "?")
    emit("n8n.migration_report", text, source="n8n-watch", title="n8n Migration Report",
         fields={"target": str(report.get("targetVersion", "?")), "rules": len(instance), "workflows": len(workflows)})
    return {"status": "ok", "target": report.get("targetVersion"), "instance_rules": len(instance), "workflow_findings": len(workflows)}


def main(argv):
    from ops.fleetlib import config as cfglib
    cfg = cfglib.load()
    env = prepare.read_env_file(prepare.run_dir() / ".env")
    if argv and argv[0] == "migration":
        c = nclient.Client("http://127.0.0.1:%d" % cfglib.port(cfg, "n8n"))
        out = check_migration(c, env.get("N8N_OWNER_EMAIL", ""), secrets.value_of("N8N_OWNER_PASSWORD"))
    else:
        out = check_releases(env.get("N8N_VERSION", ""))
    print(out)
    return 0 if out.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
