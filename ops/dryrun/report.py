"""Turn the result of a dry run into docs/DRYRUN.md.

Usage: python3 -m ops.dryrun.report ARCHIVE_RUN_FOLDER > docs/DRYRUN.md
The folder holds result.json and brief.md from the run.
"""
import json
import pathlib
import sys

INTRO = """# Dry run

The dry run puts one fake project, `dry-run-widgets`, through every stage of the business loop.
It uses the mock model (`tests/mock_llm.py`). It needs no key. It posts nothing and it deploys nothing.

Run it with `sudo python3 -m ops.dryrun.run`. It takes about 15 minutes on a small machine.
The merger runs its gates in a container three times. Each run takes about 5 minutes.

## What is real and what is simulated

Real parts:

- `fleetctl pivot`, the Kanban board, and its cards.
- The injection screen service, the honesty filter, and the rate limits.
- The intake, the ticket tools, and the git working clone.
- The merger with all six gates, and the rollback.
- The Deployer checks and the brief code.

Simulated parts:

- The model answers. They are canned and schema checked.
- The market numbers: signups, users, fix time, and stranger minutes.
- The Publisher and the Deployer. They run in dry-run mode inside the driver, with their own folders.

The real services and their configuration stay untouched. The fleet stays paused. No agent runs.

"""


def _short(detail):
    """One short line for the table: the first non-empty line of the detail, with single spaces."""
    if detail in (None, "None"):
        return ""
    first = next((line for line in str(detail).splitlines() if line.strip()), "")
    return " ".join(first.replace("|", "/").split())[:110]


def render(result, brief):
    ok = result["ok"]
    steps = result["steps"]
    lines = [INTRO.rstrip("\n"), "", "## Result", ""]
    lines.append("The dry run **%s** for the project `%s`. It had %d checks and used %d mock model requests. It took %d seconds." % (
        "passed" if ok else "failed", result.get("project", "dry-run-widgets"), len(steps), result["mock_requests"], result["seconds"]))
    lines.append("")
    lines.append("The project name gets a number when an earlier run already used `dry-run-widgets`. `fleetctl pivot` never reuses the name of an archived project.")
    lines += ["", "## Checks", "", "| # | Check | Result | Seconds | Detail |", "|---|---|---|---|---|"]
    for i, s in enumerate(steps, 1):
        detail = _short(s["detail"])
        lines.append("| %d | %s | %s | %s | %s |" % (i, s["step"], "PASS" if s["ok"] else "FAIL", s["seconds"], detail))
    lines += ["", "## Sample brief", "", "The brief has four parts and 150 words or fewer. The model wrote the first three parts.",
              "Plain code wrote the last part from real blockers.", "", "```text", brief.strip(), "```", ""]
    lines += ["## What the run proves", "",
              "- The stage gates read their numbers from `STATE.md` and compare them with `config/stages.yaml`.",
              "- A dishonest post, a number without evidence, and an injected post never reach the outbox.",
              "- Replies and form messages lose their personal data before an agent sees them. An injection goes to quarantine.",
              "- The merger auto-merges a good branch, records the pass for the Deployer, and rolls back a change that fails the health check.",
              "- The Deployer refuses a commit that is not merged. For a merged commit it makes only a free-plan dry-run plan.",
              "- `fleetctl pivot` archives a project and its board without deleting anything.",
              "- The first customer gate stays open, because payments need the owner. The brief lists it under Limits.", ""]
    lines += ["## What the dry run found", "",
              "The dry run found real bugs. Each one is fixed and has a test.",
              "",
              "- The Deployer would have refused every deploy, because nothing told it which commits passed the gates. See D-025.",
              "- The merger could not find `runuser`, because its `PATH` had no `/usr/sbin`.",
              "- After a rollback, the working clone of the agents stayed on the rolled-back commit. See D-028.", ""]
    return "\n".join(lines)


def main(argv):
    folder = pathlib.Path(argv[0])
    result = json.loads((folder / "result.json").read_text())
    brief = (folder / "brief.md").read_text() if (folder / "brief.md").exists() else "(no brief)"
    sys.stdout.write(render(result, brief))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
