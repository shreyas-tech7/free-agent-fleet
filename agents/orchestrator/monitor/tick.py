#!/usr/bin/env python3
"""Stable summary of the facts that matter to the orchestrator.

Hermes runs this script before each tick. When the output is the same as last time, the agent does not run.
That saves model calls. The output holds no time stamps. It holds numbers and short codes only.
"""
import json
import pathlib
import re

LIVE = pathlib.Path("/opt/agent-fleet/repo")
STATE = pathlib.Path("/var/lib/agent-fleet")
OWN_PREFIX = "agent/orchestrator/"


def front_matter(path):
    try:
        text = path.read_text()
    except OSError:
        return []
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    return m.group(1).splitlines() if m else []


def merger_counts(merger, own_prefix=OWN_PREFIX):
    """Count the merger results that are news to the orchestrator.

    A merged branch of the orchestrator itself is not news. The orchestrator opens a state branch at the end of each
    tick. If that merge changed this output, every tick would wake the agent, and the monitor would save nothing.
    A rejected or rolled-back branch of the orchestrator still counts, because the orchestrator must fix it.
    """
    results = {}
    for name, rec in (merger.get("branches") or {}).items():
        result = rec.get("result", "?") if isinstance(rec, dict) else "?"
        if result == "merged" and str(name).startswith(own_prefix):
            continue
        results[result] = results.get(result, 0) + 1
    return results


def main():
    lines = []
    projects = []
    try:
        for d in sorted((LIVE / "projects").iterdir()):
            if d.is_dir() and not d.name.startswith("_") and (d / "STATE.md").is_file():
                fm = front_matter(d / "STATE.md")
                if any(re.match(r"active:\s*true", l) for l in fm):
                    projects.append((d.name, fm))
    except OSError:
        pass
    if not projects:
        lines.append("project: none")
    for name, fm in projects:
        lines.append("project: " + name)
        lines += ["  " + l for l in fm if re.match(r"\s*(stage|missed_gates|[a-z_]+:\s*[0-9]+)", l)]
    counts = {}
    try:
        for p in sorted((STATE / "tickets").glob("t-*.json")):
            data = json.loads(p.read_text())
            key = "%s/%s" % (data.get("status", "open"), data.get("severity", "?"))
            counts[key] = counts.get(key, 0) + 1
    except (OSError, ValueError):
        pass
    lines.append("tickets: " + (", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "none"))
    try:
        merger = json.loads((STATE / "status" / "merger.json").read_text())
        lines.append("merger: " + (", ".join("%s=%d" % kv for kv in sorted(merger_counts(merger).items())) or "none"))
    except (OSError, ValueError):
        lines.append("merger: none")
    try:
        slow = bool(json.loads((STATE / "budget.json").read_text()).get("slowdown"))
    except (OSError, ValueError):
        slow = False
    lines.append("budget_slowdown: %s" % ("yes" if slow else "no"))
    lines.append("paused: %s" % ("yes" if (STATE / "PAUSED").exists() else "no"))
    try:
        done = sorted(p.name for p in (STATE / "requests").glob("pivot.*.json"))
    except OSError:
        done = []
    lines.append("pivot_files: " + (",".join(done) or "none"))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
