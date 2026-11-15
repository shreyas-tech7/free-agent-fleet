"""Policy: no agent has untrusted input, sensitive access, and outside actions together."""
import textwrap

from core.policy import rule_of_two as r2


def write(tmp_path, name, flags):
    d = tmp_path / name
    d.mkdir()
    (d / "agent.yaml").write_text(textwrap.dedent("""\
        rule_of_two:
          untrusted_input: %s
          sensitive_access: %s
          external_actions: %s
        """ % flags))


def test_two_flags_are_allowed(tmp_path):
    write(tmp_path, "a", ("true", "true", "false"))
    write(tmp_path, "b", ("true", "false", "true"))
    write(tmp_path, "c", ("false", "true", "true"))
    assert r2.violations(r2.load_agents(tmp_path)) == []


def test_three_flags_are_refused(tmp_path):
    write(tmp_path, "bad", ("true", "true", "true"))
    problems = r2.violations(r2.load_agents(tmp_path))
    assert len(problems) == 1 and "bad" in problems[0]


def test_missing_or_wrong_flags_are_refused(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "agent.yaml").write_text("name: x\n")
    (tmp_path / "y").mkdir()
    (tmp_path / "y" / "agent.yaml").write_text("rule_of_two:\n  untrusted_input: yes please\n")
    problems = r2.violations(r2.load_agents(tmp_path))
    assert any("x:" in p for p in problems) and any("y:" in p for p in problems)
