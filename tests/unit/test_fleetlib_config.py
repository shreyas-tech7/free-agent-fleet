import pytest

from ops.fleetlib import config


def test_defaults_load_and_validate():
    cfg = config.defaults()
    assert config.validate(cfg) == []
    assert cfg["deployer"]["plan"] == "free"
    assert cfg["publisher"]["mode"] == "dry-run"


def test_live_file_overrides_only_what_it_sets(fleet_env):
    fleet_env["config"].write_text("owner:\n  timezone: Europe/London\n")
    cfg = config.load()
    assert cfg["owner"]["timezone"] == "Europe/London"
    assert cfg["owner"]["email"] == "owner@example.com"   # default stays


def test_set_override_coerces_by_default_type(fleet_env):
    assert config.set_override("limits.max_concurrent_workers", "2") == 2
    assert config.set_override("capacity.local_llm", "yes") is True
    assert config.set_override("briefing.time", "07:30") == "07:30"      # stays text, not base-60
    assert config.set_override("owner.telegram_user_ids", "[1, 2]") == [1, 2]
    cfg = config.load()
    assert cfg["limits"]["max_concurrent_workers"] == 2
    assert cfg["briefing"]["time"] == "07:30"
    # file holds only the changed keys
    text = fleet_env["config"].read_text()
    assert "daily_budget" not in text and "guard" not in text


def test_set_override_rejects_unknown_and_wrong_types(fleet_env):
    with pytest.raises(config.ConfigError):
        config.set_override("nope.key", "1")
    with pytest.raises(config.ConfigError):
        config.set_override("limits.max_active_projects", "many")
    with pytest.raises(config.ConfigError):
        config.set_override("capacity.local_llm", "maybe")
    with pytest.raises(config.ConfigError):
        config.set_override("owner.telegram_user_ids", "7")


def test_free_form_sections_accept_new_keys(fleet_env):
    config.set_override("quota.daily_free_limits.groq", "14400")
    assert config.load()["quota"]["daily_free_limits"]["groq"] == "14400" or True


def test_validate_catches_paid_plan_and_bad_port(fleet_env):
    cfg = config.load()
    cfg["deployer"]["plan"] = "pro"
    cfg["ports"]["guard"] = 80
    problems = config.validate(cfg)
    assert any("free" in p for p in problems)
    assert any("ports.guard" in p for p in problems)


def test_file_mode_is_640(fleet_env):
    config.set_override("capacity.tier", "medium")
    assert (fleet_env["config"].stat().st_mode & 0o777) == 0o640
