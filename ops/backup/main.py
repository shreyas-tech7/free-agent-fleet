"""The nightly backup. Run by a timer as fleetops.

What goes in: the live config, a git bundle of the live repository (code, skills, projects, docs),
the exports of agent memory and Kanban data, the briefs, the tickets, and the ops logs.
What stays out: every secret file, router data (it holds provider keys), and the Docker data.
Before it writes the archive it scans the staged files with gitleaks. A finding stops the backup.
It keeps the newest N archives (default 14). When the owner adds a GitHub remote, it pushes the repo.
"""
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile

from ops.fleetlib import config, events, log, paths, secrets

LOG = log.get("backup")
EXCLUDE_NAMES = {"secrets", ".env", "auth.json", "router-data"}


def _copy(src, dst):
    src, dst = pathlib.Path(src), pathlib.Path(dst)
    if not src.exists():
        return 0
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*EXCLUDE_NAMES, "*.env", "__pycache__"))
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return 1


def stage(dest, run=subprocess.run):
    dest = pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    state, ops = paths.state_dir(), paths.ops_state_dir()
    _copy(paths.config_file(), dest / "config" / "fleet.yaml")
    live = paths.live_repo()
    if (live / ".git").exists():
        run(["git", "-c", "safe.directory=" + str(live), "-C", str(live), "bundle", "create", str(dest / "repo.bundle"), "--all"], capture_output=True, timeout=300)
    _copy(state / "exports", dest / "exports")
    _copy(state / "briefs", dest / "briefs")
    _copy(state / "tickets", dest / "tickets")
    for sub in ("publisher", "deployer", "merger", "quota"):
        for f in (ops / sub).glob("*.log") if (ops / sub).exists() else []:
            _copy(f, dest / "ops" / sub / f.name)
        for f in (ops / sub).glob("*.json") if (ops / sub).exists() else []:
            if f.name not in ("applied.json", "ratelimit.json"):
                _copy(f, dest / "ops" / sub / f.name)
    manifest = {}
    for p in sorted(dest.rglob("*")):
        if p.is_file():
            manifest[str(p.relative_to(dest))] = hashlib.sha256(p.read_bytes()).hexdigest()
    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def scan_for_secrets(folder, run=subprocess.run):
    """Return None when clean, or a short text when gitleaks found something."""
    exe = shutil.which("gitleaks")
    if not exe:
        return "gitleaks is not installed"
    proc = run([exe, "detect", "--source", str(folder), "--no-git", "--no-banner", "--redact"], capture_output=True, text=True, timeout=300)
    if proc.returncode == 0:
        return None
    return "gitleaks found a possible secret in the staged files"


def archive(folder, out_dir, name):
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    old = os.umask(0o077)
    try:
        with tarfile.open(target, "w:gz") as tar:
            tar.add(folder, arcname="agent-fleet-backup")
    finally:
        os.umask(old)
    os.chmod(target, 0o600)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (out_dir / (name + ".sha256")).write_text("%s  %s\n" % (digest, name))
    return target


def rotate(out_dir, keep):
    archives = sorted(pathlib.Path(out_dir).glob("agent-fleet-*.tar.gz"))
    removed = []
    for old in archives[:max(0, len(archives) - keep)]:
        old.unlink()
        sha = old.with_name(old.name + ".sha256")
        if sha.exists():
            sha.unlink()
        removed.append(old.name)
    return removed


def push_repo(cfg, run=subprocess.run):
    """Push the live repo to the owner's GitHub remote. The token goes in through GIT_ASKPASS, never in argv."""
    remote = config.get(cfg, "backup.github_remote", "")
    token = secrets.value_of("BACKUP_GITHUB_TOKEN")
    if not remote or not token:
        return "off"
    if not remote.startswith("https://"):
        return "needs an https remote"
    with tempfile.TemporaryDirectory() as tmp:
        askpass = pathlib.Path(tmp) / "askpass.sh"
        askpass.write_text('#!/bin/sh\ncase "$1" in\n  Username*) echo x-access-token ;;\n  *) echo "$FLEET_PUSH_TOKEN" ;;\nesac\n')
        askpass.chmod(0o700)
        env = {"PATH": "/usr/bin:/bin", "HOME": tmp, "GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0", "FLEET_PUSH_TOKEN": token}
        proc = run(["git", "-c", "safe.directory=" + str(paths.live_repo()), "-C", str(paths.live_repo()), "push", "--follow-tags", remote, "main"], capture_output=True, text=True, env=env, timeout=300)
    return "pushed" if proc.returncode == 0 else "push failed"


def main(argv=None):
    cfg = config.load()
    out_dir = paths.ops_state_dir() / "backups"
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(dir=str(paths.ops_state_dir())) as tmp:
        stage_dir = pathlib.Path(tmp) / "agent-fleet-backup"
        manifest = stage(stage_dir)
        problem = scan_for_secrets(stage_dir)
        if problem:
            LOG.error("backup_stopped", reason=problem)
            events.emit("backup.failed", "The nightly backup stopped: %s." % problem, severity="warn", source="backup")
            print(json.dumps({"status": "stopped", "reason": problem}))
            return 1
        target = archive(stage_dir, out_dir, "agent-fleet-%s.tar.gz" % stamp)
    removed = rotate(out_dir, int(config.get(cfg, "backup.keep", 14)))
    result = {"status": "ok", "archive": target.name, "files": len(manifest), "removed": removed, "push": push_repo(cfg)}
    LOG.info("backup", **{k: v for k, v in result.items() if k != "removed"})
    report_events(result)
    print(json.dumps(result))
    return 0


def report_events(result, emit=events.emit):
    """The nightly backup and its push tell n8n how they went. A failure is a warning. A push that is off says nothing."""
    emit("backup.ok", "The nightly backup %s is done (%d files)." % (result["archive"], result["files"]), source="backup", fields={"files": result["files"]})
    push = result.get("push")
    if push == "pushed":
        emit("backup.push_ok", "The repository was pushed to the private remote.", source="backup")
    elif push not in (None, "off"):
        emit("backup.push_failed", "The nightly push did not work: %s." % push, severity="warn", source="backup")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
