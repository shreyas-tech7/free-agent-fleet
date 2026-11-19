"""The tick monitor of the orchestrator. Hermes runs the agent only when the output of the monitor changes."""
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_tick():
    spec = importlib.util.spec_from_file_location("orchestrator_tick_monitor", ROOT / "agents" / "orchestrator" / "monitor" / "tick.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def output(tick, tmp_path, branches, capsys):
    state = tmp_path / "state"
    (state / "status").mkdir(parents=True, exist_ok=True)
    (state / "status" / "merger.json").write_text(json.dumps({"branches": branches}))
    tick.STATE = state
    tick.LIVE = tmp_path / "live"
    tick.main()
    return capsys.readouterr().out


def test_an_own_merged_branch_does_not_change_the_output(tmp_path, capsys):
    """The orchestrator opens a state branch at the end of each tick. Its merge must not wake the next tick. See F-18."""
    tick = load_tick()
    base = {"agent/research/pains": {"result": "merged"}, "agent/orchestrator/state-1": {"result": "merged"}}
    before = output(tick, tmp_path, base, capsys)
    after = output(tick, tmp_path, dict(base, **{"agent/orchestrator/state-2": {"result": "merged"}}), capsys)
    assert before == after and "merger: merged=1" in before


def test_other_agents_and_own_failures_still_wake_the_orchestrator(tmp_path, capsys):
    tick = load_tick()
    base = {"agent/orchestrator/state-1": {"result": "merged"}}
    before = output(tick, tmp_path, base, capsys)
    assert "merger: none" in before
    assert output(tick, tmp_path, dict(base, **{"agent/build/fix": {"result": "merged"}}), capsys) != before
    rejected = output(tick, tmp_path, dict(base, **{"agent/orchestrator/state-2": {"result": "rejected"}}), capsys)
    assert rejected != before and "rejected=1" in rejected
    rolled = output(tick, tmp_path, dict(base, **{"agent/orchestrator/state-2": {"result": "rolled_back"}}), capsys)
    assert "rolled_back=1" in rolled


def test_counts_survive_a_damaged_status_file(tmp_path, capsys):
    tick = load_tick()
    assert tick.merger_counts({"branches": {"agent/x/y": "not a dict", "agent/orchestrator/z": {}}}) == {"?": 2}
    assert tick.merger_counts({}) == {}
