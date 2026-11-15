#!/usr/bin/env python3
"""Script job of the improver profile. It writes the weekly trace summary. No model is called."""
import os
import sys

sys.path.insert(0, os.environ.get("FLEET_REPO", "/opt/agent-fleet/repo"))
from ops.improver.traces import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
