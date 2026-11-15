"""Policy: fleet work never reaches fleet-dev.

fleet-dev holds the NVIDIA and Cohere trial models. Their trial terms forbid business use.
Only tests may call it. No fleet agent, fleet combo, or fleet client key may reach it. See D-031.
"""
import copy
import pathlib

import pytest
import yaml

from ops.router import apply as ra

ROOT = pathlib.Path(__file__).resolve().parents[3]
FLEET_COMBOS = ("fleet-smart", "fleet-worker", "fleet-coder", "fleet-local")
# The only files that may name fleet-dev. Tests and docs may name it as well.
MAY_NAME_IT = {"ops/router/apply.py", "ops/router/litellm_gen.py", "config/router/combos.yaml", "config/secrets.yaml"}
CODE_DIRS = ("ops", "agents", "skills", "config", "install", "systemd", "scripts", "projects")


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load((ROOT / "config" / "router" / "combos.yaml").read_text())


def trial_providers(cfg):
    return {pid for pid, p in cfg["providers"].items() if p.get("commercial_use") is False}


def test_fleet_dev_exists_and_it_is_the_only_dev_only_combo(cfg):
    assert cfg["combos"]["fleet-dev"]["dev_only"] is True
    assert [name for name, combo in cfg["combos"].items() if combo.get("dev_only")] == ["fleet-dev"]


def test_the_trial_providers_are_nvidia_and_cohere(cfg):
    assert trial_providers(cfg) == {"nvidia", "cohere"}


def test_no_fleet_combo_holds_a_trial_provider(cfg):
    trial = trial_providers(cfg)
    for name in FLEET_COMBOS:
        providers = {t.split("/")[0] for t in cfg["combos"][name]["targets"]}
        assert not providers & trial, "%s holds %s" % (name, sorted(providers & trial))


def test_fleet_dev_holds_only_trial_providers(cfg):
    providers = {t.split("/")[0] for t in cfg["combos"]["fleet-dev"]["targets"]}
    assert providers and providers <= trial_providers(cfg)


def test_no_fleet_client_key_can_call_fleet_dev():
    for label, (_secret, allowed) in ra.CLIENTS.items():
        if label == "dev":
            assert allowed == ["fleet-dev"]
        else:
            assert "fleet-dev" not in allowed, label


def test_no_agent_uses_fleet_dev_as_its_model_or_fallback():
    files = sorted((ROOT / "agents").glob("*/agent.yaml"))
    assert files
    for path in files:
        assert "fleet-dev" not in path.read_text(), path.name
        model = yaml.safe_load(path.read_text()).get("model")
        assert model in FLEET_COMBOS, "%s uses %r" % (path.parent.name, model)


def test_only_the_router_files_name_fleet_dev():
    found = set()
    for top in CODE_DIRS:
        base = ROOT / top
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix in (".pyc", ".onnx", ".png", ".svg"):
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, PermissionError):
                continue
            if "fleet-dev" in text:
                found.add(str(path.relative_to(ROOT)))
    assert found <= MAY_NAME_IT, sorted(found - MAY_NAME_IT)


def test_the_fallback_router_does_not_serve_fleet_dev():
    fallback = yaml.safe_load((ROOT / "config" / "router" / "litellm.yaml").read_text())
    assert "fleet-dev" not in {m["model_name"] for m in fallback["model_list"]}


def test_check_scopes_accepts_the_real_config(cfg):
    ra.check_scopes(cfg)


def test_check_scopes_refuses_a_trial_provider_in_a_fleet_combo(cfg):
    bad = copy.deepcopy(cfg)
    bad["combos"]["fleet-worker"]["targets"].append("cohere/command-r-08-2024")
    with pytest.raises(ra.RouterError, match="trial terms"):
        ra.check_scopes(bad)


def test_check_scopes_refuses_a_fleet_client_that_may_call_fleet_dev(cfg):
    clients = dict(ra.CLIENTS)
    clients["hermes"] = ("ROUTER_KEY_HERMES", ["fleet-smart", "fleet-dev"])
    with pytest.raises(ra.RouterError, match="dev_only"):
        ra.check_scopes(cfg, clients)


def test_check_scopes_refuses_a_dev_key_that_may_call_a_fleet_combo(cfg):
    clients = dict(ra.CLIENTS)
    clients["dev"] = ("ROUTER_KEY_DEV", ["fleet-dev", "fleet-worker"])
    with pytest.raises(ra.RouterError, match="dev key"):
        ra.check_scopes(cfg, clients)
