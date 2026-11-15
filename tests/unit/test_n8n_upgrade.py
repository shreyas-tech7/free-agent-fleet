import json
import os

import pytest

from ops.fleetlib import httpc, secrets
from ops.n8n import backup as bk
from ops.n8n import prepare as prep
from ops.n8n import upgrade as up
from tests.unit.fctl_helpers import Runner

NOW = 1_800_000_000.0
DIGEST = "sha256:" + "cd" * 32


def release(published_days_ago, prerelease=False):
    ts = NOW - published_days_ago * 86400
    import datetime
    iso = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"published_at": iso, "prerelease": prerelease, "draft": False}


def github(days=4.0, prerelease=False, status=200):
    def request(method, url, **kw):
        return httpc.Response(status, {}, json.dumps(release(days, prerelease)).encode())
    return request


class Docker(Runner):
    """Answers the digest, the pull, the label, and records restarts."""

    def __init__(self, label="2.47.6", stable=True, digest_ok=True, **kw):
        super().__init__(**kw)
        self.label, self.stable, self.digest_ok = label, stable, digest_ok

    def __call__(self, argv, timeout=60, user=None, extra_env=None, input_text=None):
        argv = list(argv)
        self.calls.append((argv, user))
        if argv[:3] == ["docker", "buildx", "imagetools"]:
            return (0, "Name: x\nDigest:    %s\n" % DIGEST) if self.digest_ok else (1, "no such image")
        if argv[:3] == ["docker", "image", "inspect"]:
            return 0, "%s|[PATH=/x %s]" % (self.label, "N8N_RELEASE_TYPE=stable" if self.stable else "N8N_RELEASE_TYPE=beta")
        return 0, ""


class Client:
    def __init__(self, ready=True):
        self.ready = ready

    def wait_ready(self, seconds=0):
        return self.ready


@pytest.fixture
def n8n(fleet_env):
    prep.ensure_dirs()
    (prep.run_dir() / ".env").write_text("N8N_IMAGE=docker.n8n.io/n8nio/n8n:2.47.4@sha256:" + "ab" * 32 + "\nN8N_VERSION=2.47.4\nN8N_OWNER_EMAIL=o@example.org\n")
    (prep.run_dir() / "data" / "database.sqlite").write_bytes(b"OLD-DB")
    secrets.set_value("N8N_ENCRYPTION_KEY", "k" * 44)
    return fleet_env


def fake_backup():
    folder = bk.backups_dir() / "n8n-20261115-020000"
    folder.mkdir(parents=True)
    (folder / "database.sqlite").write_bytes(b"BACKUP-DB")
    return {"backup": folder.name, "workflows": 1, "credentials": 1, "removed": []}


def go(version="2.47.6", runner=None, smoke=None, emitted=None, **kw):
    emitted = emitted if emitted is not None else []
    return up.run_upgrade(version, runner=runner or Docker(), request=kw.pop("request", github()), now=lambda: NOW, client=Client(),
                          smoke_fn=smoke or (lambda: [("healthz", True, "ok")]), backup_fn=fake_backup,
                          emit=lambda *a, **k: emitted.append((a, k)), **kw), emitted


@pytest.mark.parametrize("bad", ["next", "beta", "3.0.0", "3.2.2", "2.47", "2.47.4-rc1", "latest", "1.123.4", "", "2.x.y"])
def test_only_a_plain_2x_version_is_accepted(bad):
    with pytest.raises(up.UpgradeError):
        up.vtuple(bad)


def test_a_newer_release_is_installed_and_the_pin_moves(n8n):
    out, emitted = go()
    assert out["status"] == "upgraded" and out["from"] == "2.47.4" and out["to"] == "2.47.6" and out["image"].endswith("@" + DIGEST)
    env = prep.read_env_file(prep.run_dir() / ".env")
    assert env["N8N_VERSION"] == "2.47.6" and env["N8N_IMAGE"] == "docker.n8n.io/n8nio/n8n:2.47.6@" + DIGEST
    assert emitted[0][0][0] == "n8n.upgraded" and "vendor/pins.env" in emitted[0][0][1]
    assert (prep.run_dir() / "data" / "database.sqlite").read_bytes() == b"OLD-DB"          # a good upgrade keeps the live database


def test_the_order_is_backup_then_pin_then_restart_then_checks(n8n):
    order = []
    r = Docker()
    orig = r.__call__

    def spy(argv, **kw):
        if list(argv)[:2] == ["systemctl", "restart"]:
            order.append("restart")
        return orig(argv, **kw)
    out = up.run_upgrade("2.47.6", runner=spy, request=github(), now=lambda: NOW, client=Client(),
                         smoke_fn=lambda: order.append("smoke") or [("healthz", True, "ok")],
                         backup_fn=lambda: order.append("backup") or fake_backup(), emit=lambda *a, **k: None)
    assert order == ["backup", "restart", "smoke"] and out["status"] == "upgraded"


@pytest.mark.parametrize("running", ["2.47.4", "2.47.5"])
def test_the_same_or_an_older_version_is_refused(n8n, running):
    (prep.run_dir() / ".env").write_text("N8N_IMAGE=x\nN8N_VERSION=%s\n" % running)
    with pytest.raises(up.UpgradeError, match="not newer"):
        go(running if running == "2.47.4" else "2.47.4")


def test_a_release_younger_than_three_days_is_refused_unless_allowed(n8n):
    with pytest.raises(up.UpgradeError, match="1.8 days old"):
        go(request=github(days=1.8))
    out, _ = go(request=github(days=1.8), allow_young=True)
    assert out["status"] == "upgraded"


def test_a_pre_release_is_always_refused(n8n):
    with pytest.raises(up.UpgradeError, match="pre-release"):
        go(request=github(days=10, prerelease=True), allow_young=True)


def test_an_unreadable_release_date_stops_the_upgrade(n8n):
    with pytest.raises(up.UpgradeError, match="release date"):
        go(request=github(status=403))


def test_the_brief_window_blocks_the_restart(n8n):
    r = Docker()
    with pytest.raises(up.UpgradeError, match="06:40 and 07:10"):
        go(runner=r, brief_window=lambda: True)
    assert not r.ran("systemctl", "restart") and not r.ran("docker", "pull")


@pytest.mark.parametrize("kw", [dict(label="2.47.7"), dict(stable=False), dict(digest_ok=False)])
def test_an_image_that_does_not_match_is_never_used(n8n, kw):
    r = Docker(**kw)
    with pytest.raises(up.UpgradeError):
        go(runner=r)
    assert not r.ran("systemctl", "restart")
    assert prep.read_env_file(prep.run_dir() / ".env")["N8N_VERSION"] == "2.47.4"


def test_a_failed_check_rolls_back_the_database_the_pin_and_the_container(n8n):
    (prep.run_dir() / "data" / "database.sqlite").write_bytes(b"MIGRATED-BY-NEW-VERSION")
    (prep.run_dir() / "data" / "database.sqlite-wal").write_bytes(b"wal")
    r = Docker()
    out, emitted = go(runner=r, smoke=lambda: [("healthz", True, "ok"), ("workflows published", False, "missing: fleet-tools")])
    assert out["status"] == "rolled_back" and out["failed"] == ["workflows published"]
    assert (prep.run_dir() / "data" / "database.sqlite").read_bytes() == b"BACKUP-DB"
    assert not (prep.run_dir() / "data" / "database.sqlite-wal").exists()
    assert prep.read_env_file(prep.run_dir() / ".env")["N8N_VERSION"] == "2.47.4"
    kept = list(bk.backups_dir().glob("failed-upgrade-*"))
    assert len(kept) == 1 and (kept[0] / "database.sqlite").read_bytes() == b"MIGRATED-BY-NEW-VERSION"      # nothing is deleted
    assert r.ran("systemctl", "stop", "agent-fleet-n8n.service") and [c for c, _u in r.calls if c[:2] == ["systemctl", "restart"]].__len__() == 2
    assert emitted[0][0][0] == "n8n.upgrade_failed" and emitted[0][1]["severity"] == "warn"


def test_a_failed_start_is_a_failed_check_too(n8n):
    emitted = []
    out = up.run_upgrade("2.47.6", runner=Docker(), request=github(), now=lambda: NOW, client=Client(ready=False),
                         smoke_fn=lambda: pytest.fail("the checks must not run when n8n did not start"), backup_fn=fake_backup,
                         emit=lambda *a, **k: emitted.append((a, k)))
    assert out["status"] in ("rolled_back", "rollback_failed") and out["failed"] == ["restart"]
    assert emitted[0][0][0] == "n8n.upgrade_failed"


def test_if_the_old_version_does_not_come_back_the_alert_is_critical(n8n):
    class Flaky(Client):
        calls = 0

        def wait_ready(self, seconds=0):
            Flaky.calls += 1
            return Flaky.calls == 1                      # the new version starts, the old one does not
    emitted = []
    out = up.run_upgrade("2.47.6", runner=Docker(), request=github(), now=lambda: NOW, client=Flaky(),
                         smoke_fn=lambda: [("healthz", False, "bad")], backup_fn=fake_backup, emit=lambda *a, **k: emitted.append((a, k)))
    assert out["status"] == "rollback_failed" and emitted[0][1]["severity"] == "critical"
