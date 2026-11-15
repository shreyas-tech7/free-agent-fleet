#!/usr/bin/env python3
"""List the inbox items that nobody handled yet. The agent runs only when this list changes."""
import json
import pathlib

STATE = pathlib.Path("/var/lib/agent-fleet")


def main():
    try:
        acked = set(json.loads((STATE / "feedback" / "acked.json").read_text()))
    except (OSError, ValueError):
        acked = set()
    try:
        items = sorted(p.name for p in (STATE / "inbox").glob("*.json") if p.name not in acked)
    except OSError:
        items = []
    print("inbox: " + (("%d waiting\n" % len(items)) + "\n".join(items[:50]) if items else "empty"))


if __name__ == "__main__":
    main()
