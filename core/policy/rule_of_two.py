"""Rule of Two. It is part of the locked core.

An agent that reads untrusted input, can reach sensitive data, and can take outside
actions is dangerous, because a hostile text could make it leak or act.
No agent may have all three at once. Each agents/<name>/agent.yaml sets three flags:

    rule_of_two:
      untrusted_input: true|false     reads text that outsiders wrote
      sensitive_access: true|false    holds a secret, or reads customer or personal data
      external_actions: true|false    posts, deploys, sends, pays, or changes outside systems
"""
import pathlib

import yaml

FLAGS = ("untrusted_input", "sensitive_access", "external_actions")
REQUIRED_AGENTS = ("orchestrator", "research", "build", "marketing", "feedback", "security", "improver")


def load_agents(agents_dir):
    agents = {}
    for path in sorted(pathlib.Path(agents_dir).glob("*/agent.yaml")):
        agents[path.parent.name] = yaml.safe_load(path.read_text()) or {}
    return agents


def violations(agents):
    """Return a list of messages. An empty list means every agent obeys the rule."""
    problems = []
    for name, spec in agents.items():
        flags = (spec or {}).get("rule_of_two")
        if not isinstance(flags, dict):
            problems.append("%s: rule_of_two block is missing" % name)
            continue
        for flag in FLAGS:
            if not isinstance(flags.get(flag), bool):
                problems.append("%s: rule_of_two.%s must be true or false" % (name, flag))
        if all(flags.get(f) is True for f in FLAGS):
            problems.append("%s: has all three of untrusted input, sensitive access, and outside actions" % name)
    return problems
