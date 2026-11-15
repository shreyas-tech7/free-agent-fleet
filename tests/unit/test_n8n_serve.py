import json
import os
import pwd
import stat

import pytest

from ops.fleetlib import secrets
from ops.n8n import mcpenv, serve
from ops.n8n import prepare as prep
from tests.unit.fctl_helpers import Runner

STATUS = json.dumps({"BackendState": "Running", "Self": {"DNSName": "box.tail1234.ts.net."}})


@pytest.fixture
def n8n(fleet_env):
    prep.ensure_dirs()
    (prep.run_dir() / ".env").write_text("N8N_IMAGE=img\nN8N_VERSION=2.47.4\nN8N_EDITOR_BASE_URL=\nWEBHOOK_URL=\nN8N_PROXY_HOPS=0\n")
    return fleet_env


def test_serve_on_runs_tailscale_serve_and_sets_the_addresses(n8n):
    r = Runner([(["status", "--json"], (0, STATUS)), (["serve"], (0, ""))])
    url = serve.serve_on(r, port=8443)
    assert url == "https://box.tail1234.ts.net:8443"
    argv = next(c for c, _u in r.calls if c[:2] == ["tailscale", "serve"])
    assert argv == ["tailscale", "serve", "--bg", "--https=8443", "http://127.0.0.1:5678"]
    env = prep.read_env_file(prep.run_dir() / ".env")
    assert env["N8N_EDITOR_BASE_URL"] == url and env["WEBHOOK_URL"] == url + "/" and env["N8N_PROXY_HOPS"] == "1"
    assert stat.S_IMODE((prep.run_dir() / ".env").stat().st_mode) == 0o600


def test_funnel_can_never_be_built(n8n):
    r = Runner([(["status", "--json"], (0, STATUS)), (["serve"], (0, ""))])
    serve.serve_on(r)
    serve.serve_off(r)
    assert not any("funnel" in " ".join(c) for c, _u in r.calls)


def test_serve_off_clears_the_addresses_and_runs_serve_off(n8n):
    r = Runner([(["status", "--json"], (0, STATUS)), (["serve"], (0, ""))])
    serve.serve_on(r)
    assert serve.serve_off(r) is True
    assert any(c == ["tailscale", "serve", "--https=8443", "off"] for c, _u in r.calls)
    env = prep.read_env_file(prep.run_dir() / ".env")
    assert "N8N_EDITOR_BASE_URL" not in env and "WEBHOOK_URL" not in env and env["N8N_PROXY_HOPS"] == "0"


@pytest.mark.parametrize("answer", [(0, json.dumps({"BackendState": "NeedsLogin"})), (0, "not json"), (0, json.dumps({"BackendState": "Running", "Self": {"DNSName": ""}})),
                                    (0, json.dumps({"BackendState": "Running", "Self": {"DNSName": "bad name;rm.ts.net."}}))])
def test_serve_refuses_when_tailscale_is_not_ready_or_the_name_is_odd(n8n, answer):
    r = Runner([(["status", "--json"], answer)])
    with pytest.raises(serve.ServeError):
        serve.serve_on(r)
    assert not r.ran("serve")


def test_a_failed_tailscale_serve_changes_nothing_in_the_env(n8n):
    before = (prep.run_dir() / ".env").read_text()
    r = Runner([(["status", "--json"], (0, STATUS)), (["serve"], (1, "serve is not enabled on your tailnet"))])
    with pytest.raises(serve.ServeError, match="not enabled"):
        serve.serve_on(r)
    assert (prep.run_dir() / ".env").read_text() == before


# ------------------------------------------------------------------------------------------------ mcp env
def test_the_mcp_env_file_has_the_key_with_mode_600(n8n, monkeypatch, tmp_path):
    secrets.set_value("N8N_API_KEY", "eyJ" + "a" * 40 + ".b.c")
    me = pwd.getpwuid(os.getuid()).pw_name
    monkeypatch.setenv("SUDO_USER", me)
    monkeypatch.setattr(mcpenv.pwd, "getpwnam", lambda name: type("P", (), {"pw_dir": str(tmp_path), "pw_name": me, "pw_uid": os.getuid(), "pw_gid": os.getgid()})())
    path = mcpenv.write()
    assert path == tmp_path / ".config" / "n8n-mcp" / "env" and stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    assert text.startswith("N8N_API_URL=http://127.0.0.1:5678\n") and "N8N_API_KEY=eyJ" in text


def test_the_mcp_env_needs_a_key_and_a_real_user(n8n, monkeypatch):
    monkeypatch.setenv("SUDO_USER", "someone")
    with pytest.raises(mcpenv.McpEnvError, match="no API key"):
        mcpenv.write()
    monkeypatch.delenv("SUDO_USER", raising=False)
    with pytest.raises(mcpenv.McpEnvError, match="sudo"):
        mcpenv.target_path()
    monkeypatch.setenv("SUDO_USER", "root")
    with pytest.raises(mcpenv.McpEnvError):
        mcpenv.target_path()
