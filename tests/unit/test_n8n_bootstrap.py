import json
import pathlib

import pytest

from ops.fleetlib import secrets, tokens
from ops.n8n import bootstrap as bs
from ops.n8n import client as nc
from ops.n8n import prepare as prep
from tests.fake_n8n import FakeN8n

ROOT = pathlib.Path(__file__).resolve().parents[2]
EMAIL, PASSWORD = "owner@example.org", "pw-test-0123456789abcdef"


@pytest.fixture
def n8n(fleet_env):
    """A fake n8n, the compose .env that holds the owner email, and every secret that bootstrap needs."""
    prep.ensure_dirs()
    (prep.run_dir() / ".env").write_text("N8N_OWNER_EMAIL=%s\n" % EMAIL)
    secrets.set_value("N8N_OWNER_PASSWORD", PASSWORD)
    for name in ("FLEET_EVENT_TOKEN", "N8N_TOOLS_TOKEN"):
        secrets.set_value(name, "x" * 20 + name.lower().replace("_", "")[:20] + "0" * 4)
    tokens.rotate("FLEET_ACTION_TOKEN")
    with FakeN8n(EMAIL, PASSWORD) as fake:
        yield fake


def run(fake, **kw):
    return bs.bootstrap(c=nc.Client(fake.url, sleep=lambda s: None), **kw)


def test_first_run_makes_the_key_the_credentials_and_publishes_every_workflow(n8n):
    report = run(n8n)
    assert report["api_key"] == "made"
    assert report["credentials"] == {"fleet-event-token": "created", "fleet-action-token": "created", "fleet-tools-token": "created"}
    assert set(report["workflows"]) == set(bs.ORDER) and all(v in ("created", "updated") for v in report["workflows"].values())
    assert sorted(report["published"]) == sorted(bs.ORDER)
    assert all(w["active"] for w in n8n.workflows.values()) and len(n8n.workflows) == len(bs.ORDER)
    # the key went to its file, was never printed, and has only the smallest scopes
    key = secrets.value_of("N8N_API_KEY")
    assert key and key not in json.dumps(report)
    assert n8n.api_keys[0]["scopes"] == nc.API_SCOPES and not [s for s in n8n.api_keys[0]["scopes"] if s.endswith(":delete") or s.startswith("user:")]


def test_the_credentials_hold_the_real_secret_values(n8n):
    run(n8n)
    by_name = {c["name"]: c for c in n8n.credentials.values()}
    assert by_name["fleet-event-token"]["data"] == {"name": "X-Fleet-Token", "value": secrets.value_of("FLEET_EVENT_TOKEN")}
    assert by_name["fleet-action-token"]["data"] == {"token": secrets.value_of("FLEET_ACTION_TOKEN")}
    assert by_name["fleet-tools-token"]["data"] == {"token": secrets.value_of("N8N_TOOLS_TOKEN")}


def test_every_placeholder_in_every_workflow_was_replaced_by_a_real_id(n8n):
    run(n8n)
    blob = json.dumps(list(n8n.workflows.values()))
    assert "FLEET_CRED:" not in blob and "FLEET_WF:" not in blob
    cred_ids = set(n8n.credentials)
    wf_ids = set(n8n.workflows)
    event = next(w for w in n8n.workflows.values() if w["name"] == "fleet-event")
    assert event["settings"]["errorWorkflow"] in wf_ids
    webhook = next(n for n in event["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert webhook["credentials"]["httpHeaderAuth"]["id"] in cred_ids
    tools = next(w for w in n8n.workflows.values() if w["name"] == "fleet-tools")
    sub = next(n for n in tools["nodes"] if n["name"] == "fetch_feed")
    assert sub["parameters"]["workflowId"]["value"] in wf_ids


def test_a_second_run_changes_nothing(n8n):
    run(n8n)
    puts = [c for c in n8n.log if c[0] in ("PUT", "POST") and c[1].startswith("/api/v1/workflows")]
    n8n.log.clear()
    report = run(n8n)
    assert report["api_key"] == "kept" and set(report["credentials"].values()) == {"kept"} and set(report["workflows"].values()) == {"kept"}
    assert not [c for c in n8n.log if c[0] in ("POST", "PUT", "PATCH", "DELETE")], n8n.log
    assert len(n8n.api_keys) == 1 and puts


def test_a_rotated_token_updates_only_its_credential(n8n):
    run(n8n)
    secrets.set_value("N8N_TOOLS_TOKEN", "n" * 40)
    report = run(n8n)
    assert report["credentials"] == {"fleet-event-token": "kept", "fleet-action-token": "kept", "fleet-tools-token": "updated"}
    by_name = {c["name"]: c for c in n8n.credentials.values()}
    assert by_name["fleet-tools-token"]["data"] == {"token": "n" * 40} and len(n8n.credentials) == 3


def test_a_changed_workflow_file_is_updated_and_published_again(n8n, tmp_path):
    folder = tmp_path / "wf"
    folder.mkdir()
    for p in (ROOT / "n8n" / "workflows").glob("*.json"):
        (folder / p.name).write_text(p.read_text())
    run(n8n, folder=folder)
    changed = json.loads((folder / "health-watch.json").read_text())
    changed["nodes"][0]["position"] = [5, 5]
    (folder / "health-watch.json").write_text(json.dumps(changed))
    n8n.log.clear()
    report = run(n8n, folder=folder)
    assert report["workflows"]["health-watch"] == "updated" and report["workflows"]["fleet-event"] == "kept"
    assert next(w for w in n8n.workflows.values() if w["name"] == "health-watch")["active"] is True
    assert ("POST", "/api/v1/workflows/%s/deactivate" % next(i for i, w in n8n.workflows.items() if w["name"] == "health-watch")) in n8n.log


def test_a_dead_stored_key_is_replaced_and_the_old_fleetctl_key_is_deleted(n8n):
    run(n8n)
    secrets.set_value("N8N_API_KEY", "eyJ" + "dead" * 12 + ".x.y")
    report = run(n8n)
    assert report["api_key"] == "made" and len(n8n.api_keys) == 1 and secrets.value_of("N8N_API_KEY") == n8n.api_keys[0]["raw"]


def test_it_waits_for_n8n_to_be_ready(n8n):
    n8n.ready_after = 3
    assert run(n8n)["api_key"] == "made" and n8n.ready_calls == 4


def test_it_stops_with_a_clear_message_when_n8n_never_gets_ready(n8n):
    n8n.ready_after = 10 ** 9
    c = nc.Client(n8n.url, sleep=lambda s: None)
    c.wait_ready = lambda seconds=180, step=3: False
    with pytest.raises(nc.N8nError, match="not ready"):
        bs.bootstrap(c=c)


def test_a_wrong_owner_password_fails_without_showing_it(n8n):
    secrets.set_value("N8N_OWNER_PASSWORD", "wrong-" + "p" * 20)
    with pytest.raises(nc.N8nError) as err:
        run(n8n)
    assert "wrong-" not in str(err.value) and "login failed" in str(err.value)


def test_a_missing_secret_stops_the_credential_step(n8n):
    secrets.unset_value("FLEET_EVENT_TOKEN")
    with pytest.raises(nc.N8nError, match="fleet-event-token"):
        run(n8n)


def test_resolve_refuses_a_placeholder_without_an_id():
    assert bs.resolve('{"a": "FLEET_CRED:x"}', {"x": "id1"}, {}) == '{"a": "id1"}'
    with pytest.raises(nc.N8nError, match="no id"):
        bs.resolve('{"a": "FLEET_WF:ghost"}', {}, {})


def test_the_main_function_reports_a_failure_as_json_with_no_secret(n8n, capsys):
    secrets.unset_value("N8N_OWNER_PASSWORD")
    assert bs.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed" and "prepare" in out["reason"]


def test_the_client_hides_long_tokens_in_messages():
    jwt_shaped = ".".join(["ey" + "JhbGciOiJIUzI1NiJ9", "abcdefghijklmnopqrstuvwxyz", "signaturepart"])      # built at run time. It is not a real key.
    assert "eyJ" not in nc.secret_free("bad key " + jwt_shaped)
    assert nc.secret_free("workflow 12 failed") == "workflow 12 failed"


# ---- D-053: n8n may need a moment before it shows a new publication

def test_bootstrap_waits_when_n8n_shows_the_published_error_workflow_late(n8n):
    n8n.publish_lag = 4                         # four reads still see the error workflow as unpublished
    sleeps = []
    report = bs.bootstrap(c=nc.Client(n8n.url, sleep=sleeps.append))
    assert sorted(report["published"]) == sorted(bs.ORDER)
    assert sleeps, "the client must have waited at least once"
    assert all(w["active"] for w in n8n.workflows.values())


def test_a_workflow_that_points_at_a_late_error_workflow_is_saved_again_after_a_short_wait(n8n):
    c = nc.Client(n8n.url, sleep=lambda s: None)
    c.login(EMAIL, PASSWORD)
    key = c.create_api_key("test")
    stub = {"name": "x", "nodes": [{"id": "1", "name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}}],
            "connections": {}, "settings": {}}
    error_id = c.save_workflow(key, dict(stub, name="err"))
    n8n.publish_lag = 3
    c.call("POST", "/api/v1/workflows/%s/activate" % error_id, {}, key=key)        # no wait, like a client that does not wait
    saved = c.save_workflow(key, dict(stub, name="child", settings={"errorWorkflow": error_id}))
    assert saved in n8n.workflows and n8n.workflows[saved]["settings"]["errorWorkflow"] == error_id


def test_an_error_workflow_that_is_never_published_still_fails_with_the_real_reason(n8n):
    c = nc.Client(n8n.url, sleep=lambda s: None)
    c.login(EMAIL, PASSWORD)
    key = c.create_api_key("test")
    stub = {"name": "x", "nodes": [{"id": "1", "name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0], "parameters": {}}],
            "connections": {}, "settings": {}}
    error_id = c.save_workflow(key, dict(stub, name="err"))              # never published
    with pytest.raises(nc.N8nError, match="no published version"):
        c.save_workflow(key, dict(stub, name="child", settings={"errorWorkflow": error_id}))


def test_a_publication_that_never_shows_fails_after_a_bounded_wait(n8n):
    n8n.publish_lag = 10_000
    sleeps = []
    c = nc.Client(n8n.url, sleep=sleeps.append)
    c.login(EMAIL, PASSWORD)
    key = c.create_api_key("test")
    wid = c.save_workflow(key, {"name": "err", "nodes": [{"id": "1", "name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0],
                                                          "parameters": {}}], "connections": {}, "settings": {}})
    with pytest.raises(nc.N8nError, match="did not show a published version"):
        c.set_active(key, wid, True)
    assert len(sleeps) == nc.PUBLISH_WAIT_TRIES - 1


def test_an_n8n_that_does_not_report_the_published_version_is_not_waited_for(n8n, monkeypatch):
    sleeps = []
    c = nc.Client(n8n.url, sleep=sleeps.append)
    monkeypatch.setattr(c, "workflow", lambda key, wid: {"id": wid, "active": True})      # no activeVersionId field at all
    assert c.wait_published("key", "abc") is True and sleeps == []


def test_an_unknown_error_workflow_is_not_retried(n8n):
    sleeps = []
    c = nc.Client(n8n.url, sleep=sleeps.append)
    c.login(EMAIL, PASSWORD)
    key = c.create_api_key("test")
    with pytest.raises(nc.N8nError, match="does not exist"):
        c.save_workflow(key, {"name": "child", "nodes": [{"id": "1", "name": "Start", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 0],
                                                            "parameters": {}}], "connections": {}, "settings": {"errorWorkflow": "missing123"}})
    assert sleeps == []
