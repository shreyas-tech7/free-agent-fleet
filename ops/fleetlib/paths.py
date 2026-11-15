"""Paths that every part of the fleet uses.

Tests and development can override a path with an environment variable.
Root ignores the overrides unless FLEET_TESTING=1, so a hostile environment
cannot redirect a root process.
"""
import os
import pathlib


def _override_allowed():
    return os.geteuid() != 0 or os.environ.get("FLEET_TESTING") == "1"


def _get(env, default):
    value = os.environ.get(env)
    if value and _override_allowed():
        return pathlib.Path(value)
    return pathlib.Path(default)


def repo_root():
    """The directory that holds this code (the live copy or a development clone)."""
    return pathlib.Path(__file__).resolve().parents[2]


def config_file():
    return _get("FLEET_CONFIG", "/etc/agent-fleet/fleet.yaml")


def owner_config():
    """The private owner file. It sits beside the live config. See D-046."""
    env = os.environ.get("FLEET_OWNER_CONFIG")
    return pathlib.Path(env) if env else pathlib.Path(config_file()).parent / "owner.yaml"


def secrets_dir():
    return _get("FLEET_SECRETS_DIR", "/etc/agent-fleet/secrets")


def state_dir():
    return _get("FLEET_STATE", "/var/lib/agent-fleet")


def ops_state_dir():
    return _get("FLEET_OPS_STATE", "/var/lib/agent-fleet-ops")


def hermes_home():
    return _get("FLEET_HERMES_HOME", "/home/fleet/.hermes")


def live_repo():
    return _get("FLEET_LIVE_REPO", "/opt/agent-fleet/repo")


def hidden_tests_dir():
    return _get("FLEET_HIDDEN_TESTS", "/opt/agent-fleet/core/tests/hidden")


def skills_view_dir():
    """Root-owned folder with one subfolder for each agent. Each holds a copy of the skills that the agent may use."""
    return _get("FLEET_SKILLS_VIEW", "/opt/agent-fleet/skills-view")


def paused_flag():
    return state_dir() / "PAUSED"


def budget_file():
    return state_dir() / "budget.json"


def work_clone():
    return _get("FLEET_WORK_CLONE", str(state_dir() / "work" / "agent-fleet"))


def merger_dir():
    """Root-only state of the merger: staging repo, run folders, history."""
    return _get("FLEET_MERGER_STATE", "/var/lib/agent-fleet-merger")


def merger_runs_dir():
    """Run folders that the throwaway container may read. They belong to fleetops. The merger deletes them after each run."""
    return _get("FLEET_MERGER_RUNS", str(ops_state_dir() / "merger" / "runs"))


def example_config():
    return repo_root() / "config" / "fleet.example.yaml"


def secrets_registry():
    return repo_root() / "config" / "secrets.yaml"


def stages_file():
    return repo_root() / "config" / "stages.yaml"
