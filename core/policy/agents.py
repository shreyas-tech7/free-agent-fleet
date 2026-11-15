"""Checks of agents/*/agent.yaml. Part of the locked core.

Each agent is one Hermes profile. Its agent.yaml sets the model, the tools, the skills, the MCP tools,
the sandbox mounts, the schedule, and the Rule of Two flags. Agents cannot change this file.
"""
import pathlib
import re

import yaml

REQUIRED_AGENTS = ("orchestrator", "research", "build", "marketing", "feedback", "security", "improver")
MODELS = {"fleet-smart", "fleet-worker", "fleet-coder", "fleet-local"}
ALLOWED_TOOLSETS = {"kanban", "memory", "todo", "session_search", "skills", "file", "terminal", "web"}
ALWAYS_DISABLED = {"browser", "computer_use", "cronjob", "delegation", "code_execution"}
# MCP tool -> the local token that its service needs. One agent holds each token.
TOKEN_TOOLS = {"publish_post": "PUBLISHER_API_TOKEN", "reply_faq": "PUBLISHER_REPLY_TOKEN", "deploy_commit": "DEPLOYER_API_TOKEN"}
STATE_MOUNTS = {"status", "tickets", "requests"}
# The tools of the fleet-tools MCP server in n8n. Each one only messages the owner or the fleet log, or reads a feed.
# fetch_feed returns outside text that passed the injection screen. Only an agent that reads untrusted text may have it.
N8N_TOOLS = {"notify_owner", "request_approval", "log_event", "fetch_feed"}
N8N_UNTRUSTED_TOOLS = {"fetch_feed"}
CRON_RE = re.compile(r"^\S+( \S+){4}$")


def load(agent_dir):
    return yaml.safe_load((pathlib.Path(agent_dir) / "agent.yaml").read_text()) or {}


def problems(repo_root, tool_names):
    """Return a list of messages. An empty list means every agent file is in order."""
    root = pathlib.Path(repo_root)
    out = []
    holders = {}
    for name in REQUIRED_AGENTS:
        d = root / "agents" / name
        if not (d / "SOUL.md").is_file() or not (d / "agent.yaml").is_file():
            out.append("%s: SOUL.md or agent.yaml is missing" % name)
            continue
        try:
            a = load(d)
        except yaml.YAMLError:
            out.append("%s: agent.yaml does not parse" % name)
            continue
        if a.get("name") != name:
            out.append("%s: name must equal the folder name" % name)
        if a.get("model") not in MODELS:
            out.append("%s: model must be one of %s" % (name, ", ".join(sorted(MODELS))))
        toolsets = a.get("toolsets") or []
        disabled = set(a.get("disabled_toolsets") or [])
        if not set(toolsets) <= ALLOWED_TOOLSETS:
            out.append("%s: toolsets not allowed: %s" % (name, ", ".join(sorted(set(toolsets) - ALLOWED_TOOLSETS))))
        if ("kanban" in toolsets) != (name == "orchestrator"):
            out.append("%s: only the orchestrator has the kanban toolset" % name)
        if not ALWAYS_DISABLED <= disabled:
            out.append("%s: disabled_toolsets must include %s" % (name, ", ".join(sorted(ALWAYS_DISABLED - disabled))))
        if set(toolsets) & disabled:
            out.append("%s: a toolset is both enabled and disabled: %s" % (name, ", ".join(sorted(set(toolsets) & disabled))))
        ws = a.get("workspace") or {}
        if ws.get("repo") not in ("rw", "none"):
            out.append("%s: workspace.repo must be rw or none" % name)
        if not set(ws.get("state") or []) <= STATE_MOUNTS:
            out.append("%s: workspace.state may hold only %s" % (name, ", ".join(sorted(STATE_MOUNTS))))
        if ({"file", "terminal"} & set(toolsets)) and ws.get("repo") != "rw":
            out.append("%s: file and terminal tools need workspace.repo rw" % name)
        if ws.get("repo") == "rw" and not ({"file", "terminal"} & set(toolsets)):
            out.append("%s: workspace.repo rw needs the file or terminal toolset" % name)
        for skill in a.get("skills") or []:
            if not (root / "skills" / skill / "SKILL.md").is_file():
                out.append("%s: skill %s is not in skills/" % (name, skill))
        flags = a.get("rule_of_two") or {}
        if not all(isinstance(flags.get(f), bool) for f in ("untrusted_input", "sensitive_access", "external_actions")):
            out.append("%s: rule_of_two flags must be true or false" % name)
        elif all(flags.values()):
            out.append("%s: has all three Rule of Two properties" % name)
        elif flags["sensitive_access"]:
            out.append("%s: no agent may hold sensitive access. Keys belong to the services." % name)
        servers = a.get("mcp_servers") or {}
        for sname, spec in servers.items():
            if sname == "playwright":
                if name != "build" or spec.get("local_only") is not True:
                    out.append("%s: only the build agent may use playwright, and only for local pages" % name)
                continue
            if sname == "n8n_tools":
                n8n_tools = spec.get("tools") or []
                bad = sorted(set(n8n_tools) - N8N_TOOLS)
                if bad:
                    out.append("%s: unknown n8n tools: %s" % (name, ", ".join(bad)))
                if len(n8n_tools) > 4 or len(set(n8n_tools)) != len(n8n_tools):
                    out.append("%s: n8n_tools must list each tool once and at most four" % name)
                for t in n8n_tools:
                    if t in N8N_UNTRUSTED_TOOLS and not flags.get("untrusted_input"):
                        out.append("%s: tool %s returns outside text, so untrusted_input must be true" % (name, t))
                extra_keys = set(spec) - {"tools"}
                if extra_keys:
                    out.append("%s: n8n_tools takes only a tool list, not %s" % (name, ", ".join(sorted(extra_keys))))
                continue
            if sname != "fleet_tools":
                out.append("%s: unknown MCP server %s" % (name, sname))
                continue
            tools = spec.get("tools") or []
            unknown = sorted(set(tools) - set(tool_names))
            if unknown:
                out.append("%s: unknown MCP tools: %s" % (name, ", ".join(unknown)))
            env = set(spec.get("env_from_profile") or [])
            for t in tools:
                if t in TOKEN_TOOLS:
                    holders.setdefault(t, []).append(name)
                    if TOKEN_TOOLS[t] not in env:
                        out.append("%s: tool %s needs env_from_profile %s" % (name, t, TOKEN_TOOLS[t]))
                    if not flags.get("external_actions"):
                        out.append("%s: tool %s is an outside action, so external_actions must be true" % (name, t))
            extra = env - set(TOKEN_TOOLS.values())
            if extra:
                out.append("%s: env_from_profile may hold only local tokens: %s" % (name, ", ".join(sorted(extra))))
        turns = a.get("max_turns")
        if not isinstance(turns, int) or not 5 <= turns <= 200:
            out.append("%s: max_turns must be a number from 5 to 200" % name)
        for job in a.get("schedule") or []:
            if not isinstance(job.get("name"), str) or not CRON_RE.match(str(job.get("cron", ""))):
                out.append("%s: a schedule entry needs a name and a 5 field cron" % name)
            script_only = bool(job.get("script"))
            if not script_only and (not str(job.get("prompt", "")).strip() or len(str(job.get("prompt", ""))) > 400):
                out.append("%s: a schedule prompt must have 1 to 400 characters" % name)
            for key in ("monitor", "script"):
                if job.get(key) and not (d / "monitor" / job[key]).is_file():
                    out.append("%s: %s script %s is missing" % (name, key, job[key]))
    for tool, who in holders.items():
        if len(who) != 1:
            out.append("tool %s is held by %d agents. It must be exactly one." % (tool, len(who)))
    return out
