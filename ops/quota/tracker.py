"""The quota tracker. Every 15 minutes it reads the router usage and writes budget.json.

budget.json says how much of each free daily limit is used. At 80 percent of any limit it sets
"slowdown": true. Agents read the file before each task. A Hermes hook also adds a reminder to
every model turn while the flag is on, so the orchestrator hears it even if it forgets to look.
Daily use is the change in the router's total request count since local midnight.
"""
import datetime
import json
import sys
import time

from ops.fleetlib import config, httpc, jsonio, log, paths, secrets

LOG = log.get("quota")


def percent_entries(quota_json):
    """Find {provider, used, limit} or a percent in the router quota answer. The shape can vary."""
    found = []

    def walk(node, provider=None):
        if isinstance(node, dict):
            provider = node.get("provider") or node.get("providerId") or provider
            used = node.get("used", node.get("usedCount"))
            limit = node.get("limit", node.get("total"))
            pct = node.get("percentUsed", node.get("usedPercent"))
            if pct is None and isinstance(node.get("remainingPercentage"), (int, float)):
                pct = 100 - node["remainingPercentage"]
            if isinstance(pct, (int, float)) and provider:
                found.append((str(provider), float(pct)))
            elif isinstance(used, (int, float)) and isinstance(limit, (int, float)) and limit > 0 and provider:
                found.append((str(provider), 100.0 * used / limit))
            for v in node.values():
                walk(v, provider)
        elif isinstance(node, list):
            for v in node:
                walk(v, provider)
    walk(quota_json)
    return found


def compute(cfg, history, quota, state, today):
    """Pure function. Returns (budget dict, new state)."""
    limits = config.get(cfg, "quota.daily_free_limits", {}) or {}
    threshold = float(config.get(cfg, "limits.slowdown_percent", 80))
    by = (history or {}).get("byProvider") or {}
    if state.get("date") != today:
        state = {"date": today, "baseline": {p: v.get("requests", 0) for p, v in by.items()}}
    providers = {}
    for name, v in by.items():
        used = max(0, v.get("requests", 0) - state["baseline"].get(name, 0))
        limit = limits.get(name)
        providers[name] = {"requests_today": used, "limit": limit,
                           "percent": round(100.0 * used / limit, 1) if isinstance(limit, (int, float)) and limit > 0 else None}
    for name, pct in percent_entries(quota or {}):
        entry = providers.setdefault(name, {"requests_today": None, "limit": None, "percent": None})
        entry["percent"] = max(round(pct, 1), entry["percent"] or 0)
    hot = sorted(n for n, e in providers.items() if e["percent"] is not None and e["percent"] >= threshold)
    budget = {"updated": int(time.time()), "date": today, "threshold_percent": threshold, "providers": providers,
              "hot": hot, "slowdown": bool(hot)}
    return budget, state


def fetch(cfg):
    """Read usage from the router. Returns (history, quota) or (None, None) when the router is not ready."""
    base = "http://127.0.0.1:%d" % config.port(cfg, "router")
    password = secrets.value_of("INITIAL_PASSWORD")
    if not password:
        return None, None
    try:
        login = httpc.post(base + "/api/auth/login", json_body={"password": password}, timeout=15)
        cookie = login.headers.get("set-cookie", "").split(";", 1)[0]
        if login.status != 200 or not cookie.startswith("auth_token="):
            return None, None
        out = []
        for path in ("/api/usage/history", "/api/usage/quota"):
            resp = httpc.get(base + path, headers={"Cookie": cookie}, timeout=30)
            out.append(resp.json() if resp.status == 200 else {})
        return out[0], out[1]
    except (httpc.TransportError, ValueError):
        return None, None


def counters(now, ops_dir, budget):
    """Facts for the improver's weekly summary: why the Publisher blocked posts, and how long the budget slowed the fleet."""
    week = now - 7 * 86400
    blocked = {}
    try:
        for line in (ops_dir / "publisher" / "posts.log").read_text().splitlines()[-5000:]:
            rec = json.loads(line)
            if rec.get("status") == "blocked" and rec.get("ts", 0) >= week:
                blocked[rec.get("reason", "?")] = blocked.get(rec.get("reason", "?"), 0) + 1
    except (OSError, ValueError):
        pass
    state = jsonio.read_json(ops_dir / "quota" / "slowdown_samples.json", []) or []
    state = [t for t in state if now - t < 7 * 86400]
    if budget.get("slowdown"):
        state.append(now)
    jsonio.write_json(ops_dir / "quota" / "slowdown_samples.json", state, mode=0o640)
    return {"publisher_blocked": dict(sorted(blocked.items())), "slowdown_hours": round(len(state) * 0.25, 2), "updated": int(now)}


def main(argv=None):
    cfg = config.load()
    history, quota = fetch(cfg)
    ops = paths.ops_state_dir() / "quota"
    state = jsonio.read_json(ops / "state.json", {}) or {}
    if history is None:
        budget = {"updated": int(time.time()), "providers": {}, "hot": [], "slowdown": False, "note": "router not ready"}
    else:
        budget, state = compute(cfg, history, quota, state, config.owner_now(cfg).date().isoformat())
        jsonio.write_json(ops / "state.json", state, mode=0o640)
    jsonio.write_json(paths.budget_file(), budget, mode=0o644)
    try:                                              # agents read this copy through their sandbox mount of the status folder
        jsonio.write_json(paths.state_dir() / "status" / "budget.json", budget, mode=0o644)
    except OSError:
        pass
    try:
        jsonio.write_json(paths.state_dir() / "status" / "counters.json", counters(time.time(), paths.ops_state_dir(), budget), mode=0o644)
    except OSError:
        pass
    LOG.info("budget", slowdown=budget["slowdown"], hot=budget["hot"])
    print(json.dumps({"slowdown": budget["slowdown"], "hot": budget["hot"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
