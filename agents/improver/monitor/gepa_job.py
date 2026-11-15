#!/usr/bin/env python3
"""Script job of the improver profile. It runs the weekly GEPA optimization when it is safe. No agent calls it."""
import os
import sys

sys.path.insert(0, os.environ.get("FLEET_REPO", "/opt/agent-fleet/repo"))
from ops.improver.gepa import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
