"""The helper scripts of n8n-mcp. The helper is for Claude Code only. A stub server stands in for it, and no real key is used."""
import os
import pathlib
import re
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "scripts" / "n8n-mcp-run.sh"
INSTALL = ROOT / "scripts" / "n8n-mcp-install.sh"
TOOLS = (ROOT / "vendor" / "n8n-mcp" / "tools.txt").read_text().split()
EXPECTED_OFF = {"n8n_delete_workflow", "n8n_executions", "n8n_workflow_versions", "n8n_manage_datatable", "n8n_manage_folders",
                "n8n_manage_credentials", "n8n_manage_agents", "n8n_audit_instance"}


def disabled_in_script():
    match = re.search(r'export DISABLED_TOOLS="([^"]+)"', RUN.read_text())
    return set(match.group(1).split(","))


@pytest.fixture
def stub(tmp_path):
    """A home with a stub server that prints the names of the settings that it sees. It never prints the key."""
    home = tmp_path / "mcp-home"
    server = home / "node_modules" / "n8n-mcp" / "dist" / "mcp"
    server.mkdir(parents=True)
    (server / "stdio-wrapper.js").write_text(
        "const keys=['MCP_MODE','LOG_LEVEL','DISABLE_CONSOLE_OUTPUT','WEBHOOK_SECURITY_MODE','N8N_MCP_TELEMETRY_DISABLED','N8N_API_URL','DISABLED_TOOLS'];\n"
        "const out={};for(const k of keys){out[k]=process.env[k];}\n"
        "out.has_key=Boolean(process.env.N8N_API_KEY);\nconsole.log(JSON.stringify(out));\n")
    return home


def run(stub, env_file, extra=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(stub.parent), "N8N_MCP_HOME": str(stub), "N8N_MCP_ENV_FILE": str(env_file)}
    env.update(extra or {})
    return subprocess.run(["bash", str(RUN)], capture_output=True, text=True, env=env, timeout=30)  # nosemgrep: dangerous-subprocess-use-audit


def test_both_scripts_are_valid_bash_and_executable():
    for script in (RUN, INSTALL):
        assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0  # nosemgrep: dangerous-subprocess-use-audit
        assert script.stat().st_mode & stat.S_IXUSR


def test_every_tool_that_can_delete_is_off_and_each_off_tool_exists():
    assert disabled_in_script() == EXPECTED_OFF
    assert EXPECTED_OFF <= set(TOOLS) and len(TOOLS) == len(set(TOOLS)) == 28


def test_the_server_gets_the_settings_of_prompt_3_and_the_key_stays_out_of_the_output(stub, tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text("N8N_API_URL=http://127.0.0.1:5678\nN8N_API_KEY=placeholder-not-a-real-key\n")
    env_file.chmod(0o600)
    done = run(stub, env_file)
    assert done.returncode == 0, done.stderr
    seen = __import__("json").loads(done.stdout)
    assert seen["MCP_MODE"] == "stdio" and seen["LOG_LEVEL"] == "error" and seen["DISABLE_CONSOLE_OUTPUT"] == "true"
    assert seen["WEBHOOK_SECURITY_MODE"] == "moderate" and seen["N8N_MCP_TELEMETRY_DISABLED"] == "true"
    assert seen["N8N_API_URL"] == "http://127.0.0.1:5678" and seen["has_key"] is True
    assert set(seen["DISABLED_TOOLS"].split(",")) == EXPECTED_OFF
    assert "placeholder-not-a-real-key" not in done.stdout + done.stderr


def test_a_missing_url_defaults_to_the_loopback_address(stub, tmp_path):
    env_file = tmp_path / "env"
    env_file.write_text("N8N_API_KEY=placeholder-not-a-real-key\n")
    env_file.chmod(0o600)
    assert __import__("json").loads(run(stub, env_file).stdout)["N8N_API_URL"] == "http://127.0.0.1:5678"


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o660, 0o666, 0o700])
def test_an_env_file_that_is_not_mode_600_is_refused(stub, tmp_path, mode):
    env_file = tmp_path / "env"
    env_file.write_text("N8N_API_KEY=placeholder-not-a-real-key\n")
    env_file.chmod(mode)
    done = run(stub, env_file)
    assert done.returncode == 1 and "mode 600" in done.stderr and done.stdout == ""
    assert "placeholder-not-a-real-key" not in done.stderr


def test_a_missing_env_file_says_what_to_run(stub, tmp_path):
    done = run(stub, tmp_path / "nothing")
    assert done.returncode == 1 and "fleetctl n8n mcp-env" in done.stderr


def test_the_lockfile_pins_the_helper_and_the_fixed_sdk_with_no_install_scripts_outside_the_optional_module():
    import json
    package = json.loads((ROOT / "vendor" / "n8n-mcp" / "package.json").read_text())
    assert package["dependencies"] == {"n8n-mcp": "2.102.0"} and package["overrides"] == {"@modelcontextprotocol/sdk": "1.32.2"}
    packages = json.loads((ROOT / "vendor" / "n8n-mcp" / "package-lock.json").read_text())["packages"]
    assert packages["node_modules/n8n-mcp"]["version"] == "2.102.0" and packages["node_modules/@modelcontextprotocol/sdk"]["version"] == "1.32.2"
    assert [k for k, v in packages.items() if v.get("hasInstallScript")] == ["node_modules/better-sqlite3"]
    assert packages["node_modules/better-sqlite3"].get("optional") is True
    assert all(str(v.get("resolved", "")).startswith("https://registry.npmjs.org/") for k, v in packages.items() if k)
    assert all(v.get("integrity", "").startswith("sha512-") for k, v in packages.items() if k)
