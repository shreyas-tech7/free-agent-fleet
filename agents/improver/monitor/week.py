#!/usr/bin/env python3
"""Name the newest weekly trace summary. The agent runs when a new one exists."""
import pathlib

WORK = pathlib.Path("/var/lib/agent-fleet/work/agent-fleet")


def main():
    base = WORK / "projects" / "_improver" / "traces"
    try:
        newest = max(p.name for p in base.glob("*.json"))
    except (OSError, ValueError):
        newest = "none"
    print("traces: " + newest)


if __name__ == "__main__":
    main()
