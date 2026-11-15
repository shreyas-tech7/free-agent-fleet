"""The #board mirror. Each Kanban card is one Slack message that the bot edits in place. See D-052.

The board exporter writes a snapshot. This module reads it and keeps one message for each card in #board.
A finished card gets a thread reply with its closing report (Done, Next, Blocked, Needs you).
It posts at most once a second for each channel, and it never makes more than 15 calls in one round.
A burst of changes becomes one summary line. On the first run it records the cards that exist and posts none of them.
Inside quiet hours it waits.
"""
import hashlib
import json
import pathlib
import time

from ops.control import quiet, slackapi, slackviews
from ops.fleetlib import config, housestyle, jsonio, log, paths, reports

LOG = log.get("board-mirror")
MAX_CALLS = 15


def state_file():
    return paths.ops_state_dir() / "control" / "board-state.json"


def snapshot_file():
    return paths.state_dir() / "board" / "board.json"


def _fingerprint(card):
    keys = ("title", "assignee", "status", "run_seconds", "tokens")
    return hashlib.sha256(json.dumps([card.get(k) for k in keys], sort_keys=True).encode()).hexdigest()[:16]


def _run_time(card):
    seconds = int(card.get("run_seconds") or 0)
    if seconds <= 0:
        return "n/a"
    return "%d min" % max(1, seconds // 60) if seconds >= 60 else "%d s" % seconds


def _view(card):
    shown = dict(card, run_time=_run_time(card), tokens=card.get("tokens") if card.get("tokens") is not None else "n/a")
    return slackviews.board_message(shown)


def _report_text(card):
    summary = card.get("summary") or ""
    report = reports.parse(summary)
    text = reports.render(report) if report else housestyle.fix(" ".join(summary.split()))[:600]
    return text


class BoardMirror:
    def __init__(self, cfg, api, snapshot_path=None, state_path=None, clock=time.time):
        self.cfg, self.api, self.clock = cfg, api, clock
        self.snapshot_path = pathlib.Path(snapshot_path or snapshot_file())
        self.state_path = pathlib.Path(state_path or state_file())

    def channel(self):
        return config.get(self.cfg, "slack.channels.board", "")

    def poll(self):
        """One round. Returns what it did, as counts. It never raises for a Slack error."""
        out = {"posted": 0, "updated": 0, "threads": 0, "summary": 0, "skipped": ""}
        channel = self.channel()
        if not channel:
            out["skipped"] = "no board channel yet"
            return out
        if quiet.is_quiet(self.cfg, self.clock()):
            out["skipped"] = "quiet hours"
            return out
        snap = jsonio.read_json(self.snapshot_path, {}) or {}
        cards = {"%s/%s" % (slug, c.get("id")): c for slug, b in (snap.get("boards") or {}).items() for c in (b.get("cards") or []) if c.get("id")}
        if not cards:
            out["skipped"] = "no snapshot"
            return out
        state = jsonio.read_json(self.state_path, {}) or {}
        known = state.get("cards") or {}
        if not state:                                                  # first run: record what exists. Post none of it.
            state = {"cards": {k: {"hash": _fingerprint(c), "ts": "", "reported": True} for k, c in cards.items()}, "since": int(self.clock())}
            jsonio.write_json(self.state_path, state, mode=0o600)
            out["skipped"] = "first run: %d cards recorded" % len(cards)
            return out
        work = [(k, c) for k, c in cards.items() if k not in known or known[k].get("hash") != _fingerprint(c) or (c.get("status") == "done" and not known[k].get("reported") and c.get("summary"))]
        if len(work) > MAX_CALLS:                                      # a burst. One line instead of many messages.
            try:
                self.api.post(channel, "%d cards changed at once. Use `/fleet board` for the list." % len(work))
            except slackapi.SlackApiError as exc:
                LOG.warn("board_summary_failed", error=exc.error)
                return out
            for k, c in work:
                known[k] = {"hash": _fingerprint(c), "ts": known.get(k, {}).get("ts", ""), "reported": c.get("status") == "done"}
            out["summary"] = len(work)
            work = []
        calls = 0
        for key, card in work:
            if calls >= MAX_CALLS:
                break
            entry = known.get(key) or {"ts": "", "hash": "", "reported": False}
            text, blocks = _view(card)
            try:
                if not entry["ts"]:
                    entry["ts"] = self.api.post(channel, text, blocks=blocks, **slackviews.identity(card.get("assignee", "")))["ts"]
                    out["posted"] += 1
                elif entry["hash"] != _fingerprint(card):
                    self.api.update(channel, entry["ts"], text, blocks=blocks)
                    out["updated"] += 1
                calls += 1
                entry["hash"] = _fingerprint(card)
                if card.get("status") == "done" and card.get("summary") and not entry.get("reported"):
                    self.api.post(channel, _report_text(card), thread_ts=entry["ts"], **slackviews.identity(card.get("assignee", "")))
                    entry["reported"] = True
                    out["threads"] += 1
                    calls += 1
                elif card.get("status") == "blocked" and card.get("error") and entry.get("error") != card["error"]:
                    self.api.post(channel, "Blocked: %s" % housestyle.fix(card["error"])[:300], thread_ts=entry["ts"])
                    entry["error"] = card["error"]
                    out["threads"] += 1
                    calls += 1
            except slackapi.SlackApiError as exc:
                LOG.warn("board_post_failed", error=exc.error)
                if exc.error == "ratelimited":
                    break
                continue
            known[key] = entry
        state["cards"] = known
        jsonio.write_json(self.state_path, state, mode=0o600)
        return out

    def loop(self, stop, interval=10):
        while not stop.is_set():
            try:
                self.poll()
            except Exception as exc:
                LOG.error("board_poll_failed", error=type(exc).__name__)
            stop.wait(interval)
