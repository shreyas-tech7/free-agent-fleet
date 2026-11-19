"""Run the gates in a throwaway rootless container. Root starts it, but the container runs as fleetops.

The container has no network, a read-only root file system, no capabilities, and limited memory.
The candidate code is mounted read-only. The hidden tests exist in the run folder for this run only.
The merger deletes the run folder at the end, whatever the result.
"""
import json
import os
import pathlib
import pwd
import shutil
import subprocess
import time
import uuid

GATE_IMAGE_FILE = "/opt/agent-fleet/gate-image"
OSV_MAX_AGE = 24 * 3600


class GateError(Exception):
    pass


def ops_command(argv, user="fleetops"):
    """Wrap a docker command so that it runs as the user fleetops with the rootless daemon."""
    if os.geteuid() != 0:
        return list(argv)
    info = pwd.getpwnam(user)
    return ["runuser", "-u", user, "--", "env", "HOME=" + info.pw_dir, "XDG_RUNTIME_DIR=/run/user/%d" % info.pw_uid,
            "DOCKER_HOST=unix:///run/user/%d/docker.sock" % info.pw_uid, "PATH=/usr/local/bin:/usr/bin:/bin"] + list(argv)


def pinned_image(runner=subprocess.run, image_file=GATE_IMAGE_FILE):
    """The gate image ID from the root-owned file. It must match what the daemon holds."""
    try:
        tag, want = pathlib.Path(image_file).read_text().split()
    except (OSError, ValueError):
        raise GateError("the gate image file %s is missing. Run install/55-gate.sh." % image_file)
    proc = runner(ops_command(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]), capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or proc.stdout.strip() != want:
        raise GateError("the gate image %s is missing or changed" % tag)
    return want


def docker_argv(image_id, run_dir, name, rules_dir, osv_dir, memory="2g", cpus="2"):
    run_dir = str(run_dir)
    return ["docker", "run", "--rm", "--name", name, "--network", "none", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/tmp:rw,exec,size=768m,mode=1777", "--memory", memory, "--memory-swap", memory, "--cpus", cpus,
            "--pids-limit", "512", "--user", "65534:65534", "-e", "HOME=/tmp",
            "-v", run_dir + "/src:/src:ro", "-v", run_dir + "/hidden:/hidden:ro", "-v", run_dir + "/gate:/gate:ro",
            "-v", "%s:/rules:ro" % rules_dir, "-v", "%s:/osv:ro" % osv_dir,
            image_id, "/opt/test/bin/python", "/gate/gate_entry.py", "/gate/job.json"]


def _chown_tree(path, uid, gid):
    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            os.lchown(os.path.join(root, name), uid, gid)
    os.lchown(path, uid, gid)


def prepare_run(run_dir, tree_exporter, hidden_dir, entry_file, job, owner="fleetops"):
    """Fill the run folder. tree_exporter(dest) writes the candidate tree."""
    run_dir = pathlib.Path(run_dir)
    for sub in ("src", "hidden", "gate"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    tree_exporter(run_dir / "src")
    hidden = pathlib.Path(hidden_dir) if hidden_dir else None        # None: this run gets no hidden tests
    if hidden is not None and hidden.is_dir():
        for f in hidden.glob("*.py"):                       # the seed file stays behind
            shutil.copyfile(f, run_dir / "hidden" / f.name)
            os.chmod(run_dir / "hidden" / f.name, 0o444)
    shutil.copyfile(entry_file, run_dir / "gate" / "gate_entry.py")
    (run_dir / "gate" / "job.json").write_text(json.dumps(job))
    for f in (run_dir / "gate").iterdir():
        os.chmod(f, 0o444)
    for d in (run_dir, run_dir / "src", run_dir / "hidden", run_dir / "gate"):
        os.chmod(d, 0o755)
    if os.geteuid() == 0:
        info = pwd.getpwnam(owner)
        _chown_tree(str(run_dir), info.pw_uid, info.pw_gid)


def run_gates(run_dir, image_id, rules_dir, osv_dir, timeout=2400, runner=subprocess.run, memory="2g", cpus="2"):
    """Start the container and read the result line. Returns the result dict."""
    name = "agent-fleet-gate-" + uuid.uuid4().hex[:10]
    cmd = ops_command(docker_argv(image_id, run_dir, name, rules_dir, osv_dir, memory, cpus))
    try:
        proc = runner(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        runner(ops_command(["docker", "kill", name]), capture_output=True, text=True, timeout=60)
        raise GateError("the gate container ran longer than %d seconds" % timeout)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("GATE_RESULT "):
            return json.loads(line[len("GATE_RESULT "):])
    raise GateError("the gate container gave no result (exit %d): %s" % (proc.returncode, (proc.stderr or proc.stdout)[-300:]))


def cleanup(run_dir):
    shutil.rmtree(run_dir, ignore_errors=True)


TRUSTED_GATES = ("G1", "G2", "G4")


def changed_projects(changed):
    """The project folders that a change touches. Their tests are agent code and run in the second phase."""
    from ops.merger import gate_entry
    return gate_entry.project_dirs(changed)


def trusted_passed(result):
    return all(bool((result.get(g) or {}).get("ok")) for g in TRUSTED_GATES)


def merge_phases(trusted, projects):
    """Join the two phases into one result. Only the key "P" is read from the second phase.

    The second container runs agent code. Whatever else it prints is ignored, so it cannot change G1, G2, or G4.
    A missing or failed "P" fails G1, because the tests of a changed project are part of G1.
    """
    out = {g: dict(trusted.get(g) or {"ok": False, "detail": "no result"}) for g in TRUSTED_GATES}
    out["seconds"] = trusted.get("seconds", 0)
    if projects is None:
        return out
    part = projects.get("P") if isinstance(projects, dict) else None
    ok = isinstance(part, dict) and part.get("ok") is True
    detail = str(part.get("detail", ""))[:1200] if isinstance(part, dict) else "the project test run gave no result"
    out["G1"]["ok"] = bool(out["G1"].get("ok")) and ok
    out["G1"]["detail"] = ("%s; project tests: %s" % (out["G1"].get("detail", ""), detail))[:2500]
    if isinstance(projects, dict) and isinstance(projects.get("seconds"), (int, float)):
        out["seconds"] += projects["seconds"]
    return out


def make_readable(osv_dir):
    """The gate container runs as nobody. It must read the vulnerability databases, which are public data.

    Something can set the top folder back to mode 750 after a refresh. Then the next gate says that the databases are
    missing. So this runs on every call and not only after a download. See D-044.
    """
    osv_dir = pathlib.Path(osv_dir)
    for p in [osv_dir] + list(osv_dir.rglob("*")):
        os.chmod(p, 0o755 if p.is_dir() else 0o644)


def refresh_osv(osv_dir, runner=subprocess.run, now=time.time, max_age=OSV_MAX_AGE):
    """Download the vulnerability databases as fleetops, with network, outside any container. At most once a day."""
    osv_dir = pathlib.Path(osv_dir)
    stamp = osv_dir / ".refreshed"
    if stamp.exists() and now() - stamp.stat().st_mtime < max_age:
        make_readable(osv_dir)
        return "fresh"
    osv_dir.mkdir(parents=True, exist_ok=True)
    tmp = osv_dir / ".seed"
    tmp.mkdir(exist_ok=True)
    (tmp / "requirements.txt").write_text("requests==2.32.4\n")
    (tmp / "package-lock.json").write_text(json.dumps({"name": "seed", "lockfileVersion": 3, "packages": {"": {"name": "seed"}, "node_modules/lodash": {"version": "4.17.21"}}}))
    if os.geteuid() == 0:
        info = pwd.getpwnam("fleetops")
        _chown_tree(str(osv_dir), info.pw_uid, info.pw_gid)
    cmd = ops_command(["env", "OSV_SCANNER_LOCAL_DB_CACHE_DIRECTORY=" + str(osv_dir), "osv-scanner", "scan", "source", "--offline-vulnerabilities",
                       "--download-offline-databases", "--no-resolve", "-L", str(tmp / "requirements.txt"), "-L", str(tmp / "package-lock.json")])
    proc = runner(cmd, capture_output=True, text=True, timeout=900)
    if not any(osv_dir.rglob("all.zip")):
        return "failed: " + (proc.stderr or proc.stdout)[-200:]
    make_readable(osv_dir)                                               # public data. The container user must read it.
    stamp.write_text(str(int(now())))
    return "refreshed"
