"""The Slack adapter. A fake Bolt app and a fake Slack. The real Bolt package is not needed. See D-052."""
import sys
import threading
import types

import pytest

from tests.clock import NOON_CHICAGO
from ops.control import approvals, commands, slackapi, slackbot, slackcmd, slackviews
from ops.fleetlib import config, jsonio, paths, secrets
from tests.fake_slack import FakeSlack

OWNER = "UOWNER0001"


class Run:
    def __call__(self, argv, **kw):
        return 0, "active"


def bot_for(fleet_env, fake=None, queue_card=None):
    cfg = config.defaults()
    cfg["owner"]["slack_user_ids"] = [OWNER]
    ctl = commands.Controller(cfg, run=Run(), clock=lambda: NOON_CHICAGO)
    fc = slackcmd.FleetCommands(cfg, ctl, queue_card=queue_card, clock=lambda: NOON_CHICAGO)
    fake = fake or FakeSlack()
    api = slackapi.SlackApi("xoxb-" + "p" * 24, request=fake, clock=lambda: 0.0, sleep=lambda s: None)
    return slackbot.SlackBot(cfg, ctl, fc, api, clock=lambda: NOON_CHICAGO), fake


class Spy:
    def __init__(self):
        self.acked, self.replies = 0, []

    def ack(self, *a, **k):
        self.acked += 1

    def respond(self, **k):
        self.replies.append(k)


def test_a_command_is_acknowledged_first_and_answered_for_the_owner(fleet_env):
    bot, _ = bot_for(fleet_env)
    spy = Spy()
    bot.on_command(spy.ack, {"user_id": OWNER, "text": "status"}, spy.respond)
    assert spy.acked == 1 and spy.replies[0]["response_type"] == "ephemeral" and "Services:" in spy.replies[0]["text"]


def test_a_stranger_is_acknowledged_and_gets_no_answer(fleet_env):
    bot, _ = bot_for(fleet_env)
    spy = Spy()
    bot.on_command(spy.ack, {"user_id": "USTRANGER1", "text": "stop"}, spy.respond)
    bot.on_action(spy.ack, {"user": {"id": "USTRANGER1"}}, {"action_id": "fleet_stop_confirm", "value": "stop"}, spy.respond)
    assert spy.acked == 2 and spy.replies == []


def test_a_button_click_by_the_owner_replaces_the_message(fleet_env):
    bot, _ = bot_for(fleet_env)
    record = approvals.create("spec", "project-001", "Sell the thing.")
    spy = Spy()
    bot.on_action(spy.ack, {"user": {"id": OWNER}}, {"action_id": "fleet_approve", "value": record["id"]}, spy.respond)
    assert spy.acked == 1 and spy.replies[0]["replace_original"] and approvals.is_approved("spec", "project-001")


def test_a_missing_field_does_not_crash_the_handlers(fleet_env):
    bot, _ = bot_for(fleet_env)
    spy = Spy()
    bot.on_command(spy.ack, {}, spy.respond)
    bot.on_action(spy.ack, {}, {}, spy.respond)
    assert spy.acked == 2 and spy.replies == []


def test_app_home_is_published_for_the_owner_with_real_state(fleet_env):
    bot, fake = bot_for(fleet_env)
    approvals.create("release", "p@abc1234567", "Deploy.")
    jsonio.write_json(slackcmd.board_file(), {"boards": {"p1": {"cards": [{"id": "t1", "title": "Write the page", "assignee": "build", "status": "running"},
                                                                       {"id": "t2", "title": "Old", "assignee": "research", "status": "done"}]}}})
    jsonio.write_json(paths.budget_file(), {"providers": {"groq": {"percent": 12.5, "limit": 14400, "requests_today": 1800}}})
    bot.on_home_opened({"user": OWNER})
    view = fake.params("views.publish")[0]
    text = str(view)
    assert view["user_id"] == OWNER and view["view"]["type"] == "home" and "busy on Write the page" in text and "12.5%" in text and "fleet_approve" in text
    assert "research: idle" in text and "build: busy" in text


def test_app_home_is_not_published_for_a_stranger_and_an_api_error_is_logged_not_raised(fleet_env):
    bot, fake = bot_for(fleet_env)
    bot.on_home_opened({"user": "USTRANGER1"})
    assert fake.calls == []
    failing, fake2 = bot_for(fleet_env, fake=FakeSlack(fail={"views.publish": "not_enabled"}))
    failing.on_home_opened({"user": OWNER})
    assert len(fake2.calls) == 1


def test_the_home_state_has_every_agent_and_works_with_no_files(fleet_env):
    bot, _ = bot_for(fleet_env)
    state = bot.home_state()
    assert [a["name"] for a in state["agents"]] == [a[0] for a in slackviews.AGENTS] and all(a["card"] is None for a in state["agents"])
    assert state["providers"] == {} and state["approvals"] == [] and set(state["services"])


class FakeApp:
    def __init__(self):
        self.registered = []

    def command(self, name):
        return lambda fn: self.registered.append(("command", name, fn))

    def action(self, name):
        return lambda fn: self.registered.append(("action", name, fn))

    def event(self, name):
        return lambda fn: self.registered.append(("event", name, fn))


def test_register_puts_the_command_the_four_buttons_and_app_home_on_the_app(fleet_env):
    bot, _ = bot_for(fleet_env)
    app = slackbot.register(FakeApp(), bot)
    kinds = [(k, n) for k, n, _ in app.registered]
    assert kinds == [("command", "/fleet"), ("action", "fleet_approve"), ("action", "fleet_reject"), ("action", "fleet_stop_confirm"), ("action", "fleet_stop_cancel"),
                     ("event", "app_home_opened")]
    assert app.registered[0][2] == bot.on_command and app.registered[-1][2] == bot.on_home_opened


def test_the_bolt_app_is_built_for_socket_mode_with_no_signing_secret_and_no_startup_call(monkeypatch):
    seen = {}

    class App:
        def __init__(self, **kw):
            seen.update(kw)
    monkeypatch.setitem(sys.modules, "slack_bolt", types.SimpleNamespace(App=App))
    slackbot.build_app("xoxb-" + "p" * 24)
    assert seen == {"token": "xoxb-" + "p" * 24, "request_verification_enabled": False, "ssl_check_enabled": False, "token_verification_enabled": False}


def test_the_socket_handler_gets_the_app_and_the_app_token(monkeypatch):
    seen = {}

    class Handler:
        def __init__(self, app, app_token):
            seen.update(app=app, token=app_token)

        def start(self):
            seen["started"] = True
    adapter = types.ModuleType("slack_bolt.adapter.socket_mode")
    adapter.SocketModeHandler = Handler
    monkeypatch.setitem(sys.modules, "slack_bolt.adapter.socket_mode", adapter)
    slackbot.start_handler("APP", "xapp-" + "p" * 24)
    assert seen == {"app": "APP", "token": "xapp-" + "p" * 24, "started": True}


def test_main_idles_without_tokens_and_never_builds_the_app(fleet_env):
    built = []
    stop = threading.Event()
    waits = []

    def wait(seconds):
        waits.append(seconds)
        stop.set()
    slackbot.main(stop=stop, build=lambda t: built.append(t), start=lambda a, t: built.append("started"), sleep=wait)
    assert built == [] and waits == [slackbot.IDLE_SECONDS]


def test_main_with_both_tokens_builds_registers_and_starts_once_per_connection_and_retries_after_a_failure(fleet_env, monkeypatch):
    fleet_env["secrets"].mkdir(exist_ok=True)
    secrets.set_value("SLACK_HQ_BOT_TOKEN", "xoxb-" + "p" * 24)
    secrets.set_value("SLACK_HQ_APP_TOKEN", "xapp-" + "p" * 24)
    monkeypatch.setattr(slackbot.board.BoardMirror, "loop", lambda self, stop, interval=10: None)
    stop = threading.Event()
    calls, waits = [], []

    def start(app, token):
        calls.append(token)
        if len(calls) == 1:
            raise ConnectionError("lost")                           # the first connection fails
        stop.set()

    slackbot.main(stop=stop, build=lambda t: FakeApp(), start=start, sleep=waits.append)
    assert calls == ["xapp-" + "p" * 24, "xapp-" + "p" * 24] and waits == [slackbot.RETRY_SECONDS, slackbot.RETRY_SECONDS]


def test_one_token_is_not_enough(fleet_env):
    fleet_env["secrets"].mkdir(exist_ok=True)
    secrets.set_value("SLACK_HQ_BOT_TOKEN", "xoxb-" + "p" * 24)
    built = []
    stop = threading.Event()
    slackbot.main(stop=stop, build=lambda t: built.append(t), start=lambda a, t: None, sleep=lambda s: stop.set())
    assert built == []
