"""The systemd unit templates and the Slack installer step. See D-052.

CLAUDE.md says that every fleet unit sets HERMES_MANAGED_DIR. The kill-switch parts run outside agent-fleet.target.
Only the merger, the n8n migration job, and the pivot job run as root. No unit holds a secret value.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = sorted((ROOT / "systemd").glob("*.service.in"))
OUTSIDE_TARGET = {"agent-fleet-control.service.in", "agent-fleet-slack.service.in"}     # /fleet stop must not stop the part that answers it
ROOT_UNITS = {"agent-fleet-merger.service.in", "agent-fleet-n8n-migration.service.in", "agent-fleet-pivot.service.in"}
PATH_TRIGGERED = {"agent-fleet-cards.service.in", "agent-fleet-pivot.service.in"}        # a .path unit starts these


def logical_lines(text):
    """systemd joins a line that ends in a backslash with the next one."""
    joined, buffer = [], ""
    for line in text.splitlines():
        if line.rstrip().endswith("\\"):
            buffer += line.rstrip()[:-1] + " "
        else:
            joined.append(buffer + line)
            buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


def sections(path):
    out, current = {}, None
    for line in logical_lines(path.read_text()):
        m = re.match(r"^\[(\w+)\]$", line.strip())
        if m:
            current = m.group(1)
            out.setdefault(current, [])
        elif current and line.strip() and not line.lstrip().startswith("#"):
            out[current].append(line.strip())
    return out


def values(path, section, key):
    return [line.split("=", 1)[1] for line in sections(path).get(section, []) if line.startswith(key + "=")]


def test_there_are_unit_templates_to_check():
    assert len(SERVICES) >= 20
    assert {"agent-fleet-slack.service.in", "agent-fleet-board.service.in"} <= {p.name for p in SERVICES}


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_every_service_pins_the_hermes_managed_scope(unit):
    assert "/etc/hermes" in values(unit, "Service", "Environment") or any(v == "HERMES_MANAGED_DIR=/etc/hermes" for v in values(unit, "Service", "Environment"))


def memory_capped(unit):
    """A plain unit sets MemoryMax. A container unit caps the container with --memory, or with mem_limit in its compose file."""
    if values(unit, "Service", "MemoryMax"):
        return True
    commands = " ".join(values(unit, "Service", "ExecStart"))
    if re.search(r"docker run .*--memory\s+\S+", commands):
        return True
    if "docker compose" in commands:
        return "mem_limit" in (ROOT / "n8n" / "docker-compose.yml").read_text()
    return False


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_every_service_has_a_user_a_memory_cap_and_an_absolute_command(unit):
    assert values(unit, "Service", "User"), "no User= line"
    assert memory_capped(unit), "no memory cap on the unit or on its container"
    for command in values(unit, "Service", "ExecStart"):
        assert command.startswith("/"), command


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_only_the_named_units_run_as_root(unit):
    user = values(unit, "Service", "User")[0]
    assert (user == "root") == (unit.name in ROOT_UNITS), "%s runs as %s" % (unit.name, user)
    assert user in ("root", "fleet", "fleetops")


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_the_kill_switch_parts_stay_outside_the_target_and_the_rest_stay_inside(unit):
    in_target = any("agent-fleet.target" in v for key in ("PartOf", "BindsTo") for v in values(unit, "Unit", key)) or \
        any("agent-fleet.target" in v for v in values(unit, "Install", "WantedBy"))
    if unit.name in OUTSIDE_TARGET:
        assert not in_target, "/fleet stop would stop this unit"
        assert "multi-user.target" in " ".join(values(unit, "Install", "WantedBy"))
    elif unit.name not in PATH_TRIGGERED:
        assert in_target, "%s would survive a stop of the target" % unit.name


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_no_unit_holds_a_secret_value(unit):
    for line in values(unit, "Service", "Environment"):
        name, _, value = line.partition("=")
        assert not re.search(r"(TOKEN|KEY|PASSWORD|SECRET|PASS)$", name.strip('"')), "%s sets a secret in the unit" % name
        assert not re.search(r"(xox[abp]-|xapp-|sk-|ghp_|AKIA)", value)
    assert not re.search(r"(?<!\w)/home/(?!fleet\b|fleetops\b)[a-z]", unit.read_text().replace("/home/fleet", "").replace("/home/fleetops", "")), "a home path of a person"


@pytest.mark.parametrize("unit", SERVICES, ids=lambda p: p.name)
def test_the_repository_path_is_a_placeholder(unit):
    body = "\n".join(values(unit, "Service", "WorkingDirectory") + values(unit, "Service", "Environment"))
    assert "/opt/agent-fleet/repo" not in body.replace("@REPO@", ""), "use @REPO@ so the installer can place the repository"


# ------------------------------------------------------------------------------------------------ the Slack adapter and the board exporter
def test_the_slack_adapter_sees_only_the_control_secret_file_and_runs_the_locked_venv():
    unit = ROOT / "systemd" / "agent-fleet-slack.service.in"
    assert values(unit, "Service", "ExecStart") == ["/opt/agent-fleet/venvs/control/bin/python -m ops.control.slackbot"]
    assert values(unit, "Service", "BindReadOnlyPaths") == ["/etc/agent-fleet/secrets/control.env"]
    assert values(unit, "Service", "TemporaryFileSystem") == ["/etc/agent-fleet/secrets:ro"]
    assert values(unit, "Service", "Restart") == ["always"]
    assert values(unit, "Service", "User") == ["fleetops"]


def test_the_slack_tokens_live_in_the_file_that_the_adapter_can_see():
    import yaml
    secrets = yaml.safe_load((ROOT / "config" / "secrets.yaml").read_text())
    entries = {s["name"]: s for s in (secrets.get("secrets") or secrets)}
    assert pathlib.PurePath(entries["SLACK_HQ_BOT_TOKEN"]["file"]).name == "control.env"
    assert pathlib.PurePath(entries["SLACK_HQ_APP_TOKEN"]["file"]).name == "control.env"


def test_the_hermes_slack_tokens_go_to_the_orchestrator_env_under_the_names_that_hermes_reads():
    import yaml
    secrets = yaml.safe_load((ROOT / "config" / "secrets.yaml").read_text())
    entries = {s["name"]: s for s in (secrets.get("secrets") or secrets)}
    assert entries["SLACK_HERMES_BOT_TOKEN"]["alias"] == "SLACK_BOT_TOKEN"
    assert entries["SLACK_HERMES_APP_TOKEN"]["alias"] == "SLACK_APP_TOKEN"
    for name in ("SLACK_HERMES_BOT_TOKEN", "SLACK_HERMES_APP_TOKEN"):
        assert "orchestrator" in entries[name]["file"] and entries[name]["file"].endswith(".env")


def test_the_board_exporter_runs_as_fleet_inside_the_target_and_writes_only_the_snapshot_folder():
    unit = ROOT / "systemd" / "agent-fleet-board.service.in"
    assert values(unit, "Service", "User") == ["fleet"]
    assert values(unit, "Service", "ExecStart") == ["/usr/bin/python3 -m ops.board.export --loop"]
    assert values(unit, "Service", "NoNewPrivileges") == ["yes"]
    assert "agent-fleet.target" in values(unit, "Unit", "PartOf")


def test_the_slack_installer_step_is_safe_and_hash_locked():
    script = (ROOT / "install" / "54-slack.sh").read_text()
    assert "set -euo pipefail" in script and "need_root" in script
    assert "--require-hashes" in script and "vendor/control-requirements.txt" in script
    assert "install -d -m 2755 -o fleet -g fleetops /var/lib/agent-fleet/board" in script
    for package in ("slack-bolt", "slack-sdk"):
        assert "lock_add" in script and package in script
    assert "tailscale" not in script and "curl" not in script, "this step needs no network except the pinned package install"


def test_the_slack_installer_step_reads_versions_from_the_package_metadata():
    """slack_bolt has no __version__ attribute. An attribute read failed the first live run of this step. See D-058."""
    script = (ROOT / "install" / "54-slack.sh").read_text()
    assert "importlib.metadata" in script and "m.version('slack-bolt')" in script and "m.version('slack-sdk')" in script
    assert "__version__" not in script.replace("without a __version__", "").replace("no __version__ attribute", "")


def test_the_control_requirements_pin_exact_versions_with_hashes():
    text = (ROOT / "vendor" / "control-requirements.txt").read_text()
    packages = re.findall(r"^([A-Za-z0-9_.-]+)==([0-9][^\s\\]*)", text, re.M)
    assert {name for name, _v in packages} == {"slack-bolt", "slack-sdk"}
    blocks = re.split(r"\n(?=[A-Za-z0-9_.-]+==)", text)
    for block in blocks:
        if "==" in block.split("\n", 1)[0]:
            assert re.search(r"--hash=sha256:[0-9a-f]{64}", block), block[:40]


def test_the_units_installer_picks_up_every_template_by_a_glob():
    script = (ROOT / "install" / "60-units.sh").read_text()
    assert '"$REPO"/systemd/*' in script and "systemctl enable" in script


def test_the_bring_up_starts_both_parts_that_live_outside_the_target():
    script = (ROOT / "install" / "70-bringup.sh").read_text()
    assert "systemctl start agent-fleet.target" in script
    for unit in sorted(OUTSIDE_TARGET):
        assert "systemctl start %s" % unit.replace(".service.in", ".service") in script, unit


def test_the_slack_venv_sees_the_system_packages_and_the_installer_imports_the_real_adapter():
    """The first live start failed with: No module named yaml. See D-062."""
    script = (ROOT / "install" / "54-slack.sh").read_text()
    assert "--system-site-packages" in script and "include-system-site-packages = true" in script
    assert 'import ops.control.slackbot' in script and 'die "the Slack adapter cannot import its modules"' in script
    assert script.index("include-system-site-packages = true") < script.index("import ops.control.slackbot")


def test_the_modules_that_the_slack_adapter_imports_need_nothing_beyond_bolt_the_sdk_and_the_system_packages():
    """Every third-party import under ops.control and ops.fleetlib must be one of these. A new one needs the lock file or the system package list."""
    import ast
    import sys
    allowed = {"slack_bolt", "slack_sdk", "yaml", "jsonschema"}          # yaml and jsonschema come from the apt packages that 10-apt.sh installs
    stdlib = set(sys.stdlib_module_names)
    found = set()
    for folder in ("ops/control", "ops/fleetlib", "ops/board"):
        for path in (ROOT / folder).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0 else [])
                for name in names:
                    top = name.split(".")[0]
                    if top not in stdlib and top != "ops":
                        found.add(top)
    assert found <= allowed, "new third-party imports: %s" % sorted(found - allowed)


def test_the_apt_step_installs_the_system_packages_that_the_slack_venv_relies_on():
    apt = (ROOT / "install" / "10-apt.sh").read_text()
    assert "python3-yaml" in apt and "python3-jsonschema" in apt
