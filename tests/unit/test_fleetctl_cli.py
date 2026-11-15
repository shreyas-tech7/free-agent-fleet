import argparse
import os

import pytest

from ops.fleetctl import cli, system
from tests.unit.fctl_helpers import Script


def commands():
    parser = cli.build_parser()
    for action in parser._subparsers._group_actions:
        return set(action.choices)


def test_every_command_of_the_task_exists():
    need = {"status", "doctor", "test", "start", "stop", "pause", "resume", "brief", "secret", "config", "pivot", "project",
            "logs", "backup", "update", "revoke-checklist", "setup"}
    assert need <= commands()


def test_secret_actions_and_no_value_argument():
    p = cli.build_parser()
    assert p.parse_args(["secret", "set", "GROQ_API_KEY"]).name == "GROQ_API_KEY"
    assert p.parse_args(["secret", "list"]).secret_command == "list"
    assert p.parse_args(["secret", "check"]).secret_command == "check"
    assert p.parse_args(["secret", "wizard", "--group", "router"]).group == "router"
    with pytest.raises(SystemExit):                 # a value must never be an argument
        p.parse_args(["secret", "set", "GROQ_API_KEY", "gsk_value"])


def test_brief_and_config_and_project_arguments():
    p = cli.build_parser()
    assert p.parse_args(["brief", "--now"]).now is True
    a = p.parse_args(["config", "set", "publisher.mode", "live"])
    assert (a.key, a.value) == ("publisher.mode", "live")
    assert p.parse_args(["project", "status"]).project_command == "status"
    assert p.parse_args(["pivot", "next-thing", "--reason", "x"]).slug == "next-thing"
    assert p.parse_args(["pivot", "--archive-only"]).archive_only is True


def test_version_and_empty_run(capsys):
    assert cli.main(["--version"]) == 0
    assert "fleetctl" in capsys.readouterr().out
    assert cli.main([]) == 0


def test_a_missing_action_returns_2(fleet_env):
    script = Script()
    assert cli.main(["secret"], console=script.con) == 2
    assert "--help" in script.text


def test_non_root_commands_go_through_sudo(monkeypatch):
    monkeypatch.delenv("FLEET_TESTING", raising=False)
    monkeypatch.setattr(system, "is_root", lambda: False)
    monkeypatch.setattr(cli, "trusted_launcher", lambda: "/usr/local/bin/fleetctl")
    seen = {}
    monkeypatch.setattr(os, "execvp", lambda prog, argv: seen.update(prog=prog, argv=argv) or (_ for _ in ()).throw(SystemExit(0)))
    with pytest.raises(SystemExit):
        cli.main(["status"])
    assert seen["prog"] == "sudo"
    assert "/usr/local/bin/fleetctl" in seen["argv"] and seen["argv"][-1] == "status"


def test_the_test_command_needs_no_root(monkeypatch):
    monkeypatch.delenv("FLEET_TESTING", raising=False)
    monkeypatch.setattr(system, "is_root", lambda: False)
    called = []
    monkeypatch.setattr(os, "execvp", lambda *a: called.append(a))
    from ops.fleetctl import testcmd
    monkeypatch.setattr(testcmd, "cmd_test", lambda args, console: 0)
    assert cli.main(["test"]) == 0
    assert called == []


def test_an_unexpected_error_prints_one_line_without_a_trace(fleet_env, monkeypatch):
    from ops.fleetctl import basic
    monkeypatch.setattr(basic, "cmd_status", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret-token-xyz")))
    script = Script()
    assert cli.main(["status"], console=script.con) == 1
    assert "RuntimeError" in script.text and "secret-token-xyz" not in script.text


def test_trusted_launcher_needs_root_ownership(tmp_path, monkeypatch):
    f = tmp_path / "fleetctl"
    f.write_text("x")
    monkeypatch.setattr(cli, "INSTALLED", str(tmp_path / "missing"))
    monkeypatch.setattr("sys.argv", [str(f)])
    assert cli.trusted_launcher() is None           # owned by the test user, not root


# ---- D-063: a root process must not write bytecode into the locked tree
def test_the_launcher_never_writes_bytecode_before_it_imports_the_code():
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[2] / "fleetctl").read_text()
    assert "sys.dont_write_bytecode = True" in text
    assert text.index("sys.dont_write_bytecode = True") < text.index("from ops.fleetctl.cli import main")


def test_a_unit_test_never_sees_the_container_marker_of_the_clean_machine_test(tmp_path):
    import os
    from ops.fleetctl import doctor
    assert os.environ["FLEET_TEST_MODE_FILE"].endswith("no-container-marker") and not doctor.test_mode()
