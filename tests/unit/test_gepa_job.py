import datetime
import json
import subprocess
import urllib.error

from ops.fleetctl import doctor
from ops.improver import gepa
from ops.fleetlib import config, secrets
from ops.fleetlib.status import FAIL, OK, WAITING
from tests.unit.fctl_helpers import Runner

NOW = datetime.datetime(2026, 11, 15, 2, 0, tzinfo=datetime.timezone.utc)      # a Sunday, ISO week 46


def test_it_skips_while_paused_or_near_a_limit_or_without_the_tool_or_a_key(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    venv = tmp_path / "python"
    assert "not installed" in gepa.skip_reason(state, venv, "key")
    venv.write_text("")
    assert "WAITING_FOR_KEYS" in gepa.skip_reason(state, venv, "")
    assert gepa.skip_reason(state, venv, "key") is None
    (state / "budget.json").write_text(json.dumps({"slowdown": True}))
    assert "limit is near" in gepa.skip_reason(state, venv, "key")
    (state / "PAUSED").write_text("{}")
    assert "paused" in gepa.skip_reason(state, venv, "key")


def test_the_skill_follows_the_week_number_and_skips_private_folders():
    names = ["b-skill", "a-skill", "_enabled", "c-skill"]
    assert [gepa.pick_skill(names, w) for w in (0, 1, 2, 3)] == ["a-skill", "b-skill", "c-skill", "a-skill"]
    assert gepa.pick_skill([], 3) is None


def test_the_model_check_needs_a_200_answer():
    class R:
        def __init__(self, s):
            self.status = s

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    assert gepa.model_available("k", urlopen=lambda req, timeout: R(200)) is True
    assert gepa.model_available("k", urlopen=lambda req, timeout: R(500)) is False

    def down(req, timeout):
        raise urllib.error.URLError("down")
    assert gepa.model_available("k", urlopen=down) is False


def test_the_command_uses_the_router_names_and_a_small_iteration_count():
    argv = gepa.command("copywriting", "/repo")
    assert argv[1:3] == ["-m", "evolution.skills.evolve_skill"] and "openai/fleet-smart" in argv and "openai/fleet-worker" in argv
    assert argv[argv.index("--iterations") + 1] == "5" and argv[argv.index("--hermes-repo") + 1] == "/repo"


def test_a_full_run_writes_to_the_working_clone_and_reports(fleet_env, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    for name in ("alpha", "beta"):
        (repo / "skills" / name).mkdir(parents=True)
        (repo / "skills" / name / "SKILL.md").write_text("---\nname: %s\n---\n" % name)
    venv = tmp_path / "python"
    venv.write_text("")
    monkeypatch.setattr(gepa, "REPO", str(repo))
    monkeypatch.setattr(gepa, "VENV_PYTHON", str(venv))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / ".env").write_text("ROUTER_KEY_HERMES=sk-test-key-123\n")
    monkeypatch.setenv("FLEET_WORK_CLONE", str(tmp_path / "work"))
    calls = []

    def fake_run(argv, **kw):
        calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, 0, "line one\nBest score 0.82\n", "")  # nosemgrep: dangerous-subprocess-use-audit

    class R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    message, code = gepa.run(NOW, urlopen=lambda req, timeout: R(), runner=fake_run)
    assert code == 0 and message.startswith("evolved") and "Best score 0.82" in message
    argv, kw = calls[0]
    assert argv[argv.index("--skill") + 1] in ("alpha", "beta")
    assert kw["env"]["OPENAI_API_KEY"] == "sk-test-key-123" and kw["env"]["OPENAI_API_BASE"].startswith("http://127.0.0.1:")
    assert (tmp_path / "work" / "projects" / "_improver" / "gepa" / "2026-11-15").is_dir()
    assert "sk-test-key-123" not in message


def test_a_failed_or_timed_out_run_is_reported_with_a_nonzero_code(fleet_env, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "skills" / "alpha").mkdir(parents=True)
    (repo / "skills" / "alpha" / "SKILL.md").write_text("x")
    venv = tmp_path / "python"
    venv.write_text("")
    monkeypatch.setattr(gepa, "REPO", str(repo))
    monkeypatch.setattr(gepa, "VENV_PYTHON", str(venv))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / ".env").write_text("ROUTER_KEY_HERMES=k\n")
    monkeypatch.setenv("FLEET_WORK_CLONE", str(tmp_path / "work"))

    class R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    assert gepa.run(NOW, lambda req, timeout: R(), lambda argv, **kw: subprocess.CompletedProcess(argv, 3, "", "boom"))[1] == 1  # nosemgrep: dangerous-subprocess-use-audit

    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)  # nosemgrep: dangerous-subprocess-use-audit
    assert "longer than" in gepa.run(NOW, lambda req, timeout: R(), slow)[0]


def test_without_a_working_model_the_run_waits_for_keys(fleet_env, tmp_path, monkeypatch):
    venv = tmp_path / "python"
    venv.write_text("")
    monkeypatch.setattr(gepa, "VENV_PYTHON", str(venv))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / ".env").write_text("ROUTER_KEY_HERMES=k\n")

    def down(req, timeout):
        raise urllib.error.URLError("no provider")
    message, code = gepa.run(NOW, urlopen=down)
    assert code == 0 and "WAITING_FOR_KEYS" in message


def test_the_doctor_check_for_the_runner(fleet_env, tmp_path, monkeypatch):
    python = tmp_path / "evolve-python"
    source = tmp_path / "evolve-src"
    monkeypatch.setattr(doctor, "GEPA_PYTHON", str(python))
    monkeypatch.setattr(doctor, "GEPA_SOURCE", str(source))
    ctx = doctor.Ctx(config.load(), run=Runner())
    assert doctor.check_gepa(ctx)[0].status == FAIL
    python.write_text("")
    source.mkdir()
    assert doctor.check_gepa(ctx)[0].status == WAITING
    secrets.set_value("GROQ_API_KEY", "gsk_" + "A1b2C3d4E5" * 3)
    assert doctor.check_gepa(ctx)[0].status == OK


def test_a_failed_run_keeps_the_last_error_line_and_hides_a_key(fleet_env, tmp_path, monkeypatch):
    """The first weekly run said only exit 1. The reason must reach the cron note. See D-071."""
    repo = tmp_path / "repo"
    (repo / "skills" / "alpha").mkdir(parents=True)
    (repo / "skills" / "alpha" / "SKILL.md").write_text("x")
    venv = tmp_path / "python"
    venv.write_text("")
    monkeypatch.setattr(gepa, "REPO", str(repo))
    monkeypatch.setattr(gepa, "VENV_PYTHON", str(venv))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "profile"))
    (tmp_path / "profile").mkdir()
    (tmp_path / "profile" / ".env").write_text("ROUTER_KEY_HERMES=k\n")
    monkeypatch.setenv("FLEET_WORK_CLONE", str(tmp_path / "work"))
    key = "sk-" + "a" * 30

    class R:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    stderr = "Traceback (most recent call last):\nlitellm.RateLimitError: 429 for key %s" % key
    msg, code = gepa.run(NOW, lambda req, timeout: R(), lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", stderr))  # nosemgrep: dangerous-subprocess-use-audit
    assert code == 1 and "RateLimitError" in msg and key not in msg
    msg, code = gepa.run(NOW, lambda req, timeout: R(), lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", ""))  # nosemgrep: dangerous-subprocess-use-audit
    assert code == 1 and msg == "failed for alpha (exit 1)"
