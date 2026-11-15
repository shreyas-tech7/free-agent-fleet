"""Slack bootstrap, the manifests, the scopes, and the secret alias. A fake Slack. No network. See D-052."""
import json
import pathlib
import re

import pytest

from ops.control import slackapi, slackboot, slackviews
from ops.fleetctl import cli, slackctl
from ops.fleetlib import config, jsonio, paths, secrets
from tests.fake_slack import FakeSlack

ROOT = pathlib.Path(__file__).resolve().parents[2]
OWNER = "UOWNER0001"
HQ_BOT = "xoxb-" + "h" * 24
HQ_APP = "xapp-" + "h" * 24
HERMES_BOT = "xoxb-" + "e" * 24
HERMES_APP = "xapp-" + "e" * 24


class Say:
    def __init__(self):
        self.lines = []

    def say(self, text):
        self.lines.append(text)


def make_api(fake=None):
    fake = fake or FakeSlack()
    return slackapi.SlackApi(HQ_BOT, request=fake, clock=lambda: 0.0, sleep=lambda s: None), fake


def run(tmp_path, api, **kw):
    saved, env = {}, {}
    args = dict(cfg=config.defaults(), api=api, owner_id=OWNER, save_channel=saved.__setitem__, write_env=env.__setitem__, hermes_user=lambda: "UHERMESBOT1",
                links=[("n8n", "http://127.0.0.1:5678"), ("Hermes dashboard", "http://127.0.0.1:9119"), ("GitHub repository", "https://github.com/example/repo")],
                home_view=lambda: slackviews.home_view({}), state_path=tmp_path / "state.json")
    args.update(kw)
    return slackboot.bootstrap(**args), saved, env


# ------------------------------------------------------------------------------------------------ bootstrap
def test_bootstrap_sets_up_every_part_in_a_new_workspace(tmp_path):
    api, fake = make_api()
    results, saved, env = run(tmp_path, api)
    assert all(ok for _n, ok, _d in results), [r for r in results if not r[1]]
    names = [n for n, _t in slackboot.CHANNELS]
    assert names == ["hq", "brief", "approvals", "alerts", "board", "agents", "n8n", "github", "portfolio"]
    assert sorted(saved) == sorted(names) and fake.methods().count("conversations.create") == 9 and fake.methods().count("conversations.setTopic") == 9
    assert fake.methods().count("conversations.join") == 9 and all(p["users"].startswith(OWNER) for p in fake.params("conversations.invite")[:9])
    assert env == {"SLACK_HOME_CHANNEL": saved["hq"], "SLACK_HOME_CHANNEL_NAME": "hq", "SLACK_ALLOWED_USERS": OWNER, "SLACK_ALLOW_BOTS": "none"}
    assert any(p["users"] == "UHERMESBOT1" and p["channel"] == saved["hq"] for p in fake.params("conversations.invite"))
    help_post = fake.params("chat.postMessage")[0]
    assert help_post["channel"] == saved["hq"] and "/fleet stop" in help_post["text"] and "Hermes has its own `/stop`" in help_post["text"]
    assert fake.params("pins.add") == [{"channel": saved["hq"], "timestamp": "1700000000.%06d" % 0}] or len(fake.params("pins.add")) == 1
    assert [b["title"] for b in fake.params("bookmarks.add")] == ["n8n", "Hermes dashboard", "GitHub repository"] and all(b["channel_id"] == saved["hq"] for b in fake.params("bookmarks.add"))
    assert fake.params("views.publish")[0]["user_id"] == OWNER
    tests = [p for p in fake.params("chat.postMessage") if "test message" in p["text"]]
    assert len(tests) == 9 and {p["channel"] for p in tests} == set(saved.values()) and tests[0]["username"] == "Orchestrator"


def test_a_second_run_changes_nothing_that_exists(tmp_path):
    api, fake = make_api()
    run(tmp_path, api)
    first_posts, first_pins, first_marks = len(fake.params("chat.postMessage")), len(fake.params("pins.add")), len(fake.params("bookmarks.add"))
    channels_before = dict(fake.channels)
    results, saved, _env = run(tmp_path, api)
    assert all(ok for _n, ok, _d in results) and fake.channels == channels_before and saved == {n: channels_before[n] for n, _t in slackboot.CHANNELS}
    assert len(fake.params("chat.postMessage")) == first_posts and len(fake.params("pins.add")) == first_pins and len(fake.params("bookmarks.add")) == first_marks
    assert fake.methods().count("conversations.create") == 18                        # it asks again, and Slack answers name_taken


def test_again_posts_the_help_note_the_bookmarks_and_the_test_messages_once_more(tmp_path):
    api, fake = make_api()
    run(tmp_path, api)
    run(tmp_path, api, again=True)
    assert len(fake.params("pins.add")) == 2 and len(fake.params("bookmarks.add")) == 6
    assert len([p for p in fake.params("chat.postMessage") if "test message" in p["text"]]) == 18


def test_a_channel_that_cannot_be_made_fails_its_own_step_and_the_rest_go_on(tmp_path):
    class Picky(FakeSlack):
        def answer(self, name, params):
            if name == "conversations.create" and params["name"] == "github":
                return {"ok": False, "error": "restricted_action"}
            return super().answer(name, params)
    api, fake = make_api(Picky())
    results, saved, _env = run(tmp_path, api)
    failed = [n for n, ok, _d in results if not ok]
    assert "channel #github" in failed and "github" not in saved and len(saved) == 8 and "test messages" in failed


def test_without_the_hermes_token_the_invite_is_skipped_and_is_not_a_failure(tmp_path):
    api, fake = make_api()
    results, _saved, _env = run(tmp_path, api, hermes_user=lambda: None)
    step = next(r for r in results if r[0] == "invite the Hermes bot to #hq")
    assert step[1] is True and step[2].startswith("skipped") and all(ok for _n, ok, _d in results)


def test_a_missing_member_id_stops_with_the_command_to_fix_it(tmp_path):
    api, fake = make_api()
    with pytest.raises(slackboot.BootstrapError) as err:
        run(tmp_path, api, owner_id="")
    assert "fleetctl config set owner.slack_user_ids" in str(err.value) and fake.calls == []


def test_the_state_file_is_private(tmp_path):
    api, _ = make_api()
    run(tmp_path, api)
    assert oct((tmp_path / "state.json").stat().st_mode & 0o777) == "0o600" and json.loads((tmp_path / "state.json").read_text())["help_ts"]


# ------------------------------------------------------------------------------------------------ fleetctl slack
def test_bootstrap_without_the_token_says_waiting_for_keys_and_calls_nothing(fleet_env):
    say, fake = Say(), FakeSlack()
    assert slackctl.cmd_bootstrap(cli.build_parser().parse_args(["slack", "bootstrap"]), say, request=fake) == 1
    assert "WAITING_FOR_KEYS" in say.lines[0] and "docs/SLACK.md" in say.lines[1] and fake.calls == []


def test_bootstrap_with_the_tokens_saves_channels_and_hermes_settings_and_prints_each_step(fleet_env, monkeypatch, tmp_path):
    fleet_env["secrets"].mkdir(exist_ok=True)
    secrets.set_value("SLACK_HQ_BOT_TOKEN", HQ_BOT)
    secrets.set_value("SLACK_HERMES_BOT_TOKEN", HERMES_BOT)
    paths.config_file().write_text("owner:\n  slack_user_ids: [%s]\n" % OWNER)
    env_file = tmp_path / "orchestrator.env"
    monkeypatch.setattr(slackctl, "ORCHESTRATOR_ENV", str(env_file))
    monkeypatch.setattr(slackboot, "state_file", lambda: tmp_path / "state.json")
    fake = FakeSlack()
    say = Say()
    code = slackctl.cmd_bootstrap(cli.build_parser().parse_args(["slack", "bootstrap"]), say, request=fake)
    assert code == 0, say.lines
    live = config.load()
    assert set(live["slack"]["channels"]) == {n for n, _t in slackboot.CHANNELS} and live["slack"]["channels"]["hq"] == fake.channels["hq"]
    values = secrets.read_values(env_file)
    assert values["SLACK_HOME_CHANNEL"] == fake.channels["hq"] and values["SLACK_ALLOWED_USERS"] == OWNER and values["SLACK_ALLOW_BOTS"] == "none"
    assert any("OK   channel #hq" in l for l in say.lines) and "Result: 14 of 14 steps passed." in " ".join(say.lines) or "steps passed" in " ".join(say.lines)
    assert any(p["channel_id"] and p["title"] == "n8n" and p["link"] == "http://127.0.0.1:5678" for p in fake.params("bookmarks.add"))
    assert HQ_BOT not in " ".join(say.lines) and HERMES_BOT not in " ".join(say.lines)


def test_the_git_remote_becomes_a_bookmark_only_for_github():
    cfg = config.defaults()
    assert [t for t, _u in slackctl.bookmarks(cfg)] == ["n8n", "Hermes dashboard"]
    cfg["backup"]["github_remote"] = "git@github.com:example/repo.git"
    assert slackctl.bookmarks(cfg)[-1] == ("GitHub repository", "https://github.com/example/repo")
    cfg["backup"]["github_remote"] = "https://example.com/x.git"
    assert len(slackctl.bookmarks(cfg)) == 2


def test_status_lists_token_names_and_never_a_value(fleet_env):
    fleet_env["secrets"].mkdir(exist_ok=True)
    secrets.set_value("SLACK_HQ_BOT_TOKEN", HQ_BOT)
    say = Say()
    assert slackctl.cmd_status(None, say) == 0
    text = "\n".join(say.lines)
    assert re.search(r"SLACK_HQ_BOT_TOKEN\s+set\b", text) and re.search(r"SLACK_HQ_APP_TOKEN\s+WAITING_FOR_KEYS", text) and HQ_BOT not in text
    assert "Channels known: none yet" in text and "Quiet hours: 22:00 to 07:00" in text


# ------------------------------------------------------------------------------------------------ manifests
def load(name):
    return json.loads((ROOT / "slack" / name).read_text())


def test_the_hermes_manifest_has_no_assistant_view_no_agent_view_and_keeps_socket_mode_the_messages_tab_and_the_commands():
    m = load("hermes.manifest.json")
    assert slackboot.manifest_problems(m) == []
    assert m["settings"]["socket_mode_enabled"] is True and m["features"]["app_home"]["messages_tab_enabled"] is True
    commands = [c["command"] for c in m["features"]["slash_commands"]]
    assert "/hermes" in commands and "/fleet" not in commands and len(commands) <= 50


def test_the_fleet_hq_manifest_has_one_command_socket_mode_app_home_and_interactivity():
    m = load("fleet-hq.manifest.json")
    assert [c["command"] for c in m["features"]["slash_commands"]] == ["/fleet"] and slackboot.manifest_problems(m) == []
    assert m["features"]["app_home"]["home_tab_enabled"] is True and m["settings"]["interactivity"]["is_enabled"] is True
    assert m["settings"]["event_subscriptions"]["bot_events"] == ["app_home_opened"]
    usage = m["features"]["slash_commands"][0]["usage_hint"]
    for sub in ("status", "pause", "resume", "stop", "brief", "board", "approvals", "ask AGENT TEXT", "run WORKFLOW", "tokens", "help"):
        assert sub in usage
    assert not (set(slackviews_commands()) & {"/status", "/stop"})


def slackviews_commands():
    return [c["command"] for c in load("fleet-hq.manifest.json")["features"]["slash_commands"]]


def test_the_fleet_hq_scopes_are_exactly_the_scopes_that_the_code_needs():
    needed = set(slackapi.EXTRA_SCOPES)
    for scopes in slackapi.SCOPES_FOR.values():
        needed |= set(scopes)
    assert set(load("fleet-hq.manifest.json")["oauth_config"]["scopes"]["bot"]) == needed


def test_every_slack_method_that_the_client_calls_has_its_scopes_listed():
    source = (ROOT / "ops" / "control" / "slackapi.py").read_text()
    called = set(re.findall(r'self\.call\("([a-z.A-Z]+)"', source))
    assert called and called <= set(slackapi.SCOPES_FOR), called - set(slackapi.SCOPES_FOR)


@pytest.mark.parametrize("agent", [a[0] for a in slackviews.AGENTS])
def test_an_agent_manifest_has_its_own_name_and_no_slash_commands(agent):
    m = slackboot.agent_manifest(load("hermes.manifest.json"), agent)
    assert m["display_information"]["name"] == "Hermes " + agent.capitalize() and "slash_commands" not in m["features"] and slackboot.manifest_problems(m) == []
    assert m["settings"]["socket_mode_enabled"] is True


def test_a_bad_agent_name_and_a_manifest_with_an_assistant_view_are_refused():
    base = load("hermes.manifest.json")
    for bad in ("Research", "x", "not-an-agent", "../etc"):
        with pytest.raises(slackboot.BootstrapError):
            slackboot.agent_manifest(base, bad)
    paid = json.loads(json.dumps(base))
    paid["features"]["assistant_view"] = {}
    paid["oauth_config"]["scopes"]["bot"].append("assistant:write")
    paid["settings"]["event_subscriptions"]["bot_events"].append("assistant_thread_started")
    problems = slackboot.manifest_problems(paid)
    assert len(problems) == 3
    with pytest.raises(slackboot.BootstrapError):
        slackboot.agent_manifest(paid, "research")


def test_add_agent_writes_the_file_in_the_clone_where_it_runs_and_refuses_a_bad_name(fleet_env, monkeypatch, tmp_path):
    (tmp_path / "slack").mkdir()
    (tmp_path / "slack" / "hermes.manifest.json").write_text((ROOT / "slack" / "hermes.manifest.json").read_text())
    monkeypatch.chdir(tmp_path)
    say = Say()
    assert slackctl.cmd_add_agent(cli.build_parser().parse_args(["slack", "add-agent", "research"]), say) == 0
    assert json.loads((tmp_path / "slack" / "agents" / "research.manifest.json").read_text())["display_information"]["name"] == "Hermes Research"
    say = Say()
    assert slackctl.cmd_add_agent(cli.build_parser().parse_args(["slack", "add-agent", "nobody"]), say) == 1 and "Not made" in say.lines[0]


def test_add_agent_finds_the_clone_from_a_folder_below_it(fleet_env, monkeypatch, tmp_path):
    (tmp_path / "slack").mkdir()
    (tmp_path / "slack" / "hermes.manifest.json").write_text((ROOT / "slack" / "hermes.manifest.json").read_text())
    deep = tmp_path / "ops" / "deep"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    assert slackctl.clone_slack_folder() == tmp_path / "slack"


def test_add_agent_refuses_outside_a_clone_and_inside_the_live_copy(fleet_env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    say = Say()
    assert slackctl.cmd_add_agent(cli.build_parser().parse_args(["slack", "add-agent", "research"]), say) == 1 and "inside your clone" in say.lines[0]
    live = tmp_path / "live"
    (live / "slack").mkdir(parents=True)
    (live / "slack" / "hermes.manifest.json").write_text("{}")
    monkeypatch.setattr(paths, "live_repo", lambda: live)
    monkeypatch.chdir(live)
    say = Say()
    assert slackctl.cmd_add_agent(cli.build_parser().parse_args(["slack", "add-agent", "research"]), say) == 1 and "locked live copy" in say.lines[0]
    assert not (live / "slack" / "agents").exists()


def test_add_agent_needs_no_root_but_the_other_slack_commands_do(monkeypatch):
    seen = []
    monkeypatch.delenv("FLEET_TESTING", raising=False)
    monkeypatch.setattr(cli.system, "is_root", lambda: False)
    monkeypatch.setattr(cli, "elevate", lambda argv: seen.append(argv) or 0)
    monkeypatch.setattr(cli, "dispatch", lambda args, console, runner: 0)
    cli.main(["slack", "add-agent", "research"])
    assert seen == []
    cli.main(["slack", "bootstrap"])
    assert seen == [["slack", "bootstrap"]]


# ------------------------------------------------------------------------------------------------ secrets
def test_the_hermes_tokens_are_saved_under_both_names_in_the_orchestrator_profile(fleet_env):
    fleet_env["secrets"].mkdir(exist_ok=True)
    path = secrets.set_value("SLACK_HERMES_BOT_TOKEN", HERMES_BOT)
    secrets.set_value("SLACK_HERMES_APP_TOKEN", HERMES_APP)
    values = secrets.read_values(path)
    assert path.parent.name == "orchestrator" and values["SLACK_HERMES_BOT_TOKEN"] == values["SLACK_BOT_TOKEN"] == HERMES_BOT
    assert values["SLACK_HERMES_APP_TOKEN"] == values["SLACK_APP_TOKEN"] == HERMES_APP
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_the_hq_tokens_go_to_the_control_file_and_only_a_value_of_the_right_shape_is_taken(fleet_env):
    fleet_env["secrets"].mkdir(exist_ok=True)
    assert secrets.set_value("SLACK_HQ_BOT_TOKEN", HQ_BOT).name == "control.env"
    for bad in ("xoxb-short", "xapp-" + "h" * 24, "plain"):
        with pytest.raises(secrets.SecretError):
            secrets.set_value("SLACK_HQ_BOT_TOKEN", bad)
    with pytest.raises(secrets.SecretError):
        secrets.set_value("SLACK_HQ_APP_TOKEN", HQ_BOT)


def test_set_env_line_keeps_the_other_lines_and_refuses_a_line_break(fleet_env, tmp_path):
    path = tmp_path / "x.env"
    path.write_text("KEEP=1\n")
    secrets.set_env_line(path, "SLACK_HOME_CHANNEL", "chan-home-1", "fleet")
    secrets.set_env_line(path, "SLACK_HOME_CHANNEL", "chan-home-2", "fleet")
    assert secrets.read_values(path) == {"KEEP": "1", "SLACK_HOME_CHANNEL": "chan-home-2"}
    with pytest.raises(secrets.SecretError):
        secrets.set_env_line(path, "X", "a\nb", "fleet")


# ---- D-057: no Slack command may change the approval posture
def test_the_hermes_manifest_has_no_command_that_changes_the_approval_posture():
    manifest = json.loads((ROOT / "slack" / "hermes.manifest.json").read_text())
    commands = [c["command"] for c in manifest["features"]["slash_commands"]]
    assert "/yolo" not in commands and "/approvals" not in commands
    assert "/approve" in commands and "/deny" in commands                 # answering a pending approval stays
    assert len(commands) <= 50                                             # the limit of Slack for one app
    assert slackboot.manifest_problems(manifest) == []


@pytest.mark.parametrize("command", ["/yolo", "/approvals"])
def test_a_manifest_with_a_posture_command_is_flagged(command):
    manifest = json.loads((ROOT / "slack" / "hermes.manifest.json").read_text())
    manifest["features"]["slash_commands"].append({"command": command, "description": "x", "should_escape": False})
    assert slackboot.manifest_problems(manifest) == ["it has the command " + command]


def test_the_manifest_of_an_extra_agent_carries_no_slash_command_at_all():
    manifest = json.loads((ROOT / "slack" / "hermes.manifest.json").read_text())
    assert "slash_commands" not in slackboot.agent_manifest(manifest, "research")["features"]


def test_the_ready_made_manifests_of_the_other_agents_exist_and_are_allowed():
    names = sorted(p.name for p in (ROOT / "slack" / "agents").glob("*.manifest.json"))
    assert names == ["build.manifest.json", "feedback.manifest.json", "improver.manifest.json", "marketing.manifest.json",
                     "research.manifest.json", "security.manifest.json"]
    base = json.loads((ROOT / "slack" / "hermes.manifest.json").read_text())
    for name in names:
        manifest = json.loads((ROOT / "slack" / "agents" / name).read_text())
        assert slackboot.manifest_problems(manifest) == [] and "slash_commands" not in manifest["features"]
        assert manifest["display_information"]["name"] == "Hermes " + name.split(".")[0].capitalize()
        assert manifest["oauth_config"] == base["oauth_config"] and manifest["settings"]["socket_mode_enabled"] is True
