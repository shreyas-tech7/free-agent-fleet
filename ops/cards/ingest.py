"""The card ingester. It turns a queued request into a Kanban card. It runs as the user fleet.

The control bot (user fleetops) writes a request file into /var/lib/agent-fleet/cards-in after the API checks it.
This folder is in no agent sandbox, so only the control bot writes there. This tool checks the request again,
runs the injection screen again, and then calls `hermes kanban create`. A systemd path unit starts it.

Rules
- The agent must be one of the seven profiles. The board must be a valid slug. The text has size limits.
- The screen must say SAFE. If the screen is down, the request stays and the next run tries again.
  After 15 minutes without an answer the request moves to the bad folder.
- The title and body go to `hermes` as separate arguments, after `--` for the title. No shell runs.
- At most 10 requests in one run. See D-038.
"""
import json
import os
import pathlib
import re
import subprocess
import sys
import time

from ops.fleetlib import config, events, httpc, jsonio, log, paths

LOG = log.get("card-ingest")
AGENTS = ("orchestrator", "research", "build", "marketing", "feedback", "security", "improver")
BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
FILE_RE = re.compile(r"^c-[0-9]+-[0-9a-f]{6}\.json$")
MAX_PER_RUN = 10
GIVE_UP_SECONDS = 900


def inbox():
    return paths.state_dir() / "cards-in"


def scan(cfg, text):
    try:
        resp = httpc.post("http://127.0.0.1:%d/scan" % config.port(cfg, "guard"), json_body={"text": text}, timeout=60)
        data = resp.json()
        return data if resp.status == 200 and "label" in data else None
    except (httpc.TransportError, ValueError):
        return None


def active_board():
    """The slug of the active project, or empty. The default board is used then."""
    try:
        from ops.fleetctl import projects
        for item in projects.list_projects():
            if item.get("active"):
                return item["slug"]
    except Exception:                      # no project, or a broken STATE.md. The default board is fine.
        pass
    return ""


def kanban_create(board, agent, title, body, run=subprocess.run):
    argv = ["hermes", "kanban"] + (["--board", board] if board else []) + ["create", "--assignee", agent, "--body=" + body, "--json", "--", title]
    proc = run(argv, capture_output=True, text=True, timeout=120)  # nosemgrep: dangerous-subprocess-use-audit
    out = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r'"id"\s*:\s*"([^"]+)"', out) or re.search(r"\b(t_[a-z0-9]+)\b", out)
    if proc.returncode != 0 or not match:
        raise RuntimeError("kanban create failed")
    return match.group(1)


def valid(record):
    """The request, cleaned, or None."""
    if not isinstance(record, dict) or record.get("v") != 1:
        return None
    agent, title, body, board = record.get("agent"), record.get("title"), record.get("body", ""), record.get("board", "")
    if agent not in AGENTS or not isinstance(title, str) or not isinstance(body, str) or not isinstance(board, str):
        return None
    title = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", title).strip().lstrip("-").strip()
    body = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", body).strip()
    if not title or len(title) > 120 or len(body) > 2000 or (board and not BOARD_RE.match(board)):
        return None
    return {"agent": agent, "title": title, "body": body, "board": board}


def run_once(cfg=None, folder=None, scanner=None, create=kanban_create, now=time.time, emit=events.emit):
    cfg = cfg or config.load()
    folder = pathlib.Path(folder or inbox())
    scanner = scanner or (lambda text: scan(cfg, text))
    bad = folder / "bad"
    counts = {"created": 0, "bad": 0, "kept": 0}
    for path in sorted(p for p in folder.glob("c-*.json") if FILE_RE.match(p.name))[:MAX_PER_RUN]:
        def reject(reason):
            bad.mkdir(parents=True, exist_ok=True)
            try:
                path.rename(bad / path.name)
            except OSError:
                path.unlink(missing_ok=True)
            counts["bad"] += 1
            LOG.warn("card_rejected", file=path.name, reason=reason)
            emit("alert.card_rejected", "A queued card was rejected (%s)." % reason, severity="warn", source="cards")
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            reject("not valid JSON")
            continue
        card = valid(record)
        if card is None:
            reject("the request is not valid")
            continue
        answer = scanner(card["title"] + "\n" + card["body"])
        if answer is None:
            if now() - path.stat().st_mtime > GIVE_UP_SECONDS:
                reject("the injection screen did not answer")
            else:
                counts["kept"] += 1
            continue
        if answer.get("label") != "SAFE":
            reject("the injection screen blocked it")
            continue
        try:
            card_id = create(card["board"] or active_board(), card["agent"], card["title"], card["body"])
        except (RuntimeError, OSError, subprocess.SubprocessError):
            counts["kept"] += 1
            LOG.warn("card_create_failed", file=path.name)
            if now() - path.stat().st_mtime > GIVE_UP_SECONDS:
                reject("Kanban did not accept it")
            continue
        path.unlink(missing_ok=True)
        counts["created"] += 1
        LOG.info("card_created", card=card_id, agent=card["agent"])
        emit("board.card_created", "Card %s for %s: %s" % (card_id, card["agent"], card["title"]), source="cards", fields={"agent": card["agent"], "card": card_id})
    return counts


def main():
    counts = run_once()
    print(json.dumps(counts, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
