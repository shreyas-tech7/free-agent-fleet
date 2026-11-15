import json

import pytest

from ops.fleetlib import pause


def test_running_when_no_flag(fleet_env):
    assert pause.paused() is None
    pause.require_running()


def test_paused_reads_json_and_plain_flags(fleet_env):
    flag = fleet_env["state"] / "PAUSED"
    flag.write_text(json.dumps({"reason": "owner", "since": "2026-01-01T00:00:00Z"}))
    assert pause.paused()["reason"] == "owner"
    with pytest.raises(pause.Paused):
        pause.require_running()
    flag.write_text("stop please")
    assert pause.paused()["reason"] == "stop please"
