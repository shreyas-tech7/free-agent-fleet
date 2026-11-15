import json
import pathlib
import subprocess
from types import SimpleNamespace

import pytest

from core.killswitch import killswitch
from ops.fleetctl import basic, system, testcmd, update
from ops.fleetlib import config, secrets
from tests.unit.fctl_helpers import Runner, Script


def ns(**kw):
    return SimpleNamespace(**kw)


def test_status_text_and_json(fleet_env):
    r = Runner([(["is-active"], (0, "active"))])
    s = Script()
    assert basic.cmd_status(ns(json=False), s.con, r) == 0
    assert "Fleet: RUNNING" in s.text and "router active" in s.text and "Keys set:" in s.text
    s2 = Script()
    basic.cmd_status(ns(json=True), s2.con, r)
    data = json.loads(s2.text)
    assert data["fleet"] == "RUNNING" and data["services"]["router"] == "active" and "internal" not in data["keys"]


def test_status_shows_the_pause_and_the_slowdown(fleet_env):
    (fleet_env["state"] / "PAUSED").write_text(json.dumps({"reason": "waiting_for_keys", "since": "2026-11-12T10:00:00Z"}))
    (fleet_env["state"] / "budget.json").write_text(json.dumps({"slowdown": True, "hot": ["groq"]}))
    s = Script()
    basic.cmd_status(ns(json=False), s.con, Runner([(["is-active"], (3, "inactive"))]))
    assert "PAUSED (waiting_for_keys)" in s.text and "slowing down (groq)" in s.text and "router inactive" in s.text


def test_pause_creates_the_flag_and_resume_removes_it(fleet_env):
    r = Runner()
    s = Script()
    assert basic.cmd_pause(ns(reason="owner", by="test"), s.con, r) == 0
    flag = json.loads((fleet_env["state"] / "PAUSED").read_text())
    assert flag["reason"] == "owner" and flag["by"] == "test"
    assert r.ran("hermes", "pause") and r.ran("hermes", "config", "set", "kanban.dispatch_profiles", "[]")
    s2 = Script()
    assert basic.cmd_resume(ns(force=False), s2.con, r) == 0
    assert not (fleet_env["state"] / "PAUSED").exists() and "Fleet resumed" in s2.text
    assert r.ran("hermes", "config", "unset", "kanban.dispatch_profiles")


def test_resume_refuses_while_the_fleet_waits_for_keys(fleet_env):
    basic.cmd_pause(ns(reason="waiting_for_keys", by="installer"), Script().con, Runner())
    s = Script()
    assert basic.cmd_resume(ns(force=False), s.con, Runner()) == 1
    assert (fleet_env["state"] / "PAUSED").exists() and "waiting for keys" in s.text
    assert basic.cmd_resume(ns(force=True), Script().con, Runner()) == 0
    assert not (fleet_env["state"] / "PAUSED").exists()


def test_resume_is_allowed_with_a_key_or_the_mock_model(fleet_env):
    basic.cmd_pause(ns(reason="waiting_for_keys", by="installer"), Script().con, Runner())
    secrets.set_value("GROQ_API_KEY", "gsk_" + "A1b2C3d4E5" * 3)
    assert basic.cmd_resume(ns(force=False), Script().con, Runner()) == 0


def test_resume_when_not_paused(fleet_env):
    s = Script()
    assert basic.cmd_resume(ns(force=False), s.con, Runner()) == 0 and "not paused" in s.text


def test_stop_and_start_use_systemctl_on_the_target(fleet_env):
    r = Runner()
    s = Script()
    assert basic.cmd_stop(ns(), s.con, r) == 0 and r.ran("systemctl", "stop", "agent-fleet.target")
    assert "control bot keeps running" in s.text
    assert basic.cmd_start(ns(), Script().con, r) == 0 and r.ran("systemctl", "start", "agent-fleet.target")
    bad = Runner([(["systemctl"], (1, "boom"))])
    assert basic.cmd_stop(ns(), Script().con, bad) == 1


def test_start_warns_when_still_paused(fleet_env):
    (fleet_env["state"] / "PAUSED").write_text(json.dumps({"reason": "owner"}))
    s = Script()
    basic.cmd_start(ns(), s.con, Runner())
    assert "still paused" in s.text


def test_brief_shows_the_latest_and_can_make_a_new_one(fleet_env):
    briefs = fleet_env["state"] / "briefs"
    briefs.mkdir()
    (briefs / "latest.md").write_text("Achieved: nothing yet.")
    r = Runner()
    s = Script()
    assert basic.cmd_brief(ns(now=False), s.con, r) == 0 and "Achieved: nothing yet." in s.text and not r.calls
    s2 = Script()
    assert basic.cmd_brief(ns(now=True), s2.con, r) == 0 and r.ran("systemctl", "start", "agent-fleet-briefing.service")
    assert basic.cmd_brief(ns(now=True), Script().con, Runner([(["systemctl"], (1, "failed"))])) == 1


def test_brief_before_the_first_one(fleet_env):
    s = Script()
    assert basic.cmd_brief(ns(now=False), s.con, Runner()) == 0 and "07:00" in s.text


def test_logs(fleet_env):
    r = Runner([(["journalctl"], (0, "line one\nline two"))])
    s = Script()
    assert basic.cmd_logs(ns(service="guard", lines=5, follow=False), s.con, r) == 0 and "line two" in s.text
    assert r.ran("journalctl", "-u", "agent-fleet-guard.service", "-n", "5")
    assert basic.cmd_logs(ns(service="nope", lines=5, follow=False), Script().con, r) == 2


def test_backup_runs_export_then_backup(fleet_env):
    folder = fleet_env["ops"] / "backups"
    folder.mkdir()
    (folder / "agent-fleet-20261112-010000.tar.gz").write_bytes(b"x" * 2048)
    r = Runner()
    s = Script()
    assert basic.cmd_backup(ns(), s.con, r) == 0 and "no secrets" in s.text
    units = [a[-1] for a, _u in r.calls if a[:2] == ["systemctl", "start"]]
    assert units == ["agent-fleet-export.service", "agent-fleet-backup.service"]
    assert basic.cmd_backup(ns(), Script().con, Runner([(["systemctl"], (1, "failed"))])) == 1


def test_revoke_checklist_has_no_secret_values(fleet_env):
    secrets.set_value("GROQ_API_KEY", "gsk_" + "A1b2C3d4E5" * 3)
    s = Script()
    assert basic.cmd_revoke(ns(), s.con) == 0
    assert "GROQ_API_KEY" in s.text and "console.groq.com/keys" in s.text and "gsk_A1b2" not in s.text


# ------------------------------------------------------------------ test command
def test_counts():
    assert testcmd._counts("12 passed, 1 skipped in 3.2s") == {"passed": 12, "skipped": 1}


def test_the_test_command_runs_three_suites_then_collects_them_in_one_session(fleet_env, tmp_path, monkeypatch):
    seen = []

    def fake(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "1700 tests collected in 5s" if "--collect-only" in argv else "7 passed in 1s", "")  # nosemgrep: dangerous-subprocess-use-audit
    s = Script()
    assert testcmd.cmd_test(ns(suite=[], k=None, x=False), s.con, runner=fake) == 0
    assert len(seen) == 4 and "fleetctl test: PASS" in s.text and "1700 collected in one session" in s.text
    assert [a[4] for a in seen[:3]] == ["core/tests/policy", "tests/unit", "tests/injection"]
    assert seen[3][-3:] == ["core/tests/policy", "tests/unit", "tests/injection"] and "--collect-only" in seen[3] and "not deployed" in seen[3]


def test_a_collection_error_in_the_combined_session_fails_the_test_command(fleet_env):
    def fake(argv, **kw):
        if "--collect-only" in argv:
            return subprocess.CompletedProcess(argv, 2, "ERROR tests/unit/test_x.py\nImportError: cannot import name", "")  # nosemgrep: dangerous-subprocess-use-audit
        return subprocess.CompletedProcess(argv, 0, "7 passed in 1s", "")  # nosemgrep: dangerous-subprocess-use-audit
    s = Script()
    assert testcmd.cmd_test(ns(suite=[], k=None, x=False), s.con, runner=fake) == 1
    assert "gate       FAIL (collection error)" in s.text and "FAIL (gate)" in s.text and "ImportError" in s.text


def test_one_named_suite_does_not_run_the_gate_stage(fleet_env):
    seen = []

    def fake(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "7 passed in 1s", "")  # nosemgrep: dangerous-subprocess-use-audit
    assert testcmd.cmd_test(ns(suite=["unit"], k=None, x=False), Script().con, runner=fake) == 0
    assert len(seen) == 1
    seen.clear()
    assert testcmd.cmd_test(ns(suite=["gate"], k=None, x=False), Script().con, runner=fake) == 0
    assert len(seen) == 1 and "--collect-only" in seen[0]


def test_the_gate_stage_collects_the_same_folders_as_gate_g1_of_the_merger():
    import re
    source = (pathlib.Path(__file__).resolve().parents[2] / "ops" / "merger" / "gate_entry.py").read_text()
    command = next(line for line in source.splitlines() if '"-m", "not deployed", "core/tests/policy"' in line)
    folders = re.findall(r'"((?:core/tests|tests)/[a-z]+)"', command)
    assert folders == testcmd.GATE_FOLDERS


def test_the_test_command_reports_a_failure(fleet_env):
    def fake(argv, **kw):
        return subprocess.CompletedProcess(argv, 1 if "tests/unit" in argv else 0, "1 failed, 3 passed", "")  # nosemgrep: dangerous-subprocess-use-audit
    s = Script()
    assert testcmd.cmd_test(ns(suite=["unit"], k="x", x=True), s.con, runner=fake) == 1
    assert "FAIL (unit)" in s.text


def test_the_test_environment_is_clean(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "abc")
    monkeypatch.setenv("PYTHONPATH", "/evil")
    env = testcmd._env()
    assert "SECRET_TOKEN" not in env and "PYTHONPATH" not in env and env["PYTHONDONTWRITEBYTECODE"] == "1"


# ------------------------------------------------------------------ update
def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo)] + list(a), check=True, capture_output=True, text=True,  # nosemgrep: dangerous-subprocess-use-audit
                          env={"PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@t.invalid", "GIT_CONFIG_GLOBAL": "/dev/null"}).stdout


class FakeMerger:
    def __init__(self, tmp_path, verdict, outcome=None):
        self.live = tmp_path / "live"
        self.state_dir = tmp_path / "state"
        self.staging = tmp_path / "state" / "staging.git"
        self.verdict, self.outcome, self.calls = verdict, outcome or {"result": "merged", "old": "a" * 40, "new": "b" * 40}, []

    def evaluate(self, branch, sha, origin="agent"):
        self.calls.append(("evaluate", branch, origin))
        return self.verdict

    def passed(self, verdict):
        return all(verdict["gates"].get(g) == "pass" for g in update.GATES)

    def deploy(self, verdict):
        self.calls.append(("deploy",))
        return self.outcome

    def changelog(self, verdict, outcome):
        self.calls.append(("changelog", outcome))


def source_repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    (src / "a.txt").write_text("1")
    git(src, "add", "-A")
    git(src, "commit", "-qm", "one")
    return src


GATES_OK = {g: "pass" for g in update.GATES}


def test_update_runs_the_owner_gates_then_deploys(tmp_path, fleet_env):
    src = source_repo(tmp_path)
    m = FakeMerger(tmp_path, {"merge": "m" * 40, "gates": GATES_OK, "details": {}, "reason": None})
    (tmp_path / "live").mkdir()
    s = Script()
    r = Runner()
    assert update.run_update(s.con, source=str(src), merger=m, runner=r) == 0
    assert ("evaluate", "owner/update", "owner") in m.calls and ("deploy",) in m.calls
    assert ("changelog", "update merged") in m.calls
    assert r.ran("bash", "--from", "30") and "Updated aaaaaaaaaa to bbbbbbbbbb" in s.text


def test_update_stops_when_a_gate_fails_and_changes_nothing(tmp_path, fleet_env):
    src = source_repo(tmp_path)
    verdict = {"merge": "m" * 40, "gates": dict(GATES_OK, G2="fail"), "details": {"G2": "gitleaks found a secret"}, "reason": None}
    m = FakeMerger(tmp_path, verdict)
    (tmp_path / "live").mkdir()
    s = Script()
    assert update.run_update(s.con, source=str(src), merger=m, runner=Runner()) == 1
    assert ("deploy",) not in m.calls and "gitleaks found a secret" in s.text and "Nothing changed" in s.text


def test_update_says_when_there_is_nothing_new(tmp_path, fleet_env):
    src = source_repo(tmp_path)
    m = FakeMerger(tmp_path, {"merge": None, "gates": {}, "details": {}, "reason": "the branch has no new commits"})
    (tmp_path / "live").mkdir()
    s = Script()
    assert update.run_update(s.con, source=str(src), merger=m, runner=Runner()) == 0 and "Already up to date" in s.text


def test_update_reports_a_rollback(tmp_path, fleet_env):
    src = source_repo(tmp_path)
    m = FakeMerger(tmp_path, {"merge": "m" * 40, "gates": GATES_OK, "details": {}, "reason": None},
                   outcome={"result": "rolled_back", "old": "a" * 40, "new": "b" * 40, "health": ["agent-fleet-guard is not active"]})
    (tmp_path / "live").mkdir()
    s = Script()
    r = Runner()
    assert update.run_update(s.con, source=str(src), merger=m, runner=r) == 1
    assert "rolled the change back" in s.text and not r.ran("bash")


def test_update_input_checks(tmp_path, fleet_env):
    m = FakeMerger(tmp_path, {})
    (tmp_path / "live").mkdir()
    with pytest.raises(update.UpdateError):
        update.run_update(Script().con, source="x", ref="--upload-pack=evil", merger=m)
    with pytest.raises(update.UpdateError):
        update.run_update(Script().con, source=None, merger=m, runner=Runner([(["git"], (1, ""))]))
    with pytest.raises(update.UpdateError):
        update.run_update(Script().con, source=str(tmp_path / "does-not-exist"), merger=m, runner=Runner())


def test_update_fetches_a_tag_when_the_ref_is_not_a_branch(tmp_path, fleet_env):
    src = source_repo(tmp_path)
    git(src, "tag", "v0.2.0")
    m = FakeMerger(tmp_path, {"merge": None, "gates": {}, "details": {}, "reason": "the branch has no new commits"})
    (tmp_path / "live").mkdir()
    assert update.fetch_candidate(m, str(src), "v0.2.0") == git(src, "rev-parse", "v0.2.0").strip()


def test_the_pytest_options_are_set_once_so_the_summary_line_stays():
    seen = []

    def fake(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "3 passed in 0.1s\n", "")  # nosemgrep: dangerous-subprocess-use-audit
    rc, out = testcmd.run_suite(pathlib.Path("."), "tests/unit", ["-x"], runner=fake)
    argv = seen[0]
    assert "-q" not in argv and argv[argv.index("-o") + 1] == "addopts=-p no:cacheprovider -q" and argv[-1] == "-x"
    assert testcmd._counts(out) == {"passed": 3}
