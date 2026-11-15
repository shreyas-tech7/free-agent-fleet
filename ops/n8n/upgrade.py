"""fleetctl n8n upgrade VERSION. A careful move to a newer n8n 2.x release. It never runs by itself.

 1. The version must be 2.x and newer than the running one. Never next, beta, or 3.x. n8n 3.0 is a one-way move.
 2. The release must be 3 days old or more, unless the owner passes --allow-young for a critical advisory.
 3. Make a backup. Pull the image by digest. Check its labels.
 4. Switch the pin in the compose .env, restart n8n, and run the smoke checks.
 5. If a check fails, stop n8n, put the old database and the old .env back, start n8n, and report the rollback.
A restart between 06:40 and 07:10 is refused, so the brief stays safe. See D-036 and D-040.
"""
import datetime
import json
import os
import re
import shutil
import sys
import time

from ops.fleetctl import system
from ops.fleetlib import config, events, httpc, jsonio, log, paths
from ops.n8n import backup, prepare

LOG = log.get("n8n-upgrade")
REPO = "docker.n8n.io/n8nio/n8n"
GITHUB = "https://api.github.com/repos/n8n-io/n8n"
VERSION_RE = re.compile(r"^2\.([0-9]{1,3})\.([0-9]{1,3})$")
MIN_AGE_DAYS = 3


class UpgradeError(Exception):
    pass


def vtuple(version):
    m = VERSION_RE.match(version)
    if not m:
        raise UpgradeError("the version must look like 2.47.6. Never next, beta, or 3.x.")
    return (2, int(m.group(1)), int(m.group(2)))


def get_json(url, request=httpc.request):
    try:
        resp = request("GET", url, headers={"Accept": "application/vnd.github+json"}, timeout=25, max_bytes=8_000_000)
        return resp.json() if resp.status == 200 else None
    except (httpc.TransportError, ValueError):
        return None


def release_age_days(version, request=httpc.request, now=time.time):
    """Days since n8n published this release, or raise. A pre-release is refused."""
    info = get_json("%s/releases/tags/n8n@%s" % (GITHUB, version), request)
    if not info or not info.get("published_at"):
        raise UpgradeError("cannot read the release date of n8n %s from GitHub" % version)
    if info.get("prerelease") or info.get("draft"):
        raise UpgradeError("n8n %s is a pre-release" % version)
    published = datetime.datetime.fromisoformat(info["published_at"].replace("Z", "+00:00")).timestamp()
    return (now() - published) / 86400.0


def resolve_ref(version, runner=system.run):
    """The image reference with its digest, from the registry. Pinning by digest means a moved tag changes nothing."""
    rc, out = runner(["docker", "buildx", "imagetools", "inspect", "%s:%s" % (REPO, version)], timeout=120, user="fleetops")
    m = re.search(r"^Digest:\s+(sha256:[0-9a-f]{64})", out, re.M)
    if rc != 0 or not m:
        raise UpgradeError("cannot read the image digest for %s:%s" % (REPO, version))
    return "%s:%s@%s" % (REPO, version, m.group(1))


def image_label(ref, runner=system.run):
    rc, out = runner(["docker", "image", "inspect", ref, "--format", '{{index .Config.Labels "org.opencontainers.image.version"}}|{{.Config.Env}}'],
                     timeout=60, user="fleetops")
    version, _, env = out.strip().partition("|")
    return version, "N8N_RELEASE_TYPE=stable" in env


def set_env_pin(env_path, ref, version):
    text = env_path.read_text()
    text = re.sub(r"(?m)^N8N_IMAGE=.*$", "N8N_IMAGE=" + ref, text)
    text = re.sub(r"(?m)^N8N_VERSION=.*$", "N8N_VERSION=" + version, text)
    owner = "fleetops" if os.geteuid() == 0 else None
    jsonio.atomic_write(env_path, text, mode=0o600, owner=owner, group=owner)


def restart_and_wait(runner, client, seconds=300):
    rc, out = runner(["systemctl", "restart", "agent-fleet-n8n.service"], timeout=240)
    return rc == 0 and client.wait_ready(seconds)


def rollback(runner, client, env_path, old_env_text, backup_dir, now):
    """Put the old database and the old .env back. The failed database is kept in the backups folder, not deleted."""
    runner(["systemctl", "stop", "agent-fleet-n8n.service"], timeout=180)
    data = prepare.run_dir() / "data"
    failed = backup.backups_dir() / ("failed-upgrade-%s" % datetime.datetime.fromtimestamp(now()).strftime("%Y%m%d-%H%M%S"))
    failed.mkdir(parents=True, exist_ok=True)
    for name in ("database.sqlite", "database.sqlite-wal", "database.sqlite-shm"):
        if (data / name).exists():
            shutil.move(str(data / name), str(failed / name))
    shutil.copy2(backup_dir / "database.sqlite", data / "database.sqlite")
    if os.geteuid() == 0:
        shutil.chown(data / "database.sqlite", "fleetops", "fleetops")
    os.chmod(data / "database.sqlite", 0o600)
    owner = "fleetops" if os.geteuid() == 0 else None
    jsonio.atomic_write(env_path, old_env_text, mode=0o600, owner=owner, group=owner)
    return restart_and_wait(runner, client)


def run_upgrade(version, allow_young=False, runner=system.run, request=httpc.request, now=time.time, client=None, smoke_fn=None,
                backup_fn=None, brief_window=lambda: False, emit=events.emit):
    from ops.n8n import client as nclient
    from ops.n8n import smoke
    new = vtuple(version)
    env_path = prepare.run_dir() / ".env"
    env = prepare.read_env_file(env_path)
    running = env.get("N8N_VERSION", "")
    if running and vtuple(running) >= new:
        raise UpgradeError("n8n %s is not newer than the running %s. A downgrade needs a restore." % (version, running))
    if brief_window():
        raise UpgradeError("a restart between 06:40 and 07:10 could meet the daily brief. Try again after 07:10.")
    age = release_age_days(version, request, now)
    if age < MIN_AGE_DAYS and not allow_young:
        raise UpgradeError("n8n %s is %.1f days old. The install gate wants %d days. Use --allow-young only for a critical advisory." % (version, age, MIN_AGE_DAYS))
    ref = resolve_ref(version, runner)
    rc, out = runner(["docker", "pull", "--quiet", ref], timeout=1200, user="fleetops")
    if rc != 0:
        raise UpgradeError("docker pull failed: %s" % out[-120:])
    label, stable = image_label(ref, runner)
    if label != version or not stable:
        raise UpgradeError("the image says version %s and stable %s. It does not match n8n %s." % (label or "?", stable, version))
    cfg = config.load()
    c = client or nclient.Client("http://127.0.0.1:%d" % config.port(cfg, "n8n"))
    made = (backup_fn or backup.run_backup)()
    backup_dir = backup.backups_dir() / made["backup"]
    old_env = env_path.read_text()
    set_env_pin(env_path, ref, version)
    ok = restart_and_wait(runner, c)
    results = (smoke_fn or (lambda: smoke.run_all(cfg, runner)))() if ok else [("restart", False, "n8n did not come back")]
    failed = [r for r in results if not r[1]]
    if not failed:
        emit("n8n.upgraded", "n8n now runs %s (was %s). Update vendor/pins.env through the merger." % (version, running or "?"), source="n8n-upgrade",
             fields={"from": running, "to": version})
        return {"status": "upgraded", "from": running, "to": version, "backup": made["backup"], "image": ref}
    back = rollback(runner, c, env_path, old_env, backup_dir, now)
    emit("n8n.upgrade_failed", "The n8n upgrade to %s failed (%s). Rolled back to %s%s." % (version, "; ".join(r[0] for r in failed)[:120], running or "?", "" if back else ". The old version did not come back"),
         severity="critical" if not back else "warn", source="n8n-upgrade")
    return {"status": "rolled_back" if back else "rollback_failed", "failed": [r[0] for r in failed], "backup": made["backup"], "from": running, "to": version}
