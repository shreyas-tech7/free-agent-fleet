"""Collect the facts for the daily brief. Only structured sources. Never agent prose."""
import json
import pathlib
import re
import time

import yaml

from ops.fleetlib import config, jsonio, paths, pause, secrets


def read_state_front_matter(path):
    """Parse the YAML block between the first two --- lines of a project STATE.md. Allow only simple values."""
    try:
        text = pathlib.Path(path).read_text()
    except OSError:
        return {}
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}
    return clean(data) if isinstance(data, dict) else {}


def clean(node, depth=0):
    """Keep numbers, booleans, and short plain strings. Drop everything else."""
    if depth > 3:
        return None
    if isinstance(node, bool) or isinstance(node, (int, float)):
        return node
    if isinstance(node, str):
        return node[:60] if re.fullmatch(r"[A-Za-z0-9 _\-./:%]+", node) else None
    if isinstance(node, dict):
        return {str(k)[:40]: v for k, v in ((k, clean(v, depth + 1)) for k, v in list(node.items())[:30]) if v is not None}
    if isinstance(node, list):
        return [v for v in (clean(x, depth + 1) for x in node[:20]) if v is not None]
    return None


def count_log(path, since, **where):
    n = 0
    try:
        for line in pathlib.Path(path).read_text().splitlines():
            rec = json.loads(line)
            if rec.get("ts", 0) >= since and all(rec.get(k) == v for k, v in where.items()):
                n += 1
    except (OSError, ValueError):
        pass
    return n


def blockers(cfg):
    """What the owner must still do. Each item has a stable id, a short label, and the command or link."""
    items = []
    router_keys = [r for r in secrets.status_rows("router") if r["set"]]
    # The briefing cannot read router.env. A provider that served requests today proves a key works.
    served = (jsonio.read_json(paths.budget_file(), {}) or {}).get("providers", {})
    router_keys = router_keys or [p for p, v in served.items() if not p.startswith("fleet-") and (v or {}).get("requests_today", 0) > 0]
    if not router_keys and not config.get(cfg, "features.mock_llm", False):
        items.append(("router_keys", "no model provider key", "Run `fleetctl setup` and add a free provider key."))
    elif not config.get(cfg, "router.approved_providers", []):
        items.append(("provider_terms", "no provider approved", "Run `fleetctl router approve groq` after you read its terms."))
    if not (config.get(cfg, "control.telegram.enabled", False) or config.get(cfg, "control.discord.enabled", False)):
        items.append(("control_bot", "no control bot", "Run `fleetctl secret wizard --group control` to add a bot."))
    if not secrets.is_set("CLOUDFLARE_API_TOKEN"):
        items.append(("cloudflare", "no Cloudflare token", "Run `fleetctl secret wizard --group deployer`. Guide: docs/HUMAN_STEPS.md."))
    if not any(config.get(cfg, "publisher.platforms.%s.enabled" % p, False) for p in ("bluesky", "mastodon", "discord", "telegram", "devto", "github")):
        items.append(("social", "no social channel on", "Run `fleetctl secret wizard --group publisher`."))
    items.append(("payments", "no payment account for the first customer", "Set up payments when a product is ready. See docs/HUMAN_STEPS.md."))
    return items


def collect(cfg, now=None):
    now = now or time.time()
    since = now - 86400
    flag = pause.paused()
    ops = paths.ops_state_dir()
    data = {
        "date": config.owner_now(cfg, now).strftime("%Y-%m-%d"),
        "paused": bool(flag),
        "pause_reason": (flag or {}).get("reason", "") if flag else "",
        "publisher_mode": config.get(cfg, "publisher.mode", "dry-run"),
        "deployer_mode": config.get(cfg, "deployer.mode", "dry-run"),
        "posts_sent": count_log(ops / "publisher" / "posts.log", since, status="sent"),
        "posts_dry_run": count_log(ops / "publisher" / "posts.log", since, status="dry_run"),
        "posts_blocked": count_log(ops / "publisher" / "posts.log", since, status="blocked"),
        "deploys": count_log(ops / "deployer" / "deploys.log", since, status="deployed"),
        "tickets_open": len(list((paths.state_dir() / "tickets").glob("*.json"))) if (paths.state_dir() / "tickets").exists() else 0,
        "budget": jsonio.read_json(paths.budget_file(), {}) or {},
        "merged_24h": count_log(ops / "merger" / "merges.log", since, status="merged"),
        "rolled_back_24h": count_log(ops / "merger" / "merges.log", since, status="rolled_back"),
        "projects": {},
    }
    base = paths.live_repo() / "projects"
    try:
        for d in sorted(base.iterdir()):
            if d.is_dir() and not d.name.startswith("_"):
                st = read_state_front_matter(d / "STATE.md")
                if st:
                    data["projects"][d.name] = st
    except OSError:
        pass
    data["blockers"] = [{"id": i, "label": l, "do": do} for i, l, do in blockers(cfg)]
    return data
