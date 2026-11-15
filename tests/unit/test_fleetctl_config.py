import json
from types import SimpleNamespace

import pytest
import yaml

from ops.fleetctl import cfgcmd
from ops.fleetlib import config
from tests.unit.fctl_helpers import Runner, Script


def ns(**kw):
    return SimpleNamespace(**kw)


def test_set_writes_only_the_changed_key(fleet_env):
    s = Script()
    assert cfgcmd.cmd_set(ns(key="publisher.mode", value="live"), s.con, Runner()) == 0
    assert yaml.safe_load(fleet_env["config"].read_text()) == {"publisher": {"mode": "live"}}
    assert config.get(config.load(), "publisher.mode") == "live"


def test_a_list_and_a_number_and_a_bool_are_typed(fleet_env):
    r = Runner()
    s = Script()
    assert cfgcmd.cmd_set(ns(key="owner.telegram_user_ids", value="[1234567]"), s.con, r) == 0
    assert cfgcmd.cmd_set(ns(key="limits.max_concurrent_workers", value="2"), s.con, r) == 0
    assert cfgcmd.cmd_set(ns(key="features.mock_llm", value="true"), s.con, r) == 0
    cfg = config.load()
    assert config.get(cfg, "owner.telegram_user_ids") == [1234567]
    assert config.get(cfg, "limits.max_concurrent_workers") == 2 and config.get(cfg, "features.mock_llm") is True


@pytest.mark.parametrize("key,value,why", [
    ("deployer.plan", "paid", "free"),
    ("publisher.mode", "maybe", "dry-run or live"),
    ("owner.timezone", "Mars", "IANA"),
    ("no.such.key", "1", "unknown config key"),
    ("limits.max_concurrent_workers", "many", "whole number"),
    ("features.mock_llm", "perhaps", "true or false"),
    ("owner.telegram_user_ids", "5", "list"),
])
def test_a_bad_change_is_refused_and_nothing_is_written(fleet_env, key, value, why):
    s = Script()
    assert cfgcmd.cmd_set(ns(key=key, value=value), s.con, Runner()) == 2
    assert why in s.text and "Not changed" in s.text
    assert not fleet_env["config"].exists()


def test_rule_r1_the_deployer_plan_cannot_leave_free(fleet_env):
    with pytest.raises(config.ConfigError):
        cfgcmd.would_be("deployer.plan", "pro")


def test_a_publisher_change_restarts_the_publisher_if_it_runs(fleet_env):
    r = Runner(rules=[(["is-active", "agent-fleet-publisher.service"], (0, "active"))])
    s = Script()
    cfgcmd.cmd_set(ns(key="publisher.mode", value="live"), s.con, r)
    assert r.ran("restart", "agent-fleet-publisher.service")
    assert "agent-fleet-publisher restarted" in s.text


def test_a_stopped_service_is_not_started_by_a_config_change(fleet_env):
    r = Runner(rules=[(["is-active"], (3, "inactive"))])
    cfgcmd.cmd_set(ns(key="guard.threshold", value="0.95"), Script().con, r)
    assert not r.ran("restart") and not r.ran("start")


def test_time_changes_render_the_units_again(fleet_env):
    r = Runner()
    s = Script()
    cfgcmd.cmd_set(ns(key="briefing.time", value="06:30"), s.con, r)
    assert r.ran("bash") and any("60-units.sh" in " ".join(a) for a, _u in r.calls)


def test_worker_limit_goes_to_hermes(fleet_env):
    r = Runner()
    cfgcmd.cmd_set(ns(key="limits.max_concurrent_workers", value="2"), Script().con, r)
    assert r.ran("hermes", "config", "set", "kanban.max_in_progress", "2")


def test_router_changes_point_to_the_apply_step(fleet_env):
    s = Script()
    cfgcmd.cmd_set(ns(key="router.approved_providers", value="[groq]"), s.con, Runner())
    assert "fleetctl router apply" in s.text


def test_get_and_show(fleet_env):
    cfgcmd.cmd_set(ns(key="briefing.time", value="08:15"), Script().con, Runner())
    s = Script()
    assert cfgcmd.cmd_get(ns(key="briefing.time"), s.con) == 0 and "08:15" in s.text
    s2 = Script()
    assert cfgcmd.cmd_get(ns(key="briefing"), s2.con) == 0 and json.loads(s2.text)["time"] == "08:15"
    assert cfgcmd.cmd_get(ns(key="nothing.here"), Script().con) == 1
    s3 = Script()
    cfgcmd.cmd_show(ns(), s3.con)
    assert "08:15" in s3.text
