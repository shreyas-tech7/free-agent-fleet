"""The board exporter. It runs as the user fleet, because the Kanban data belongs to fleet. See D-052.

Every 45 seconds it reads each Hermes Kanban board with the `hermes kanban` commands and writes one snapshot,
/var/lib/agent-fleet/board/board.json. The control bot reads the snapshot and mirrors the cards into Slack #board.
It reads cards only. It never writes to a board. It makes at most 6 `show` calls in one round, to read completion reports.
"""
import json
import subprocess
import sys
import time

from ops.fleetlib import jsonio, log, paths

LOG = log.get("board-export")
MAX_SHOW = 6
INTERVAL = 45
KEEP_STATUS_FOR_SUMMARY = ("done", "blocked", "review")


def snapshot_file():
    return paths.state_dir() / "board" / "board.json"


def hermes(argv, runner=subprocess.run, timeout=60):
    """Run one hermes command. Returns its output, or an empty string on any failure."""
    try:
        proc = runner(["hermes"] + list(argv), capture_output=True, text=True, timeout=timeout)  # nosemgrep: dangerous-subprocess-use-audit
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def _json(text, default):
    try:
        return json.loads(text) if text.strip() else default
    except ValueError:
        return default


def board_slugs(run):
    rows = _json(run(["kanban", "boards", "list", "--json"]), [])
    rows = rows.get("boards", []) if isinstance(rows, dict) else rows
    slugs = [str(r.get("slug") or r.get("name")) for r in rows if isinstance(r, dict) and not r.get("archived") and (r.get("slug") or r.get("name"))]
    return slugs or ["default"]


def card_of(task, now, previous=None):
    """The fields of one card that the mirror needs. The text is clipped, and the body is left out."""
    started, completed = task.get("started_at") or 0, task.get("completed_at") or 0
    seconds = max(0, (completed or now) - started) if started else 0
    card = {"id": str(task.get("id", ""))[:40], "title": str(task.get("title", ""))[:120], "assignee": str(task.get("assignee") or "")[:20],
            "status": str(task.get("status", ""))[:20], "created": task.get("created_at") or 0, "started": started, "completed": completed,
            "run_seconds": seconds, "error": str(task.get("last_failure_error") or "")[:200], "tokens": None, "summary": (previous or {}).get("summary", "")}
    return card


def summary_of(run, slug, card_id):
    data = _json(run(["kanban", "--board", slug, "show", card_id, "--json"]), {})
    if not isinstance(data, dict):
        return ""
    text = data.get("latest_summary") or ""
    if not text:
        for item in reversed(data.get("runs") or []):
            if isinstance(item, dict) and item.get("summary"):
                text = item["summary"]
                break
    return str(text)[:800]


def build(run, previous=None, now=None):
    """One snapshot. `previous` is the last snapshot. A card whose status did not change keeps its summary."""
    now = int(time.time() if now is None else now)
    before = {}
    for slug, board in ((previous or {}).get("boards") or {}).items():
        for card in board.get("cards") or []:
            before[(slug, card.get("id"))] = card
    boards, budget = {}, MAX_SHOW
    for slug in board_slugs(run):
        tasks = _json(run(["kanban", "--board", slug, "list", "--json"]), [])
        cards = []
        for task in tasks if isinstance(tasks, list) else []:
            if not isinstance(task, dict):
                continue
            old = before.get((slug, str(task.get("id", ""))[:40]))
            card = card_of(task, now, old)
            changed = old is None or old.get("status") != card["status"]
            if card["status"] in KEEP_STATUS_FOR_SUMMARY and (changed or not card["summary"]) and budget > 0:
                card["summary"] = summary_of(run, slug, task.get("id", ""))
                budget -= 1
            cards.append(card)
        boards[slug] = {"cards": cards}
    return {"updated": now, "boards": boards}


def write(snapshot, path=None):
    path = path or snapshot_file()
    jsonio.write_json(path, snapshot, mode=0o644)


def run_once(run=hermes, path=None, now=None):
    previous = jsonio.read_json(path or snapshot_file(), {}) or {}
    snap = build(run, previous, now)
    write(snap, path)
    return snap


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    loop = "--loop" in argv
    while True:
        try:
            snap = run_once()
            LOG.info("snapshot", boards=len(snap["boards"]), cards=sum(len(b["cards"]) for b in snap["boards"].values()))
        except Exception as exc:                          # one bad round must not stop the loop
            LOG.error("snapshot_failed", error=type(exc).__name__)
        if not loop:
            return 0
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
