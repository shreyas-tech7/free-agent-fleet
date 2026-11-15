"""The board exporter and the #board mirror. Canned Kanban JSON and a fake Slack. No network. See D-052."""
import json

import pytest

from tests.clock import NOON_CHICAGO
from ops.board import export
from ops.control import board, quiet, slackapi
from ops.fleetlib import config, jsonio
from tests.fake_slack import FakeSlack

NOW = 1_800_000_000


def task(i, status="ready", assignee="build", title=None, started=0, completed=0, error=None):
    return {"id": "t%d" % i, "title": title or "Card %d" % i, "body": "SECRET BODY", "assignee": assignee, "status": status, "created_at": NOW - 1000,
            "started_at": started, "completed_at": completed, "last_failure_error": error}


class Kanban:
    """A fake `hermes` runner. Answers boards list, list, and show from canned data."""

    def __init__(self, boards=None, summaries=None, fail=False):
        self.boards = boards if boards is not None else {"p1": [task(1)]}
        self.summaries = summaries or {}
        self.calls = []
        self.fail = fail

    def __call__(self, argv):
        self.calls.append(list(argv))
        if self.fail:
            return ""
        if argv[:3] == ["kanban", "boards", "list"]:
            return json.dumps([{"slug": s} for s in self.boards] + [{"slug": "old", "archived": True}])
        if "list" in argv:
            return json.dumps(self.boards[argv[argv.index("--board") + 1]])
        if "show" in argv:
            cid = argv[argv.index("show") + 1]
            return json.dumps({"task": {}, "latest_summary": self.summaries.get(cid, ""), "runs": [], "events": [], "comments": []})
        return ""


# ------------------------------------------------------------------------------------------------ exporter
def test_the_snapshot_holds_clipped_cards_and_never_the_body(tmp_path):
    k = Kanban(boards={"p1": [task(1, status="running", started=NOW - 125, title="x" * 300)]})
    snap = export.build(k, now=NOW)
    card = snap["boards"]["p1"]["cards"][0]
    assert len(card["title"]) == 120 and card["run_seconds"] == 125 and card["status"] == "running" and card["tokens"] is None
    assert "SECRET" not in json.dumps(snap) and "old" not in snap["boards"] and snap["updated"] == NOW


def test_a_card_that_is_not_started_has_no_run_time_and_a_finished_one_counts_to_its_end():
    snap = export.build(Kanban(boards={"p1": [task(1), task(2, status="done", started=NOW - 600, completed=NOW - 100)]}), now=NOW)
    assert [c["run_seconds"] for c in snap["boards"]["p1"]["cards"]] == [0, 500]


def test_the_summary_is_read_for_a_finished_card_once_and_kept_while_the_status_stays(tmp_path):
    k = Kanban(boards={"p1": [task(1, status="done", completed=NOW)]}, summaries={"t1": "Done: x\nNext: y\nBlocked: none\nNeeds you: none"})
    first = export.build(k, now=NOW)
    assert first["boards"]["p1"]["cards"][0]["summary"].startswith("Done: x") and sum(1 for c in k.calls if "show" in c) == 1
    second = export.build(k, previous=first, now=NOW + 60)
    assert second["boards"]["p1"]["cards"][0]["summary"].startswith("Done: x") and sum(1 for c in k.calls if "show" in c) == 1


def test_at_most_six_show_calls_in_one_round():
    k = Kanban(boards={"p1": [task(i, status="done", completed=NOW) for i in range(10)]}, summaries={"t%d" % i: "s%d" % i for i in range(10)})
    snap = export.build(k, now=NOW)
    assert sum(1 for c in k.calls if "show" in c) == export.MAX_SHOW
    assert sum(1 for c in snap["boards"]["p1"]["cards"] if c["summary"]) == export.MAX_SHOW
    again = export.build(k, previous=snap, now=NOW + 60)
    assert sum(1 for c in again["boards"]["p1"]["cards"] if c["summary"]) == 10 - 0 or sum(1 for c in again["boards"]["p1"]["cards"] if c["summary"]) > export.MAX_SHOW


def test_a_failing_hermes_gives_an_empty_default_board_and_bad_json_is_ignored():
    assert export.build(Kanban(fail=True), now=NOW)["boards"] == {"default": {"cards": []}}
    assert export.board_slugs(lambda argv: "not json") == ["default"]
    assert export.build(lambda argv: "[1, 2]" if "list" in argv and "boards" not in argv else '[{"slug": "p"}]', now=NOW)["boards"] == {"p": {"cards": []}}


def test_run_once_writes_a_world_readable_snapshot_and_main_survives_a_crash(fleet_env, monkeypatch, tmp_path):
    path = tmp_path / "board" / "board.json"
    snap = export.run_once(Kanban(), path=path, now=NOW)
    assert json.loads(path.read_text()) == snap and oct(path.stat().st_mode & 0o777) == "0o644"
    monkeypatch.setattr(export, "run_once", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert export.main([]) == 0


def test_hermes_runs_the_command_and_gives_an_empty_string_on_any_failure():
    class Done:
        def __init__(self, rc, out):
            self.returncode, self.stdout = rc, out
    assert export.hermes(["kanban", "list"], runner=lambda cmd, **kw: Done(0, "[]")) == "[]"
    assert export.hermes(["x"], runner=lambda cmd, **kw: Done(1, "oops")) == ""

    def missing(cmd, **kw):
        raise OSError("no hermes")
    assert export.hermes(["x"], runner=missing) == ""


# ------------------------------------------------------------------------------------------------ mirror
def snapshot(path, cards, slug="p1"):
    jsonio.write_json(path, {"updated": NOW, "boards": {slug: {"cards": cards}}}, mode=0o644)


def card(i, status="ready", **kw):
    base = {"id": "t%d" % i, "title": "Card %d" % i, "assignee": "build", "status": status, "run_seconds": 0, "tokens": None, "summary": "", "error": ""}
    base.update(kw)
    return base


def mirror(tmp_path, fake=None, now=NOON_CHICAGO, channels=None):
    cfg = config.defaults()
    cfg["slack"]["channels"] = channels if channels is not None else {"board": "chan-board"}
    fake = fake or FakeSlack()
    api = slackapi.SlackApi("xoxb-" + "p" * 24, request=fake, clock=lambda: 0.0, sleep=lambda s: None)
    return board.BoardMirror(cfg, api, snapshot_path=tmp_path / "board.json", state_path=tmp_path / "state.json", clock=lambda: now), fake


def test_the_first_run_records_the_cards_that_exist_and_posts_none_of_them(tmp_path):
    m, fake = mirror(tmp_path)
    snapshot(tmp_path / "board.json", [card(1, "done"), card(2)])
    assert m.poll()["skipped"] == "first run: 2 cards recorded" and fake.calls == []
    assert m.poll() == {"posted": 0, "updated": 0, "threads": 0, "summary": 0, "skipped": ""} and fake.calls == []


def test_a_new_card_gets_one_message_as_its_agent_and_a_change_edits_it_in_place(tmp_path):
    m, fake = mirror(tmp_path)
    snapshot(tmp_path / "board.json", [card(1)])
    m.poll()
    snapshot(tmp_path / "board.json", [card(1), card(2, assignee="research")])
    out = m.poll()
    assert out["posted"] == 1 and fake.params("chat.postMessage")[0]["channel"] == "chan-board" and fake.params("chat.postMessage")[0]["username"] == "Research"
    ts = json.loads((tmp_path / "state.json").read_text())["cards"]["p1/t2"]["ts"]
    snapshot(tmp_path / "board.json", [card(1), card(2, "running", assignee="research", run_seconds=190)])
    out = m.poll()
    update = fake.params("chat.update")[0]
    assert out["updated"] == 1 and update["ts"] == ts and "Run time: 3 min" in json.dumps(update["blocks"]) and len(fake.params("chat.postMessage")) == 1
    assert m.poll()["updated"] == 0 and len(fake.params("chat.update")) == 1


def started(tmp_path, fake=None, first="running"):
    """A mirror that has a Slack message for card 1. Card 99 is only there so that the first run has something to record."""
    m, fake = mirror(tmp_path, fake=fake)
    snapshot(tmp_path / "board.json", [card(99)])
    m.poll()
    snapshot(tmp_path / "board.json", [card(99), card(1, first)])
    assert m.poll()["posted"] == 1
    return m, fake


def threads(fake):
    return [p for p in fake.params("chat.postMessage") if p.get("thread_ts")]


def test_a_finished_card_gets_its_closing_report_in_the_thread_once(tmp_path):
    m, fake = started(tmp_path)
    report = "Done: The page passes 14 tests \u2014 all green.\nNext: Ask for release approval.\nBlocked: none\nNeeds you: none"
    snapshot(tmp_path / "board.json", [card(99), card(1, "done", summary=report)])
    out = m.poll()
    reply = threads(fake)[0]
    assert out["updated"] == 1 and out["threads"] == 1 and reply["thread_ts"] and "*Done:* The page passes 14 tests, all green." in reply["text"] and "\u2014" not in reply["text"]
    assert m.poll()["threads"] == 0 and len(threads(fake)) == 1


def test_a_closing_note_that_is_not_a_report_is_posted_as_clean_text(tmp_path):
    m, fake = started(tmp_path)
    snapshot(tmp_path / "board.json", [card(99), card(1, "done", summary="Finished \u2014 all good; shipped.")])
    m.poll()
    assert threads(fake)[0]["text"] == "Finished, all good. Shipped."


def test_a_blocked_card_posts_its_error_once(tmp_path):
    m, fake = started(tmp_path)
    snapshot(tmp_path / "board.json", [card(99), card(1, "blocked", error="the build failed")])
    m.poll()
    m.poll()
    assert [p["text"] for p in threads(fake)] == ["Blocked: the build failed"]


def test_a_burst_of_changes_becomes_one_summary_line(tmp_path):
    m, fake = mirror(tmp_path)
    snapshot(tmp_path / "board.json", [card(1)])
    m.poll()
    snapshot(tmp_path / "board.json", [card(i) for i in range(1, 30)])
    out = m.poll()
    assert out["summary"] == 28 and out["posted"] == 0 and len(fake.calls) == 1 and "28 cards changed at once" in fake.params("chat.postMessage")[0]["text"]
    assert m.poll()["summary"] == 0 and len(fake.calls) == 1


def test_one_round_makes_at_most_fifteen_calls_and_the_rest_wait_for_the_next(tmp_path):
    m, fake = mirror(tmp_path)
    snapshot(tmp_path / "board.json", [card(1)])
    m.poll()
    snapshot(tmp_path / "board.json", [card(i) for i in range(1, 16)])         # 14 new cards: under the burst limit
    assert m.poll()["posted"] == 14
    snapshot(tmp_path / "board.json", [card(i, "running") for i in range(1, 16)])
    out = m.poll()
    assert out["updated"] == 14 and out["posted"] == 1                         # card 1 was only recorded on the first run, so it gets its first message now
    assert len(fake.calls) == 14 + 15


def test_the_mirror_waits_in_quiet_hours_and_without_a_channel_or_a_snapshot(tmp_path):
    snapshot(tmp_path / "board.json", [card(1)])
    night, fake = mirror(tmp_path, now=NOON_CHICAGO + 10 * 3600)
    assert night.poll()["skipped"] == "quiet hours" and fake.calls == [] and not (tmp_path / "state.json").exists()
    assert mirror(tmp_path, channels={})[0].poll()["skipped"] == "no board channel yet"
    (tmp_path / "board.json").unlink()
    assert mirror(tmp_path)[0].poll()["skipped"] == "no snapshot"


def test_a_slack_error_does_not_stop_the_round_and_a_rate_limit_ends_it(tmp_path):
    m, fake = mirror(tmp_path, fake=FakeSlack())
    snapshot(tmp_path / "board.json", [card(1)])
    m.poll()
    snapshot(tmp_path / "board.json", [card(1), card(2), card(3)])
    fake.fail["chat.postMessage"] = "channel_not_found"
    assert m.poll()["posted"] == 0
    fake.fail["chat.postMessage"] = "429"
    assert m.poll()["posted"] == 0 and len(fake.calls) == 2 + 1          # the first failure tried 2 cards. The 429 stopped after one.
    fake.fail.clear()
    assert m.poll()["posted"] == 2


def test_the_state_file_is_private_and_the_loop_survives_a_crash(tmp_path):
    import threading
    m, fake = mirror(tmp_path)
    snapshot(tmp_path / "board.json", [card(1)])
    m.poll()
    assert oct((tmp_path / "state.json").stat().st_mode & 0o777) == "0o600"
    stop = threading.Event()
    stop.wait = lambda interval=None: stop.set() or True
    m.poll = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    m.loop(stop, interval=0)
