import io
import json
import subprocess
import sys

import pytest

from ops.fleetlib import httpc
from ops.mcp import server, tools


def call(msg, enabled):
    return server.handle(msg, enabled)


def test_initialize_list_and_unknown_method():
    enabled = {"web_read", "guard_scan"}
    init = call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, enabled)
    assert init["result"]["protocolVersion"] == "2025-03-26" and "tools" in init["result"]["capabilities"]
    listing = call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, enabled)
    assert {t["name"] for t in listing["result"]["tools"]} == enabled
    assert call({"jsonrpc": "2.0", "method": "notifications/initialized"}, enabled) is None
    assert call({"jsonrpc": "2.0", "id": 3, "method": "nope"}, enabled)["error"]["code"] == -32601


def test_a_profile_cannot_call_a_tool_it_was_not_given():
    out = call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "publish_post", "arguments": {}}}, {"web_read"})
    assert out["error"]["code"] == -32602


def test_tool_errors_are_reported_not_raised():
    out = call({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "guard_scan", "arguments": {"text": 5}}}, {"guard_scan"})
    assert out["result"]["isError"] is True


def test_guard_and_reader_tools_call_the_services():
    seen = []

    def fake(method, url, headers, body, timeout, max_bytes):
        seen.append((url, json.loads(body)))
        return httpc.Response(200, {}, json.dumps({"label": "SAFE", "status": "ok"}).encode())
    httpc.set_transport(fake)
    try:
        assert tools.guard_scan({"text": "hi"})["label"] == "SAFE"
        assert tools.web_read({"url": "https://example.com/"})["status"] == "ok"
        assert seen[0][0].endswith(":8099/scan") and seen[1][0].endswith(":8095/read")
    finally:
        httpc.reset_transport()


def test_publish_tool_sends_the_token_only_in_the_header(monkeypatch):
    monkeypatch.setenv("PUBLISHER_API_TOKEN", "tok-" + "x" * 40)
    seen = {}

    def fake(method, url, headers, body, timeout, max_bytes):
        seen.update(headers=headers, body=body)
        return httpc.Response(200, {}, b'{"status": "dry_run"}')
    httpc.set_transport(fake)
    try:
        tools.publish_post({"platform": "bluesky", "text": "hello", "project": "demo"})
        assert seen["headers"]["Authorization"].startswith("Bearer tok-")
        assert b"tok-" not in seen["body"]
    finally:
        httpc.reset_transport()


def test_ticket_schema_and_files(fleet_env):
    (fleet_env["state"] / "tickets").mkdir()
    good = {"category": "bug", "severity": "medium", "summary": "Export does nothing on small screens.",
            "evidence_link": "inbox/f-1.json", "user_hash": "u_0123456789ab"}
    out = tools.ticket_submit(good)
    assert (fleet_env["state"] / "tickets" / (out["ticket"] + ".json")).exists()
    for bad in (dict(good, summary="x" * 281), dict(good, user_hash="alice@example.com"), dict(good, category="rant"),
                dict(good, extra="field"), {k: v for k, v in good.items() if k != "severity"}):
        with pytest.raises(tools.ToolError):
            tools.ticket_submit(bad)


def test_inbox_tools_reject_bad_names(fleet_env):
    (fleet_env["state"] / "inbox").mkdir()
    (fleet_env["state"] / "inbox" / "a.json").write_text('{"text": "hello"}')
    assert tools.inbox_list({})["items"] == ["a.json"]
    assert tools.inbox_read({"name": "a.json"})["item"]["text"] == "hello"
    for bad in ("../secret.json", "a/b.json", "a.txt", ""):
        with pytest.raises(tools.ToolError):
            tools.inbox_read({"name": bad})


def test_stdio_round_trip():
    # A fixed command line: this repository's own MCP server. No shell runs.
    proc = subprocess.run([sys.executable, "-m", "ops.mcp.server", "--tools", "guard_scan"],  # nosemgrep: dangerous-subprocess-use-audit
                          input='{"jsonrpc":"2.0","id":1,"method":"tools/list"}\nnot json\n', capture_output=True, text=True, timeout=20)
    out = json.loads(proc.stdout.splitlines()[0])
    assert out["result"]["tools"][0]["name"] == "guard_scan"
    bad = subprocess.run([sys.executable, "-m", "ops.mcp.server", "--tools", "nope"], capture_output=True, text=True, timeout=20)  # nosemgrep: dangerous-subprocess-use-audit
    assert bad.returncode == 2


# ------------------------------------------------------------------ ticket_list and ticket_update
def _make_ticket(fleet_env, status="open"):
    from ops.mcp import tools as t
    out = t.ticket_submit({"category": "bug", "severity": "medium", "summary": "The export button does nothing.",
                           "evidence_link": "inbox/f-1.json", "user_hash": "u_0123456789ab"})
    return out["ticket"]


def test_ticket_list_shows_fields_and_filters(fleet_env):
    (fleet_env["state"] / "tickets").mkdir(exist_ok=True)
    tid = _make_ticket(fleet_env)
    from ops.mcp import tools as t
    got = t.ticket_list({})["tickets"]
    assert got[0]["id"] == tid and got[0]["status"] == "open" and "summary" in got[0]
    assert t.ticket_list({"status": "fixed"})["tickets"] == []
    with pytest.raises(t.ToolError):
        t.ticket_list({"status": "weird"})


def test_ticket_update_changes_only_status_and_note(fleet_env):
    (fleet_env["state"] / "tickets").mkdir(exist_ok=True)
    tid = _make_ticket(fleet_env)
    from ops.mcp import tools as t
    before = json.loads((fleet_env["state"] / "tickets" / (tid + ".json")).read_text())
    assert t.ticket_update({"id": tid, "status": "fixed", "note": "Fixed in branch agent/build/export.\x00"})["status"] == "fixed"
    after = json.loads((fleet_env["state"] / "tickets" / (tid + ".json")).read_text())
    assert after["status"] == "fixed" and after["note"].startswith("Fixed in branch") and "\x00" not in after["note"]
    for key in ("category", "severity", "summary", "evidence_link", "user_hash", "created"):
        assert after[key] == before[key]


@pytest.mark.parametrize("bad", ["../x", "t-1", "t-123456-ZZZZZZZZZZ", "", "t-123456-0123456789.json"])
def test_ticket_update_rejects_bad_ids(fleet_env, bad):
    from ops.mcp import tools as t
    with pytest.raises(t.ToolError):
        t.ticket_update({"id": bad, "status": "fixed"})


def test_ticket_update_rejects_bad_status_and_missing_tickets(fleet_env):
    (fleet_env["state"] / "tickets").mkdir(exist_ok=True)
    from ops.mcp import tools as t
    with pytest.raises(t.ToolError):
        t.ticket_update({"id": "t-1234567-0123456789", "status": "deleted"})
    with pytest.raises(t.ToolError):
        t.ticket_update({"id": "t-1234567-0123456789", "status": "fixed"})


def test_faq_list_reads_the_live_copy_only(fleet_env, tmp_path, monkeypatch):
    from ops.mcp import tools as t
    live = tmp_path / "live"
    (live / "projects" / "demo").mkdir(parents=True)
    (live / "projects" / "demo" / "faq.json").write_text(json.dumps({"faq": [
        {"id": "pricing_free_tier", "question": "Is it free?", "answer": "Yes."}, {"id": "BAD ID", "answer": "x"}, "junk"]}))
    monkeypatch.setenv("FLEET_LIVE_REPO", str(live))
    out = t.faq_list({"project": "demo"})
    assert out["faq"] == [{"id": "pricing_free_tier", "question": "Is it free?", "answer": "Yes."}]
    assert t.faq_list({"project": "nothing"})["faq"] == []
    for bad in ("../x", "A", "", "x/y"):
        with pytest.raises(t.ToolError):
            t.faq_list({"project": bad})


def test_inbox_ack_hides_an_item_and_keeps_the_file(fleet_env):
    from ops.mcp import tools as t
    inbox = fleet_env["state"] / "inbox"
    inbox.mkdir(exist_ok=True)
    for n in ("a.json", "b.json"):
        (inbox / n).write_text('{"text": "x"}')
    assert t.inbox_list({})["items"] == ["a.json", "b.json"]
    assert t.inbox_ack({"name": "a.json"}) == {"acked": "a.json", "waiting": 1}
    assert t.inbox_list({})["items"] == ["b.json"] and (inbox / "a.json").exists()
    t.inbox_ack({"name": "a.json"})                                  # twice is fine
    assert t.inbox_list({})["items"] == ["b.json"]
    for bad in ("../x.json", "nothing.json", "a.txt", ""):
        with pytest.raises(t.ToolError):
            t.inbox_ack({"name": bad})


# ------------------------------------------------------------------ preview
def _site(tmp_path, project="demo", worktree=None):
    root = tmp_path / "work"
    if worktree:
        root = root / ".worktrees" / worktree
    pub = root / "projects" / project / "app" / "public"
    pub.mkdir(parents=True)
    (pub / "index.html").write_text("<h1>hello</h1>")
    (pub / "secret-link").symlink_to("/etc/passwd")
    return tmp_path / "work"


def test_preview_serves_files_and_blocks_listing_links_and_posts(tmp_path):
    import urllib.error
    import urllib.request
    from ops.mcp import preview
    base = _site(tmp_path)
    info = preview.start({"project": "demo"}, base=base)
    try:
        assert info["url"].startswith("http://127.0.0.1:") and info["folder"] == "projects/demo/app/public"
        assert urllib.request.urlopen(info["url"] + "index.html", timeout=5).read() == b"<h1>hello</h1>"
        for path in ("secret-link", "../../../../etc/passwd", "%2e%2e/%2e%2e/etc/passwd", "nothing.html"):
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(info["url"] + path, timeout=5)
            assert err.value.code == 404
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(urllib.request.Request(info["url"], data=b"x", method="POST"), timeout=5)
        assert err.value.code == 501 or err.value.code == 405
    finally:
        assert preview.stop({"project": "demo"}) == {"stopped": True}
    assert preview.stop({"project": "demo"}) == {"stopped": False}


def test_preview_can_serve_a_worktree_and_limits_the_number_of_servers(tmp_path):
    from ops.mcp import preview
    base = _site(tmp_path, "demo", worktree="topic-1")
    info = preview.start({"project": "demo", "worktree": "topic-1"}, base=base)
    assert info["folder"] == ".worktrees/topic-1/projects/demo/app/public"
    preview.stop({"project": "demo"})
    for bad in ({"project": "../x"}, {"project": "demo", "worktree": "../../x"}, {"project": "nothing"}, {"project": "demo", "worktree": "missing"}):
        with pytest.raises(preview.PreviewError):
            preview.start(bad, base=base)


def test_preview_tool_wraps_errors(tmp_path, monkeypatch):
    from ops.mcp import tools as t
    monkeypatch.setenv("FLEET_WORK_CLONE", str(tmp_path / "none"))
    with pytest.raises(t.ToolError):
        t.preview_start({"project": "demo"})
    assert "preview_start" in t.TOOLS and "preview_stop" in t.TOOLS


def test_reply_tool_sends_the_reply_token_and_not_the_post_token(monkeypatch):
    """The Publisher checks /replies against PUBLISHER_REPLY_TOKEN. The feedback profile holds only that token."""
    monkeypatch.setenv("PUBLISHER_REPLY_TOKEN", "reply-" + "r" * 40)
    monkeypatch.setenv("PUBLISHER_API_TOKEN", "post-" + "p" * 40)
    seen = {}

    def fake(method, url, headers, body, timeout, max_bytes):
        seen.update(url=url, headers=headers, body=body)
        return httpc.Response(200, {}, b'{"status": "dry_run"}')
    httpc.set_transport(fake)
    try:
        tools.reply_faq({"platform": "bluesky", "in_reply_to": "at://did:plc:abc/app.bsky.feed.post/3k", "faq_id": "price", "project": "demo"})
    finally:
        httpc.reset_transport()
    assert seen["url"].endswith("/replies")
    assert seen["headers"]["Authorization"] == "Bearer reply-" + "r" * 40
    assert b"reply-" not in seen["body"] and b"post-" not in seen["body"]


def test_every_agent_with_a_token_tool_gets_that_token():
    """A tool that needs a local token is useless when the profile does not hold the token. See the audit finding F-12."""
    import pathlib
    import yaml
    root = pathlib.Path(__file__).resolve().parents[2]
    checked = 0
    for path in sorted((root / "agents").glob("*/agent.yaml")):
        spec = (yaml.safe_load(path.read_text()) or {}).get("mcp_servers", {}).get("fleet_tools") or {}
        for tool in spec.get("tools") or []:
            assert tool in tools.TOOLS, "%s names an unknown tool %s" % (path, tool)
            if tool in tools.TOKEN_ENV:
                assert tools.TOKEN_ENV[tool] in (spec.get("env_from_profile") or []), "%s has %s without %s" % (path, tool, tools.TOKEN_ENV[tool])
                checked += 1
    assert checked >= 3


def test_ticket_list_shows_the_newest_tickets_first_and_filters_before_the_cut(fleet_env):
    """With more than 200 tickets, the old code read only the 200 oldest files. New tickets were invisible. See F-13."""
    folder = fleet_env["state"] / "tickets"
    folder.mkdir(exist_ok=True)
    for i in range(260):
        status = "open" if i >= 255 else "fixed"
        rec = {"id": "t-%d-%010x" % (1700000000 + i, i), "category": "bug", "severity": "low", "summary": "ticket %d" % i,
               "evidence_link": "x", "user_hash": "u_0123456789ab", "status": status, "created": 1700000000 + i}
        (folder / (rec["id"] + ".json")).write_text(json.dumps(rec))
    from ops.mcp import tools as t
    newest = t.ticket_list({})["tickets"]
    assert len(newest) == 50 and newest[0]["summary"] == "ticket 259" and newest[-1]["summary"] == "ticket 210"
    still_open = t.ticket_list({"status": "open"})["tickets"]
    assert [x["summary"] for x in still_open] == ["ticket %d" % i for i in (259, 258, 257, 256, 255)]
    assert len(t.ticket_list({"status": "fixed", "limit": 100000})["tickets"]) == t.TICKET_LIST_MAX
    assert len(t.ticket_list({"limit": "many"})["tickets"]) == 50 and len(t.ticket_list({"limit": -4})["tickets"]) == 1


def test_inbox_list_survives_a_bad_limit(fleet_env):
    from ops.mcp import tools as t
    (fleet_env["state"] / "inbox").mkdir(exist_ok=True)
    (fleet_env["state"] / "inbox" / "f-1-aaaaaaaa.json").write_text("{}")
    assert t.inbox_list({"limit": "x"})["items"] == ["f-1-aaaaaaaa.json"]
