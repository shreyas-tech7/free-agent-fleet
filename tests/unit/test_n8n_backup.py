import json
import os
import pathlib
import stat

import pytest

from ops.fleetlib import secrets
from ops.n8n import backup as bk
from ops.n8n import prepare as prep
from tests.unit.fctl_helpers import Runner

KEY = "k" * 44


class Docker:
    """A runner that acts like `docker exec` and `sqlite3`: it writes the files that the real commands would write."""

    def __init__(self, fail=None, workflows=3, credentials=2):
        self.calls, self.fail, self.workflows, self.credentials = [], fail, workflows, credentials

    def __call__(self, argv, timeout=60, user=None, extra_env=None, input_text=None):
        self.calls.append(list(argv))
        if self.fail and self.fail in " ".join(argv):
            return 1, "boom"
        if argv[:3] == ["docker", "exec", bk.CONTAINER] and "export:workflow" in argv:
            out = prep.run_dir() / "files" / "backup-tmp" / "workflows"
            for i in range(self.workflows):
                (out / ("wf%d.json" % i)).write_text(json.dumps({"id": "wf%d" % i, "name": "w%d" % i, "nodes": []}))
            return 0, "Successfully exported %d workflows" % self.workflows
        if argv[:3] == ["docker", "exec", bk.CONTAINER] and "export:credentials" in argv:
            out = prep.run_dir() / "files" / "backup-tmp" / "credentials"
            for i in range(self.credentials):
                (out / ("c%d.json" % i)).write_text(json.dumps({"id": "c%d" % i, "name": "cred%d" % i, "data": "U2FsdGVkX1+encrypted"}))
            return 0, "ok"
        if argv[0] == "sqlite3":
            target = argv[2].split("'")[1]
            pathlib.Path(target).write_bytes(b"SQLite format 3\x00" + b"x" * 100)
            return 0, ""
        return 0, ""


@pytest.fixture(autouse=True)
def no_real_ports(monkeypatch):
    """The tests must not depend on what listens on this machine."""
    monkeypatch.setattr(bk.prepare, "port_in_use", lambda port: False)


@pytest.fixture
def n8n(fleet_env):
    prep.ensure_dirs()
    (prep.run_dir() / ".env").write_text("N8N_VERSION=2.47.4\nN8N_OWNER_EMAIL=o@example.org\nN8N_IMAGE=img\n")
    (prep.run_dir() / "data" / "database.sqlite").write_bytes(b"live")
    secrets.set_value("N8N_ENCRYPTION_KEY", KEY)
    return fleet_env


def make(n8n, now, fail=None, **kw):
    emitted = []
    out = bk.run_backup(Docker(fail=fail), now=lambda: now, emit=lambda *a, **k: emitted.append((a, k)), **kw)
    return out, emitted


def test_a_backup_holds_workflows_encrypted_credentials_a_database_copy_and_a_manifest(n8n):
    out, emitted = make(n8n, 1_700_000_000)
    folder = bk.backups_dir() / out["backup"]
    assert out["workflows"] == 3 and out["credentials"] == 2 and bk.NAME_RE.match(out["backup"])
    assert {p.name for p in folder.iterdir()} == {"workflows", "credentials", "database.sqlite", "MANIFEST.json"}
    manifest = json.loads((folder / "MANIFEST.json").read_text())
    assert manifest["n8n_version"] == "2.47.4" and manifest["key_fingerprint"] == bk.key_fingerprint() and len(manifest["files"]) == 6
    assert KEY not in (folder / "MANIFEST.json").read_text()                         # the key is never in a backup
    assert emitted[0][0][0] == "backup.n8n_ok" and "3 workflows and 2 credentials" in emitted[0][0][1]


def test_everything_in_a_backup_is_private(n8n):
    out, _ = make(n8n, 1_700_000_000)
    folder = bk.backups_dir() / out["backup"]
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    for p in folder.rglob("*"):
        assert stat.S_IMODE(p.stat().st_mode) == (0o700 if p.is_dir() else 0o600), p


def test_the_scratch_folder_is_removed(n8n):
    make(n8n, 1_700_000_000)
    assert not (prep.run_dir() / "files" / "backup-tmp").exists()


def test_the_database_is_copied_with_the_sqlite_backup_command_not_a_file_copy(n8n):
    d = Docker()
    bk.run_backup(d, now=lambda: 1_700_000_000, emit=lambda *a, **k: None)
    call = next(c for c in d.calls if c[0] == "sqlite3")
    assert call[1].endswith("data/database.sqlite") and call[2].startswith(".backup '")


def test_only_the_newest_seven_backups_stay(n8n):
    names = []
    for i in range(9):
        out, _ = make(n8n, 1_700_000_000 + i * 86400)
        names.append(out["backup"])
    left = [p.name for p in bk.list_backups()]
    assert left == names[-7:] and len(left) == 7


def test_a_failed_export_leaves_no_partial_backup_and_sends_a_warning(n8n):
    with pytest.raises(bk.BackupError):
        make(n8n, 1_700_000_000, fail="export:credentials")
    assert bk.list_backups() == [] and not (prep.run_dir() / "files" / "backup-tmp").exists()
    emitted = []
    with pytest.raises(bk.BackupError):
        bk.run_backup(Docker(fail="sqlite3"), now=lambda: 1_700_000_001, emit=lambda *a, **k: emitted.append((a, k)))
    assert emitted[0][0][0] == "backup.n8n_failed" and emitted[0][1]["severity"] == "warn"


def test_a_failed_run_does_not_delete_the_older_backups(n8n):
    good, _ = make(n8n, 1_700_000_000)
    with pytest.raises(bk.BackupError):
        make(n8n, 1_700_086_400, fail="sqlite3")
    assert [p.name for p in bk.list_backups()] == [good["backup"]]


def test_the_umask_is_restored_after_a_backup(n8n):
    before = os.umask(0o022)
    os.umask(before)
    make(n8n, 1_700_000_000)
    after = os.umask(0o022)
    os.umask(after)
    assert before == after


def test_main_prints_json_with_no_secret(n8n, monkeypatch, capsys):
    monkeypatch.setattr(bk.system, "run", Docker())
    monkeypatch.setattr(bk, "run_backup", lambda: {"backup": "n8n-x", "workflows": 1, "credentials": 1, "removed": []})
    assert bk.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


# ------------------------------------------------------------------------------------------------ restore test
class FakeClient:
    """Answers in the shape of n8n 2.47: {"count": n, "data": [rows]}. `nested=True` gives the older shape with one more level."""

    def __init__(self, base, flows=3, active=3, creds=2, nested=False):
        self.base, self.flows, self.active, self.creds, self.nested = base, flows, active, creds, nested

    def wait_ready(self, seconds=0):
        return True

    def login(self, email, password):
        return {}

    def call(self, method, path, body=None, **kw):
        if path.startswith("/rest/workflows"):
            rows = [{"id": str(i), "active": i < self.active} for i in range(self.flows)]
            return 200, ({"data": {"count": len(rows), "data": rows}} if self.nested else {"count": len(rows), "data": rows})
        if path == "/rest/credentials":
            rows = [{"id": str(i)} for i in range(self.creds)]
            return 200, ({"data": {"count": len(rows), "data": rows}} if self.nested else {"count": len(rows), "data": rows})
        return 404, None


def test_the_restore_test_starts_a_container_checks_it_and_cleans_up(n8n):
    secrets.set_value("N8N_OWNER_PASSWORD", "p" * 24)
    make(n8n, 1_700_000_000)
    r = Runner(rules=[(["docker", "exec", bk.TEST_CONTAINER], (0, "2"))])
    out = bk.restore_test(r, client_factory=lambda base: FakeClient(base), ready_seconds=1)
    assert out["ok"] is True and out["workflows"] == 3 and out["active"] == 3 and out["credentials"] == 2 and out["decrypts"] is True
    run_call = next(c for c, _u in r.calls if c[:2] == ["docker", "run"])
    assert "--env-file" in run_call and prep.N8N_SECRETS_FILE in run_call and "127.0.0.1" in " ".join(run_call)     # the same key, loopback only
    assert "5688" in " ".join(run_call) and "5678" not in " ".join(run_call).replace("5688", "")
    assert r.ran("docker", "stop", bk.TEST_CONTAINER)
    assert not (prep.run_dir() / "restore-test").exists()


def test_the_restore_test_stops_the_container_even_when_a_check_fails(n8n):
    secrets.set_value("N8N_OWNER_PASSWORD", "p" * 24)
    make(n8n, 1_700_000_000)
    r = Runner(rules=[(["docker", "exec", bk.TEST_CONTAINER], (1, "decrypt failed"))])
    out = bk.restore_test(r, client_factory=lambda base: FakeClient(base), ready_seconds=1)
    assert out["ok"] is False and out["decrypts"] is False and r.ran("docker", "stop", bk.TEST_CONTAINER)
    assert not (prep.run_dir() / "restore-test").exists()


def test_the_restore_test_fails_when_workflows_are_missing(n8n):
    secrets.set_value("N8N_OWNER_PASSWORD", "p" * 24)
    make(n8n, 1_700_000_000)
    r = Runner(rules=[(["docker", "exec", bk.TEST_CONTAINER], (0, "2"))])
    assert bk.restore_test(r, client_factory=lambda base: FakeClient(base, flows=1), ready_seconds=1)["ok"] is False


def test_the_restore_test_needs_a_backup_and_the_same_key(n8n):
    with pytest.raises(bk.BackupError, match="no backup"):
        bk.restore_test(Runner())
    make(n8n, 1_700_000_000)
    secrets.set_value("N8N_ENCRYPTION_KEY", "z" * 44)
    with pytest.raises(bk.BackupError, match="another encryption key"):
        bk.restore_test(Runner())


def test_the_restore_test_does_not_start_when_a_test_port_is_busy(n8n, monkeypatch):
    make(n8n, 1_700_000_000)
    monkeypatch.setattr(bk.prepare, "port_in_use", lambda port: port == 5689)
    r = Runner()
    with pytest.raises(bk.BackupError, match="5689"):
        bk.restore_test(r)
    assert not r.calls


def test_a_backup_that_root_starts_belongs_to_fleetops(n8n, monkeypatch):
    """The container and the sqlite3 copy run as fleetops. They cannot use a folder that root made. See D-053."""
    given = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(bk.shutil, "chown", lambda path, user=None, group=None: given.append((pathlib.Path(path), user, group)))
    out, _ = make(n8n, 1_700_000_000)
    folder = bk.backups_dir() / out["backup"]
    owners = {(user, group) for _path, user, group in given}
    assert owners == {("fleetops", "fleetops")}
    handed = {path for path, _u, _g in given}
    assert folder in handed and folder / "database.sqlite" in handed and folder / "MANIFEST.json" in handed
    assert {folder / "workflows", folder / "credentials"} <= handed
    # the temporary folders that the container writes into were handed over before the export ran
    assert prep.run_dir() / "files" / "backup-tmp" in handed


def test_the_hand_over_happens_before_the_container_export(n8n, monkeypatch):
    order = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(bk.shutil, "chown", lambda path, user=None, group=None: order.append(("chown", pathlib.Path(path).name)))
    docker = Docker()

    def runner(argv, **kw):
        order.append(("run", "export:workflow" if "export:workflow" in argv else argv[0]))
        return docker(argv, **kw)
    bk.run_backup(runner, now=lambda: 1_700_000_000, emit=lambda *a, **k: None)
    first_export = next(i for i, item in enumerate(order) if item == ("run", "export:workflow"))
    assert ("chown", "backup-tmp") in order[:first_export] and ("chown", "workflows") in order[:first_export]


def test_a_backup_that_fleetops_starts_changes_no_owner(n8n, monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 1002)
    monkeypatch.setattr(bk.shutil, "chown", lambda *a, **k: pytest.fail("chown must not run for a normal user"))
    out, _ = make(n8n, 1_700_000_000)
    assert out["workflows"] == 3


def test_the_rows_of_every_known_list_shape_are_read():
    rows = [{"id": "1"}, {"id": "2"}]
    assert bk.rows_of({"count": 2, "data": rows}) == rows                       # n8n 2.47
    assert bk.rows_of({"data": {"count": 2, "data": rows}}) == rows             # an older shape
    assert bk.rows_of(rows) == rows
    assert bk.rows_of({"data": None}) == [] and bk.rows_of(None) == [] and bk.rows_of({"data": {"x": 1}}) == []


def test_the_restore_test_reads_the_older_nested_shape_too(n8n):
    secrets.set_value("N8N_OWNER_PASSWORD", "p" * 24)
    make(n8n, 1_700_000_000)
    r = Runner(rules=[(["docker", "exec", bk.TEST_CONTAINER], (0, "2"))])
    out = bk.restore_test(r, client_factory=lambda base: FakeClient(base, nested=True), ready_seconds=1)
    assert out["ok"] is True and out["workflows"] == 3 and out["credentials"] == 2


# ---- D-062: the test copy runs with the settings of the live compose file
def test_the_test_copy_gets_the_file_limits_and_the_blocked_nodes_of_the_live_n8n(n8n):
    args = bk._test_docker_args("img", "/d", "/f", {"N8N_OWNER_EMAIL": "o@example.org"})
    flags = {args[i + 1].split("=", 1)[0]: args[i + 1].split("=", 1)[1] for i, a in enumerate(args) if a == "-e"}
    assert flags["N8N_RESTRICT_FILE_ACCESS_TO"] == "/home/node/files" and flags["N8N_BLOCK_FILE_ACCESS_TO_N8N_FILES"] == "true"
    assert flags["N8N_BLOCK_ENV_ACCESS_IN_NODE"] == "true" and flags["N8N_RUNNERS_MODE"] == "internal" and "executeCommand" in flags["NODES_EXCLUDE"]
    assert flags["N8N_PORT"] == "5688" and flags["N8N_RUNNERS_BROKER_PORT"] == "5689" and flags["N8N_LISTEN_ADDRESS"] == "127.0.0.1"
    assert flags["N8N_INSTANCE_OWNER_EMAIL"] == "o@example.org" and flags["N8N_MCP_MANAGED_BY_ENV"] == "true"


def test_every_fixed_setting_of_the_compose_file_reaches_the_test_copy_except_the_ones_it_owns(n8n):
    fixed = bk.compose_environment()
    args = bk._test_docker_args("img", "/d", "/f", {})
    sent = {args[i + 1].split("=", 1)[0]: args[i + 1].split("=", 1)[1] for i, a in enumerate(args) if a == "-e"}
    for key, value in fixed.items():
        assert sent.get(key) == value, key
    assert bk.OWN_SETTINGS.isdisjoint(fixed)                                  # the test copy never takes a live port or the live owner


def test_a_value_with_a_variable_is_not_copied(tmp_path):
    compose = tmp_path / "c.yml"
    compose.write_text("services:\n  n8n:\n    environment:\n      A: fixed\n      B: ${B_FROM_ENV:-x}\n      N8N_PORT: '1'\n")
    assert bk.compose_environment(compose) == {"A": "fixed"}


def test_the_test_copy_still_has_no_published_port_and_no_secret_in_its_arguments(n8n):
    args = " ".join(bk._test_docker_args("img", "/d", "/f", {"N8N_OWNER_EMAIL": "o@example.org"}))
    assert "-p " not in args and "--network host" in args and "N8N_ENCRYPTION_KEY=" not in args and "PASSWORD=" not in args
