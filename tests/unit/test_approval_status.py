"""The approval status file and the read-only tool. See D-050."""
import json
import time

import pytest

from ops.control import approvals
from ops.fleetlib import paths
from ops.mcp import tools


def status_json():
    return json.loads(approvals.status_file().read_text())


def test_creating_an_approval_publishes_a_status_with_no_summary_and_no_name(fleet_env):
    record = approvals.create("spec", "project-001", "Sell an order tracker for $9 a month.", requester="orchestrator")
    data = status_json()
    assert data["approvals"] == {"spec:project-001": {"status": "pending", "created": record["created"]}}
    text = approvals.status_file().read_text()
    assert "order tracker" not in text and "orchestrator" not in text
    assert oct(approvals.status_file().stat().st_mode & 0o777) == "0o644"


def test_a_decision_updates_the_status_and_the_newest_request_for_an_item_wins(fleet_env):
    first = approvals.create("release", "project-001", "Launch the site.", now=1_800_000_000)
    approvals.decide(first["id"], False, "owner", now=1_800_000_100)
    assert status_json()["approvals"]["release:project-001"]["status"] == "rejected"
    second = approvals.create("release", "project-001", "Launch the site again with a new price.", now=1_800_000_200)
    assert status_json()["approvals"]["release:project-001"] == {"status": "pending", "created": 1_800_000_200}
    approvals.decide(second["id"], True, "owner", now=1_800_000_300)
    assert status_json()["approvals"]["release:project-001"]["status"] == "approved"


def test_old_entries_leave_the_status_file(fleet_env):
    approvals.create("install", "x@abc", "Old.", now=1_800_000_000)
    out = approvals.status_summary(now=1_800_000_000 + approvals.STATUS_KEEP_SECONDS + 10)
    assert out == {}


def test_a_failed_write_never_breaks_the_approval(fleet_env, monkeypatch):
    monkeypatch.setattr(approvals.jsonio, "write_json", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")) if "approvals.json" in str(a[0]) else None)
    assert approvals.publish_status() is False


def test_a_custom_folder_publishes_nothing(fleet_env, tmp_path):
    approvals.create("spec", "demo", "x", base=tmp_path / "custom")
    assert not approvals.status_file().exists()


@pytest.mark.parametrize("status", ["pending", "approved", "rejected"])
def test_the_tool_reads_the_status(fleet_env, status):
    record = approvals.create("spec", "project-001", "Sell the thing.")
    if status != "pending":
        approvals.decide(record["id"], status == "approved", "owner")
    assert tools.approval_status({"kind": "spec", "ref": "project-001"}) == {"kind": "spec", "ref": "project-001", "status": status}


def test_the_tool_says_none_for_an_unknown_item_and_when_there_is_no_file(fleet_env):
    assert tools.approval_status({"kind": "spec", "ref": "nothing"})["status"] == "none"
    approvals.create("spec", "other", "x")
    assert tools.approval_status({"kind": "spec", "ref": "nothing"})["status"] == "none"


def test_a_pending_approval_reads_as_expired_after_72_hours_even_when_no_event_refreshed_the_file(fleet_env, monkeypatch):
    approvals.create("spec", "late", "x", now=int(time.time()) - 73 * 3600)
    assert tools.approval_status({"kind": "spec", "ref": "late"})["status"] == "expired"


@pytest.mark.parametrize("args", [{"kind": "spend", "ref": "x"}, {"kind": "spec", "ref": ""}, {"kind": "spec", "ref": "a b"}, {"kind": "spec", "ref": "x" * 81}, {}])
def test_the_tool_refuses_a_bad_kind_or_reference(fleet_env, args):
    with pytest.raises(tools.ToolError):
        tools.approval_status(args)


def test_the_tool_is_registered_read_only_for_the_orchestrator_and_security_agents():
    import pathlib
    import yaml
    root = pathlib.Path(__file__).resolve().parents[2]
    assert "approval_status" in tools.TOOLS and tools.TOOLS["approval_status"]["schema"]["required"] == ["kind", "ref"]
    holders = sorted(p.parent.name for p in (root / "agents").glob("*/agent.yaml")
                     if "approval_status" in (((yaml.safe_load(p.read_text()).get("mcp_servers") or {}).get("fleet_tools") or {}).get("tools") or []))
    assert holders == ["orchestrator", "security"]
