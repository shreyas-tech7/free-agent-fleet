"""Policy: the seven agents are complete, least-privilege, and obey the Rule of Two."""
import pathlib

import pytest

from core.policy import agents as ag
from core.policy import rule_of_two as r2
from ops.mcp import tools
from ops.merger import evals
from tests.helpers import copy_tree

ROOT = pathlib.Path(__file__).resolve().parents[3]


def test_every_agent_is_there_and_passes_the_file_checks():
    assert ag.problems(ROOT, tools.TOOLS) == []


def test_rule_of_two_holds_for_every_real_agent():
    found = r2.load_agents(ROOT / "agents")
    assert set(found) == set(ag.REQUIRED_AGENTS)
    assert r2.violations(found) == []


def test_no_agent_has_sensitive_access():
    for name, spec in r2.load_agents(ROOT / "agents").items():
        assert spec["rule_of_two"]["sensitive_access"] is False, name


def test_reading_untrusted_text_and_acting_outside_never_come_with_keys():
    for name, spec in r2.load_agents(ROOT / "agents").items():
        flags = spec["rule_of_two"]
        if flags["untrusted_input"] and flags["external_actions"]:
            assert not flags["sensitive_access"], name


def test_each_local_token_belongs_to_one_agent():
    holders = {}
    for name, spec in r2.load_agents(ROOT / "agents").items():
        env = ((spec.get("mcp_servers") or {}).get("fleet_tools") or {}).get("env_from_profile") or []
        for token in env:
            holders.setdefault(token, []).append(name)
    assert holders == {"PUBLISHER_API_TOKEN": ["marketing"], "PUBLISHER_REPLY_TOKEN": ["feedback"], "DEPLOYER_API_TOKEN": ["build"]}


def test_the_orchestrator_reads_no_outside_text():
    spec = ag.load(ROOT / "agents" / "orchestrator")
    assert spec["rule_of_two"] == {"untrusted_input": False, "sensitive_access": False, "external_actions": False}
    assert not {"web", "search", "browser"} & set(spec["toolsets"])
    assert not {"web_read", "source_search", "guard_scan", "inbox_read"} & set(spec["mcp_servers"]["fleet_tools"]["tools"])


def test_the_feedback_agent_has_no_files_and_no_free_text_tool():
    spec = ag.load(ROOT / "agents" / "feedback")
    assert not {"file", "terminal", "web", "kanban"} & set(spec["toolsets"])
    tools_ = spec["mcp_servers"]["fleet_tools"]["tools"]
    assert "reply_faq" in tools_ and "publish_post" not in tools_


def test_only_marketing_can_post_and_only_build_can_deploy():
    for name in ag.REQUIRED_AGENTS:
        tools_ = (ag.load(ROOT / "agents" / name).get("mcp_servers") or {}).get("fleet_tools", {}).get("tools", [])
        assert ("publish_post" in tools_) == (name == "marketing"), name
        assert ("deploy_commit" in tools_) == (name == "build"), name


@pytest.mark.parametrize("name", ag.REQUIRED_AGENTS)
def test_each_soul_is_small_and_scores_high_on_the_prompt_rubric(name):
    path = ROOT / "agents" / name / "SOUL.md"
    text = path.read_text()
    assert len(text.encode()) <= 15 * 1024
    score, failed = evals.score("agents/%s/SOUL.md" % name, text)
    assert score >= 95 and not failed, failed


@pytest.mark.parametrize("name", ag.REQUIRED_AGENTS)
def test_each_soul_names_its_rules_and_its_branch(name):
    text = (ROOT / "agents" / name / "SOUL.md").read_text()
    assert "core/RULES.md" in text and "PAUSED" in text and "budget.json" in text
    if name != "feedback":
        assert "agent/%s/" % name in text


def test_the_checker_catches_broken_files(tmp_path):
    copy_tree(ROOT / "agents", tmp_path / "agents")
    copy_tree(ROOT / "skills", tmp_path / "skills")
    spec = ag.load(tmp_path / "agents" / "research")
    spec["rule_of_two"]["sensitive_access"] = True
    spec["rule_of_two"]["external_actions"] = True
    spec["toolsets"].append("browser")
    spec["model"] = "gpt-5"
    spec["skills"].append("not-a-skill")
    import yaml
    (tmp_path / "agents" / "research" / "agent.yaml").write_text(yaml.safe_dump(spec))
    found = "\n".join(ag.problems(tmp_path, tools.TOOLS))
    for text in ("all three", "toolsets not allowed: browser", "model must be one of", "skill not-a-skill"):
        assert text in found


# ---- Prompt 3: the fleet-tools MCP server in n8n ----
def n8n_tools_of(name):
    return ((ag.load(ROOT / "agents" / name).get("mcp_servers") or {}).get("n8n_tools") or {}).get("tools", [])


def test_every_agent_has_a_small_list_of_n8n_tools_and_the_names_exist_in_the_workflow():
    import json
    wf = json.loads((ROOT / "n8n" / "workflows" / "fleet-tools.json").read_text())
    exposed = {n["name"] for n in wf["nodes"] if not n["type"].endswith("mcpTrigger")}
    assert exposed == ag.N8N_TOOLS
    for name in ag.REQUIRED_AGENTS:
        tools_ = n8n_tools_of(name)
        assert tools_ and set(tools_) <= exposed and len(tools_) <= 3, name


def test_only_the_research_agent_can_read_feeds():
    holders = [name for name in ag.REQUIRED_AGENTS if "fetch_feed" in n8n_tools_of(name)]
    assert holders == ["research"]


def test_a_feed_tool_needs_the_untrusted_input_flag(tmp_path):
    copy_tree(ROOT / "agents", tmp_path / "agents")
    copy_tree(ROOT / "skills", tmp_path / "skills")
    path = tmp_path / "agents" / "orchestrator" / "agent.yaml"
    path.write_text(path.read_text().replace("n8n_tools:\n    tools: [", "n8n_tools:\n    tools: [fetch_feed, ", 1))
    found = ag.problems(tmp_path, tools.TOOLS)
    assert any("orchestrator: tool fetch_feed returns outside text" in m for m in found), found


def test_the_checker_refuses_an_unknown_n8n_tool_and_an_extra_key(tmp_path):
    copy_tree(ROOT / "agents", tmp_path / "agents")
    copy_tree(ROOT / "skills", tmp_path / "skills")
    path = tmp_path / "agents" / "research" / "agent.yaml"
    path.write_text(path.read_text().replace("n8n_tools:\n    tools: [", "n8n_tools:\n    command: bash\n    tools: [run_shell, ", 1))
    found = ag.problems(tmp_path, tools.TOOLS)
    assert any("unknown n8n tools: run_shell" in m for m in found) and any("takes only a tool list" in m for m in found), found
