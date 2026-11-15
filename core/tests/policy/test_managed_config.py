"""Policy: the Hermes managed config pins every required key."""
import os
import pathlib
import stat

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "config" / "hermes.managed.yaml"
INSTALLED = pathlib.Path("/etc/hermes/config.yaml")

REQUIRED = {
    "approvals.mode": "smart",
    "approvals.cron_mode": "deny",
    "approvals.single_query_mode": "deny",
    "approvals.unattended_mode": "deny",
    "terminal.backend": "docker",
    "terminal.docker_forward_env": [],
    "terminal.env_passthrough": [],
    "terminal.docker_extra_args": [],
    "security.redact_secrets": True,
    "providers.fleet-router.api": "http://127.0.0.1:20128/v1",
    "providers.fleet-router.key_env": "ROUTER_KEY_HERMES",
    "model.provider": "fleet-router",
    "model.base_url": "http://127.0.0.1:20128/v1",
    "updates.check": False,
    "skills.write_approval": True,
    "web.search_backend": "searxng",
    "web.keyless_fallback": False,
    "web.keyless_rescue": False,
    "hooks_auto_accept": True,
}


def dig(cfg, dotted):
    node = cfg
    for part in dotted.split("."):
        assert isinstance(node, dict) and part in node, "missing key: " + dotted
        node = node[part]
    return node


def check(cfg):
    for key, want in REQUIRED.items():
        assert dig(cfg, key) == want, "%s must be %r" % (key, want)
    deny = dig(cfg, "approvals.deny")
    assert isinstance(deny, list) and len(deny) >= 10
    for must in ("sudo *", "docker *", "systemctl *", "git push --force*"):
        assert must in deny, "approvals.deny must hold " + must
    for event in ("pre_tool_call", "pre_llm_call"):
        hooks = dig(cfg, "hooks." + event)
        assert hooks and hooks[0]["command"].endswith("/core/killswitch/hermes-paused-hook.sh")
    assert dig(cfg, "hooks.pre_tool_call")[0]["fail_closed"] is True
    image = dig(cfg, "terminal.docker_image")
    assert "@sha256:" in image or image.startswith("@@"), "the sandbox image must be pinned by digest"


def test_template_pins_every_required_key():
    check(yaml.safe_load(TEMPLATE.read_text()))


def test_template_has_no_secret_values():
    text = TEMPLATE.read_text()
    assert "api_key" not in text.lower().replace("key_env", "")


def test_installed_config_pins_every_required_key(live_repo):
    if not INSTALLED.exists():
        pytest.skip("Hermes managed config is not installed yet")
    cfg = yaml.safe_load(INSTALLED.read_text())
    check(cfg)
    assert "@@" not in INSTALLED.read_text(), "a placeholder was not replaced"


def test_installed_config_is_owned_by_root_and_not_writable(live_repo):
    if not INSTALLED.exists():
        pytest.skip("Hermes managed config is not installed yet")
    st = INSTALLED.stat()
    assert st.st_uid == 0
    assert stat.S_IMODE(st.st_mode) == 0o644
    dst = INSTALLED.parent.stat()
    assert dst.st_uid == 0 and stat.S_IMODE(dst.st_mode) == 0o755
