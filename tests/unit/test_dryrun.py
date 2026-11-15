import json
import pathlib
import shutil
import subprocess

import pytest

from ops.dryrun import report, run, schemas
from ops.fleetctl import projects
from ops.fleetlib import schema

FIXTURE = pathlib.Path(run.__file__).resolve().parent / "fixtures" / "widgets-app"


def test_every_stage_of_the_list_has_a_method():
    for name in run.STAGES:
        assert callable(getattr(run.Run, "stage_" + name)), name


def test_the_project_name_is_the_first_free_one(fleet_env, tmp_path, monkeypatch):
    live = tmp_path / "live"
    (live / "projects" / "_graveyard").mkdir(parents=True)
    monkeypatch.setenv("FLEET_LIVE_REPO", str(live))
    r = run.Run()
    assert r.choose_slug(resume=False) == "dry-run-widgets"
    (live / "projects" / "_graveyard" / "dry-run-widgets-20261112").mkdir()
    assert r.choose_slug(resume=False) == "dry-run-widgets-2"
    (live / "projects" / "_graveyard" / "dry-run-widgets-2-20261113").mkdir()
    assert r.choose_slug(resume=False) == "dry-run-widgets-3"


def test_a_resumed_run_keeps_the_active_project(fleet_env, tmp_path, monkeypatch):
    live = tmp_path / "live"
    folder = live / "projects" / "dry-run-widgets-2"
    folder.mkdir(parents=True)
    (live / "projects" / "_graveyard").mkdir()
    (live / "projects" / "_graveyard" / "dry-run-widgets-20261112").mkdir()
    (folder / "STATE.md").write_text(projects.render_state(projects.default_state("dry-run-widgets-2", "2026-11-13"), "# x\n"))
    monkeypatch.setenv("FLEET_LIVE_REPO", str(live))
    r = run.Run()
    assert r.choose_slug(resume=True) == "dry-run-widgets-2"
    assert r.choose_slug(resume=False) == "dry-run-widgets-3"


def test_the_stage_range_options(fleet_env):
    r = run.Run(only_from="build", until="launch")
    assert (r.first, r.last) == (run.STAGES.index("build"), run.STAGES.index("launch"))


def test_the_mock_answers_match_the_schemas():
    from tests import mock_llm
    S = mock_llm.SCENARIOS
    assert not schema.validate(S["pain_report"], schemas.PAIN_REPORT)
    assert not schema.validate(S["landing"], schemas.LANDING)
    assert not schema.validate(S["post_validate"], schemas.POST) and not schema.validate(S["post_launch"], schemas.POST)
    assert not schema.validate(S["fix_note"], schemas.FIX_NOTE) and not schema.validate(S["brief"], schemas.BRIEF)


def test_the_fix_patch_matches_the_fixture_text():
    js = (FIXTURE / "worker.js").read_text()
    assert 'return (options.compact ? rows.slice(1) : rows).join("\\n") + "\\n";' in js
    src = pathlib.Path(run.__file__).read_text()
    assert 'return (options.compact ? rows.slice(1) : rows).join("\\\\n") + "\\\\n";' in src


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed here")
def test_the_sample_app_passes_its_own_tests(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "app")
    out = subprocess.run(["node", "--test"], cwd=tmp_path / "app", capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "pass 7" in out.stdout.replace("ℹ ", "")


def test_the_sample_app_obeys_the_deployer_layout():
    spec = json.loads((FIXTURE / "deploy.json").read_text())
    assert set(spec) <= {"compatibility_date", "d1"}
    assert (FIXTURE / "worker.js").stat().st_size < 1_000_000


def test_the_report_renders_a_table_and_the_brief():
    result = {"ok": True, "project": "dry-run-widgets-2", "mock_requests": 9, "seconds": 600,
              "steps": [{"step": "a check", "ok": True, "detail": "x | y", "seconds": 1.5}]}
    text = report.render(result, "Achieved: done.\nNext: wait.")
    assert "dry-run-widgets-2" in text and "| 1 | a check | PASS | 1.5 | x / y |" in text and "Achieved: done." in text
    assert "failed" in report.render(dict(result, ok=False), "")


def test_the_archive_gets_the_result_and_never_the_salt(fleet_env):
    r = run.Run()
    r.dir.mkdir(parents=True)
    (r.dir / "salt").write_text("secret-salt")
    (r.dir / "brief.md").write_text("Achieved: x")
    r.copy_run_folder()
    kept = r.archive_folder() / "run"
    assert (kept / "brief.md").is_file() and not (kept / "salt").exists()
    assert not (kept / "result.json").exists()
    assert r.finish(True) == 0
    assert json.loads((kept / "result.json").read_text())["ok"] is True
    assert not (kept / "salt").exists()


def test_a_run_that_never_reached_the_archive_stage_makes_no_archive_folder(fleet_env):
    r = run.Run()
    assert r.finish(False) == 1
    assert not r.archive_folder().exists()


def test_the_gate_text_is_one_short_line():
    ok = {"result": "merged", "gates": {"G2": "pass", "G1": "pass"}}
    assert run.Run.gate_text(ok) == "result merged. Gates G1 G2: all pass"
    assert "fail" in run.Run.gate_text({"result": "rejected", "gates": {"G1": "fail"}})
    assert "no record" in run.Run.gate_text(None)


def test_the_report_leaves_a_none_detail_empty():
    text = report.render({"ok": True, "mock_requests": 1, "seconds": 5, "steps": [{"step": "x", "ok": True, "detail": "None", "seconds": 1}]}, "b")
    assert "| 1 | x | PASS | 1 |  |" in text


def test_the_scratch_worktrees_of_the_dry_run_are_removed_and_others_are_kept(fleet_env, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()

    def git(*args, cwd=work):
        done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", *args], cwd=str(cwd), capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
    git("init", "-q", "-b", "main")
    (work / "a").write_text("a")
    git("add", "-A")
    git("commit", "-q", "-m", "A")
    git("worktree", "add", "-q", str(work / ".worktrees" / "mvp-20261113-010101"), "-b", "agent/build/mvp-20261113-010101", "main")
    git("worktree", "add", "-q", str(work / ".worktrees" / "manual-topic"), "-b", "agent/build/manual-topic", "main")
    monkeypatch.setenv("FLEET_WORK_CLONE", str(work))
    r = run.Run()

    def local(argv, timeout=300):
        done = subprocess.run(argv, capture_output=True, text=True)
        return done.returncode, done.stdout + done.stderr
    r.fleet = local
    assert r.cleanup_worktrees() == 1
    assert not (work / ".worktrees" / "mvp-20261113-010101").exists()
    assert (work / ".worktrees" / "manual-topic").is_dir()
    branches = subprocess.run(["git", "branch", "--list", "agent/*"], cwd=str(work), capture_output=True, text=True).stdout
    assert "agent/build/mvp-20261113-010101" in branches            # the branch stays as a record


def _fake_systemctl(states):
    """states: unit -> list of answers for is-active, used one by one. The last answer repeats."""
    calls = []

    def sh(argv, user=None, timeout=300):
        calls.append(list(argv))
        if argv[:2] == ["systemctl", "is-active"]:
            answers = states[argv[2]]
            return 0, (answers.pop(0) if len(answers) > 1 else answers[0]) + "\n"
        return 0, ""
    return sh, calls


def test_the_driver_holds_the_merger_timer_and_gives_it_back(fleet_env):
    r = run.Run()
    r.sh, calls = _fake_systemctl({"agent-fleet-merger.timer": ["active"], "agent-fleet-merger.service": ["activating", "activating", "inactive"]})
    waits = []
    assert r.hold_merger_timer(sleep=waits.append) is True
    assert ["systemctl", "stop", "agent-fleet-merger.timer"] in calls
    assert waits == [10, 10]                                   # it waited for the timer run that had started
    r.release_merger_timer()
    assert calls[-1] == ["systemctl", "start", "agent-fleet-merger.timer"]


def test_a_timer_that_was_off_stays_off(fleet_env):
    r = run.Run()
    r.sh, calls = _fake_systemctl({"agent-fleet-merger.timer": ["inactive"], "agent-fleet-merger.service": ["inactive"]})
    assert r.hold_merger_timer(sleep=lambda s: None) is False
    r.release_merger_timer()
    assert not any(c[:2] in (["systemctl", "stop"], ["systemctl", "start"]) for c in calls)


def test_the_timer_comes_back_even_when_a_stage_fails(fleet_env, monkeypatch):
    r = run.Run()
    r.sh, calls = _fake_systemctl({"agent-fleet-merger.timer": ["active"], "agent-fleet-merger.service": ["inactive"]})
    monkeypatch.setattr(run, "MockLLM", lambda *a, **k: __import__("contextlib").nullcontext(None))
    monkeypatch.setattr(r, "choose_slug", lambda resume: "x")

    def boom():
        raise run.StepFailed("boom")
    monkeypatch.setattr(run.Run, "stage_start", lambda self: boom())
    assert r.run() == 1
    assert calls[-1] == ["systemctl", "start", "agent-fleet-merger.timer"]


def test_the_report_keeps_only_the_first_line_of_a_detail():
    assert report._short("widgets-2             (empty)\n\nCurrent board: x") == "widgets-2 (empty)"
    assert report._short(None) == "" and report._short("None") == ""
    assert report._short("a | b") == "a / b"
    assert len(report._short("x" * 500)) == 110
