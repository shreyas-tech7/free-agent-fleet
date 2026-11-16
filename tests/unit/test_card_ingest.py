import json
import os
import time

import pytest

from ops.cards import ingest
from ops.fleetlib import config

SAFE = {"label": "SAFE", "score": 0.01}


def write(folder, name, record, age=0):
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    p.write_text(json.dumps(record) if not isinstance(record, str) else record)
    if age:
        old = time.time() - age
        os.utime(p, (old, old))
    return p


GOOD = {"v": 1, "id": "c-1-aaaaaa", "agent": "research", "title": "Read the feed", "body": "Read three items.", "board": "project-001", "ts": 1, "source": "n8n"}


class Kanban:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, board, agent, title, body):
        self.calls.append((board, agent, title, body))
        if self.fail:
            raise RuntimeError("nope")
        return "t_abc123"


def run(folder, scanner=lambda text: SAFE, create=None, now=time.time):
    emitted = []
    out = ingest.run_once(config.defaults(), folder, scanner, create or Kanban(), now, emit=lambda *a, **k: emitted.append((a, k)))
    return out, emitted


def test_a_good_request_becomes_a_card_and_the_file_goes(tmp_path):
    write(tmp_path, "c-1-aaaaaa.json", GOOD)
    k = Kanban()
    out, emitted = run(tmp_path, create=k)
    assert out == {"created": 1, "bad": 0, "kept": 0}
    assert k.calls == [("project-001", "research", "Read the feed", "Read three items.")]
    assert not list(tmp_path.glob("c-*.json"))
    assert emitted[0][0][0] == "board.card_created" and "t_abc123" in emitted[0][0][1]


def test_the_active_project_is_the_default_board(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "active_board", lambda: "project-009")
    write(tmp_path, "c-1-aaaaaa.json", dict(GOOD, board=""))
    k = Kanban()
    run(tmp_path, create=k)
    assert k.calls[0][0] == "project-009"


@pytest.mark.parametrize("change", [{"agent": "ceo"}, {"title": ""}, {"title": "t" * 121}, {"body": "b" * 2001}, {"board": "Bad Board"}, {"v": 2}, {"agent": 5}])
def test_an_invalid_request_goes_to_the_bad_folder_without_a_card(tmp_path, change):
    write(tmp_path, "c-1-aaaaaa.json", dict(GOOD, **change))
    k = Kanban()
    out, emitted = run(tmp_path, create=k)
    assert out["bad"] == 1 and k.calls == [] and (tmp_path / "bad" / "c-1-aaaaaa.json").exists()
    assert emitted[0][0][0] == "alert.card_rejected"


def test_garbage_and_odd_file_names_are_not_cards(tmp_path):
    write(tmp_path, "c-1-aaaaaa.json", "{not json")
    write(tmp_path, "notes.json", GOOD)                      # a name that is not c-<digits>-<hex>.json is left alone
    write(tmp_path, "c-2-../x.json".replace("/", "_"), GOOD)
    k = Kanban()
    out, _ = run(tmp_path, create=k)
    assert out["bad"] == 1 and k.calls == [] and (tmp_path / "notes.json").exists()


def test_the_screen_is_asked_again_and_a_block_is_final(tmp_path):
    write(tmp_path, "c-1-aaaaaa.json", GOOD)
    asked = []
    k = Kanban()
    out, _ = run(tmp_path, scanner=lambda text: asked.append(text) or {"label": "INJECTION", "score": 0.99}, create=k)
    assert out["bad"] == 1 and k.calls == [] and "Read the feed" in asked[0] and "Read three items." in asked[0]


def test_when_the_screen_is_down_the_request_waits_then_gives_up(tmp_path):
    write(tmp_path, "c-1-aaaaaa.json", GOOD)
    k = Kanban()
    out, _ = run(tmp_path, scanner=lambda t: None, create=k)
    assert out == {"created": 0, "bad": 0, "kept": 1} and k.calls == []
    old = write(tmp_path, "c-2-bbbbbb.json", GOOD, age=1000)
    out, _ = run(tmp_path, scanner=lambda t: None, create=k)
    assert out["bad"] == 1 and (tmp_path / "bad" / "c-2-bbbbbb.json").exists() and (tmp_path / "c-1-aaaaaa.json").exists()


def test_a_kanban_failure_keeps_the_request_for_the_next_run(tmp_path):
    write(tmp_path, "c-1-aaaaaa.json", GOOD)
    out, _ = run(tmp_path, create=Kanban(fail=True))
    assert out == {"created": 0, "bad": 0, "kept": 1} and (tmp_path / "c-1-aaaaaa.json").exists()


def test_a_title_that_starts_with_a_dash_cannot_become_an_option(tmp_path):
    write(tmp_path, "c-1-aaaaaa.json", dict(GOOD, title="--assignee=build now"))
    k = Kanban()
    run(tmp_path, create=k)
    assert k.calls[0][2] == "assignee=build now"


def test_at_most_ten_requests_in_one_run(tmp_path):
    for i in range(14):
        write(tmp_path, "c-%d-aaaaa%d.json" % (i + 1, i % 10), GOOD)
    k = Kanban()
    out, _ = run(tmp_path, create=k)
    assert out["created"] == 10 and len(list(tmp_path.glob("c-*.json"))) == 4


def test_kanban_create_builds_a_safe_argument_list():
    seen = []

    class Done:
        returncode, stdout, stderr = 0, '{"id": "t_zzz"}', ""

    def fake(argv, **kw):
        seen.append(argv)
        return Done
    assert ingest.kanban_create("p1", "build", "A title", "A body", run=fake) == "t_zzz"
    assert seen[0] == ["hermes", "kanban", "--board", "p1", "create", "--assignee", "build", "--body=A body", "--json", "--", "A title"]
    seen.clear()
    ingest.kanban_create("", "build", "T", "--assignee=x", run=fake)
    assert seen[0][2] == "create" and "--body=--assignee=x" in seen[0] and seen[0][-2:] == ["--", "T"]


def test_kanban_create_fails_without_an_id():
    class Bad:
        returncode, stdout, stderr = 0, "no id here", ""
    with pytest.raises(RuntimeError):
        ingest.kanban_create("p1", "build", "T", "B", run=lambda argv, **kw: Bad)


def test_the_ingester_runs_when_the_live_config_is_unreadable(monkeypatch):
    """The user fleet cannot read fleet.yaml. Every queued card waited for a day because of this. See D-070."""
    def refuse(*a, **k):
        raise config.ConfigError("cannot read /etc/agent-fleet/fleet.yaml. Run this command with sudo.")
    monkeypatch.setattr(config, "load", refuse)
    assert ingest.load_config()["ports"]["guard"] == config.defaults()["ports"]["guard"]


def test_a_failed_run_still_exits_zero_so_the_path_unit_keeps_its_pause(monkeypatch, capsys):
    monkeypatch.setattr(ingest, "run_once", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    assert ingest.main() == 0 and "OSError" in capsys.readouterr().out
