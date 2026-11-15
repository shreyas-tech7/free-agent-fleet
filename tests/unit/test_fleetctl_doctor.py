import json
import os
import time
from types import SimpleNamespace

import pytest
import yaml

from ops.fleetctl import doctor
from ops.fleetlib import config, secrets
from ops.fleetlib.status import DEGRADED, FAIL, OK, WAITING
from tests.unit.fctl_helpers import Runner, Script


def ctx(fleet_env, run=None, http=None, cfg=None):
    return doctor.Ctx(cfg or config.load(), run=run or Runner(), http=http or (lambda url, **kw: (200, {"status": "OK"})))


def by_name(results):
    return {c.name: c for c in results}


def test_exit_code_is_0_only_for_ok_and_waiting():
    from ops.fleetlib.status import Component as C
    assert doctor.exit_code([C("a", OK), C("b", WAITING)]) == 0
    assert doctor.exit_code([C("a", OK), C("b", DEGRADED)]) == 1
    assert doctor.exit_code([C("a", FAIL)]) == 1


def test_render_has_a_count_line():
    from ops.fleetlib.status import Component as C
    text = doctor.render([C("a", OK, "fine"), C("b", WAITING), C("c", FAIL, "bad")])
    assert "Result: 1 OK, 1 WAITING_FOR_KEYS, 0 DEGRADED, 1 FAIL" in text and "a" in text


def test_a_crashing_check_is_one_fail_line_and_the_others_still_run(fleet_env, monkeypatch):
    def boom(c):
        raise RuntimeError("secret detail")
    monkeypatch.setattr(doctor, "CHECKS", [boom, doctor.check_config])
    results = doctor.run_all(ctx(fleet_env))
    names = by_name(results)
    assert names["boom"].status == FAIL and "RuntimeError" in names["boom"].detail and "secret detail" not in names["boom"].detail
    assert names["config"].status == OK


def test_config_check(fleet_env):
    assert doctor.check_config(ctx(fleet_env))[0].status == OK
    cfg = config.load()
    cfg["deployer"]["plan"] = "pro"
    assert doctor.check_config(ctx(fleet_env, cfg=cfg))[0].status == FAIL


UFW_OK = "Status: active\nDefault: deny (incoming), allow (outgoing), deny (routed)\n22/tcp ALLOW IN Anywhere"


def test_firewall(fleet_env):
    assert doctor.check_firewall(ctx(fleet_env, run=Runner([(["ufw"], (0, UFW_OK))])))[0].status == OK
    assert doctor.check_firewall(ctx(fleet_env, run=Runner([(["ufw"], (0, "Status: inactive"))])))[0].status == FAIL


def test_ports_flag_a_public_listener(fleet_env):
    good = "LISTEN 0 4096 127.0.0.1:8098 0.0.0.0:*\nLISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
    bad = good + "LISTEN 0 4096 0.0.0.0:8097 0.0.0.0:*\n"
    assert doctor.check_ports(ctx(fleet_env, run=Runner([(["ss"], (0, good))])))[0].status == OK
    c = doctor.check_ports(ctx(fleet_env, run=Runner([(["ss"], (0, bad))])))[0]
    assert c.status == FAIL and "8097" in c.detail


def test_users_check(fleet_env):
    ok = Runner([(["id"], (0, "fleet")), (["passwd"], (0, "fleet L 2026-01-01 0 99999 7 -1"))])
    assert doctor.check_users(ctx(fleet_env, run=ok))[0].status == OK
    sudoer = Runner([(["id"], (0, "fleet sudo")), (["passwd"], (0, "fleet L x"))])
    c = doctor.check_users(ctx(fleet_env, run=sudoer))[0]
    assert c.status == FAIL and "sudo" in c.detail
    pw = Runner([(["id"], (0, "fleet")), (["passwd"], (0, "fleet P 2026-01-01 0 99999 7 -1"))])
    assert doctor.check_users(ctx(fleet_env, run=pw))[0].status == FAIL


def test_pause_states(fleet_env):
    assert doctor.check_pause(ctx(fleet_env))[0].status == OK
    flag = fleet_env["state"] / "PAUSED"
    flag.write_text(json.dumps({"reason": "waiting_for_keys", "since": "now"}))
    assert doctor.check_pause(ctx(fleet_env))[0].status == WAITING
    flag.write_text(json.dumps({"reason": "owner", "since": "now"}))
    c = doctor.check_pause(ctx(fleet_env))[0]
    assert c.status == OK and "paused" in c.detail


def write_hermes_config(fleet_env, data):
    (fleet_env["hermes"] / "config.yaml").write_text(yaml.safe_dump(data))


GOOD_KANBAN = {"kanban": {"dispatch_in_gateway": True, "orchestrator_profile": "orchestrator", "auto_decompose": False}}


def test_kanban_must_follow_the_pause_state(fleet_env):
    write_hermes_config(fleet_env, GOOD_KANBAN)
    assert doctor.check_kanban(ctx(fleet_env))[0].status == OK
    (fleet_env["state"] / "PAUSED").write_text(json.dumps({"reason": "owner"}))
    c = doctor.check_kanban(ctx(fleet_env))[0]
    assert c.status == FAIL and "dispatcher is not stopped" in c.detail
    stopped = dict(GOOD_KANBAN)
    stopped["kanban"] = dict(GOOD_KANBAN["kanban"], dispatch_profiles=[])
    write_hermes_config(fleet_env, stopped)
    assert doctor.check_kanban(ctx(fleet_env))[0].status == OK


def test_kanban_checks_the_task_settings(fleet_env):
    write_hermes_config(fleet_env, {"kanban": {"dispatch_in_gateway": False, "orchestrator_profile": "x", "auto_decompose": True}})
    c = doctor.check_kanban(ctx(fleet_env))[0]
    assert c.status == FAIL and c.detail.count(";") == 2


def test_keys_report_waiting_then_ok(fleet_env):
    results = by_name(doctor.check_secrets(ctx(fleet_env)))
    assert results["keys_router"].status == WAITING and results["keys_router"].detail == "0 of 7 set"
    assert results["keys_internal"].status == FAIL                     # the installer did not make them in this fake machine
    secrets.set_value("GROQ_API_KEY", "gsk_" + "A1b2C3d4E5" * 3)
    assert by_name(doctor.check_secrets(ctx(fleet_env)))["keys_router"].status == OK


def test_a_secret_file_with_a_wide_mode_fails(fleet_env):
    secrets.set_value("GROQ_API_KEY", "gsk_" + "A1b2C3d4E5" * 3)
    os.chmod(secrets.secret_path("GROQ_API_KEY"), 0o644)
    c = by_name(doctor.check_secrets(ctx(fleet_env)))["secret_files"]
    assert c.status == FAIL and "mode 600" in c.detail


def test_router_waits_for_keys_and_for_approval(fleet_env):
    up = Runner([(["is-active"], (0, "active"))])
    assert doctor.check_router(ctx(fleet_env, run=up))[0].status == WAITING
    secrets.set_value("GROQ_API_KEY", "gsk_" + "A1b2C3d4E5" * 3)
    c = doctor.check_router(ctx(fleet_env, run=up))[0]
    assert c.status == WAITING and "approve" in c.detail
    cfg = config.load()
    cfg["router"]["approved_providers"] = ["groq"]
    assert doctor.check_router(ctx(fleet_env, run=up, cfg=cfg))[0].status == OK


def test_router_down_fails(fleet_env):
    down = Runner([(["is-active"], (3, "inactive"))])
    assert doctor.check_router(ctx(fleet_env, run=down))[0].status == FAIL


def test_the_mock_model_counts_as_a_provider(fleet_env):
    cfg = config.load()
    cfg["features"]["mock_llm"] = True
    assert doctor.check_router(ctx(fleet_env, run=Runner([(["is-active"], (0, "active"))]), cfg=cfg))[0].status == OK


def test_guard_degraded(fleet_env):
    up = Runner([(["is-active"], (0, "active"))])
    assert doctor.check_guard(ctx(fleet_env, run=up, http=lambda u, **k: (200, {"status": "OK", "mode": "model+rules"})))[0].status == OK
    c = doctor.check_guard(ctx(fleet_env, run=up, http=lambda u, **k: (200, {"status": "DEGRADED", "mode": "rules"})))[0]
    assert c.status == DEGRADED


def test_a_service_that_is_up_but_unhealthy_fails(fleet_env):
    up = Runner([(["is-active"], (0, "active"))])
    c = doctor.check_reader(ctx(fleet_env, run=up, http=lambda u, **k: (0, None)))[0]
    assert c.status == FAIL and "answers" in c.detail


def test_publisher_waits_until_a_platform_is_ready(fleet_env):
    up = Runner([(["is-active"], (0, "active"))])

    def http(url, **kw):
        if url.endswith("/platforms"):
            return 200, {"platforms": {"bluesky": {"enabled": True, "ready": True}, "mastodon": {"enabled": False, "ready": False}}}
        return 200, {"status": "OK", "mode": "dry-run"}
    c = doctor.check_publisher(ctx(fleet_env, run=up, http=http))[0]
    assert c.status == OK and "bluesky" in c.detail
    c = doctor.check_publisher(ctx(fleet_env, run=up, http=lambda u, **k: (200, {"status": "OK", "mode": "dry-run", "platforms": {}})))[0]
    assert c.status == WAITING


def test_deployer_waits_for_the_token(fleet_env):
    up = Runner([(["is-active"], (0, "active"))])
    assert doctor.check_deployer(ctx(fleet_env, run=up, http=lambda u, **k: (200, {"mode": "dry-run"})))[0].status == WAITING
    secrets.set_value("CLOUDFLARE_API_TOKEN", "c" * 40)
    assert doctor.check_deployer(ctx(fleet_env, run=up, http=lambda u, **k: (200, {"mode": "dry-run"})))[0].status == OK


def test_control_bot_waits_with_no_channel(fleet_env):
    up = Runner([(["is-active"], (0, "active"))])
    assert doctor.check_control(ctx(fleet_env, run=up, http=lambda u, **k: (200, {"channels": []})))[0].status == WAITING
    assert doctor.check_control(ctx(fleet_env, run=up, http=lambda u, **k: (200, {"channels": ["telegram"]})))[0].status == OK


def test_budget_freshness(fleet_env):
    path = fleet_env["state"] / "budget.json"
    assert doctor.check_budget(ctx(fleet_env))[0].status == DEGRADED
    path.write_text(json.dumps({"slowdown": False}))
    assert doctor.check_budget(ctx(fleet_env))[0].status == OK
    old = time.time() - 7200
    os.utime(path, (old, old))
    assert doctor.check_budget(ctx(fleet_env))[0].status == DEGRADED


def test_tailscale_waits_for_the_owner(fleet_env):
    assert doctor.check_tailscale(ctx(fleet_env, run=Runner([(["tailscale"], (0, "Logged out."))])))[0].status == WAITING
    assert doctor.check_tailscale(ctx(fleet_env, run=Runner([(["tailscale"], (0, "100.1.1.1 host user linux -"))])))[0].status == OK
    assert doctor.check_tailscale(ctx(fleet_env, run=Runner([(["tailscale"], (127, "x"))])))[0].status == FAIL


def test_docker_check_wants_rootless_daemons_and_no_system_daemon(fleet_env):
    ok = Runner([(["docker"], (0, "29.9.3 options=[name=seccomp,profile=builtin name=rootless]")), (["is-enabled"], (1, "disabled"))])
    assert doctor.check_docker(ctx(fleet_env, run=ok))[0].status == OK
    system_on = Runner([(["docker"], (0, "29.9.3 options=[name=seccomp,profile=builtin name=rootless]")), (["is-enabled"], (0, "enabled"))])
    assert doctor.check_docker(ctx(fleet_env, run=system_on))[0].status == FAIL
    not_rootless = Runner([(["docker"], (0, "29.9.3 options=[name=seccomp,profile=builtin]")), (["is-enabled"], (1, "disabled"))])
    assert doctor.check_docker(ctx(fleet_env, run=not_rootless))[0].status == FAIL


def test_managed_config_is_compared_with_the_pins(fleet_env, monkeypatch, tmp_path):
    managed = {"approvals": {"mode": "smart", "cron_mode": "deny", "deny": ["sudo *"]}, "terminal": {"backend": "docker"},
               "security": {"redact_secrets": True}, "updates": {"check": False}, "model": {"base_url": "http://127.0.0.1:20128/v1"}}
    f = tmp_path / "managed.yaml"
    f.write_text(yaml.safe_dump(managed))
    real_path = doctor.pathlib.Path
    monkeypatch.setattr(doctor.pathlib, "Path", lambda p: real_path(f if str(p) == "/etc/hermes/config.yaml" else p))
    assert doctor.check_managed_config(ctx(fleet_env))[0].status == OK
    managed["approvals"]["mode"] = "off"
    f.write_text(yaml.safe_dump(managed))
    c = doctor.check_managed_config(ctx(fleet_env))[0]
    assert c.status == FAIL and "approvals.mode" in c.detail


def test_cmd_doctor_json_and_exit_code(fleet_env, monkeypatch):
    from ops.fleetlib.status import Component as C
    monkeypatch.setattr(doctor, "run_all", lambda c, only=None: [C("x", OK, "fine"), C("y", WAITING)])
    s = Script()
    assert doctor.cmd_doctor(SimpleNamespace(json=True, only=None), s.con, Runner()) == 0
    assert [r["status"] for r in json.loads(s.text)] == ["OK", "WAITING_FOR_KEYS"]
    monkeypatch.setattr(doctor, "run_all", lambda c, only=None: [C("x", FAIL, "bad")])
    s2 = Script()
    assert doctor.cmd_doctor(SimpleNamespace(json=False, only=None), s2.con, Runner()) == 1 and "Exit code 1." in s2.text


# ---- n8n (Prompt 3, Phase 1) ----

def n8n_http(health=(200, {"status": "ok"})):
    def http(url, **kw):
        return health if url.endswith("/healthz") else (404, None)
    return http


def n8n_runner(active=True, running="2.47.4"):
    return Runner([(["is-active", "agent-fleet-n8n.service"], (0, "active") if active else (3, "inactive")),
                   (["docker", "inspect", "agent-fleet-n8n"], (0, running + "\n"))])


def test_n8n_ok_when_up_healthy_and_on_the_pinned_version(fleet_env, monkeypatch):
    monkeypatch.setattr(doctor, "_n8n_pinned_version", lambda c: "2.47.4")
    out = doctor.check_n8n(ctx(fleet_env, run=n8n_runner(), http=n8n_http()))[0]
    assert out.status == OK and "2.47.4" in out.detail


def test_n8n_fails_when_the_unit_is_down_or_unhealthy_or_on_another_version(fleet_env, monkeypatch):
    monkeypatch.setattr(doctor, "_n8n_pinned_version", lambda c: "2.47.4")
    assert doctor.check_n8n(ctx(fleet_env, run=n8n_runner(active=False), http=n8n_http()))[0].status == FAIL
    assert doctor.check_n8n(ctx(fleet_env, run=n8n_runner(), http=n8n_http(health=(503, None))))[0].status == FAIL
    wrong = doctor.check_n8n(ctx(fleet_env, run=n8n_runner(running="2.47.7"), http=n8n_http()))[0]
    assert wrong.status == FAIL and "2.47.4" in wrong.detail and "2.47.7" in wrong.detail


def test_the_pinned_version_comes_from_the_compose_env_that_upgrade_moves(fleet_env):
    from ops.n8n import prepare
    prepare.ensure_dirs()
    (prepare.run_dir() / ".env").write_text("N8N_VERSION=2.47.6\nN8N_IMAGE=x\n")
    assert doctor._n8n_pinned_version(ctx(fleet_env)) == "2.47.6"


def test_a_missing_container_label_skips_the_version_check(fleet_env, monkeypatch):
    monkeypatch.setattr(doctor, "_n8n_pinned_version", lambda c: "2.47.4")
    r = Runner([(["is-active", "agent-fleet-n8n.service"], (0, "active")), (["docker", "inspect"], (1, "no such container"))])
    assert doctor.check_n8n(ctx(fleet_env, run=r, http=n8n_http()))[0].status == OK


def test_n8n_can_be_switched_off_in_the_config(fleet_env):
    cfg = config.load()
    cfg["n8n"]["enabled"] = False
    assert doctor.check_n8n(ctx(fleet_env, cfg=cfg))[0].status == OK


def test_the_n8n_ports_must_be_loopback_only(fleet_env):
    exposed = "LISTEN 0 511 0.0.0.0:5679 0.0.0.0:*\nLISTEN 0 511 127.0.0.1:5678 0.0.0.0:*"
    out = doctor.check_ports(ctx(fleet_env, run=Runner([(["ss"], (0, exposed))])))[0]
    assert out.status == FAIL and "5679" in out.detail
    fine = "LISTEN 0 511 127.0.0.1:5679 0.0.0.0:*\nLISTEN 0 511 127.0.0.1:5678 0.0.0.0:*"
    assert doctor.check_ports(ctx(fleet_env, run=Runner([(["ss"], (0, fine))])))[0].status == OK


def test_the_workflow_check_needs_the_api_key_and_every_workflow_published(fleet_env):
    from tests.fake_n8n import FakeN8n
    from ops.n8n import bootstrap as bs, client as nc, prepare
    from ops.fleetlib import tokens
    assert doctor.check_n8n_workflows(ctx(fleet_env))[0].status == FAIL and "bootstrap" in doctor.check_n8n_workflows(ctx(fleet_env))[0].detail
    prepare.ensure_dirs()
    (prepare.run_dir() / ".env").write_text("N8N_OWNER_EMAIL=owner@example.org\n")
    secrets.set_value("N8N_OWNER_PASSWORD", "pw-test-0123456789abcdef")
    for name in ("FLEET_EVENT_TOKEN", "N8N_TOOLS_TOKEN"):
        secrets.set_value(name, "x" * 40)
    tokens.rotate("FLEET_ACTION_TOKEN")
    with FakeN8n("owner@example.org", "pw-test-0123456789abcdef") as fake:
        config.set_override("ports.n8n", str(fake.port))
        bs.bootstrap(c=nc.Client(fake.url, sleep=lambda s: None))
        assert doctor.check_n8n_workflows(ctx(fleet_env))[0].status == OK
        next(w for w in fake.workflows.values() if w["name"] == "fleet-tools")["active"] = False
        bad = doctor.check_n8n_workflows(ctx(fleet_env))[0]
        assert bad.status == FAIL and "fleet-tools" in bad.detail


def test_the_event_relay_check(fleet_env):
    from ops.fleetlib import jsonio, paths
    assert doctor.check_events(ctx(fleet_env))[0].status == OK
    f = paths.ops_state_dir() / "events" / "status.json"
    jsonio.write_json(f, {"depth": 3, "oldest_seconds": 60, "sent": 40, "dropped": 0})
    assert doctor.check_events(ctx(fleet_env))[0].status == OK
    jsonio.write_json(f, {"depth": 80, "oldest_seconds": 60, "sent": 40, "dropped": 0})
    assert doctor.check_events(ctx(fleet_env))[0].status == DEGRADED
    jsonio.write_json(f, {"depth": 2, "oldest_seconds": 1800, "sent": 40, "dropped": 0})
    out = doctor.check_events(ctx(fleet_env))[0]
    assert out.status == DEGRADED and "30 minutes" in out.detail


def test_the_unsent_alert_check_is_degraded_when_an_alert_waits_too_long(fleet_env):
    from ops.control import unsent
    from ops.n8n import prepare
    prepare.ensure_dirs()
    assert doctor.check_unsent_alerts(ctx(fleet_env))[0].status == OK
    f = unsent.unsent_dir() / "alert-5-slack.json"
    f.write_text("{}")
    now = f.stat().st_mtime
    fresh = doctor.Ctx(config.load(), run=Runner(), http=lambda url, **kw: (200, {}), now=lambda: now + 120)
    assert doctor.check_unsent_alerts(fresh)[0].status == OK
    late = doctor.Ctx(config.load(), run=Runner(), http=lambda url, **kw: (200, {}), now=lambda: now + 1800)
    out = doctor.check_unsent_alerts(late)[0]
    assert out.status == DEGRADED and "30 minutes" in out.detail and "control" in out.detail


def test_the_n8n_backup_check_is_ok_when_fresh_and_degraded_when_old(fleet_env):
    from ops.n8n import prepare
    prepare.ensure_dirs()
    assert doctor.check_n8n_backup(ctx(fleet_env))[0].status == OK and "none yet" in doctor.check_n8n_backup(ctx(fleet_env))[0].detail
    folder = prepare.run_dir() / "backups" / "n8n-20261115-022000"
    folder.mkdir()
    now = folder.stat().st_mtime
    c = doctor.Ctx(config.load(), run=Runner(), http=lambda url, **kw: (200, {}), now=lambda: now + 3600)
    assert doctor.check_n8n_backup(c)[0].status == OK
    c = doctor.Ctx(config.load(), run=Runner(), http=lambda url, **kw: (200, {}), now=lambda: now + 40 * 3600)
    assert doctor.check_n8n_backup(c)[0].status == DEGRADED


# ------------------------------------------------------------------------------------------------ the clean-machine test. See D-051.
def test_container_test_mode_skips_what_needs_systemd_or_docker_and_still_runs_the_rest(fleet_env, monkeypatch, tmp_path):
    marker = tmp_path / "test-mode"
    marker.write_text("container\n")
    monkeypatch.setenv("FLEET_TEST_MODE_FILE", str(marker))
    results = doctor.run_all(ctx(fleet_env))
    by = {c.name: c for c in results}
    for name in ("docker", "router", "guard", "n8n", "gateway", "firewall", "merger", "publisher", "control"):
        assert by[name].status == "SKIPPED" and "container test mode" in by[name].detail, name
    assert by["config"].status != "SKIPPED" and by["users"].status != "SKIPPED" and by["locked_core"].status != "SKIPPED"
    assert doctor.exit_code([c for c in results if c.status == "SKIPPED"]) == 0
    assert "SKIPPED (container test mode)" in doctor.render(results)


def test_without_the_marker_nothing_is_skipped(fleet_env, monkeypatch, tmp_path):
    monkeypatch.setenv("FLEET_TEST_MODE_FILE", str(tmp_path / "missing"))
    assert not doctor.test_mode()
    assert not [c for c in doctor.run_all(ctx(fleet_env), only="docker") if c.status == "SKIPPED"]


def test_skipped_counts_as_good_for_the_exit_code_but_a_fail_does_not():
    from ops.fleetlib.status import Component
    assert doctor.exit_code([Component("a", "SKIPPED"), Component("b", "OK"), Component("c", "WAITING_FOR_KEYS")]) == 0
    assert doctor.exit_code([Component("a", "SKIPPED"), Component("b", "FAIL")]) == 1
    assert doctor.exit_code([Component("a", "DEGRADED")]) == 1


# ------------------------------------------------------------------------------------------------ Slack and the board. See D-052.
HQ_BOT = "xoxb-" + "h" * 24
HQ_APP = "xapp-" + "h" * 24


def slack_ctx(fleet_env, state="active", channels=None, sink=True):
    from ops.fleetlib import paths
    cfg = config.load()
    cfg["slack"]["channels"] = channels or {}
    cfg["slack"]["sink_mode"] = sink
    return ctx(fleet_env, run=Runner([(["is-active"], (0, state))]), cfg=cfg)


def test_slack_waits_for_keys_until_the_fleet_hq_tokens_exist(fleet_env):
    out = {c.name: c for c in doctor.check_slack(slack_ctx(fleet_env))}
    assert out["slack"].status == WAITING and "docs/SLACK.md" in out["slack"].detail and out["slack_hermes"].status == WAITING


def test_slack_goes_from_failing_to_degraded_to_ok_as_it_is_set_up(fleet_env):
    from ops.fleetlib import secrets
    fleet_env["secrets"].mkdir(exist_ok=True)
    secrets.set_value("SLACK_HQ_BOT_TOKEN", HQ_BOT)
    assert doctor.check_slack(slack_ctx(fleet_env))[0].status == WAITING                        # one token is not enough
    secrets.set_value("SLACK_HQ_APP_TOKEN", HQ_APP)
    assert doctor.check_slack(slack_ctx(fleet_env, state="inactive"))[0].status == FAIL
    no_channels = doctor.check_slack(slack_ctx(fleet_env))[0]
    assert no_channels.status == DEGRADED and "fleetctl slack bootstrap" in no_channels.detail
    sink = doctor.check_slack(slack_ctx(fleet_env, channels={"hq": "chan-hq"}))[0]
    assert sink.status == DEGRADED and "slack.sink_mode false" in sink.detail
    done = doctor.check_slack(slack_ctx(fleet_env, channels={"hq": "chan-hq", "brief": "chan-brief"}, sink=False))[0]
    assert done.status == OK and "2 channels" in done.detail


def test_the_hermes_tokens_are_reported_apart_from_the_hq_tokens(fleet_env):
    from ops.fleetlib import secrets
    fleet_env["secrets"].mkdir(exist_ok=True)
    secrets.set_value("SLACK_HERMES_BOT_TOKEN", "xoxb-" + "e" * 24)
    secrets.set_value("SLACK_HERMES_APP_TOKEN", "xapp-" + "e" * 24)
    out = {c.name: c for c in doctor.check_slack(slack_ctx(fleet_env))}
    assert out["slack_hermes"].status == OK and out["slack"].status == WAITING


def test_the_board_exporter_check(fleet_env):
    import os
    from ops.board import export
    assert doctor.check_board(slack_ctx(fleet_env, state="failed"))[0].status == FAIL
    assert doctor.check_board(slack_ctx(fleet_env))[0].status == DEGRADED
    snap = export.snapshot_file()
    snap.parent.mkdir(parents=True, exist_ok=True)
    snap.write_text("{}")
    now = snap.stat().st_mtime
    fresh = doctor.Ctx(config.load(), run=Runner([(["is-active"], (0, "active"))]), http=lambda url, **kw: (200, {}), now=lambda: now + 30)
    assert doctor.check_board(fresh)[0].status == OK
    old = doctor.Ctx(config.load(), run=Runner([(["is-active"], (0, "active"))]), http=lambda url, **kw: (200, {}), now=lambda: now + 900)
    assert doctor.check_board(old)[0].status == DEGRADED and "15 minutes" in doctor.check_board(old)[0].detail


def test_the_slack_check_never_prints_a_token(fleet_env):
    from ops.fleetlib import secrets
    fleet_env["secrets"].mkdir(exist_ok=True)
    secrets.set_value("SLACK_HQ_BOT_TOKEN", HQ_BOT)
    secrets.set_value("SLACK_HQ_APP_TOKEN", HQ_APP)
    text = " ".join(c.line() for c in doctor.check_slack(slack_ctx(fleet_env)))
    assert HQ_BOT not in text and HQ_APP not in text


# ---- D-053: the running control bot must accept the action token of n8n
def test_the_action_token_check_passes_when_the_bot_accepts_it(fleet_env):
    from ops.fleetlib import secrets
    secrets.set_value("FLEET_ACTION_TOKEN", "k" * 48)
    seen = {}

    def http(url, **kw):
        seen.update(url=url, headers=kw.get("headers"))
        return 200, {}
    out = doctor.check_action_token(ctx(fleet_env, http=http))[0]
    assert out.status == OK and seen["headers"] == {"Authorization": "Bearer " + "k" * 48} and "k" * 48 not in out.detail
    assert seen["url"].endswith("/v1/status")


def test_the_action_token_check_fails_with_the_fix_when_the_bot_says_401(fleet_env):
    from ops.fleetlib import secrets
    secrets.set_value("FLEET_ACTION_TOKEN", "k" * 48)
    out = doctor.check_action_token(ctx(fleet_env, http=lambda url, **kw: (401, {"error": "x"})))[0]
    assert out.status == FAIL and "systemctl restart agent-fleet-control" in out.detail and "k" * 48 not in out.detail


def test_the_action_token_check_waits_without_a_token_and_degrades_when_the_bot_is_silent(fleet_env):
    from ops.fleetlib import secrets
    assert doctor.check_action_token(ctx(fleet_env))[0].status == WAITING
    secrets.set_value("FLEET_ACTION_TOKEN", "k" * 48)
    assert doctor.check_action_token(ctx(fleet_env, http=lambda url, **kw: (0, None)))[0].status == DEGRADED
    assert doctor.check_action_token(ctx(fleet_env, http=lambda url, **kw: (500, None)))[0].status == DEGRADED


def test_the_action_token_check_is_off_when_n8n_is_off(fleet_env):
    cfg = config.load()
    cfg["n8n"]["enabled"] = False
    assert doctor.check_action_token(ctx(fleet_env, cfg=cfg)) == []


# ---- D-063: the first full clean-machine run showed two checks that did not know about the container
def test_in_container_mode_a_missing_hermes_hooks_folder_is_not_a_failure(fleet_env, monkeypatch, tmp_path):
    marker = tmp_path / "test-mode"
    marker.write_text("container\n")
    monkeypatch.setenv("FLEET_TEST_MODE_FILE", str(marker))
    c = ctx(fleet_env)
    c.hermes_home = lambda: str(tmp_path / "no-hermes")
    detail = doctor.check_locked_core(c)[0].detail
    assert "/hooks is missing" not in detail


def test_on_a_real_machine_a_missing_hooks_folder_is_still_a_failure(fleet_env, monkeypatch, tmp_path):
    monkeypatch.setenv("FLEET_TEST_MODE_FILE", str(tmp_path / "missing"))
    c = ctx(fleet_env)
    c.hermes_home = lambda: str(tmp_path / "no-hermes")
    out = doctor.check_locked_core(c)[0]
    assert out.status == FAIL and "is missing" in out.detail


def test_in_container_mode_internal_keys_that_need_docker_are_skipped_not_failed(fleet_env, monkeypatch, tmp_path):
    marker = tmp_path / "test-mode"
    marker.write_text("container\n")
    monkeypatch.setenv("FLEET_TEST_MODE_FILE", str(marker))
    by = {c.name: c for c in doctor.check_secrets(ctx(fleet_env))}
    assert by["keys_internal"].status == "SKIPPED" and "need Docker" in by["keys_internal"].detail


def test_on_a_real_machine_a_missing_internal_key_is_a_failure(fleet_env, monkeypatch, tmp_path):
    monkeypatch.setenv("FLEET_TEST_MODE_FILE", str(tmp_path / "missing"))
    by = {c.name: c for c in doctor.check_secrets(ctx(fleet_env))}
    assert by["keys_internal"].status == FAIL and "missing:" in by["keys_internal"].detail
