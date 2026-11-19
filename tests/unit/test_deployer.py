import json

import pytest

from ops.deployer import core
from ops.deployer.cloudflare import Cloudflare, CloudflareError
from ops.fleetlib import config, httpc

C1 = "a" * 40
C2 = "b" * 40
WORKER = b"export default { fetch() { return new Response('hi') } }"


class FakeRepo:
    def __init__(self, files=None, merged=(C1,)):
        self.files = files if files is not None else {
            "projects/demo/app/worker.js": WORKER,
            "projects/demo/app/deploy.json": b'{"compatibility_date": "2025-09-01"}',
        }
        self.merged = set(merged)

    def on_main(self, c):
        return c in self.merged

    def read(self, c, path):
        return self.files.get(path)

    def list(self, c, folder):
        return sorted(p for p in self.files if p.startswith(folder.rstrip("/") + "/"))


class FakeCF:
    def __init__(self, token_ok=True, subs=None, fail=None):
        self.calls, self.token_ok, self.subs, self.fail = [], token_ok, subs or [], fail

    def verify_token(self):
        self.calls.append("verify"); return self.token_ok

    def subscriptions(self):
        self.calls.append("subs"); return self.subs

    def d1_ensure(self, name):
        self.calls.append("d1_ensure:" + name); return "uuid-1"

    def d1_query(self, db, sql):
        self.calls.append("d1_query:" + sql[:20])

    def upload_worker(self, script, source, date, d1_id=None):
        if self.fail:
            raise CloudflareError(self.fail)
        self.calls.append("upload:%s:d1=%s" % (script, d1_id))

    def subdomain(self):
        return "studio"


def make(tmp_path, repo=None, cf=None, mode="live", tested=lambda c: True, plan="free", paused=lambda: None, approved=lambda kind, ref: True):
    cfg = config.defaults()
    cfg["deployer"]["mode"] = mode
    cfg["deployer"]["plan"] = plan
    return core.Deployer(cfg, cf or FakeCF(), repo or FakeRepo(), tested, tmp_path, paused=paused, approved=approved), cf


def test_live_deploy_of_a_tested_merged_commit(tmp_path):
    cf = FakeCF()
    dep, _ = make(tmp_path, cf=cf)
    out = dep.deploy({"project": "demo", "commit": C1})
    assert out == {"status": "deployed", "url": "https://fleet-demo.studio.workers.dev"}
    assert cf.calls == ["verify", "subs", "upload:fleet-demo:d1=None"]
    assert json.loads((tmp_path / "deployments.json").read_text())["demo"]["commit"] == C1
    assert json.loads((tmp_path / "deploys.log").read_text().splitlines()[-1])["status"] == "deployed"


@pytest.mark.parametrize("req,reason", [
    ({"project": "Bad Name", "commit": C1}, "bad_request"),
    ({"project": "demo", "commit": "abc"}, "bad_request"),
    ({"project": "demo", "commit": C2}, "not_merged"),
    ({}, "bad_request"),
])
def test_bad_requests_and_unmerged_commits_are_refused(tmp_path, req, reason):
    cf = FakeCF()
    dep, _ = make(tmp_path, cf=cf)
    assert dep.deploy(req)["reason"] == reason
    assert cf.calls == []


def test_an_untested_commit_is_never_deployed(tmp_path):
    cf = FakeCF()
    dep, _ = make(tmp_path, cf=cf, tested=lambda c: False)
    assert dep.deploy({"project": "demo", "commit": C1})["reason"] == "not_tested"
    assert cf.calls == []


def test_pause_blocks_a_deploy(tmp_path):
    dep, _ = make(tmp_path, paused=lambda: {"reason": "owner"})
    assert dep.deploy({"project": "demo", "commit": C1})["reason"] == "paused"


def test_dry_run_writes_a_plan_and_calls_nothing(tmp_path):
    cf = FakeCF()
    dep, _ = make(tmp_path, cf=cf, mode="dry-run")
    out = dep.deploy({"project": "demo", "commit": C1})
    assert out["status"] == "dry_run" and out["plan"]["script"] == "fleet-demo" and cf.calls == []
    assert (tmp_path / "plans" / ("demo-%s.json" % C1[:10])).exists()


@pytest.mark.parametrize("files,reason", [
    ({}, "no_worker_js"),
    ({"projects/demo/app/worker.js": WORKER, "projects/demo/app/deploy.json": b"not json"}, "bad_deploy_json"),
    ({"projects/demo/app/worker.js": WORKER, "projects/demo/app/deploy.json": b'{"kv": true}'}, "unknown_deploy_key: kv"),
    ({"projects/demo/app/worker.js": WORKER, "projects/demo/app/deploy.json": b'{"r2": "bucket"}'}, "unknown_deploy_key: r2"),
    ({"projects/demo/app/worker.js": b"x" * 1_000_001}, "module_too_large"),
    ({"projects/demo/app/worker.js": WORKER, "projects/demo/app/migrations/1.sql": b"create table t(a)"}, "migrations_without_d1"),
])
def test_products_that_break_the_free_rules_are_refused(tmp_path, files, reason):
    cf = FakeCF()
    dep, _ = make(tmp_path, repo=FakeRepo(files), cf=cf)
    assert dep.deploy({"project": "demo", "commit": C1})["reason"] == reason
    assert cf.calls == []


def test_a_paid_plan_setting_blocks_everything(tmp_path):
    cf = FakeCF()
    dep, _ = make(tmp_path, cf=cf, plan="pro")
    assert dep.deploy({"project": "demo", "commit": C1})["reason"] == "plan_not_free"
    assert cf.calls == []


def test_an_account_with_a_paid_subscription_blocks_the_deploy(tmp_path):
    cf = FakeCF(subs=[{"price": 5, "rate_plan": {"id": "workers_paid"}}])
    dep, _ = make(tmp_path, cf=cf)
    assert dep.deploy({"project": "demo", "commit": C1})["reason"] == "account_has_paid_subscription"
    assert not any(c.startswith("upload") for c in cf.calls)
    assert core.plan_is_free([{"price": 0}, {"price": None}]) and not core.plan_is_free([{"price": 20}])


def test_d1_database_and_migrations_run_once_in_order(tmp_path):
    files = {"projects/demo/app/worker.js": WORKER, "projects/demo/app/deploy.json": b'{"d1": true}',
             "projects/demo/app/migrations/002_b.sql": b"create table b(x);",
             "projects/demo/app/migrations/001_a.sql": b"create table a(x);"}
    cf = FakeCF()
    dep, _ = make(tmp_path, repo=FakeRepo(files), cf=cf)
    assert dep.deploy({"project": "demo", "commit": C1})["status"] == "deployed"
    queries = [c for c in cf.calls if c.startswith("d1_query")]
    assert queries == ["d1_query:create table a(x);", "d1_query:create table b(x);"]
    cf2 = FakeCF()
    dep2, _ = make(tmp_path, repo=FakeRepo(files), cf=cf2)
    dep2.deploy({"project": "demo", "commit": C1})
    assert not [c for c in cf2.calls if c.startswith("d1_query")]       # already applied


def test_cloudflare_errors_do_not_leak_the_token(tmp_path):
    dep, _ = make(tmp_path, cf=FakeCF(fail="boom"))
    out = dep.deploy({"project": "demo", "commit": C1})
    assert out["reason"] == "cloudflare_error"
    assert not FakeCF().token_ok is False


def test_cloudflare_client_builds_a_multipart_upload_and_hides_the_token():
    seen = []

    def fake(method, url, headers, body, timeout, max_bytes):
        seen.append((method, url, headers, body))
        return httpc.Response(200, {}, b'{"success": true, "result": {}}')
    httpc.set_transport(fake)
    try:
        cf = Cloudflare("tok-" + "z" * 36, "acct")
        cf.upload_worker("fleet-demo", WORKER, "2025-09-01", d1_id="uuid-9")
        method, url, headers, body = seen[0]
        assert method == "PUT" and url.endswith("/accounts/acct/workers/scripts/fleet-demo")
        assert headers["Content-Type"].startswith("multipart/form-data; boundary=fleet-")
        assert b'"main_module": "worker.js"' in body and b'"type": "d1"' in body and WORKER in body
        assert b"tok-" not in body
        assert seen[1][1].endswith("/subdomain")
        httpc.set_transport(lambda *a: httpc.Response(403, {}, b'{"success": false, "errors": [{"message": "no access"}]}'))
        with pytest.raises(CloudflareError) as err:
            cf.verify_token()
        assert "tok-" not in str(err.value) and "no access" in str(err.value)
    finally:
        httpc.reset_transport()


# ------------------------------------------------------------------------------------------------ D-050: the owner approves each release
def test_a_live_deploy_needs_a_release_approval_for_that_commit(tmp_path):
    cf = FakeCF()
    asked = []

    def approved(kind, ref):
        asked.append((kind, ref))
        return False
    dep, _ = make(tmp_path, cf=cf, approved=approved)
    out = dep.deploy({"project": "demo", "commit": C1})
    assert out["status"] == "blocked" and out["reason"] == "no_release_approval"
    assert asked == [("release", "demo@%s" % C1[:10])] and cf.calls == []


def test_a_live_deploy_goes_ahead_when_the_owner_approved_that_commit_and_only_that_commit(tmp_path):
    dep, cf = make(tmp_path, cf=FakeCF(), approved=lambda kind, ref: (kind, ref) == ("release", "demo@%s" % C1[:10]))
    assert dep.deploy({"project": "demo", "commit": C1})["status"] == "deployed"
    (tmp_path / "other").mkdir(exist_ok=True)
    other, cf2 = make(tmp_path / "other", repo=FakeRepo(merged=(C1, C2)), cf=FakeCF(), approved=lambda kind, ref: (kind, ref) == ("release", "demo@%s" % C1[:10]))
    assert other.deploy({"project": "demo", "commit": C2})["reason"] == "no_release_approval"


def test_a_deployer_with_no_approver_never_deploys_live(tmp_path):
    cfg = config.defaults()
    cfg["deployer"]["mode"] = "live"
    dep = core.Deployer(cfg, FakeCF(), FakeRepo(), lambda c: True, tmp_path)
    assert dep.deploy({"project": "demo", "commit": C1})["reason"] == "no_release_approval"


@pytest.mark.parametrize("approved,label", [(True, "approved"), (False, "missing")])
def test_a_dry_run_plan_says_whether_the_release_is_approved_and_is_never_blocked_by_it(tmp_path, approved, label):
    dep, cf = make(tmp_path, cf=FakeCF(), mode="dry-run", approved=lambda kind, ref: approved)
    out = dep.deploy({"project": "demo", "commit": C1})
    assert out["status"] == "dry_run" and out["plan"]["release_approval"] == label and cf.calls == []


def test_a_migration_that_ran_is_recorded_even_when_the_upload_fails(tmp_path):
    """The old code saved the names only after the upload. A failed upload ran every migration again. See F-04."""
    files = {"projects/demo/app/worker.js": WORKER, "projects/demo/app/deploy.json": b'{"d1": true}',
             "projects/demo/app/migrations/001_a.sql": b"create table a(x);"}
    broken = FakeCF(fail="the upload broke")
    dep, _ = make(tmp_path, repo=FakeRepo(files), cf=broken)
    out = dep.deploy({"project": "demo", "commit": C1})
    assert out["status"] == "blocked" and out["reason"] == "cloudflare_error"
    assert [c for c in broken.calls if c.startswith("d1_query")] == ["d1_query:create table a(x);"]
    good = FakeCF()
    dep2, _ = make(tmp_path, repo=FakeRepo(files), cf=good)
    assert dep2.deploy({"project": "demo", "commit": C1})["status"] == "deployed"
    assert not [c for c in good.calls if c.startswith("d1_query")]
    saved = json.loads((tmp_path / "deployments.json").read_text())["demo"]
    assert saved["migrations"] == ["001_a.sql"] and saved["commit"] == C1 and saved["url"].startswith("https://fleet-demo.")
