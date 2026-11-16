import io
import json
import os
import stat
from types import SimpleNamespace

import pytest

from ops.fleetctl import checks, secretcmd
from ops.fleetlib import config, httpc, secrets, tokens
from tests.unit.fctl_helpers import FakeHttp, Runner, Script

GROQ = "gsk_" + "A1b2C3d4E5" * 3


def args(**kw):
    return SimpleNamespace(**kw)


def test_set_from_stdin_saves_with_mode_600_and_never_prints_the_value(fleet_env, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(GROQ + "\n"))
    s = Script(interactive=False)
    assert secretcmd.cmd_set(args(name="GROQ_API_KEY", stdin=True), s.con, Runner()) == 0
    path = secrets.secret_path("GROQ_API_KEY")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert secrets.value_of("GROQ_API_KEY") == GROQ
    assert GROQ not in s.text and "Saved GROQ_API_KEY" in s.text


def test_set_with_a_hidden_prompt(fleet_env):
    s = Script(hidden=[GROQ])
    assert secretcmd.cmd_set(args(name="GROQ_API_KEY", stdin=False), s.con, Runner()) == 0
    assert secrets.is_set("GROQ_API_KEY") and GROQ not in s.text


def test_set_needs_a_terminal_unless_stdin(fleet_env):
    s = Script(interactive=False)
    assert secretcmd.cmd_set(args(name="GROQ_API_KEY", stdin=False), s.con, Runner()) == 2
    assert "--stdin" in s.text and not secrets.is_set("GROQ_API_KEY")


def test_bad_format_is_refused_and_not_echoed(fleet_env):
    s = Script(hidden=["definitely-not-a-key-0123456789"])
    assert secretcmd.cmd_set(args(name="GROQ_API_KEY", stdin=False), s.con, Runner()) == 2
    assert "definitely-not" not in s.text and "console.groq.com" in s.text
    assert not secrets.is_set("GROQ_API_KEY")


def test_an_installer_secret_cannot_be_typed(fleet_env):
    s = Script(hidden=["x" * 40])
    assert secretcmd.cmd_set(args(name="JWT_SECRET", stdin=False), s.con, Runner()) == 2
    assert "rotate" in s.text


def test_unknown_name(fleet_env):
    s = Script()
    assert secretcmd.cmd_set(args(name="NOPE", stdin=False), s.con, Runner()) == 2


def test_list_shows_names_and_state_but_no_value(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    s = Script()
    assert secretcmd.cmd_list(args(group=None), s.con) == 0
    assert "GROQ_API_KEY" in s.text and "set" in s.text and GROQ not in s.text
    s2 = Script()
    assert secretcmd.cmd_list(args(group="nope"), s2.con) == 2


def test_list_by_group(fleet_env):
    s = Script()
    secretcmd.cmd_list(args(group="control"), s.con)
    assert "CONTROL_TELEGRAM_BOT_TOKEN" in s.text and "GROQ_API_KEY" not in s.text


def test_restart_only_units_that_run(fleet_env):
    r = Runner(rules=[(["is-active", "agent-fleet-publisher.service"], (0, "active")),
                      (["is-active", "agent-fleet-control.service"], (3, "inactive"))])
    notes = secretcmd.restart_units(["agent-fleet-publisher", "agent-fleet-control"], r)
    assert r.ran("restart", "agent-fleet-publisher.service") and not r.ran("restart", "agent-fleet-control.service")
    assert any("not running" in n for n in notes)


def test_set_in_a_service_group_restarts_that_service(fleet_env):
    r = Runner(rules=[(["is-active"], (0, "active"))])
    s = Script(hidden=["MASTODONTOKEN" + "x" * 20])
    assert secretcmd.cmd_set(args(name="MASTODON_ACCESS_TOKEN", stdin=False), s.con, r) == 0
    assert r.ran("restart", "agent-fleet-publisher.service")


def test_a_router_key_points_to_the_approve_step(fleet_env):
    s = Script(hidden=[GROQ])
    secretcmd.cmd_set(args(name="GROQ_API_KEY", stdin=False), s.con, Runner())
    assert "router approve" in s.text


# ------------------------------------------------------------------ check
def test_check_sends_the_key_as_a_bearer_header_and_does_not_print_it(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    http = FakeHttp({"https://api.groq.com/openai/v1/models": (200, {"data": []})})
    state, detail = checks.check_secret("GROQ_API_KEY", config.load(), http=http)
    assert state == "ok" and GROQ not in detail
    assert http.calls[0]["headers"]["Authorization"] == "Bearer " + GROQ


def test_check_reports_a_refused_key(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    http = FakeHttp({"https://api.groq.com": (401, {})})
    assert checks.check_secret("GROQ_API_KEY", config.load(), http=http)[0] == "failed"


def test_check_reports_a_network_failure(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    http = FakeHttp({"https://api.groq.com": None})
    assert checks.check_secret("GROQ_API_KEY", config.load(), http=http)[0] == "unreachable"


def test_check_states_for_unset_and_skipped(fleet_env):
    assert checks.check_secret("GROQ_API_KEY", config.load())[0] == "not set"
    secrets.set_value("PUBLISHER_API_TOKEN", "t" * 40, enforce_format=False)
    assert checks.check_secret("PUBLISHER_API_TOKEN", config.load())[0] == "skipped"


def test_header_style_check_uses_the_named_header(fleet_env):
    key = "AIza" + "B" * 35
    secrets.set_value("GEMINI_API_KEY", key)
    http = FakeHttp()
    assert checks.check_secret("GEMINI_API_KEY", config.load(), http=http)[0] == "ok"
    assert http.calls[0]["headers"]["x-goog-api-key"] == key


def test_telegram_check_puts_the_token_in_the_url_only(fleet_env):
    token = "123456789:" + "A" * 35
    secrets.set_value("CONTROL_TELEGRAM_BOT_TOKEN", token)
    http = FakeHttp({"https://api.telegram.org/bot": (200, {"ok": True})})
    state, detail = checks.check_secret("CONTROL_TELEGRAM_BOT_TOKEN", config.load(), http=http)
    assert state == "ok" and token not in detail


def test_telegram_check_fails_when_ok_is_false(fleet_env):
    token = "123456789:" + "A" * 35
    secrets.set_value("CONTROL_TELEGRAM_BOT_TOKEN", token)
    http = FakeHttp({"https://api.telegram.org/bot": (200, {"ok": False})})
    assert checks.check_secret("CONTROL_TELEGRAM_BOT_TOKEN", config.load(), http=http)[0] == "failed"


def test_bluesky_check_needs_the_handle_first(fleet_env):
    secrets.set_value("BLUESKY_APP_PASSWORD", "abcd-efgh-ijkl-mnop")
    assert checks.check_secret("BLUESKY_APP_PASSWORD", config.load(), http=FakeHttp())[0] == "skipped"
    cfg = config.load()
    cfg["publisher"]["platforms"]["bluesky"]["handle"] = "shop.bsky.social"
    http = FakeHttp({"https://bsky.social/xrpc/com.atproto.server.createSession": (200, {"accessJwt": "j"})})
    assert checks.check_secret("BLUESKY_APP_PASSWORD", cfg, http=http)[0] == "ok"
    assert http.calls[0]["json"]["identifier"] == "shop.bsky.social"


def test_mastodon_check_needs_an_https_instance(fleet_env):
    secrets.set_value("MASTODON_ACCESS_TOKEN", "m" * 30)
    assert checks.check_secret("MASTODON_ACCESS_TOKEN", config.load(), http=FakeHttp())[0] == "skipped"


def test_smtp_check_logs_in_and_quits(fleet_env):
    secrets.set_value("SMTP_PASSWORD", "pw" * 8)
    cfg = config.load()
    cfg["briefing"]["email"].update({"smtp_host": "mail.example", "smtp_user": "me@example.com"})
    events = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=0):
            events.append(("connect", host, port))

        def starttls(self):
            events.append("tls")

        def login(self, user, password):
            events.append(("login", user))

        def quit(self):
            events.append("quit")

    mod = SimpleNamespace(SMTP=FakeSMTP, SMTP_SSL=FakeSMTP)
    assert checks.check_secret("SMTP_PASSWORD", cfg, smtp_module=mod)[0] == "ok"
    assert events == [("connect", "mail.example", 587), "tls", ("login", "me@example.com"), "quit"]


def test_the_check_command_lists_only_set_keys_and_returns_1_on_a_failure(fleet_env, monkeypatch):
    secrets.set_value("GROQ_API_KEY", GROQ)
    monkeypatch.setattr(checks, "check_secret", lambda name, cfg, **kw: ("failed", "refused") if secrets.is_set(name) else ("not set", ""))
    s = Script()
    assert secretcmd.cmd_check(args(names=[]), s.con, Runner()) == 1
    assert "GROQ_API_KEY" in s.text and "OPENROUTER_API_KEY" not in s.text


def test_the_check_command_with_no_keys(fleet_env):
    s = Script()
    assert secretcmd.cmd_check(args(names=[]), s.con, Runner()) == 0
    assert "No key is set" in s.text


# ------------------------------------------------------------------ rotate
def test_rotate_a_local_token_changes_the_value_and_the_hash(fleet_env):
    tokens.rotate("PUBLISHER_API_TOKEN")
    old = secrets.value_of("PUBLISHER_API_TOKEN")
    old_hash = tokens.hashes("PUBLISHER_API_TOKEN")
    s = Script()
    r = Runner(rules=[(["is-active"], (0, "active"))])
    assert secretcmd.cmd_rotate(args(name="PUBLISHER_API_TOKEN"), s.con, r) == 0
    assert secrets.value_of("PUBLISHER_API_TOKEN") != old and tokens.hashes("PUBLISHER_API_TOKEN") != old_hash
    assert r.ran("restart", "agent-fleet-publisher.service") and r.ran("restart", "agent-fleet-gateway.service")
    assert old not in s.text


def test_rotate_refuses_provider_keys_and_unsafe_secrets(fleet_env):
    s = Script()
    assert secretcmd.cmd_rotate(args(name="GROQ_API_KEY"), s.con, Runner()) == 2
    assert "console.groq.com" in s.text
    s2 = Script()
    assert secretcmd.cmd_rotate(args(name="STORAGE_ENCRYPTION_KEY"), s2.con, Runner()) == 2


def test_rotate_a_router_client_key_asks_the_router_for_a_new_one(fleet_env, monkeypatch):
    secrets.set_value("ROUTER_KEY_BRIEFING", "k" * 40, enforce_format=False)
    called = []
    monkeypatch.setattr(secretcmd, "cmd_router_apply", lambda a, c, r, **k: called.append(1) or 0)
    s = Script()
    assert secretcmd.cmd_rotate(args(name="ROUTER_KEY_BRIEFING"), s.con, Runner()) == 0
    assert called and not secrets.is_set("ROUTER_KEY_BRIEFING")


# ------------------------------------------------------------------ router
def test_router_approve_asks_first(fleet_env, monkeypatch):
    monkeypatch.setattr(secretcmd, "cmd_router_apply", lambda *a, **k: 0)
    s = Script(interactive=False)
    assert secretcmd.cmd_router_approve(args(provider="groq", yes=False), s.con, Runner()) == 2
    assert config.get(config.load(), "router.approved_providers") == []
    s2 = Script(inputs=["no"])
    assert secretcmd.cmd_router_approve(args(provider="groq", yes=False), s2.con, Runner()) == 1
    assert "groq.com" in s2.text                         # the terms link is shown
    s3 = Script(inputs=["approve"])
    assert secretcmd.cmd_router_approve(args(provider="groq", yes=False), s3.con, Runner()) == 0
    assert config.get(config.load(), "router.approved_providers") == ["groq"]


def test_router_approve_with_yes_and_unknown_provider(fleet_env, monkeypatch):
    monkeypatch.setattr(secretcmd, "cmd_router_apply", lambda *a, **k: 0)
    s = Script(interactive=False)
    assert secretcmd.cmd_router_approve(args(provider="cerebras", yes=True), s.con, Runner()) == 2
    assert secretcmd.cmd_router_approve(args(provider="gemini", yes=True), s.con, Runner()) == 0
    assert config.get(config.load(), "router.approved_providers") == ["gemini"]


def test_router_apply_reports_without_keys(fleet_env):
    def fake_main():
        print(json.dumps({"providers": {"groq": "created"}, "combos": {"fleet-smart": "updated (2 targets)"}, "keys": {"hermes": "kept"}, "warnings": ["w"]}))
    s = Script()
    assert secretcmd.cmd_router_apply(args(), s.con, Runner(), apply_main=fake_main) == 0
    assert "provider groq: created" in s.text and "warning: w" in s.text


def test_router_apply_failure_is_one_line(fleet_env):
    from ops.router import apply as ra

    def fake_main():
        raise ra.RouterError("the router is not reachable: refused")
    s = Script()
    assert secretcmd.cmd_router_apply(args(), s.con, Runner(), apply_main=fake_main) == 1
    assert "not reachable" in s.text


# ---- Prompt 3: set needs a hidden prompt, show works only in an interactive terminal ----

def test_stdin_flag_on_a_terminal_is_refused_because_the_typing_would_show(fleet_env, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(GROQ + "\n"))
    s = Script(interactive=True)
    assert secretcmd.cmd_set(args(name="GROQ_API_KEY", stdin=True), s.con, Runner()) == 2
    assert "would show" in s.text and GROQ not in s.text
    assert not secrets.is_set("GROQ_API_KEY")


def test_show_is_refused_when_input_is_not_a_terminal(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    s = Script(interactive=False, tty_out=True)
    assert secretcmd.cmd_show(args(name="GROQ_API_KEY"), s.con) == 2
    assert "Refused" in s.text and GROQ not in s.text


def test_show_is_refused_when_output_is_not_a_terminal(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    s = Script(interactive=True, tty_out=False)
    assert secretcmd.cmd_show(args(name="GROQ_API_KEY"), s.con) == 2
    assert "Refused" in s.text and GROQ not in s.text


def test_show_prints_on_a_terminal_and_hides_it_again(fleet_env, monkeypatch):
    monkeypatch.setenv("TERM", "xterm")
    secrets.set_value("GROQ_API_KEY", GROQ)
    s = Script(inputs=[""], interactive=True, tty_out=True)
    assert secretcmd.cmd_show(args(name="GROQ_API_KEY"), s.con) == 0
    assert GROQ in s.text and "Press Enter" not in s.text    # the prompt goes through the input function
    assert "\x1b[" in s.text                                  # the erase sequence was sent after Enter


def test_show_does_not_erase_on_a_dumb_terminal(fleet_env, monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    secrets.set_value("GROQ_API_KEY", GROQ)
    s = Script(inputs=[""], interactive=True, tty_out=True)
    assert secretcmd.cmd_show(args(name="GROQ_API_KEY"), s.con) == 0
    assert "\x1b[" not in s.text


def test_show_says_when_the_secret_is_not_set_or_unknown(fleet_env):
    s = Script(interactive=True, tty_out=True)
    assert secretcmd.cmd_show(args(name="GROQ_API_KEY"), s.con) == 1
    assert "is not set" in s.text
    s2 = Script(interactive=True, tty_out=True)
    assert secretcmd.cmd_show(args(name="NOPE"), s2.con) == 2


def test_show_reaches_an_auto_secret(fleet_env, monkeypatch):
    """The owner reads installer secrets, such as a generated password, with show."""
    monkeypatch.setenv("TERM", "dumb")
    fake = "q7" * 18                       # built at run time, so no scanner sees a literal key
    path = secrets.secret_path("JWT_SECRET")
    path.write_text("JWT_SECRET=%s\n" % fake)
    s = Script(inputs=[""], interactive=True, tty_out=True)
    assert secretcmd.cmd_show(args(name="JWT_SECRET"), s.con) == 0
    assert fake in s.text


# ---- fleetctl router show ----

class StubRouter:
    """Stands in for the router client. It answers the three read calls that `router show` makes."""

    def __init__(self, fail_login=False):
        self.fail_login = fail_login

    def login(self):
        if self.fail_login:
            from ops.router import apply as ra
            raise ra.RouterError("the router is not reachable: down")

    def req(self, method, path, body=None, bearer=None, timeout=30):
        if path == "/api/providers":
            return 200, {"connections": [{"provider": "groq", "id": "c1"}, {"provider": "openrouter", "id": "c2"}]}, None
        if path == "/api/combos":
            return 200, {"combos": [{"name": "fleet-smart", "models": [{"model": "groq/openai/gpt-oss-120b"}, "openrouter/x/y:free"]}]}, None
        if path == "/api/providers/c1/models":
            return 200, {"models": [{"id": "openai/gpt-oss-120b"}, {"id": "whisper-large-v3"}]}, None
        if path == "/api/providers/c2/models":
            return 200, {"models": [{"id": "a/b:free"}, {"id": "c/d"}]}, None
        return 404, None, None


def test_router_show_lists_providers_combos_and_models_without_any_key(fleet_env):
    s = Script()
    assert secretcmd.cmd_router_show(args(offered=True), s.con, router=StubRouter()) == 0
    assert "Connected providers: groq, openrouter" in s.text
    assert "combo fleet-smart (2):" in s.text and "groq/openai/gpt-oss-120b" in s.text and "openrouter/x/y:free" in s.text
    assert "offered by groq (2)" in s.text and "offered by openrouter (1)" in s.text     # OpenRouter shows free models only
    assert "c/d" not in s.text


def test_router_show_without_offered_skips_the_model_lists(fleet_env):
    s = Script()
    assert secretcmd.cmd_router_show(args(offered=False), s.con, router=StubRouter()) == 0
    assert "offered by" not in s.text


def test_router_show_says_when_the_router_is_down(fleet_env):
    s = Script()
    assert secretcmd.cmd_router_show(args(offered=False), s.con, router=StubRouter(fail_login=True)) == 1
    assert "Cannot read the router" in s.text


# ---- Prompt 3: rotating the n8n secrets ----
HASH = "$2a$10$" + "B" * 53


@pytest.fixture
def n8n_secrets(fleet_env, monkeypatch):
    """Every n8n secret exists. The bootstrap call is replaced, so a test can see that it ran."""
    from ops.n8n import bootstrap, prepare
    prepare.ensure_dirs()
    (prepare.run_dir() / ".env").write_text("N8N_IMAGE=img\nN8N_OWNER_EMAIL=o@example.org\n")
    secrets.set_value("N8N_OWNER_PASSWORD", "p" * 30)
    secrets.set_value("N8N_INSTANCE_OWNER_PASSWORD_HASH", HASH)
    secrets.set_value("N8N_API_KEY", "eyJ" + "a" * 40 + ".b.c")
    secrets.set_value("FLEET_EVENT_TOKEN", "e" * 40)
    secrets.set_value("N8N_TOOLS_TOKEN", "t" * 40)
    tokens.rotate("FLEET_ACTION_TOKEN")
    calls = []
    monkeypatch.setattr(bootstrap, "bootstrap", lambda: calls.append("bootstrap") or {"api_key": "made", "credentials": {"fleet-event-token": "updated"}})
    monkeypatch.setattr("ops.fleetctl.n8ncmd.in_brief_window", lambda now=None: False)
    return calls


def hash_runner():
    return Runner(rules=[(["--entrypoint"], (0, "%s\nverify-ok" % HASH)), (["is-active"], (0, "active"))])


def test_rotating_the_owner_password_makes_a_new_password_and_hash_and_restarts_n8n(n8n_secrets):
    before = secrets.value_of("N8N_OWNER_PASSWORD")
    r = hash_runner()
    s = Script()
    assert secretcmd.cmd_rotate(args(name="N8N_OWNER_PASSWORD"), s.con, r) == 0
    after = secrets.value_of("N8N_OWNER_PASSWORD")
    assert after != before and len(after) >= 20 and after not in s.text and before not in s.text
    assert r.inputs[[i for i, (c, _u) in enumerate(r.calls) if "--entrypoint" in c][0]] == after        # the new password went to the hasher on standard input
    assert r.ran("restart", "agent-fleet-n8n.service") and n8n_secrets == []                           # no credential sync is needed


def test_rotating_the_hash_name_rotates_the_password_so_the_two_never_differ(n8n_secrets):
    before = secrets.value_of("N8N_OWNER_PASSWORD")
    assert secretcmd.cmd_rotate(args(name="N8N_INSTANCE_OWNER_PASSWORD_HASH"), Script().con, hash_runner()) == 0
    assert secrets.value_of("N8N_OWNER_PASSWORD") != before
    assert secrets.value_of("N8N_INSTANCE_OWNER_PASSWORD_HASH") == HASH and not secrets.value_of("N8N_INSTANCE_OWNER_PASSWORD_HASH").startswith("eyJ")


def test_a_rotated_token_reaches_n8n_through_bootstrap_and_the_unit_that_reads_it_restarts(n8n_secrets):
    before = secrets.value_of("FLEET_EVENT_TOKEN")
    r = hash_runner()
    s = Script()
    assert secretcmd.cmd_rotate(args(name="FLEET_EVENT_TOKEN"), s.con, r) == 0
    assert secrets.value_of("FLEET_EVENT_TOKEN") != before and secrets.value_of("FLEET_EVENT_TOKEN") not in s.text
    assert r.ran("restart", "agent-fleet-events.service") and n8n_secrets == ["bootstrap"]
    assert "n8n now holds the new value" in s.text


def test_the_action_token_keeps_only_its_hash_in_the_control_bot_file(n8n_secrets):
    old_hash = tokens.hashes("FLEET_ACTION_TOKEN")
    assert secretcmd.cmd_rotate(args(name="FLEET_ACTION_TOKEN"), Script().con, hash_runner()) == 0
    new = tokens.hashes("FLEET_ACTION_TOKEN")
    assert new != old_hash and len(new) == 1
    from ops.fleetlib import httpd
    assert httpd.verify_token(secrets.value_of("FLEET_ACTION_TOKEN"), new)
    assert n8n_secrets == ["bootstrap"]


def test_the_tools_token_reaches_the_profiles_and_restarts_the_gateway(n8n_secrets, fleet_env):
    for profile in (fleet_env["hermes"] / "profiles").iterdir():          # on the real machine Hermes made each profile .env
        (profile / ".env").write_text("OTHER=1\n")
    r = hash_runner()
    assert secretcmd.cmd_rotate(args(name="N8N_TOOLS_TOKEN"), Script().con, r) == 0
    token = secrets.value_of("N8N_TOOLS_TOKEN")
    assert token != "t" * 40 and secrets.read_values(fleet_env["hermes"] / "profiles" / "research" / ".env").get("N8N_TOOLS_TOKEN") == token
    assert r.ran("restart", "agent-fleet-gateway.service") and n8n_secrets == ["bootstrap"]


def test_a_rotated_api_key_is_made_again_by_bootstrap(n8n_secrets, monkeypatch):
    from ops.n8n import bootstrap
    seen = {}
    monkeypatch.setattr(bootstrap, "bootstrap", lambda: seen.setdefault("key_was_removed", not secrets.is_set("N8N_API_KEY")) and {"api_key": "made", "credentials": {}})
    s = Script()
    assert secretcmd.cmd_rotate(args(name="N8N_API_KEY"), s.con, hash_runner()) == 0
    assert seen["key_was_removed"] is True and "Old key removed" in s.text


def test_a_failed_n8n_sync_is_reported_with_the_fix_and_a_failure_exit_code(n8n_secrets, monkeypatch):
    from ops.n8n import bootstrap, client as nclient

    def boom():
        raise nclient.N8nError("n8n is not reachable: down")
    monkeypatch.setattr(bootstrap, "bootstrap", boom)
    s = Script()
    assert secretcmd.cmd_rotate(args(name="FLEET_ACTION_TOKEN"), s.con, hash_runner()) == 1
    assert "fleetctl n8n bootstrap" in s.text


def test_a_rotation_that_restarts_n8n_or_the_gateway_is_refused_in_the_brief_window(n8n_secrets, monkeypatch):
    monkeypatch.setattr("ops.fleetctl.n8ncmd.in_brief_window", lambda now=None: True)
    before = secrets.value_of("N8N_TOOLS_TOKEN")
    r = hash_runner()
    assert secretcmd.cmd_rotate(args(name="N8N_TOOLS_TOKEN"), Script().con, r) == 2
    assert secrets.value_of("N8N_TOOLS_TOKEN") == before and not r.ran("restart")
    assert secretcmd.cmd_rotate(args(name="N8N_OWNER_PASSWORD"), Script().con, r) == 2
    # The control bot holds the hash of the action token in a bind-mounted file. It must restart to see a new hash. See D-053.
    action = secrets.value_of("FLEET_ACTION_TOKEN")
    assert secretcmd.cmd_rotate(args(name="FLEET_ACTION_TOKEN"), Script().con, r) == 2
    assert secrets.value_of("FLEET_ACTION_TOKEN") == action and not r.ran("restart")


def test_rotating_the_action_token_restarts_the_control_bot_and_updates_n8n(n8n_secrets, monkeypatch):
    from ops.n8n import bootstrap
    monkeypatch.setattr("ops.fleetctl.n8ncmd.in_brief_window", lambda now=None: False)
    monkeypatch.setattr(bootstrap, "bootstrap", lambda: {"api_key": "kept", "credentials": {"fleet-action-token": "updated"}})
    r = hash_runner()
    before = secrets.value_of("FLEET_ACTION_TOKEN")
    s = Script()
    assert secretcmd.cmd_rotate(args(name="FLEET_ACTION_TOKEN"), s.con, r) == 0
    after = secrets.value_of("FLEET_ACTION_TOKEN")
    assert after and after != before and tokens.matches("FLEET_ACTION_TOKEN")
    assert r.ran("restart", "agent-fleet-control.service") and after not in s.text


def test_the_encryption_key_is_still_never_rotated(n8n_secrets):
    secrets.set_value("N8N_ENCRYPTION_KEY", "k" * 44)
    s = Script()
    assert secretcmd.cmd_rotate(args(name="N8N_ENCRYPTION_KEY"), s.con, hash_runner()) == 2
    assert secrets.value_of("N8N_ENCRYPTION_KEY") == "k" * 44 and "Do not rotate" in s.text


# ------------------------------------------------------------------ the chat probe of LLM7, Aion, and Gonka (D-069)
def test_chat_probe_sends_one_token_call_and_never_prints_the_key(fleet_env):
    key = "llm7-" + "Z9y8X7w6V5" * 3
    secrets.set_value("LLM7_API_KEY", key)
    http = FakeHttp({"https://api.llm7.io/v1/chat/completions": (200, {"choices": []})})
    state, detail = checks.check_secret("LLM7_API_KEY", config.load(), http=http)
    assert state == "ok" and key not in detail
    call = http.calls[0]
    assert call["method"] == "POST" and call["json"]["max_tokens"] == 1 and call["headers"]["Authorization"] == "Bearer " + key


def test_chat_probe_tells_a_402_from_a_refused_key(fleet_env):
    secrets.set_value("AION_API_KEY", "aion-" + "Q1w2E3r4T5" * 3)
    state, detail = checks.check_secret("AION_API_KEY", config.load(), http=FakeHttp({"https://api.aionlabs.ai": (402, {})}))
    assert state == "failed" and "no free credit" in detail and "Do not add money" in detail
    state, _ = checks.check_secret("AION_API_KEY", config.load(), http=FakeHttp({"https://api.aionlabs.ai": (401, {})}))
    assert state == "failed"


def test_gonka_probe_tells_no_free_credit_from_a_refused_key(fleet_env):
    secrets.set_value("GONKA_API_KEY", "gonka-" + "Q1w2E3r4T5" * 3)
    state, detail = checks.check_secret("GONKA_API_KEY", config.load(), http=FakeHttp({"https://proxy.gonkabroker.com": (402, {})}))
    assert state == "failed" and "no free credit" in detail and "Do not add money" in detail
    assert checks.check_secret("GONKA_API_KEY", config.load(), http=FakeHttp({"https://proxy.gonkabroker.com": (200, {})}))[0] == "ok"
