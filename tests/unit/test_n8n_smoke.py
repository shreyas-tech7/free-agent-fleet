import datetime
import json
import zoneinfo

import pytest

from ops.fleetctl import n8ncmd, system
from ops.fleetlib import config, secrets, tokens
from ops.n8n import bootstrap as bs
from ops.n8n import client as nc
from ops.n8n import prepare as prep
from ops.n8n import smoke
from tests.fake_n8n import FakeN8n
from tests.unit.fctl_helpers import Runner, Script

EMAIL, PASSWORD = "owner@example.org", "pw-test-0123456789abcdef"
LOOPBACK_SS = "LISTEN 0 511 127.0.0.1:5678 0.0.0.0:*\nLISTEN 0 511 127.0.0.1:5679 0.0.0.0:*"


@pytest.fixture
def world(fleet_env):
    prep.ensure_dirs()
    (prep.run_dir() / ".env").write_text("N8N_OWNER_EMAIL=%s\n" % EMAIL)
    secrets.set_value("N8N_OWNER_PASSWORD", PASSWORD)
    secrets.set_value("FLEET_EVENT_TOKEN", "evt-token" + "0" * 30)
    secrets.set_value("N8N_TOOLS_TOKEN", "t" * 40)
    tokens.rotate("FLEET_ACTION_TOKEN")
    with FakeN8n(EMAIL, PASSWORD, event_token="evt-token" + "0" * 30) as fake:
        config.set_override("ports.n8n", str(fake.port))          # every call of config.load() now finds the fake
        cfg = config.load()
        bs.bootstrap(c=nc.Client(fake.url, sleep=lambda s: None))
        yield {"fake": fake, "cfg": cfg, "runner": Runner([(["ss"], (0, LOOPBACK_SS.replace("5678", str(fake.port))))])}


def names(results):
    return {n: ok for n, ok, _d in results}


def test_every_check_passes_on_a_good_install(world):
    results = smoke.run_all(world["cfg"], world["runner"])
    assert all(ok for _n, ok, _d in results), results
    assert set(names(results)) == {"healthz", "loopback only", "owner login", "version", "workflows published", "webhook"}


def test_the_version_check_fails_when_n8n_runs_another_version(world):
    world["fake"].version = "2.47.7"
    r = names(smoke.run_all(world["cfg"], world["runner"]))
    assert r["version"] is False and r["healthz"] is True


def test_a_port_open_beyond_loopback_fails(world):
    exposed = Runner([(["ss"], (0, "LISTEN 0 511 0.0.0.0:5679 0.0.0.0:*\nLISTEN 0 511 127.0.0.1:%d 0.0.0.0:*" % world["fake"].port))])
    cfg = dict(world["cfg"])
    name, ok, detail = smoke.check_loopback(exposed, ports=(5679, world["fake"].port))
    assert ok is False and "5679" in detail


def test_a_workflow_that_is_not_published_fails(world):
    victim = next(w for w in world["fake"].workflows.values() if w["name"] == "health-watch")
    victim["active"] = False
    name, ok, detail = smoke.check_workflows(nc.Client(world["fake"].url), secrets.value_of("N8N_API_KEY"))
    assert ok is False and "health-watch" in detail


def test_a_missing_workflow_fails(world):
    del world["fake"].workflows[next(i for i, w in world["fake"].workflows.items() if w["name"] == "token-ledger")]
    name, ok, detail = smoke.check_workflows(nc.Client(world["fake"].url), secrets.value_of("N8N_API_KEY"))
    assert ok is False and "token-ledger" in detail


def test_a_webhook_that_takes_any_token_fails(world):
    world["fake"].open_webhook = True
    name, ok, detail = smoke.check_webhook(nc.Client(world["fake"].url), secrets.value_of("FLEET_EVENT_TOKEN"))
    assert ok is False and "wrong token gives 200" in detail


def test_the_wrong_owner_password_fails_the_login_check(world):
    secrets.set_value("N8N_OWNER_PASSWORD", "wrong-" + "p" * 24)
    assert names(smoke.run_all(world["cfg"], world["runner"]))["owner login"] is False


def test_no_api_key_fails_the_workflow_check_with_a_clear_message(world):
    secrets.unset_value("N8N_API_KEY")
    results = smoke.run_all(world["cfg"], world["runner"])
    detail = next(d for n, ok, d in results if n == "workflows published")
    assert "fleetctl n8n bootstrap" in detail


def test_the_restart_check_restarts_the_unit_and_looks_again(world):
    r = Runner([(["ss"], (0, LOOPBACK_SS.replace("5678", str(world["fake"].port)))), (["restart", "agent-fleet-n8n.service"], (0, ""))])
    results = smoke.run_all(world["cfg"], r, restart=True)
    assert r.ran("systemctl", "restart", "agent-fleet-n8n.service")
    assert names(results)["restart"] is True and names(results)["workflows stay published after a restart"] is True


# ---------------------------------------------------------------------------------- the brief window
@pytest.mark.parametrize("hour,minute,inside", [(6, 39, False), (6, 40, True), (6, 59, True), (7, 10, True), (7, 11, False), (23, 0, False), (0, 5, False)])
def test_the_brief_window_is_0640_to_0710_in_the_owner_zone(fleet_env, hour, minute, inside):
    when = datetime.datetime(2026, 11, 16, hour, minute, tzinfo=zoneinfo.ZoneInfo("America/Chicago"))
    assert n8ncmd.in_brief_window(when) is inside


def test_the_window_follows_the_owner_zone_not_the_machine_zone(fleet_env):
    utc = datetime.datetime(2026, 11, 16, 12, 55, tzinfo=datetime.timezone.utc)          # 06:55 in Chicago
    assert n8ncmd.in_brief_window(utc) is True


def test_smoke_refuses_a_restart_in_the_brief_window(world, monkeypatch):
    monkeypatch.setattr(n8ncmd, "in_brief_window", lambda now=None: True)
    s = Script()
    args = type("A", (), {"restart": True})()
    assert n8ncmd.cmd_smoke(args, s.con, world["runner"]) == 2
    assert "06:40 and 07:10" in s.text and not world["runner"].ran("restart")


def test_the_smoke_command_prints_one_line_for_each_check_and_a_count(world):
    s = Script()
    args = type("A", (), {"restart": False})()
    assert n8ncmd.cmd_smoke(args, s.con, world["runner"]) == 0
    assert "Result: 6 of 6 checks passed." in s.text and s.text.count("OK ") == 6


def test_the_status_command_reports_the_state_with_no_secret(world):
    r = Runner([(["is-active", "agent-fleet-n8n.service"], (0, "active"))])
    s = Script()
    assert n8ncmd.cmd_status(type("A", (), {})(), s.con, r) == 0
    assert "pinned: 2.47.4" in s.text and "n8n: unit active, healthy" in s.text and "workflows: 6 of 6 published" in s.text
    assert secrets.value_of("N8N_API_KEY") not in s.text and secrets.value_of("N8N_OWNER_PASSWORD") not in s.text
