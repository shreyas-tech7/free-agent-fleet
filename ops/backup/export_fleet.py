"""Export agent data for the nightly backup. Run by a timer as the user `fleet`.

The backup runs as fleetops. It cannot read the private home of fleet. This script copies what the
backup needs into /var/lib/agent-fleet/exports. It never copies a secret:
  - `hermes profile export` strips credentials by design.
  - For the default profile it copies only memories, skills, cron, SOUL.md, and config.yaml.
    It never copies .env, auth.json, or the pairing folder.
  - Kanban databases are copied with the SQLite backup API, so a running database is safe.
"""
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tarfile

HOME = pathlib.Path.home() / ".hermes"
OUT = pathlib.Path("/var/lib/agent-fleet/exports")
SAFE_DEFAULT = ("memories", "skills", "cron", "SOUL.md", "config.yaml", "profile.yaml")


def export_profiles(hermes_home=HOME, out=OUT, run=subprocess.run):
    out.mkdir(parents=True, exist_ok=True)
    done = []
    for prof in sorted((hermes_home / "profiles").glob("*")):
        if prof.is_dir():
            target = out / ("profile-%s.tar.gz" % prof.name)
            proc = run(["hermes", "profile", "export", prof.name, "-o", str(target)], capture_output=True, timeout=300)
            if proc.returncode == 0:
                done.append(target.name)
    return done


def export_default(hermes_home=HOME, out=OUT):
    out.mkdir(parents=True, exist_ok=True)
    target = out / "profile-default.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for name in SAFE_DEFAULT:
            p = hermes_home / name
            if p.exists():
                tar.add(p, arcname="default/" + name)
    return target.name


def export_kanban(hermes_home=HOME, out=OUT):
    out.mkdir(parents=True, exist_ok=True)
    done = []
    dbs = [hermes_home / "kanban.db"] + sorted((hermes_home / "kanban" / "boards").glob("*/kanban.db"))
    for db in dbs:
        if not db.exists():
            continue
        name = "kanban-default.db" if db.parent == hermes_home else "kanban-%s.db" % db.parent.name
        src = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
        dst = sqlite3.connect(str(out / name))
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        done.append(name)
    return done


def main():
    done = export_profiles() + [export_default()] + export_kanban()
    for p in OUT.glob("*"):
        p.chmod(0o644)
    print("exported: " + ", ".join(done))
    return 0


if __name__ == "__main__":
    sys.exit(main())
