"""The nightly n8n backup, and the restore test. A timer runs the backup as fleetops. `fleetctl n8n backup` runs it by hand.

A backup is one folder, /var/lib/agent-fleet-ops/n8n/backups/n8n-YYYYmmdd-HHMMSS (mode 700):
  workflows/<id>.json     every workflow as JSON. It holds no secret.
  credentials/<id>.json   every credential, ENCRYPTED with N8N_ENCRYPTION_KEY. Never put these in git.
  database.sqlite         a safe online copy of the database (the SQLite backup command)
  MANIFEST.json           the SHA-256 of each file, the n8n version, and a short fingerprint of the key that they need
The newest 7 backups stay. The key itself is never in a backup. Without the key the credentials cannot be read. See D-040.

The restore test starts a throwaway n8n on ports 5688 and 5689 from the newest backup. It uses the same key.
It checks that the workflows and the credentials load, and then removes the container and its folder.
"""
import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import re
import shutil
import sys
import time

from ops.fleetctl import system
from ops.fleetlib import config, events, jsonio, log, paths, secrets
from ops.n8n import client as nclient
from ops.n8n import prepare

LOG = log.get("n8n-backup")
CONTAINER = "agent-fleet-n8n"
TEST_CONTAINER = "agent-fleet-n8n-restore-test"
TEST_PORTS = (5688, 5689)
NAME_RE = re.compile(r"^n8n-[0-9]{8}-[0-9]{6}$")
KEEP = 7


class BackupError(Exception):
    pass


def backups_dir():
    return prepare.run_dir() / "backups"


def key_fingerprint():
    key = secrets.value_of("N8N_ENCRYPTION_KEY")
    return hashlib.sha256(("n8n-key:" + key).encode()).hexdigest()[:12] if key else ""


def list_backups():
    base = backups_dir()
    return sorted(p for p in base.iterdir() if p.is_dir() and NAME_RE.match(p.name)) if base.exists() else []


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def give_to_fleetops(*targets):
    """A backup that root starts by hand must belong to fleetops, as the nightly run makes it.

    The n8n container runs as the container root, which is fleetops on the host. It cannot enter a folder that root made.
    The sqlite3 copy also runs as fleetops. And the nightly run must be able to remove an old backup. See D-053.
    """
    if os.geteuid() != 0:
        return
    for target in targets:
        shutil.chown(target, "fleetops", "fleetops")


def run_backup(runner=system.run, now=time.time, keep=KEEP, emit=events.emit):
    """Make one backup. Returns a report. A failure is reported as an event and as BackupError."""
    stamp = datetime.datetime.fromtimestamp(now()).strftime("%Y%m%d-%H%M%S")
    dest = backups_dir() / ("n8n-" + stamp)
    tmp = prepare.run_dir() / "files" / "backup-tmp"
    old_umask = os.umask(0o077)
    try:
        dest.mkdir(parents=True)
        give_to_fleetops(dest)
        for sub in ("workflows", "credentials"):
            (tmp / sub).mkdir(parents=True, exist_ok=True)
        give_to_fleetops(tmp, tmp / "workflows", tmp / "credentials")
        for kind, folder in (("workflow", "workflows"), ("credentials", "credentials")):
            rc, out = runner(["docker", "exec", CONTAINER, "n8n", "export:" + kind, "--all", "--separate",
                              "--output=/home/node/files/backup-tmp/%s/" % folder], timeout=300, user="fleetops")
            if rc != 0:
                raise BackupError("the %s export failed: %s" % (kind, out[-120:]))
        for sub in ("workflows", "credentials"):
            (dest / sub).mkdir()
            for f in sorted((tmp / sub).glob("*.json")):
                shutil.copy2(f, dest / sub / f.name)
        db = prepare.run_dir() / "data" / "database.sqlite"
        rc, out = runner(["sqlite3", str(db), ".backup '%s'" % (dest / "database.sqlite")], timeout=300, user="fleetops")
        if rc != 0 or not (dest / "database.sqlite").exists():
            raise BackupError("the database copy failed: %s" % out[-120:])
        files = sorted(p for p in dest.rglob("*") if p.is_file())
        env = prepare.read_env_file(prepare.run_dir() / ".env")
        manifest = {"made": int(now()), "n8n_version": env.get("N8N_VERSION", "?"), "key_fingerprint": key_fingerprint(),
                    "files": {str(p.relative_to(dest)): _sha(p) for p in files}}
        (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        for p in dest.rglob("*"):
            os.chmod(p, 0o700 if p.is_dir() else 0o600)
        give_to_fleetops(*sorted(dest.rglob("*")))
    except (BackupError, OSError) as exc:
        emit("backup.n8n_failed", "The n8n backup failed: %s" % str(exc)[:200], severity="warn", source="n8n-backup")
        shutil.rmtree(dest, ignore_errors=True)
        raise BackupError(str(exc)) from None
    finally:
        os.umask(old_umask)
        shutil.rmtree(tmp, ignore_errors=True)
    removed = []
    for old in list_backups()[:-keep] if keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old.name)
    n_wf = len(list((dest / "workflows").glob("*.json")))
    n_cred = len(list((dest / "credentials").glob("*.json")))
    emit("backup.n8n_ok", "The n8n backup is done: %d workflows and %d credentials." % (n_wf, n_cred), source="n8n-backup",
         fields={"workflows": n_wf, "credentials": n_cred, "kept": len(list_backups())})
    return {"backup": dest.name, "workflows": n_wf, "credentials": n_cred, "removed": removed}


# ------------------------------------------------------------------------------------------------ restore test
# What the test copy sets for itself. Everything else in the `environment:` block of the compose file is copied, so the copy runs with the same
# file limits, blocked nodes, and runner mode as the live n8n. A copy without them hid a failure of the file-write fallback. See D-062.
OWN_SETTINGS = {"N8N_PORT", "N8N_RUNNERS_BROKER_PORT", "N8N_LISTEN_ADDRESS", "N8N_EDITOR_BASE_URL", "WEBHOOK_URL", "N8N_PROXY_HOPS",
                "N8N_INSTANCE_OWNER_EMAIL", "N8N_INSTANCE_OWNER_FIRST_NAME", "N8N_INSTANCE_OWNER_LAST_NAME", "N8N_CONCURRENCY_PRODUCTION_LIMIT"}


def compose_environment(compose=None):
    """The fixed values of the `environment:` block of the live compose file. A value with a ${VARIABLE} in it is left out."""
    import yaml
    path = pathlib.Path(compose or (paths.repo_root() / "n8n" / "docker-compose.yml"))
    block = ((yaml.safe_load(path.read_text()).get("services") or {}).get("n8n") or {}).get("environment") or {}
    return {str(k): str(v) for k, v in block.items() if "${" not in str(v) and str(k) not in OWN_SETTINGS}


def _test_docker_args(image, data_dir, files_dir, env, compose=None):
    copied = []
    for key, value in sorted(compose_environment(compose).items()):
        copied += ["-e", "%s=%s" % (key, value)]
    return ["docker", "run", "-d", "--rm", "--name", TEST_CONTAINER, "--network", "host", "--user", "0:0", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--read-only", "--tmpfs", "/tmp:rw,size=128m", "--tmpfs", "/home/node/.cache:rw,size=128m",
            "--memory", "1g", "--env-file", prepare.N8N_SECRETS_FILE,
            "-v", "%s:/home/node/.n8n" % data_dir, "-v", "%s:/home/node/files" % files_dir] + copied + [
            "-e", "N8N_PORT=%d" % TEST_PORTS[0], "-e", "N8N_LISTEN_ADDRESS=127.0.0.1", "-e", "N8N_RUNNERS_BROKER_PORT=%d" % TEST_PORTS[1],
            "-e", "N8N_INSTANCE_OWNER_EMAIL=%s" % env.get("N8N_OWNER_EMAIL", ""), "-e", "N8N_INSTANCE_OWNER_FIRST_NAME=%s" % env.get("N8N_OWNER_FIRST_NAME", "Owner"),
            "-e", "N8N_INSTANCE_OWNER_LAST_NAME=%s" % env.get("N8N_OWNER_LAST_NAME", "Account"), image]


class Throwaway:
    """A throwaway n8n that started from the newest backup. The restore test and the selftest use it."""

    def __init__(self, client, base, files, data, env, backup, runner):
        self.client, self.base, self.files, self.data, self.env, self.backup, self.runner = client, base, files, data, env, backup, runner


@contextlib.contextmanager
def throwaway(runner=system.run, client_factory=None, ready_seconds=240):
    """Start a throwaway n8n from the newest backup, with the same key. It listens on the test ports.

    Yields a Throwaway with a client that is logged in as the owner. The container and its folder are always removed.
    The live n8n is never changed. The files folder gets the same subfolders as the live one, because n8n does not make them.
    """
    backups = list_backups()
    if not backups:
        raise BackupError("there is no backup yet. Run: fleetctl n8n backup")
    newest = backups[-1]
    manifest = json.loads((newest / "MANIFEST.json").read_text())
    if manifest.get("key_fingerprint") and manifest["key_fingerprint"] != key_fingerprint():
        raise BackupError("this backup needs another encryption key than the one on this machine")
    for port in TEST_PORTS:
        if prepare.port_in_use(port):
            raise BackupError("port %d is in use. Stop the other test first." % port)
    env = prepare.read_env_file(prepare.run_dir() / ".env")
    work = prepare.run_dir() / "restore-test"
    shutil.rmtree(work, ignore_errors=True)
    data, files = work / "data", work / "files"
    folders = [work, data, files] + [files / sub for sub in ("heartbeat", "ledger", "unsent")]
    for d in folders:
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        if os.geteuid() == 0:
            shutil.chown(d, "fleetops", "fleetops")
    shutil.copy2(newest / "database.sqlite", data / "database.sqlite")
    if os.geteuid() == 0:
        shutil.chown(data / "database.sqlite", "fleetops", "fleetops")
    os.chmod(data / "database.sqlite", 0o600)
    started = False
    try:
        rc, out = runner(_test_docker_args(env["N8N_IMAGE"], data, files, env), timeout=300, user="fleetops")
        if rc != 0:
            raise BackupError("the test container did not start: %s" % out[-120:])
        started = True
        base = "http://127.0.0.1:%d" % TEST_PORTS[0]
        c = (client_factory or nclient.Client)(base)
        if not c.wait_ready(ready_seconds):
            raise BackupError("the restored n8n did not become ready")
        c.login(env["N8N_OWNER_EMAIL"], secrets.value_of("N8N_OWNER_PASSWORD"))
        yield Throwaway(c, base, files, data, env, newest, runner)
    finally:
        if started:
            runner(["docker", "stop", "-t", "10", TEST_CONTAINER], timeout=60, user="fleetops")
        shutil.rmtree(work, ignore_errors=True)


def rows_of(payload):
    """The rows of a list reply of the editor API. n8n 2.47 answers {"count": n, "data": [rows]}. Older shapes nest it once more. See D-053."""
    body = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(body, dict):
        body = body.get("data", [])
    return body if isinstance(body, list) else []


def restore_test(runner=system.run, client_factory=None, ready_seconds=240, sleep=time.sleep):
    """Start a throwaway n8n from the newest backup. Check workflows and credentials. Always clean up. Returns a report."""
    report = {"workflows": 0, "active": 0, "credentials": 0, "decrypts": False}
    with throwaway(runner, client_factory, ready_seconds) as t:
        newest, c = t.backup, t.client
        report["backup"] = newest.name
        status, data_ = c.call("GET", "/rest/workflows?take=250")
        flows = rows_of(data_) if status == 200 else []
        report["workflows"] = len(flows)
        report["active"] = sum(1 for w in flows if isinstance(w, dict) and (w.get("active") or w.get("activeVersionId")))
        status, data_ = c.call("GET", "/rest/credentials")
        report["credentials"] = len(rows_of(data_)) if status == 200 else 0
        rc, out = runner(["docker", "exec", TEST_CONTAINER, "sh", "-c",
                          "n8n export:credentials --all --decrypted --output=/tmp/c.json >/dev/null 2>&1 && "
                          "node -e \"console.log(JSON.parse(require('fs').readFileSync('/tmp/c.json','utf8')).length)\""], timeout=180, user="fleetops")
        report["decrypts"] = rc == 0 and out.strip().isdigit() and int(out.strip()) == report["credentials"]
    expected = len(list((newest / "workflows").glob("*.json")))
    report["expected_workflows"] = expected
    report["ok"] = report["workflows"] >= expected > 0 and report["decrypts"] and report["credentials"] > 0
    return report


def main(argv=None):
    try:
        report = run_backup()
    except BackupError as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}))
        return 1
    report["status"] = "ok"
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
