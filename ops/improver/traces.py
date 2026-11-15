"""Weekly trace summary for the improver. Plain code. It holds codes and counts, never raw text.

It reads the Kanban databases of the user fleet, the merger status file, and the counters file, and writes
projects/_improver/traces/<year>-W<week>.json in the working clone. The improver reads that file.
It uses only the standard library, so Hermes can run it as a script job.
"""
import datetime
import glob
import json
import os
import pathlib
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, os.environ.get("FLEET_REPO", "/opt/agent-fleet/repo"))
from ops.fleetlib import paths  # noqa: E402

WEEK = 7 * 86400
FAILED_OUTCOMES = ("crashed", "timed_out", "spawn_failed", "gave_up")


def board_dbs(home):
    home = pathlib.Path(home)
    found = [home / "kanban.db"] if (home / "kanban.db").exists() else []
    found += [pathlib.Path(p) for p in sorted(glob.glob(str(home / "kanban" / "boards" / "*" / "kanban.db")))]
    return found


def read_board(db_path, since):
    con = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        runs = [dict(r) for r in con.execute("SELECT task_id, profile, outcome, started_at, ended_at FROM task_runs WHERE started_at >= ?", (since,))]
        tasks = [dict(r) for r in con.execute("SELECT id, status, completed_at FROM tasks")]
        events = [dict(r) for r in con.execute("SELECT kind, payload FROM task_events WHERE created_at >= ? AND kind LIKE '%block%'", (since,))]
    finally:
        con.close()
    return tasks, runs, events


def summarize(home, since, now=None):
    now = now or time.time()
    tasks = {"done": 0, "blocked": 0, "failed": 0}
    by_agent, per_task_runs, block_reasons = {}, {}, {}
    for db in board_dbs(home):
        try:
            t, runs, events = read_board(db, since)
        except sqlite3.Error:
            continue
        tasks["done"] += sum(1 for x in t if x["status"] == "done" and (x["completed_at"] or 0) >= since)
        tasks["blocked"] += sum(1 for x in t if x["status"] == "blocked")
        for r in runs:
            agent = by_agent.setdefault(r["profile"] or "unknown", {"done": 0, "blocked": 0, "retries": 0, "minutes": []})
            if r["outcome"] == "completed":
                agent["done"] += 1
                if r["ended_at"]:
                    agent["minutes"].append((r["ended_at"] - r["started_at"]) / 60.0)
            elif r["outcome"] == "blocked":
                agent["blocked"] += 1
            if r["outcome"] in FAILED_OUTCOMES:
                tasks["failed"] += 1
            per_task_runs[(str(db), r["task_id"])] = per_task_runs.get((str(db), r["task_id"]), 0) + 1
            if per_task_runs[(str(db), r["task_id"])] > 1:
                agent["retries"] += 1
        for e in events:
            try:
                kind = str(json.loads(e["payload"] or "{}").get("kind", "unknown"))[:20]
            except (ValueError, AttributeError):
                kind = "unknown"
            block_reasons[kind] = block_reasons.get(kind, 0) + 1
    out_agents = {}
    for name, a in sorted(by_agent.items()):
        out_agents[name] = {"done": a["done"], "blocked": a["blocked"], "retries": a["retries"],
                            "median_minutes": round(statistics.median(a["minutes"]), 1) if a["minutes"] else None}
    return tasks, out_agents, dict(sorted(block_reasons.items()))


def merger_summary(status_dir):
    try:
        data = json.loads((pathlib.Path(status_dir) / "merger.json").read_text())
    except (OSError, ValueError):
        return None
    results, gate_fails = {}, {}
    for rec in (data.get("branches") or {}).values():
        results[rec.get("result", "?")] = results.get(rec.get("result", "?"), 0) + 1
        for gate, verdict in (rec.get("gates") or {}).items():
            if verdict == "fail":
                gate_fails[gate] = gate_fails.get(gate, 0) + 1
    return {"merged": results.get("merged", 0), "rejected": results.get("rejected", 0), "rolled_back": results.get("rolled_back", 0),
            "gate_fails": dict(sorted(gate_fails.items()))}


def week_name(now):
    d = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isocalendar()
    return "%d-W%02d" % (d[0], d[1])


def build(home, status_dir, now=None):
    now = now or time.time()
    tasks, by_agent, reasons = summarize(home, now - WEEK, now)
    out = {"week": week_name(now), "tasks": tasks, "by_agent": by_agent, "block_reasons": reasons}
    m = merger_summary(status_dir)
    if m is not None:
        out["merger"] = m
    try:
        counters = json.loads((pathlib.Path(status_dir) / "counters.json").read_text())
        if isinstance(counters.get("publisher_blocked"), dict):
            out["publisher"] = {"blocked": counters["publisher_blocked"]}
        if isinstance(counters.get("slowdown_hours"), (int, float)):
            out["budget"] = {"slowdown_hours": counters["slowdown_hours"]}
    except (OSError, ValueError):
        pass
    return out


def main(argv=None):
    now = time.time()
    data = build(paths.hermes_home(), paths.state_dir() / "status", now)
    folder = paths.work_clone() / "projects" / "_improver" / "traces"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("%s.json" % data["week"])
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print("trace summary written: projects/_improver/traces/%s.json (%d tasks done)" % (data["week"], data["tasks"]["done"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
