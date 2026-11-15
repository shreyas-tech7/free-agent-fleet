import os
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
LIVE = pathlib.Path("/opt/agent-fleet/repo")


def deployed():
    """True on an installed system. The locked-core file checks need it."""
    return LIVE.is_dir()


@pytest.fixture
def repo_root():
    return ROOT


@pytest.fixture
def live_repo():
    if not deployed():
        if os.environ.get("FLEET_REQUIRE_DEPLOYED") == "1":
            pytest.fail("the system is not installed. Run ./install.sh first.")
        pytest.skip("not installed: /opt/agent-fleet/repo is missing")
    return LIVE
