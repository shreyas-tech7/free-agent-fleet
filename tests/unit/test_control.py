import json

import pytest

from ops.control import channels, commands, main
from ops.fleetlib import config, httpc


def cfg(tg=(111,), dc=(222,)):
    c = config.defaults()
    c["owner"]["telegram_user_ids"] = list(tg)
    c["owner"]["discord_user_ids"] = list(dc)
    return c


class Runner:
    def __init__(self, rc=0, out="active"):
        self.calls, self.rc, self.out = [], rc, out

    def __call__(self, argv, timeout=120):
        self.calls.append(argv)
        return self.rc, self.out


def test_only_the_owner_is_answered(fleet_env):
    ctl = commands.Controller(cfg(), run=Runner())
    assert ctl.handle("telegram", "999", "/status") is None
    assert ctl.handle("telegram", "999", "/stop") is None
    assert ctl.handle("discord", "111", "/status") is None            # a Telegram ID is not a Discord ID
    assert ctl.handle("telegram", "111", "/status") is not None


def test_each_command_runs_only_its_one_sudo_command(fleet_env):
    expected = {
        "/pause": ["sudo", "-n", "/usr/local/bin/fleetctl", "pause"],
        "/resume": ["sudo", "-n", "/usr/local/bin/fleetctl", "resume"],
        "/stop": ["sudo", "-n", "/usr/bin/systemctl", "stop", "agent-fleet.target"],
        "/start": ["sudo", "-n", "/usr/bin/systemctl", "start", "agent-fleet.target"],
    }
    for text, argv in expected.items():
        run = Runner()
        reply = commands.Controller(cfg(), run=run).handle("telegram", "111", text)
        assert run.calls == [argv] and "done" in reply


def test_a_failed_command_says_so(fleet_env):
    reply = commands.Controller(cfg(), run=Runner(rc=1, out="sudo: a password is required")).handle("telegram", "111", "/stop")
    assert "failed" in reply and "password" in reply


def test_extra_words_cannot_change_the_command(fleet_env):
    run = Runner()
    commands.Controller(cfg(), run=run).handle("telegram", "111", "/stop now; rm -rf /")
    assert run.calls == [["sudo", "-n", "/usr/bin/systemctl", "stop", "agent-fleet.target"]]


def test_status_shows_pause_services_and_budget(fleet_env):
    (fleet_env["state"] / "PAUSED").write_text(json.dumps({"reason": "waiting_for_keys", "since": "2026-01-01T00:00:00Z"}))
    (fleet_env["state"] / "budget.json").write_text(json.dumps({"slowdown": True, "hot": ["groq"]}))
    text = commands.Controller(cfg(), run=Runner(out="active")).status()
    assert "PAUSED: waiting_for_keys" in text and "router active" in text and "slowing down (groq)" in text


def test_brief_commands(fleet_env):
    ctl = commands.Controller(cfg(), run=Runner())
    assert "No brief yet" in ctl.handle("telegram", "111", "/brief")
    (fleet_env["state"] / "briefs").mkdir()
    (fleet_env["state"] / "briefs" / "latest.md").write_text("Achieved: test")
    assert ctl.handle("telegram", "111", "/brief") == "Achieved: test"
    assert "on its way" in ctl.handle("telegram", "111", "/brief now")
    assert (fleet_env["ops"] / "briefing" / "NOW").exists()
    assert "Commands" in ctl.handle("telegram", "111", "hello there")


# ---------------- channels ----------------
class FakeHttp:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        status, body = self.handler(method, url, kw)
        return httpc.Response(status, {}, json.dumps(body).encode())


def test_telegram_accepts_only_private_text_messages_and_moves_the_offset():
    updates = [{"update_id": 5, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111}, "text": "/status"}},
               {"update_id": 6, "message": {"chat": {"id": -9, "type": "group"}, "from": {"id": 111}, "text": "/stop"}},
               {"update_id": 7, "message": {"chat": {"id": 111, "type": "private"}, "from": {"id": 111}}}]
    http = FakeHttp(lambda m, u, kw: (200, {"ok": True, "result": updates}))
    tg = channels.Telegram("123456789:" + "A" * 35, http=http, owner_ids=[111])
    assert tg.poll(wait=1) == [("111", "111", "/status")]
    assert tg.offset == 8


def test_telegram_errors_never_carry_the_token():
    token = "123456789:" + "B" * 35
    tg = channels.Telegram(token, http=FakeHttp(lambda m, u, kw: (401, {"ok": False})), owner_ids=[1])
    with pytest.raises(channels.ChannelError) as err:
        tg.send("1", "hi")
    assert token not in str(err.value)


def test_long_replies_are_split():
    parts = channels.split_text("line\n" * 1000, 400)
    assert all(len(p) <= 400 for p in parts) and "".join(parts).replace("\n", "") == "line" * 1000


def test_discord_ignores_old_messages_bots_and_other_people():
    state = {"msgs": [{"id": "10", "author": {"id": "222", "bot": False}, "content": "/stop"}]}

    def handler(method, url, kw):
        if url.endswith("/users/@me/channels"):
            return 200, {"id": "chan1"}
        if "/messages?" in url:
            return 200, state["msgs"]
        return 200, {}
    dc = channels.Discord("x" * 24 + "." + "y" * 6 + "." + "z" * 27, http=FakeHttp(handler), owner_ids=[222])
    assert dc.poll() == []                         # first look: old messages are not acted on
    state["msgs"] = [{"id": "11", "author": {"id": "222"}, "content": "/status"},
                     {"id": "12", "author": {"id": "333"}, "content": "/status"},
                     {"id": "13", "author": {"id": "444", "bot": True}, "content": "hi"}]
    got = dc.poll()
    assert [g[0] for g in got] == ["222", "333"]    # the Controller drops 333 because it is not an owner
    assert commands.Controller(cfg(), run=Runner()).handle("discord", "333", "/status") is None


def test_channels_without_tokens_or_owner_ids_stay_idle(fleet_env):
    c = config.defaults()
    c["control"]["telegram"]["enabled"] = True
    assert main.build_channels(c) == []


# ---- Prompt 3: /notify can also post to Slack, behind the sink flag ----
def _routes(fleet_env, tmp_path):
    from ops.control import slackout
    from ops.fleetlib import tokens
    tokens.rotate("CONTROL_NOTIFY_TOKEN")
    sink = tmp_path / "sink.jsonl"
    slack = slackout.SlackOut(cfg(), sink_file=sink)
    sent = []

    class Chan:
        name = "telegram"

        def notify_owner(self, text):
            sent.append(text)
    routes = main.build_routes(cfg(), commands.Controller(cfg(), run=Runner()), [Chan()], slack)
    token = __import__("ops.fleetlib.secrets", fromlist=["x"]).value_of("CONTROL_NOTIFY_TOKEN")
    return routes, token, sink, sent


class _Req:
    def __init__(self, body, token):
        self._b, self._t = body, token

    def bearer(self):
        return self._t

    def json(self):
        return self._b


def test_notify_with_a_slack_channel_goes_to_the_owner_and_to_the_sink(fleet_env, tmp_path):
    routes, token, sink, sent = _routes(fleet_env, tmp_path)
    status, out = routes[("POST", "/notify")](_Req({"text": "the brief", "slack_channel": "brief"}, token))
    assert (status, out) == (200, {"sent_to": 1, "slack": "sink"}) and sent == ["the brief"]
    assert json.loads(sink.read_text().splitlines()[0]) == {"agent": "", "channel": "brief", "severity": "info", "text": "the brief", "ts": json.loads(sink.read_text().splitlines()[0])["ts"]}


def test_notify_without_a_slack_channel_works_as_before_and_a_bad_channel_is_refused(fleet_env, tmp_path):
    routes, token, sink, sent = _routes(fleet_env, tmp_path)
    assert routes[("POST", "/notify")](_Req({"text": "no slack"}, token))[1] == {"sent_to": 1}
    assert routes[("POST", "/notify")](_Req({"text": "x", "slack_channel": "general"}, token))[1]["slack"] == "refused"
    assert not sink.exists()


def test_notify_still_needs_its_own_token_and_the_action_token_does_not_open_it(fleet_env, tmp_path):
    from ops.fleetlib import httpd
    routes, token, sink, sent = _routes(fleet_env, tmp_path)
    with pytest.raises(httpd.HttpError) as err:
        routes[("POST", "/notify")](_Req({"text": "x"}, "wrong" * 8))
    assert err.value.status == 401 and sent == []


def test_the_routes_hold_notify_health_and_the_whole_action_api(fleet_env, tmp_path):
    routes, *_ = _routes(fleet_env, tmp_path)
    assert {("POST", "/notify"), ("GET", "/health"), ("GET", "/v1/status"), ("POST", "/v1/actions/card"), ("POST", "/v1/slack/post")} <= set(routes)


def test_notify_with_chat_false_goes_to_slack_only_and_not_to_the_owner_chats(fleet_env, tmp_path):
    """The weekly portfolio log must not ping the owner's phone. See D-048."""
    routes, token, sink, sent = _routes(fleet_env, tmp_path)
    status, out = routes[("POST", "/notify")](_Req({"text": "week log", "slack_channel": "portfolio", "chat": False}, token))
    assert status == 200 and out == {"sent_to": 0, "slack": "sink"} and sent == []
    assert "week log" in sink.read_text()
    status, out = routes[("POST", "/notify")](_Req({"text": "brief", "slack_channel": "brief", "chat": True}, token))
    assert out["sent_to"] == 1 and sent == ["brief"]


def test_the_bot_health_says_that_it_can_hold_owner_messages(fleet_env, tmp_path):
    routes, token, sink, sent = _routes(fleet_env, tmp_path)
    status, out = routes[("GET", "/health")](None)
    assert status == 200 and out["hold"] is True and out["status"] == "OK"


# ------------------------------------------------------------------------------------------------ /approve, /reject, /approvals. See D-039.
def owner_controller(fleet_env):
    from ops.fleetlib import config as cfglib
    cfg = cfglib.defaults()
    cfg["owner"]["telegram_user_ids"] = [111]
    cfg["owner"]["slack_user_ids"] = ["UOWNER0001"]
    return commands.Controller(cfg, run=Runner())


def test_the_owner_lists_approves_and_rejects_with_text_commands(fleet_env):
    from ops.control import approvals
    ctl = owner_controller(fleet_env)
    assert ctl.handle("telegram", 111, "/approvals") == "No approval is open."
    a = approvals.create("spec", "project-001", "Sell an order tracker.")
    b = approvals.create("release", "project-001@abc1234567", "Deploy the site.")
    listed = ctl.handle("telegram", 111, "/approvals")
    assert a["id"] in listed and b["id"] in listed and "spec project-001" in listed
    assert ctl.handle("telegram", 111, "/approve " + a["id"].upper()) == "Approved %s (spec project-001)." % a["id"]
    assert ctl.handle("telegram", 111, "/reject " + b["id"]) == "Rejected %s (release project-001@abc1234567)." % b["id"]
    assert approvals.is_approved("spec", "project-001") and not approvals.is_approved("release", "project-001@abc1234567")
    assert approvals.get(a["id"])["decided_by"] == "telegram:111"


def test_a_decision_needs_an_id_and_a_pending_item_and_a_stranger_gets_no_answer(fleet_env):
    from ops.control import approvals
    ctl = owner_controller(fleet_env)
    assert "Use: /approve ID" in ctl.handle("telegram", 111, "/approve")
    assert ctl.handle("telegram", 111, "/approve zzzzzz").startswith("Not done:")
    a = approvals.create("install", "x@abc", "Install x.")
    assert ctl.handle("telegram", 999, "/approve " + a["id"]) is None
    assert ctl.handle("slack", "USTRANGER1", "/approve " + a["id"]) is None
    assert ctl.handle("slack", "UOWNER0001", "/approve " + a["id"]).startswith("Approved")
    assert ctl.handle("telegram", 111, "/reject " + a["id"]).startswith("Not done: that approval is already approved")
    assert "/approve ID" in ctl.handle("telegram", 111, "/help") and "/approvals" in commands.HELP
