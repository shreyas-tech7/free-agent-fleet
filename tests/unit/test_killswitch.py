import json
import pathlib
import subprocess

import pytest

from core.killswitch import killswitch as ks
from ops.fleetlib import config, pause, secrets

HOOK = pathlib.Path(__file__).resolve().parents[2] / "core" / "killswitch" / "hermes-paused-hook.sh"


class FakeRunner:
    def __init__(self, fail=None, jobs=None):
        self.calls = []
        self.fail = fail or ()
        self.jobs = jobs or {}

    def __call__(self, argv, user=None, timeout=90):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if any(f in joined for f in self.fail):
            return 1, "boom"
        if argv[:2] == ["hermes", "-p"] and argv[3:5] == ["cron", "list"]:
            return 0, json.dumps(self.jobs.get(argv[2], []))
        if argv[:3] == ["hermes", "cron", "list"]:
            return 0, json.dumps(self.jobs.get("default", []))
        return 0, "ok"

    def had(self, *words):
        return any(all(w in c for w in words) for c in self.calls)


def test_pause_writes_flag_first_even_without_hermes(fleet_env):
    def missing(argv, user=None, timeout=90):
        raise FileNotFoundError
    steps = ks.pause_fleet(reason="owner", by="test", runner=missing)
    info = pause.paused()
    assert info["reason"] == "owner" and info["by"] == "test"
    assert steps["flag"] == "created"
    assert steps["hermes_global_pause"].startswith("skipped")


def test_pause_calls_hermes_pause_cron_pause_and_stops_dispatcher(fleet_env):
    (fleet_env["hermes"] / "profiles" / "research").mkdir(parents=True, exist_ok=True)
    runner = FakeRunner(jobs={"research": [{"id": "j1", "enabled": True}, {"id": "j2", "enabled": False}],
                              "default": [{"id": "d1", "enabled": True}]})
    steps = ks.pause_fleet(runner=runner)
    assert runner.had("hermes", "cron", "pause", "d1")      # the default profile is paused too
    assert runner.had("hermes", "pause", "--reason")
    assert runner.had("-p", "research", "cron", "pause", "j1")
    assert not runner.had("cron", "pause", "j2")            # already off, left alone
    assert runner.had("config", "set", "kanban.dispatch_profiles", "[]")
    assert steps["cron_jobs_paused"] == 2


def test_pause_remembers_jobs_and_resume_restores_only_those(fleet_env):
    (fleet_env["hermes"] / "profiles" / "research").mkdir(parents=True, exist_ok=True)
    runner = FakeRunner(jobs={"research": [{"id": "j1", "enabled": True}]})
    ks.pause_fleet(runner=runner)
    saved = json.loads((fleet_env["state"] / "status" / ks.PAUSED_JOBS).read_text())
    assert saved == {"research": ["j1"]}
    config.set_override("features.mock_llm", "true")
    runner2 = FakeRunner()
    steps = ks.resume_fleet(runner=runner2)
    assert runner2.had("-p", "research", "cron", "resume", "j1")
    assert runner2.had("config", "unset", "kanban.dispatch_profiles")
    assert steps["flag"] == "removed" and pause.paused() is None


def test_resume_refuses_while_waiting_for_keys(fleet_env):
    ks.pause_fleet(reason="waiting_for_keys", runner=FakeRunner())
    with pytest.raises(ks.NotReady):
        ks.resume_fleet(runner=FakeRunner())
    assert pause.paused() is not None
    secrets.set_value("GROQ_API_KEY", "gsk_" + "A1b2C3d4E5" * 3)
    ks.resume_fleet(runner=FakeRunner())
    assert pause.paused() is None


def test_resume_keeps_flag_when_a_step_fails(fleet_env):
    ks.pause_fleet(runner=FakeRunner())
    steps = ks.resume_fleet(runner=FakeRunner(fail=("kanban.dispatch_profiles",)))
    assert pause.paused() is not None and steps["flag"].startswith("kept")
    steps = ks.resume_fleet(force=True, runner=FakeRunner(fail=("kanban.dispatch_profiles",)))
    assert pause.paused() is None


def test_resume_when_not_paused(fleet_env):
    assert ks.resume_fleet(runner=FakeRunner()) == {"flag": "not paused"}


def test_stop_and_start_use_the_target(fleet_env):
    r = FakeRunner()
    ks.stop_fleet(runner=r)
    ks.start_fleet(runner=r)
    assert ["systemctl", "stop", "agent-fleet.target"] in r.calls
    assert ["systemctl", "start", "agent-fleet.target"] in r.calls


def test_revoke_checklist_has_urls_and_no_secret_values(fleet_env):
    value = "gsk_" + "Q1w2E3r4T5" * 3
    secrets.set_value("GROQ_API_KEY", value)
    text = ks.revoke_checklist()
    assert value not in text
    assert "GROQ_API_KEY" in text and "https://console.groq.com/keys" in text
    assert "(router, set)" in text
    assert "Tailscale" in text and "fleetctl stop" in text


def test_hook_script_blocks_only_while_flag_exists(tmp_path):
    flag = tmp_path / "PAUSED"
    script = tmp_path / "hook.sh"
    # Point both files at the temp folder. The hook also reads the budget file, and the real one says "slowdown" whenever the fleet is busy. See D-063.
    script.write_text(HOOK.read_text().replace("/var/lib/agent-fleet/PAUSED", str(flag)).replace("/var/lib/agent-fleet/budget.json", str(tmp_path / "budget.json")))
    script.chmod(0o755)

    def run():
        out = subprocess.run([str(script)], input="{}", capture_output=True, text=True, timeout=10)  # nosemgrep: dangerous-subprocess-use-audit
        assert out.returncode == 0
        return json.loads(out.stdout)

    assert run() == {}
    flag.write_text("{}")
    blocked = run()
    assert blocked["action"] == "block" and "paused" in blocked["message"]
    flag.unlink()
    assert run() == {}


def hook_in(tmp_path):
    script = tmp_path / "hook.sh"
    script.write_text(HOOK.read_text().replace("/var/lib/agent-fleet/PAUSED", str(tmp_path / "PAUSED"))
                      .replace("/var/lib/agent-fleet/budget.json", str(tmp_path / "budget.json")))
    script.chmod(0o755)
    return script


def test_the_hook_adds_a_budget_reminder_only_while_the_budget_says_slowdown(tmp_path):
    script = hook_in(tmp_path)

    def run():
        out = subprocess.run([str(script)], input="{}", capture_output=True, text=True, timeout=10)  # nosemgrep: dangerous-subprocess-use-audit
        return json.loads(out.stdout)
    (tmp_path / "budget.json").write_text(json.dumps({"slowdown": False, "hot": []}))
    assert run() == {}
    (tmp_path / "budget.json").write_text(json.dumps({"slowdown": True, "hot": ["openrouter"]}))
    assert "Budget alert" in run()["context"]
    (tmp_path / "budget.json").unlink()
    assert run() == {}
