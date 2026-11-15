import json
import pathlib
import sqlite3
import tarfile

import pytest

from ops.backup import export_fleet, main as backup
from ops.fleetlib import config, secrets


@pytest.fixture
def machine(fleet_env, tmp_path, monkeypatch):
    live = tmp_path / "live"
    monkeypatch.setenv("FLEET_LIVE_REPO", str(live))
    (live / "projects" / "demo").mkdir(parents=True)
    fleet_env["config"].write_text("capacity:\n  tier: small\n")
    for sub, name, text in (("exports", "profile-research.tar.gz", "x"), ("briefs", "latest.md", "Achieved: x"), ("tickets", "t-1.json", "{}")):
        d = fleet_env["state"] / sub
        d.mkdir(exist_ok=True)
        (d / name).write_text(text)
    pub = fleet_env["ops"] / "publisher"
    pub.mkdir()
    (pub / "posts.log").write_text('{"status": "sent"}\n')
    (pub / "ratelimit.json").write_text("{}")
    (fleet_env["secrets"] / "publisher.env").write_text("BLUESKY_APP_PASSWORD=abcd-efgh-ijkl-mnop\n")
    return fleet_env


def test_stage_holds_config_logs_and_exports_but_never_secrets(machine, tmp_path):
    dest = tmp_path / "stage"
    manifest = backup.stage(dest)
    assert "config/fleet.yaml" in manifest and "ops/publisher/posts.log" in manifest and "exports/profile-research.tar.gz" in manifest
    assert "ops/publisher/ratelimit.json" not in manifest
    assert not [p for p in dest.rglob("*") if p.is_file() and ("secrets" in p.parts or p.suffix == ".env")]
    assert "abcd-efgh" not in "".join(p.read_text(errors="ignore") for p in dest.rglob("*") if p.is_file())


def test_a_planted_secret_stops_the_backup(machine, tmp_path):
    dest = tmp_path / "stage"
    backup.stage(dest)
    assert backup.scan_for_secrets(dest) is None
    (dest / "briefs" / "latest.md").write_text('token = "ghp_%s"\n' % "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dE3fG5")
    assert backup.scan_for_secrets(dest) and "possible secret" in backup.scan_for_secrets(dest)


def test_archive_is_mode_600_with_a_checksum_and_rotation_keeps_the_newest(tmp_path):
    src = tmp_path / "s"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    out = tmp_path / "out"
    for i in range(1, 18):
        t = backup.archive(src, out, "agent-fleet-2026010%02d-000000.tar.gz" % i if i < 10 else "agent-fleet-202601%d-000000.tar.gz" % i)
    assert (t.stat().st_mode & 0o777) == 0o600 and t.with_name(t.name + ".sha256").exists()
    removed = backup.rotate(out, 14)
    assert len(removed) == 3 and len(list(out.glob("agent-fleet-*.tar.gz"))) == 14
    assert not any(p.name in removed for p in out.glob("*"))        # the checksum files go too
    with tarfile.open(t) as tar:
        assert "agent-fleet-backup/a.txt" in tar.getnames()


def test_push_is_off_without_a_remote_and_never_puts_the_token_in_argv(machine):
    cfg = config.defaults()
    assert backup.push_repo(cfg) == "off"
    cfg["backup"]["github_remote"] = "https://github.com/example/agent-fleet.git"
    secrets.set_value("BACKUP_GITHUB_TOKEN", "ghp_" + "a1B2c3D4e5" * 4)
    seen = {}

    class Done:
        returncode = 0

    def fake(argv, **kw):
        seen["argv"], seen["env"] = argv, kw["env"]
        return Done()
    assert backup.push_repo(cfg, run=fake) == "pushed"
    assert "ghp_" not in " ".join(seen["argv"]) and seen["env"]["FLEET_PUSH_TOKEN"].startswith("ghp_")
    cfg["backup"]["github_remote"] = "git@github.com:example/x.git"
    assert backup.push_repo(cfg, run=fake) == "needs an https remote"


def test_main_makes_an_archive(machine, tmp_path, monkeypatch):
    assert backup.main() == 0
    made = list((machine["ops"] / "backups").glob("agent-fleet-*.tar.gz"))
    assert len(made) == 1


# ---------------- the exporter that runs as fleet ----------------
def test_exporter_never_copies_env_or_auth(tmp_path):
    home = tmp_path / ".hermes"
    (home / "memories").mkdir(parents=True)
    (home / "memories" / "MEMORY.md").write_text("notes")
    (home / ".env").write_text("ROUTER_KEY_HERMES=secret-value-123456\n")
    (home / "auth.json").write_text('{"token": "secret"}')
    (home / "config.yaml").write_text("model: {}\n")
    out = tmp_path / "exports"
    name = export_fleet.export_default(home, out)
    with tarfile.open(out / name) as tar:
        names = tar.getnames()
    assert "default/memories/MEMORY.md" in names and "default/config.yaml" in names
    assert not any(n.endswith((".env", "auth.json")) for n in names)


def test_exporter_copies_kanban_databases_safely(tmp_path):
    home = tmp_path / ".hermes"
    (home / "kanban" / "boards" / "demo").mkdir(parents=True)
    for db in (home / "kanban.db", home / "kanban" / "boards" / "demo" / "kanban.db"):
        c = sqlite3.connect(db)
        c.execute("create table tasks(id text)")
        c.execute("insert into tasks values ('t1')")
        c.commit()
        c.close()
    out = tmp_path / "exports"
    assert export_fleet.export_kanban(home, out) == ["kanban-default.db", "kanban-demo.db"]
    assert sqlite3.connect(out / "kanban-demo.db").execute("select id from tasks").fetchone() == ("t1",)


def test_exporter_calls_hermes_profile_export_per_profile(tmp_path):
    home = tmp_path / ".hermes"
    for p in ("research", "build"):
        (home / "profiles" / p).mkdir(parents=True)
    calls = []

    class Done:
        returncode = 0

    def fake(argv, **kw):
        calls.append(argv)
        return Done()
    assert export_fleet.export_profiles(home, tmp_path / "o", run=fake) == ["profile-build.tar.gz", "profile-research.tar.gz"]
    assert calls[0][:4] == ["hermes", "profile", "export", "build"]


# ---- Prompt 3: the nightly backup and its push report to n8n ----
def test_a_good_backup_and_push_send_two_info_events():
    from ops.backup import main as bmain
    got = []
    bmain.report_events({"archive": "agent-fleet-1.tar.gz", "files": 40, "push": "pushed"}, emit=lambda *a, **k: got.append((a, k)))
    assert [g[0][0] for g in got] == ["backup.ok", "backup.push_ok"] and all("severity" not in g[1] for g in got)


def test_a_failed_push_is_a_warning_and_a_push_that_is_off_says_nothing():
    from ops.backup import main as bmain
    got = []
    bmain.report_events({"archive": "a.tar.gz", "files": 1, "push": "push failed"}, emit=lambda *a, **k: got.append((a, k)))
    assert got[1][0][0] == "backup.push_failed" and got[1][1]["severity"] == "warn"
    got.clear()
    bmain.report_events({"archive": "a.tar.gz", "files": 1, "push": "off"}, emit=lambda *a, **k: got.append((a, k)))
    assert [g[0][0] for g in got] == ["backup.ok"]
