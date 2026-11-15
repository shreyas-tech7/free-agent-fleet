#!/usr/bin/env python3
"""List the skill requests that have no decision yet. The agent runs only when this list changes."""
import hashlib
import pathlib

LIVE = pathlib.Path("/opt/agent-fleet/repo")


def main():
    base = LIVE / "projects" / "_requests"
    waiting = []
    try:
        for p in sorted(base.glob("*.json")):
            if p.name.endswith(".decision.json"):
                continue
            if (base / (p.stem + ".decision.json")).exists():
                continue
            waiting.append("%s %s" % (p.name, hashlib.sha256(p.read_bytes()).hexdigest()[:12]))
    except OSError:
        pass
    print("requests: " + (("%d waiting\n" % len(waiting)) + "\n".join(waiting) if waiting else "none"))


if __name__ == "__main__":
    main()
