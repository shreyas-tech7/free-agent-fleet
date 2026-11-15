import datetime
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from ops.fleetctl import projects
from ops.fleetlib import jsonio
from tests.unit.fctl_helpers import Runner, Script

NOW = datetime.datetime(2026, 11, 12, 12, 0, tzinfo=datetime.timezone.utc)
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t.invalid", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t.invalid",
           GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1")


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo)] + list(a), check=True, capture_output=True, text=True, env=ENV).stdout


@pytest.fixture
def live(tmp_path, fleet_env, monkeypatch):
    repo = tmp_path / "live"
    (repo / "projects" / "_graveyard").mkdir(parents=True)
    (repo / "projects" / "_graveyard" / ".gitkeep").write_text("")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, env=ENV)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    monkeypatch.setenv("FLEET_LIVE_REPO", str(repo))
    return repo


def make_project(live, slug, **extra):
    state = projects.default_state(slug, "2026-11-01")
    state.update(extra)
    folder = live / "projects" / slug
    folder.mkdir(parents=True)
    (folder / "STATE.md").write_text(projects.render_state(state, "# %s\n" % slug))
    git(live, "add", "-A")
    git(live, "commit", "-qm", "add " + slug)
    return folder


def test_the_first_project_starts_at_discover(live):
    r = Runner()
    report = projects.pivot("widget-shop", live=live, runner=r, now=NOW)
    front, _ = projects.read_state(live / "projects" / "widget-shop" / "STATE.md")
    assert report["started"] == "widget-shop" and report["archived"] == []
    assert front["stage"] == "discover" and front["active"] is True and front["missed_gates"] == 0
    assert set(front["gates"]) == set(projects.STAGES)
    assert r.ran("kanban", "boards", "create", "widget-shop", "--switch")
    assert git(live, "log", "--oneline").count("\n") == 2 and "pivot" in git(live, "log", "-1", "--format=%s")


def test_a_pivot_archives_the_active_project_and_its_board(live):
    make_project(live, "old-idea", stage="validate", missed_gates=2)
    r = Runner()
    report = projects.pivot("new-idea", reason="validate missed twice", lessons="People did not pay.", live=live, runner=r, now=NOW)
    assert report["archived"] == ["projects/_graveyard/old-idea-20261112"]
    grave = live / "projects" / "_graveyard" / "old-idea-20261112"
    assert not (live / "projects" / "old-idea").exists()
    lessons = (grave / "LESSONS.md").read_text()
    assert "validate missed twice" in lessons and "People did not pay." in lessons and "Last stage: validate" in lessons
    front, _ = projects.read_state(grave / "STATE.md")
    assert front["active"] is False and front["archived"] == "2026-11-12"
    assert r.ran("kanban", "boards", "rm", "old-idea") and r.ran("kanban", "boards", "create", "new-idea")
    assert [p["slug"] for p in projects.active_projects(live)] == ["new-idea"]
    assert git(live, "status", "--porcelain") == ""


def test_the_board_is_archived_not_deleted(live):
    make_project(live, "old-idea")
    r = Runner()
    projects.pivot("next-one", live=live, runner=r, now=NOW)
    assert not r.ran("--delete")                      # rule R8: archive before removing


def test_a_failed_board_step_is_a_warning(live):
    make_project(live, "old-idea")
    r = Runner(rules=[(["boards", "rm"], (1, "no such board"))])
    report = projects.pivot("next-one", live=live, runner=r, now=NOW)
    assert report["warnings"] and "old-idea" in report["warnings"][0]
    assert (live / "projects" / "_graveyard" / "old-idea-20261112" / "LESSONS.md").exists()


def test_archive_only_starts_nothing(live):
    make_project(live, "old-idea")
    report = projects.pivot(None, archive_only=True, live=live, runner=Runner(), now=NOW)
    assert report["started"] is None and projects.active_projects(live) == []
    with pytest.raises(projects.ProjectError):
        projects.pivot(None, archive_only=True, live=live, runner=Runner(), now=NOW)


@pytest.mark.parametrize("bad", ["A", "ab", "Bad-Name", "has space", "-lead", "trail-", "../x", "x" * 50, "1abc"])
def test_bad_slugs_are_refused(live, bad):
    with pytest.raises(projects.ProjectError):
        projects.pivot(bad, live=live, runner=Runner(), now=NOW)


def test_a_used_or_archived_name_is_refused(live):
    make_project(live, "old-idea")
    projects.pivot("second-idea", live=live, runner=Runner(), now=NOW)
    with pytest.raises(projects.ProjectError):
        projects.pivot("old-idea", live=live, runner=Runner(), now=NOW)
    with pytest.raises(projects.ProjectError):
        projects.pivot("second-idea", live=live, runner=Runner(), now=NOW)


def test_with_no_slug_the_next_free_name_is_used(live):
    assert projects.next_slug(live) == "project-001"
    projects.pivot(None, live=live, runner=Runner(), now=NOW)
    assert (live / "projects" / "project-001").is_dir()
    projects.pivot(None, live=live, runner=Runner(), now=NOW)
    assert (live / "projects" / "project-002").is_dir()
    assert (live / "projects" / "_graveyard" / "project-001-20261112").is_dir()


def test_lessons_are_plain_text_and_bounded():
    text = projects.clean_text("ok\x00\x1b[31m red\x07" + "x" * 9000)
    assert "\x00" not in text and "\x1b" not in text and len(text) <= projects.LESSONS_MAX


def test_the_state_front_matter_is_read_by_the_briefing(live):
    from ops.briefing import collect
    folder = make_project(live, "demo")
    data = collect.read_state_front_matter(folder / "STATE.md")
    assert data["stage"] == "discover" and data["metrics"]["payments"] == 0


# ------------------------------------------------------------------ gates
def gate(stage, metrics=None, **state):
    base = projects.default_state("x", "2026-11-01")
    base["metrics"].update(metrics or {})
    base.update(state)
    return projects.gate_status(stage, base, projects.load_stages(), now=NOW)


def test_discover_needs_three_candidates_ten_complaints_and_proof():
    assert not gate("discover", {"candidates": 2, "min_complaints": 12, "paid_proof": True})["met"]
    assert not gate("discover", {"candidates": 3, "min_complaints": 9, "paid_proof": True})["met"]
    assert not gate("discover", {"candidates": 3, "min_complaints": 10, "paid_proof": False})["met"]
    assert gate("discover", {"candidates": 3, "min_complaints": 10, "paid_proof": True})["met"]


def test_validate_gate_uses_either_target_and_the_14_day_window():
    assert gate("validate", {"waitlist_signups": 20})["met"]
    assert gate("validate", {"written_yes_replies": 5})["met"]
    g = gate("validate", {"waitlist_signups": 19, "written_yes_replies": 4}, stage_started="2026-10-28")
    assert not g["met"] and g["missed"]
    g = gate("validate", {"waitlist_signups": 5}, stage_started="2026-11-10")
    assert not g["met"] and not g["missed"]


def test_later_gates():
    assert gate("build_mvp", {"tests_pass": True, "stranger_value_minutes": 4})["met"]
    assert not gate("build_mvp", {"tests_pass": True, "stranger_value_minutes": 7})["met"]
    assert not gate("build_mvp", {"tests_pass": False, "stranger_value_minutes": 3})["met"]
    assert gate("launch", {"active_users": 10})["met"] and not gate("launch", {"active_users": 9})["met"]
    g = gate("first_customer", {"payments": 0})
    assert not g["met"] and "owner" in g["detail"]
    assert gate("first_customer", {"payments": 1})["met"]
    assert gate("iterate", {"ticket_fix_hours": 60, "tickets_open": 2})["met"]
    assert not gate("iterate", {"ticket_fix_hours": 80, "tickets_open": 2})["met"]


def test_project_status_command(live):
    make_project(live, "demo", stage="validate", missed_gates=2)
    s = Script()
    assert projects.cmd_status(SimpleNamespace(slug=None), s.con) == 0
    assert "Project demo (active)" in s.text and "should pivot" in s.text
    s2 = Script()
    assert projects.cmd_status(SimpleNamespace(slug="nope"), s2.con) == 1


def test_project_status_with_no_project(live):
    s = Script()
    assert projects.cmd_status(SimpleNamespace(slug=None), s.con) == 0
    assert "No project yet" in s.text


# ------------------------------------------------------------------ requests from the orchestrator
def write_request(fleet_env, data):
    d = fleet_env["state"] / "requests"
    d.mkdir(exist_ok=True)
    (d / "pivot.json").write_text(json.dumps(data) if not isinstance(data, str) else data)
    return d


def test_a_request_pivots_once_and_is_consumed(live, fleet_env):
    make_project(live, "old-idea")
    d = write_request(fleet_env, {"slug": "next-idea", "reason": "gate missed twice", "lessons": "Nobody paid."})
    s = Script()
    assert projects.process_request(s.con, Runner(), now=NOW) == 0
    assert (live / "projects" / "next-idea").is_dir() and not (d / "pivot.json").exists() and (d / "pivot.done.json").exists()
    assert "Nobody paid." in (live / "projects" / "_graveyard" / "old-idea-20261112" / "LESSONS.md").read_text()


def test_a_second_request_within_24_hours_is_refused(live, fleet_env):
    make_project(live, "old-idea")
    write_request(fleet_env, {"slug": "next-idea"})
    projects.process_request(Script().con, Runner(), now=NOW)
    write_request(fleet_env, {"slug": "third-idea"})
    s = Script()
    assert projects.process_request(s.con, Runner(), now=NOW + datetime.timedelta(hours=2)) == 1
    assert "24 hours" in s.text and not (live / "projects" / "third-idea").exists()
    write_request(fleet_env, {"slug": "third-idea"})
    assert projects.process_request(Script().con, Runner(), now=NOW + datetime.timedelta(hours=25)) == 0


@pytest.mark.parametrize("payload", ["not json", "[1, 2]", {"slug": "../../etc"}, {"slug": "Bad Name"}])
def test_bad_requests_are_refused(live, fleet_env, payload):
    make_project(live, "old-idea")
    write_request(fleet_env, payload)
    s = Script()
    assert projects.process_request(s.con, Runner(), now=NOW) == 1 and "refused" in s.text
    assert (live / "projects" / "old-idea").is_dir()


def test_an_oversized_or_linked_request_is_refused(live, fleet_env):
    make_project(live, "old-idea")
    d = write_request(fleet_env, {"slug": "next-idea", "lessons": "x" * 30000})
    assert projects.process_request(Script().con, Runner(), now=NOW) == 1
    (d / "secret.txt").write_text("hello")
    os.symlink(str(d / "secret.txt"), str(d / "pivot.json"))
    assert projects.process_request(Script().con, Runner(), now=NOW) == 1
    assert (live / "projects" / "old-idea").is_dir()


def test_no_request_is_fine(live, fleet_env):
    s = Script()
    assert projects.process_request(s.con, Runner(), now=NOW) == 0 and "No pivot request" in s.text


def test_pivot_command_prints_and_returns_codes(live):
    make_project(live, "old-idea")
    s = Script()
    a = SimpleNamespace(slug="next-idea", reason="r", lessons=None, archive_only=False, from_request=False)
    assert projects.cmd_pivot(a, s.con, Runner()) == 0 and "Started next-idea" in s.text
    s2 = Script()
    a2 = SimpleNamespace(slug="NOPE", reason="", lessons=None, archive_only=False, from_request=False)
    assert projects.cmd_pivot(a2, s2.con, Runner()) == 2


def test_an_unexpected_failure_still_consumes_the_request(live, fleet_env, monkeypatch):
    make_project(live, "old-idea")
    d = write_request(fleet_env, {"slug": "next-idea"})
    monkeypatch.setattr(projects, "pivot", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    s = Script()
    assert projects.process_request(s.con, Runner(), now=NOW) == 1
    assert "failed" in s.text and not (d / "pivot.json").exists() and (d / "pivot.failed.json").exists()
