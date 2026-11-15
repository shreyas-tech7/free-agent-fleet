import json

from ops.guard.server import Screen
from ops.redteam import drill
from tests.injection import corpus


def rules_guard(text):
    return Screen(force_rules_only=True).scan(text)


def test_the_drill_passes_with_a_working_screen():
    out = drill.run(rules_guard, now=1_800_000_000)
    assert out["pass_rate"] == 1.0 and out["failures"] == [] and out["total"] > 70
    assert set(out["groups"]) == {"injection", "benign", "honesty", "pages", "skills", "intake"}
    assert out["groups"]["injection"]["total"] == len(corpus.INJECTIONS) and out["date"].startswith("2027-")


def test_a_screen_that_flags_nothing_fails_the_attack_groups():
    out = drill.run(lambda text: {"label": "SAFE", "score": 0.0})
    assert out["pass_rate"] < 0.6
    assert out["groups"]["injection"]["passed"] == 0 and out["groups"]["intake"]["passed"] <= 3
    assert any(f.startswith("injection:") for f in out["failures"])


def test_a_screen_that_blocks_everything_fails_the_benign_group():
    out = drill.run(lambda text: {"label": "INJECTION", "score": 1.0})
    assert out["groups"]["benign"]["passed"] == 0 and out["groups"]["injection"]["passed"] == len(corpus.INJECTIONS)


def test_a_screen_that_is_down_fails_closed_everywhere():
    out = drill.run(lambda text: None)
    assert out["groups"]["injection"]["passed"] == 0 and out["groups"]["intake"]["passed"] <= 3


def test_main_writes_the_status_file_and_history(fleet_env, monkeypatch, capsys):
    monkeypatch.setattr(drill, "live_guard", lambda cfg: rules_guard)
    (fleet_env["state"] / "status").mkdir()
    assert drill.main([]) == 0
    result = json.loads((fleet_env["state"] / "status" / "redteam.json").read_text())
    assert result["pass_rate"] == 1.0
    assert json.loads((fleet_env["ops"] / "redteam" / "history.jsonl").read_text().splitlines()[-1])["passed"] == result["passed"]


def test_main_skips_when_the_screen_is_not_reachable(fleet_env, monkeypatch, capsys):
    monkeypatch.setattr(drill, "live_guard", lambda cfg: (lambda text: None))
    assert drill.main([]) == 1 and "skipped" in capsys.readouterr().out
    assert not (fleet_env["state"] / "status" / "redteam.json").exists()
