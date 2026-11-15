import json
import sqlite3
import time

from ops.improver import traces

NOW = 1_800_000_000.0


def make_db(path, rows_tasks, rows_runs, rows_events):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
      CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT, completed_at INTEGER);
      CREATE TABLE task_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, profile TEXT, status TEXT, outcome TEXT, started_at INTEGER, ended_at INTEGER);
      CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER);
    """)
    con.executemany("INSERT INTO tasks VALUES (?,?,?,?)", rows_tasks)
    con.executemany("INSERT INTO task_runs (task_id, profile, status, outcome, started_at, ended_at) VALUES (?,?,?,?,?,?)", rows_runs)
    con.executemany("INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?,?,?,?)", rows_events)
    con.commit()
    con.close()


def test_the_summary_counts_runs_retries_and_block_kinds(tmp_path):
    home = tmp_path / "home"
    day = 86400
    make_db(home / "kanban.db",
            [("t1", "secret title", "done", NOW - day), ("t2", "x", "blocked", None), ("t3", "x", "done", NOW - 20 * day)],
            [("t1", "research", "done", "completed", NOW - 2 * day, NOW - 2 * day + 600),
             ("t1", "research", "done", "completed", NOW - day - 700, NOW - day),
             ("t2", "build", "blocked", "blocked", NOW - day, NOW - day + 30),
             ("t4", "build", "failed", "crashed", NOW - day, NOW - day + 10),
             ("t0", "build", "done", "completed", NOW - 30 * day, NOW - 30 * day + 60)],
            [("t2", "blocked", json.dumps({"kind": "needs_input", "reason": "free text that must not be copied"}), NOW - day)])
    make_db(home / "kanban" / "boards" / "p1" / "kanban.db", [("b1", "x", "done", NOW - day)], [], [])
    tasks, agents, reasons = traces.summarize(home, NOW - traces.WEEK, NOW)
    assert tasks == {"done": 2, "blocked": 1, "failed": 1}
    assert agents["research"] == {"done": 2, "blocked": 0, "retries": 1, "median_minutes": 10.8}
    assert agents["build"]["blocked"] == 1 and agents["build"]["done"] == 0
    assert reasons == {"needs_input": 1}
    assert "free text" not in json.dumps([tasks, agents, reasons])


def test_the_merger_summary_and_the_full_build(tmp_path):
    status = tmp_path / "status"
    status.mkdir()
    (status / "merger.json").write_text(json.dumps({"branches": {
        "agent/a/x": {"result": "merged", "gates": {"G1": "pass"}}, "agent/a/y": {"result": "rejected", "gates": {"G1": "fail", "G6": "fail"}}}}))
    (status / "counters.json").write_text(json.dumps({"publisher_blocked": {"honesty": 2}, "slowdown_hours": 3.5}))
    out = traces.build(tmp_path / "nohome", status, NOW)
    assert out["merger"] == {"merged": 1, "rejected": 1, "rolled_back": 0, "gate_fails": {"G1": 1, "G6": 1}}
    assert out["publisher"] == {"blocked": {"honesty": 2}} and out["budget"] == {"slowdown_hours": 3.5}
    assert out["tasks"] == {"done": 0, "blocked": 0, "failed": 0} and out["week"].startswith("2027-W")


def test_missing_files_are_fine(tmp_path):
    out = traces.build(tmp_path / "none", tmp_path / "none2", NOW)
    assert "merger" not in out and "publisher" not in out and out["by_agent"] == {}


def test_main_writes_the_file_in_the_working_clone(fleet_env, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("FLEET_WORK_CLONE", str(tmp_path / "work"))
    assert traces.main() == 0
    files = list((tmp_path / "work" / "projects" / "_improver" / "traces").glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["tasks"]["done"] == 0
    assert "trace summary written" in capsys.readouterr().out
