"""Shared pytest setup. Every test runs with the fleet paths moved into a temp folder."""
import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent


from tests.clock import NOON_CHICAGO      # the same constant, in a module that nothing else shares a name with


@pytest.fixture(autouse=True)
def no_container_marker(monkeypatch, tmp_path_factory):
    """The clean-machine test leaves /etc/agent-fleet/test-mode on the machine. A unit test must not see it. A test that wants it sets its own path. See D-063."""
    monkeypatch.setenv("FLEET_TEST_MODE_FILE", str(tmp_path_factory.getbasetemp() / "no-container-marker"))


@pytest.fixture(autouse=True)
def noon_clock(monkeypatch):
    from ops.control import quiet
    monkeypatch.setattr(quiet, "_clock", lambda: NOON_CHICAGO)


@pytest.fixture
def fleet_env(tmp_path, monkeypatch):
    """A fake machine layout. Nothing touches the real /etc or /var."""
    dirs = {
        "config": tmp_path / "etc" / "fleet.yaml",
        "secrets": tmp_path / "etc" / "secrets",
        "state": tmp_path / "var" / "agent-fleet",
        "ops": tmp_path / "var" / "agent-fleet-ops",
        "hermes": tmp_path / "home" / ".hermes",
    }
    dirs["secrets"].mkdir(parents=True)
    dirs["config"].parent.mkdir(parents=True, exist_ok=True)
    dirs["state"].mkdir(parents=True)
    dirs["ops"].mkdir(parents=True)
    for agent in ("orchestrator", "research", "build", "marketing", "feedback", "security", "improver"):
        (dirs["hermes"] / "profiles" / agent).mkdir(parents=True)
    monkeypatch.setenv("FLEET_TESTING", "1")
    monkeypatch.setenv("FLEET_CONFIG", str(dirs["config"]))
    monkeypatch.setenv("FLEET_SECRETS_DIR", str(dirs["secrets"]))
    monkeypatch.setenv("FLEET_STATE", str(dirs["state"]))
    monkeypatch.setenv("FLEET_OPS_STATE", str(dirs["ops"]))
    monkeypatch.setenv("FLEET_HERMES_HOME", str(dirs["hermes"]))
    return dirs
