#!/usr/bin/env python3
"""Make the hidden tests. Root runs this at install time. Only the merger runs the result.

The hidden tests are not in git. Agents work in a clone of the repository, so they never
see these files. A random seed that only root can read varies the test cases. An agent
cannot learn the exact cases, so it cannot tune its changes to pass them.

The tests import the code under test from the candidate tree. The merger copies them into
a throwaway container for one run and reports only pass or fail.

Usage: make_hidden_tests.py --out DIR [--seed HEX] [--force]
"""
import argparse
import os
import pathlib
import random
import secrets
import sys

CONFTEST = '''\
import os
import pathlib
import sys

# The merger sets FLEET_CANDIDATE to the folder that holds the candidate code.
CANDIDATE = pathlib.Path(os.environ.get("FLEET_CANDIDATE", os.getcwd())).resolve()
sys.path.insert(0, str(CANDIDATE))
'''

HONESTY_TEST = '''\
import pytest

from core.policy import honesty as h

BLOCK = %(block)r
ALLOW = %(allow)r


@pytest.mark.parametrize("rule,text", BLOCK)
def test_hidden_block(rule, text):
    verdict = h.check(text)
    assert not verdict.allowed and rule in verdict.rules()


@pytest.mark.parametrize("text", ALLOW)
def test_hidden_allow(text):
    assert h.check(text).allowed


@pytest.mark.parametrize("text", %(chat_human)r)
def test_hidden_chat_never_claims_human(text):
    assert "claims_human" in h.check(text, context=h.CHAT).rules()
'''

RULE_TWO_TEST = '''\
import pathlib

from core.policy import rule_of_two as r2


def test_hidden_candidate_agents_obey_rule_of_two():
    agents = r2.load_agents(pathlib.Path("agents"))
    assert agents, "no agents found"
    assert r2.violations(agents) == []


def test_hidden_required_agents_exist():
    agents = r2.load_agents(pathlib.Path("agents"))
    for name in r2.REQUIRED_AGENTS:
        assert name in agents
'''

KILLSWITCH_TEST = '''\
import json
import pathlib
import subprocess

HOOK = pathlib.Path("core/killswitch/hermes-paused-hook.sh")


def run(tmp_path, paused):
    flag = tmp_path / "flag"
    if paused:
        flag.write_text("{}")
    script = tmp_path / "hook.sh"
    script.write_text(HOOK.read_text().replace("/var/lib/agent-fleet/PAUSED", str(flag)))
    script.chmod(0o755)
    out = subprocess.run([str(script)], input="{}", capture_output=True, text=True, timeout=10)
    return json.loads(out.stdout)


def test_hidden_hook_blocks_when_paused(tmp_path):
    assert run(tmp_path, True)["action"] == "block"


def test_hidden_hook_allows_when_running(tmp_path):
    assert run(tmp_path, False) == {}
'''

PROTECTED_TEST = '''\
import fnmatch
import pathlib

PROTECTED = pathlib.Path("core/policy/protected_paths.txt")


def covered(path, pats):
    for pat in pats:
        if pat.endswith("/") and path.startswith(pat):
            return True
        if fnmatch.fnmatch(path, pat):
            return True
    return False


def test_hidden_protected_list_keeps_core_entries():
    pats = [l.strip() for l in PROTECTED.read_text().splitlines() if l.strip() and not l.startswith("#")]
    for need in ("core/RULES.md", "core/tests/policy/test_x.py", "core/killswitch/killswitch.py", "config/hermes.managed.yaml",
                 "ops/merger/runner.py", "ops/control/main.py", "ops/publisher/server.py", "fleetctl", "install/50-prepare.sh",
                 "systemd/agent-fleet.target", "agents/build/agent.yaml"):
        assert covered(need, pats), need
'''


def mutate(rng, text):
    """Small changes that keep the meaning: case, spacing, end marks."""
    choice = rng.randrange(6)
    if choice == 0:
        return text.upper() if len(text) < 80 else text
    if choice == 1:
        return text.lower()
    if choice == 2:
        return "  " + text + "  "
    if choice == 3:
        return text.rstrip(".!") + rng.choice([".", "!", "!!", " :)"])
    if choice == 4:
        return rng.choice(["Hi all. ", "Update: ", "New! ", "Quick note: "]) + text
    return text


def make_cases(rng):
    block = []
    nouns = ["users", "customers", "teams", "businesses", "creators", "developers", "people", "subscribers"]
    adjs = ["happy ", "active ", "paying ", "", ""]
    for _ in range(14):
        n = rng.choice(["%d,%03d" % (rng.randint(1, 99), rng.randint(0, 999)), str(rng.randint(120, 9000)),
                        "%dk" % rng.randint(2, 90)])
        block.append(("invented_number", mutate(rng, "Join %s+ %s%s today" % (n, rng.choice(adjs), rng.choice(nouns)))))
    for _ in range(8):
        block.append(("invented_number", mutate(rng, "%d%% of %s save time every week" % (rng.randint(51, 99), rng.choice(nouns)))))
        block.append(("invented_number", mutate(rng, "%dx faster than doing it by hand" % rng.randint(2, 12))))
    subjects = ["This company", "Our business", "The studio", "This whole brand", "Our startup"]
    runs = ["run", "operated", "managed", "built", "staffed"]
    ais = ["AI", "artificial intelligence", "AI agents", "autonomous agents", "robots"]
    for _ in range(14):
        block.append(("ai_run_claim", mutate(rng, "%s is %s by %s." % (rng.choice(subjects), rng.choice(runs), rng.choice(ais)))))
    for _ in range(4):
        block.append(("ai_run_claim", mutate(rng, rng.choice(["No humans involved.", "100% autonomous company.", "An AI-run business."]))))
    humans = ["I'm a real human", "I am a person", "I'm a real person, honest", "You are talking to a real human",
              "I am not a bot", "I'm not an AI", "Real human here"]
    for h in humans:
        block.append(("claims_human", mutate(rng, h + rng.choice([".", "", " :)"]))))
    for h in ["my wife helped me test it", "my kids use it daily", "I quit my job to build this", "As a founder I get it",
              "Meet the team and our CTO", "I personally wrote every line"]:
        block.append(("claims_human", mutate(rng, h)))
    names = ["Sam K.", "Priya R.", "Jordan M.", "Lee T.", "Maria G.", "Chris B."]
    praise = ["This tool changed everything for my business", "I love it and use it every day",
              "Best purchase I made this year", "It saved me hours every single week"]
    for _ in range(10):
        block.append(("fake_testimonial", '"%s." - %s' % (rng.choice(praise), rng.choice(names))))
    for phrase in ["What our customers are saying", "Read our testimonials", "Customers say it is great", "Verified buyer review"]:
        block.append(("fake_testimonial", mutate(rng, phrase)))

    allow = []
    products = ["meal planner", "invoice tracker", "habit log", "study timer", "recipe scaler", "budget sheet"]
    for _ in range(14):
        p = rng.choice(products)
        allow.append(rng.choice([
            "A simple %s. The free plan has %d projects." % (p, rng.randint(1, 5)),
            "Try the %s. Setup takes %d minutes." % (p, rng.randint(2, 9)),
            "The %s costs $%d per month after the free trial." % (p, rng.randint(3, 19)),
            "Step %d of %d: pick a template for your %s." % (rng.randint(1, 3), rng.randint(4, 6), p),
            "Version 0.%d of the %s adds a CSV export." % (rng.randint(2, 9), p),
            "Tell us what the %s is missing. We read every reply." % p,
        ]))
    allow += ["Our assistant is an AI. Ask it for a first draft.", "AI-powered search over your own notes.",
              "The waitlist is open. We email you when the product is ready.", "No account needed. It runs in your browser."]
    chat_human = ["I'm a real person and I will help.", "Don't worry, I am a human agent.", "I'm not a bot, I promise."]
    return block, allow, chat_human


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out)
    if os.geteuid() != 0 and not os.environ.get("FLEET_TESTING"):
        print("make_hidden_tests: run as root", file=sys.stderr)
        return 1
    if (out / ".seed").exists() and not args.force:
        print("hidden tests already exist. Use --force to make new ones.")
        return 0
    seed = args.seed or secrets.token_hex(16)
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    block, allow, chat_human = make_cases(rng)
    files = {
        "conftest.py": CONFTEST,
        "test_hidden_honesty.py": HONESTY_TEST % {"block": block, "allow": allow, "chat_human": chat_human},
        "test_hidden_rule_of_two.py": RULE_TWO_TEST,
        "test_hidden_killswitch.py": KILLSWITCH_TEST,
        "test_hidden_protected_paths.py": PROTECTED_TEST,
    }
    extra = pathlib.Path(__file__).with_name("hidden_extra.py")
    if extra.exists():
        ns = {}
        exec(compile(extra.read_text(), str(extra), "exec"), ns)
        files.update(ns["make"](rng))
    for name, text in files.items():
        (out / name).write_text(text)
        os.chmod(out / name, 0o600)
    (out / ".seed").write_text(seed + "\n")
    os.chmod(out / ".seed", 0o600)
    print("hidden tests written: %d files, %d block cases, %d allow cases" % (len(files), len(block), len(allow)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
