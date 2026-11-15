import json
import pathlib
import subprocess

from ops.fleetlib import config, httpc, secrets
from ops.quota import tracker

HOOK = pathlib.Path(__file__).resolve().parents[2] / "core" / "killswitch" / "hermes-paused-hook.sh"


def test_percent_entries_reads_several_shapes():
    assert tracker.percent_entries({"providers": [{"provider": "groq", "percentUsed": 85}]}) == [("groq", 85.0)]
    assert tracker.percent_entries({"providers": [{"provider": "gemini", "quotas": {"daily": {"used": 30, "limit": 40}}}]}) == [("gemini", 75.0)]
    assert tracker.percent_entries({"providers": [{"providerId": "x", "remainingPercentage": 10}]}) == [("x", 90.0)]
    assert tracker.percent_entries({"providers": []}) == [] and tracker.percent_entries(None) == []


def cfg(limits=None, slow=80):
    c = config.defaults()
    c["quota"]["daily_free_limits"] = limits or {"openrouter": 200}
    c["limits"]["slowdown_percent"] = slow
    return c


def test_daily_use_is_the_change_since_the_first_look_of_the_day():
    hist = {"byProvider": {"openrouter": {"requests": 1000}}}
    budget, state = tracker.compute(cfg(), hist, {}, {}, "2026-11-12")
    assert budget["providers"]["openrouter"]["requests_today"] == 0 and budget["slowdown"] is False
    hist = {"byProvider": {"openrouter": {"requests": 1100}}}
    budget, state = tracker.compute(cfg(), hist, {}, state, "2026-11-12")
    assert budget["providers"]["openrouter"] == {"requests_today": 100, "limit": 200, "percent": 50.0}
    hist = {"byProvider": {"openrouter": {"requests": 1170}}}
    budget, state = tracker.compute(cfg(), hist, {}, state, "2026-11-12")
    assert budget["slowdown"] is True and budget["hot"] == ["openrouter"]          # 170 of 200 is 85 percent
    budget, state = tracker.compute(cfg(), hist, {}, state, "2026-11-13")           # a new day starts at zero
    assert budget["slowdown"] is False and state["date"] == "2026-11-13"


def test_the_router_quota_can_trigger_a_slowdown_by_itself():
    quota = {"providers": [{"provider": "groq", "percentUsed": 91}]}
    budget, _ = tracker.compute(cfg({}), {"byProvider": {}}, quota, {}, "2026-11-12")
    assert budget["hot"] == ["groq"] and budget["slowdown"] is True


def test_threshold_comes_from_config():
    hist = {"byProvider": {"openrouter": {"requests": 150}}}
    _b, state = tracker.compute(cfg(), {"byProvider": {"openrouter": {"requests": 0}}}, {}, {}, "d")
    budget, _ = tracker.compute(cfg(slow=70), hist, {}, state, "d")
    assert budget["hot"] == ["openrouter"]          # 75 percent is over 70
    assert tracker.compute(cfg(slow=90), hist, {}, state, "d")[0]["hot"] == []


def test_main_writes_budget_json_and_handles_a_router_that_is_not_ready(fleet_env):
    assert tracker.main() == 0
    assert json.loads((fleet_env["state"] / "budget.json").read_text())["note"] == "router not ready"
    secrets.set_value("INITIAL_PASSWORD", "pw-test-123-abcdefgh", enforce_format=False)

    def fake(method, url, headers, body, timeout, max_bytes):
        if url.endswith("/api/auth/login"):
            return httpc.Response(200, {"Set-Cookie": "auth_token=abc; Path=/"}, b"{}")
        if url.endswith("/api/usage/history"):
            return httpc.Response(200, {}, json.dumps({"byProvider": {"openrouter": {"requests": 5}}}).encode())
        return httpc.Response(200, {}, b'{"providers": []}')
    httpc.set_transport(fake)
    try:
        assert tracker.main() == 0
        budget = json.loads((fleet_env["state"] / "budget.json").read_text())
        assert budget["slowdown"] is False and "openrouter" in budget["providers"]
        assert (fleet_env["state"] / "budget.json").stat().st_mode & 0o777 == 0o644
    finally:
        httpc.reset_transport()


def run_hook(tmp_path, paused=False, slowdown=False):
    flag, budget = tmp_path / "PAUSED", tmp_path / "budget.json"
    if paused:
        flag.write_text("{}")
    if slowdown:
        budget.write_text(json.dumps({"slowdown": True}, indent=2))
    script = tmp_path / "hook.sh"
    script.write_text(HOOK.read_text().replace("/var/lib/agent-fleet/PAUSED", str(flag)).replace("/var/lib/agent-fleet/budget.json", str(budget)))
    script.chmod(0o755)
    out = subprocess.run([str(script)], input="{}", capture_output=True, text=True, timeout=10)
    return json.loads(out.stdout)


def test_the_hook_adds_a_budget_reminder_but_pause_wins(tmp_path):
    assert run_hook(tmp_path) == {}
    assert "Slow down" in run_hook(tmp_path, slowdown=True)["context"]
    both = run_hook(tmp_path, paused=True, slowdown=True)
    assert both["action"] == "block" and "context" not in both


def test_owner_now_follows_the_owner_time_zone_and_survives_a_bad_zone():
    from ops.fleetlib import config
    cfg = config.defaults()
    cfg["owner"]["timezone"] = "Asia/Tokyo"
    assert config.owner_now(cfg, 1700000000).strftime("%Y-%m-%d %H") == "2023-11-15 07"
    cfg["owner"]["timezone"] = "Nope/Zone"
    assert config.owner_now(cfg, 1700000000).strftime("%Y-%m-%d %H") == "2023-11-14 22"


def test_main_writes_the_budget_for_the_agents_too(fleet_env, monkeypatch):
    from ops.quota import tracker
    (fleet_env["state"] / "status").mkdir()
    monkeypatch.setattr(tracker, "fetch", lambda cfg: (None, None))
    assert tracker.main([]) == 0
    a = json.loads((fleet_env["state"] / "budget.json").read_text())
    b = json.loads((fleet_env["state"] / "status" / "budget.json").read_text())
    assert a == b and a["slowdown"] is False


def test_counters_count_blocked_posts_and_slowdown_hours(fleet_env):
    now = 1_800_000_000
    ops = fleet_env["ops"]
    (ops / "publisher").mkdir()
    (ops / "quota").mkdir()
    lines = [{"status": "blocked", "reason": "honesty", "ts": now - 100}, {"status": "blocked", "reason": "honesty", "ts": now - 200},
             {"status": "blocked", "reason": "rate_limited", "ts": now - 50}, {"status": "sent", "reason": "", "ts": now - 10},
             {"status": "blocked", "reason": "honesty", "ts": now - 30 * 86400}]
    (ops / "publisher" / "posts.log").write_text("\n".join(json.dumps(l) for l in lines))
    out = tracker.counters(now, ops, {"slowdown": True})
    assert out["publisher_blocked"] == {"honesty": 2, "rate_limited": 1} and out["slowdown_hours"] == 0.25
    out = tracker.counters(now + 900, ops, {"slowdown": True})
    assert out["slowdown_hours"] == 0.5
    assert tracker.counters(now + 1800, ops, {"slowdown": False})["slowdown_hours"] == 0.5
