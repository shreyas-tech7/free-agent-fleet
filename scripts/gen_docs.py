#!/usr/bin/env python3
"""Generate files that must follow config/secrets.yaml.

  .env.example                     list of secret names with placeholders
  docs/SECURITY.md                 the secret routing table between two marker lines
  docs/COWORK_PROMPT.md            the prompt for Claude Cowork, with the two Slack manifests inside it

Usage:
  gen_docs.py            write the files
  gen_docs.py --check    exit 1 when a file is out of date (used by a test)
"""
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "secrets.yaml"
ENV_EXAMPLE = ROOT / ".env.example"
SECURITY = ROOT / "docs" / "SECURITY.md"
START = "<!-- SECRETS-TABLE:START -->"
END = "<!-- SECRETS-TABLE:END -->"


COWORK = ROOT / "docs" / "COWORK_PROMPT.md"
MANIFESTS = ("fleet-hq", "hermes")


def load():
    return yaml.safe_load(REGISTRY.read_text())


COWORK_HEAD = """# Prompt for Claude Cowork: the Slack apps

This file is made by `scripts/gen_docs.py`. Do not edit it by hand. The two manifests inside it are copies of `slack/fleet-hq.manifest.json` and `slack/hermes.manifest.json`.

## Before you paste

1. Make a Slack workspace on the free plan, or pick one that you own. Sign in to it in the browser that Cowork uses.
2. Open your own SSH window to the fleet machine. You will paste the tokens there, never into Cowork.
3. Paste everything below the line into Claude Cowork.

---

You help the owner set up Slack for a fleet of AI agents. You work in the browser. You make two Slack apps from manifests, install them, and add the GitHub app to Slack.

## Rules

1. Do not ask questions. If a step is not possible, skip it and write the reason in your final report.
2. Never read, copy, or type a token. A token starts with `xoxb-` or `xapp-`. Never put a token in a message, a file, or a note. Never take a screenshot of a token screen. Never describe it.
3. Stop at each token screen. Say to the owner: "Copy this token now. Paste it in your own SSH window with `fleetctl secret set NAME`." Use the name that the step gives. Wait until the owner says that it is done. Then go to the next step.
4. Do not create an account. Do not add a payment method. Do not change the plan. Use the free plan only.
5. Work only in the workspace that the owner is signed in to. Do not invite anyone. Do not post a message. Do not change a setting that the steps do not name.
6. Use only slack.com and api.slack.com. Use github.com only in Step C, for the sign-in of the GitHub app.
7. At the end, write a short prompt for Claude Code. It has 10 lines or less. It lists what you finished, what you skipped and why, and what the owner must do next.

## Step A. The Fleet HQ app

1. Open https://api.slack.com/apps. Choose **Create New App**, then **From a manifest**.
2. Pick the workspace. Choose the JSON tab. Paste the whole manifest named `fleet-hq` from the end of this prompt. Choose **Next**, then **Create**.
3. Choose **Install to Workspace**. Allow it.
4. Open **OAuth & Permissions**. Stop at the **Bot User OAuth Token**. Tell the owner to run `fleetctl secret set SLACK_HQ_BOT_TOKEN`. Wait.
5. Open **Basic Information**. Scroll to **App-Level Tokens**. Choose **Generate Token and Scopes**. Name it `socket`. Add the scope `connections:write`. Choose **Generate**. Stop at the token. Tell the owner to run `fleetctl secret set SLACK_HQ_APP_TOKEN`. Wait.

## Step B. The Hermes app

1. Go back to https://api.slack.com/apps. Choose **Create New App**, then **From a manifest**.
2. Pick the same workspace. Choose the JSON tab. Paste the whole manifest named `hermes` from the end of this prompt. Choose **Next**, then **Create**.
3. Choose **Install to Workspace**. Allow it.
4. Open **OAuth & Permissions**. Stop at the **Bot User OAuth Token**. Tell the owner to run `fleetctl secret set SLACK_HERMES_BOT_TOKEN`. Wait.
5. Open **Basic Information**. Scroll to **App-Level Tokens**. Choose **Generate Token and Scopes**. Name it `socket`. Add the scope `connections:write`. Choose **Generate**. Stop at the token. Tell the owner to run `fleetctl secret set SLACK_HERMES_APP_TOKEN`. Wait.

## Step C. The GitHub app

1. In the workspace, open the Slack App Directory at https://slack.com/apps. Search for **GitHub**. Choose the app that GitHub Inc. publishes.
2. Choose **Add to Slack**. Allow it. Sign in to GitHub in the window that opens, as the owner. Do not type a password. If a login is needed, stop and tell the owner to do it.
3. Choose only the repositories that the owner names. If the owner names none, choose none.
4. Do not subscribe any channel yet. The channel `#github` does not exist until the owner runs `fleetctl slack bootstrap`.

## Step D. The final report

Write the short prompt for Claude Code now. Use this form:

```text
Cowork finished: (the steps that are done)
Cowork skipped: (the steps that were not done, each with one reason)
Owner next: run fleetctl slack bootstrap, then fleetctl config set slack.sink_mode false, then fleetctl doctor
```

## Manifests

"""


def render_cowork():
    import json
    out = [COWORK_HEAD.rstrip("\n"), ""]
    for name in MANIFESTS:
        manifest = json.loads((ROOT / "slack" / ("%s.manifest.json" % name)).read_text())
        out += ["### Manifest named `%s`" % name, "", "```json", json.dumps(manifest, indent=2), "```", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def render_env_example(reg):
    out = [
        "# agent-fleet secret names. Placeholders only.",
        "#",
        "# Do not put real values in this file. Do not copy it to a real .env file.",
        "# Run `fleetctl secret set NAME` for each key. That command writes the value to the",
        "# one secret file that the right service reads, with mode 600.",
        "# The list comes from config/secrets.yaml.",
        "",
    ]
    for gname, gdesc in reg["groups"].items():
        items = [s for s in reg["secrets"] if s["group"] == gname]
        if not items:
            continue
        out.append("# ---- %s: %s ----" % (gname, gdesc))
        for s in items:
            out.append("# %s. Get it at: %s" % (s["purpose"].rstrip("."), s["get_it"]))
            out.append("%s=REPLACE_ME" % s["name"])
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_table(reg):
    rows = ["| Secret | Group | Service that reads it | File | Owner and mode |", "|---|---|---|---|---|"]
    for s in reg["secrets"]:
        rows.append("| %s | %s | %s | `%s` | %s, 600 |" % (s["name"], s["group"], s["service"], s["file"], s["owner"]))
    return "\n".join(rows)


def splice(text, table):
    if START not in text or END not in text:
        return None
    head, rest = text.split(START, 1)
    _old, tail = rest.split(END, 1)
    return head + START + "\n" + table + "\n" + END + tail


def main(argv):
    check = "--check" in argv
    reg = load()
    stale = []
    env_new = render_env_example(reg)
    if not ENV_EXAMPLE.exists() or ENV_EXAMPLE.read_text() != env_new:
        stale.append(str(ENV_EXAMPLE.relative_to(ROOT)))
        if not check:
            ENV_EXAMPLE.write_text(env_new)
    if SECURITY.exists():
        new = splice(SECURITY.read_text(), render_table(reg))
        if new is None:
            print("gen_docs: markers missing in docs/SECURITY.md", file=sys.stderr)
            return 1
        if new != SECURITY.read_text():
            stale.append(str(SECURITY.relative_to(ROOT)))
            if not check:
                SECURITY.write_text(new)
    cowork = render_cowork()
    if not COWORK.exists() or COWORK.read_text() != cowork:
        stale.append(str(COWORK.relative_to(ROOT)))
        if not check:
            COWORK.write_text(cowork)
    if check and stale:
        print("out of date: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
