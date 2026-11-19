"""The gate run has two phases. Agent-written project tests run last, alone, with no hidden tests. See D-074 and F-01."""
import json

import pytest

from ops.fleetlib import config
from ops.merger import container, gate_entry, runner

OK = {"ok": True, "detail": "fine"}
TRUSTED = {"G1": dict(OK), "G2": dict(OK), "G4": dict(OK), "seconds": 7}


# ---------------------------------------------------------------- joining the two phases
def test_no_project_phase_leaves_the_trusted_result_alone():
    out = container.merge_phases(TRUSTED, None)
    assert {g: out[g]["ok"] for g in ("G1", "G2", "G4")} == {"G1": True, "G2": True, "G4": True} and out["seconds"] == 7


def test_the_project_phase_can_only_change_g1_through_the_key_p():
    forged = {"P": {"ok": True, "detail": "2 passed"}, "G1": {"ok": True}, "G2": {"ok": True}, "G4": {"ok": True}, "seconds": 3}
    failed_scan = dict(TRUSTED, G2={"ok": False, "detail": "gitleaks found a key"})
    out = container.merge_phases(failed_scan, forged)
    assert out["G2"] == {"ok": False, "detail": "gitleaks found a key"}       # a forged G2 line changes nothing
    assert out["G1"]["ok"] is True and "project tests: 2 passed" in out["G1"]["detail"] and out["seconds"] == 10


@pytest.mark.parametrize("projects", [{}, {"P": {"ok": False, "detail": "1 failed"}}, {"P": "yes"}, {"P": {"ok": "true"}}, {"G1": {"ok": True}}, "GATE", []])
def test_a_missing_or_failed_project_result_fails_g1(projects):
    out = container.merge_phases(TRUSTED, projects)
    assert out["G1"]["ok"] is False and out["G2"]["ok"] is True and out["G4"]["ok"] is True


def test_a_missing_trusted_gate_counts_as_failed():
    out = container.merge_phases({"G1": dict(OK)}, {"P": dict(OK)})
    assert out["G2"]["ok"] is False and out["G4"]["ok"] is False and not container.trusted_passed(out)
    assert container.trusted_passed(TRUSTED)


def test_changed_projects_skips_shared_folders():
    changed = ["projects/demo/app/worker.js", "projects/demo/app/tests/a.test.js", "projects/_studio/brand/voice.md", "docs/x.md", "projects/STATE.md"]
    assert container.changed_projects(changed) == ["demo"]
    assert container.changed_projects(["skills/x/SKILL.md", "agents/build/SOUL.md"]) == []


# ---------------------------------------------------------------- inside the container
def run_entry(tmp_path, monkeypatch, capsys, job):
    called = []
    monkeypatch.setattr(gate_entry, "copy_source", lambda: called.append("copy"))
    for name in ("gate_g1", "gate_g2", "gate_g4", "gate_projects"):
        monkeypatch.setattr(gate_entry, name, lambda job, _n=name: (called.append(_n), (True, _n + " ran"))[1])
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job))
    assert gate_entry.main(["gate_entry.py", str(path)]) == 0
    line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("GATE_RESULT ")][-1]
    return called, json.loads(line[len("GATE_RESULT "):])


def test_the_trusted_phase_runs_no_project_test(tmp_path, monkeypatch, capsys):
    called, result = run_entry(tmp_path, monkeypatch, capsys, {"changed": ["projects/demo/app/tests/a.test.js"], "gates": ["G1", "G2", "G4"], "phase": "trusted"})
    assert called == ["copy", "gate_g1", "gate_g2", "gate_g4"] and set(result) == {"G1", "G2", "G4", "seconds"}


def test_a_job_with_no_phase_is_the_trusted_phase(tmp_path, monkeypatch, capsys):
    called, result = run_entry(tmp_path, monkeypatch, capsys, {"changed": []})
    assert "gate_projects" not in called and "P" not in result


def test_the_project_phase_runs_only_the_project_tests(tmp_path, monkeypatch, capsys):
    called, result = run_entry(tmp_path, monkeypatch, capsys, {"changed": ["projects/demo/app/tests/a.test.js"], "phase": "projects"})
    assert called == ["copy", "gate_projects"] and set(result) == {"P", "seconds"} and result["P"]["ok"] is True


def test_gate_g1_itself_never_starts_a_project_test(tmp_path, monkeypatch):
    work = tmp_path / "work"
    (work / "src" / "projects" / "demo").mkdir(parents=True)
    (work / "src" / "projects" / "demo" / "test_agent.py").write_text("def test_x():\n    assert False\n")
    hidden = tmp_path / "hidden"
    hidden.mkdir()
    (hidden / "test_h.py").write_text("def test_h():\n    pass\n")
    commands = []
    monkeypatch.setattr(gate_entry, "WORK", work)
    monkeypatch.setattr(gate_entry, "HIDDEN", hidden)
    monkeypatch.setattr(gate_entry, "run", lambda cmd, cwd, extra_env=None, timeout=900: (commands.append((cmd, str(cwd))), (0, "ok"))[1])
    ok, detail = gate_entry.gate_g1({"changed": ["projects/demo/test_agent.py"]})
    assert ok and len(commands) == 2 and not any("projects" in " ".join(cmd) or "projects" in cwd for cmd, cwd in commands)
    commands.clear()
    ok, detail = gate_entry.gate_projects({"changed": ["projects/demo/test_agent.py"]})
    assert len(commands) == 1 and commands[0][1].endswith("projects/demo") and str(hidden) not in " ".join(commands[0][0])


# ---------------------------------------------------------------- the merger starts the phases
@pytest.fixture
def phases(tmp_path, monkeypatch, fleet_env):
    monkeypatch.setenv("FLEET_LIVE_REPO", str(tmp_path / "live"))
    monkeypatch.setenv("FLEET_MERGER_STATE", str(tmp_path / "merger-state"))
    monkeypatch.setenv("FLEET_MERGER_RUNS", str(tmp_path / "runs"))
    monkeypatch.setenv("FLEET_HIDDEN_TESTS", str(tmp_path / "hidden"))
    runs = []
    answers = {"trusted": dict(TRUSTED), "projects": {"P": dict(OK), "seconds": 2}}

    def prepare_run(run_dir, exporter, hidden_dir, entry_file, job, owner="fleetops"):
        runs.append({"dir": str(run_dir), "hidden": hidden_dir, "job": job})

    def run_gates(run_dir, image, rules, osv, **kw):
        phase = runs[-1]["job"]["phase"]
        if isinstance(answers[phase], Exception):
            raise answers[phase]
        return answers[phase]

    monkeypatch.setattr(container, "prepare_run", prepare_run)
    monkeypatch.setattr(container, "run_gates", run_gates)
    monkeypatch.setattr(container, "refresh_osv", lambda *a, **k: "fresh")
    merger = runner.Merger(cfg=config.defaults(), ignore_pause=True, image_id="sha256:test")
    return merger, runs, answers


def test_a_change_with_no_project_runs_one_container(phases):
    merger, runs, _answers = phases
    out = merger._container_gates("a" * 40, [], ["skills/x/SKILL.md"])
    assert len(runs) == 1 and runs[0]["job"]["phase"] == "trusted" and runs[0]["hidden"] is not None
    assert out["G1"]["ok"] and out["G2"]["ok"] and out["G4"]["ok"]


def test_a_project_change_runs_a_second_container_with_no_hidden_tests(phases):
    merger, runs, _answers = phases
    out = merger._container_gates("b" * 40, [], ["projects/demo/app/tests/a.test.js"])
    assert [r["job"]["phase"] for r in runs] == ["trusted", "projects"]
    assert runs[1]["hidden"] is None and runs[0]["hidden"] is not None and runs[0]["dir"] != runs[1]["dir"]
    assert "gates" not in runs[1]["job"] and out["G1"]["ok"] is True and out["seconds"] == 9


def test_agent_code_never_runs_when_a_trusted_gate_fails(phases):
    merger, runs, answers = phases
    answers["trusted"] = dict(TRUSTED, G2={"ok": False, "detail": "semgrep finding"})
    out = merger._container_gates("c" * 40, [], ["projects/demo/app/worker.js"])
    assert len(runs) == 1 and out["G2"]["ok"] is False


def test_a_forged_result_from_the_project_container_cannot_pass_a_scan(phases):
    merger, runs, answers = phases
    answers["projects"] = {"P": {"ok": False, "detail": "1 failed"}, "G1": dict(OK), "G2": dict(OK), "G4": dict(OK)}
    out = merger._container_gates("d" * 40, [], ["projects/demo/app/worker.js"])
    assert out["G1"]["ok"] is False and out["G2"]["ok"] is True


def test_a_project_container_that_dies_fails_g1_only(phases):
    merger, runs, answers = phases
    answers["projects"] = container.GateError("the gate container gave no result")
    out = merger._container_gates("e" * 40, [], ["projects/demo/app/worker.js"])
    assert out["G1"]["ok"] is False and "project test run failed" in out["G1"]["detail"] and out["G4"]["ok"] is True


def test_prepare_run_with_no_hidden_folder_leaves_it_empty(tmp_path):
    entry = tmp_path / "gate_entry.py"
    entry.write_text("print(1)\n")
    run = tmp_path / "run"
    container.prepare_run(run, lambda dest: (dest / "f.txt").write_text("x"), None, entry, {"phase": "projects"})
    assert (run / "hidden").is_dir() and list((run / "hidden").iterdir()) == []
