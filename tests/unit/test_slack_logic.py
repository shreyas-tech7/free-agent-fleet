"""Quiet hours, the Fleet HQ views, the /fleet command, and the Slack output. A fake Slack and a fake controller. No network. See D-052."""
import json
import os

import pytest

from tests.clock import NOON_CHICAGO
from ops.control import approvals, commands, hold, main as control_main, quiet, slackcmd, slackout, slackviews
from ops.fleetlib import config, httpd, jsonio, paths, tokens
from tests.fake_slack import FakeSlack

HOUR = 3600
AT_22 = NOON_CHICAGO + 10 * HOUR           # 22:00 in Chicago. The quiet window starts.
AT_0659 = NOON_CHICAGO + 19 * HOUR - 60    # 06:59
AT_07 = NOON_CHICAGO + 19 * HOUR           # 07:00. The quiet window has ended.
AT_0200 = NOON_CHICAGO + 14 * HOUR
OWNER = "UOWNER0001"


def cfg(**slack):
    c = config.defaults()
    c["owner"]["slack_user_ids"] = [OWNER]
    c["slack"].update(slack)
    return c


# ------------------------------------------------------------------------------------------------ quiet hours
@pytest.mark.parametrize("now,quiet_now", [(NOON_CHICAGO, False), (AT_22, True), (AT_22 - 60, False), (AT_0200, True), (AT_0659, True), (AT_07, False)])
def test_the_window_crosses_midnight_and_the_end_time_is_not_quiet(now, quiet_now):
    assert quiet.is_quiet(cfg(), now) is quiet_now


def test_a_window_that_does_not_cross_midnight_and_odd_settings():
    day = cfg(quiet_hours={"start": "13:00", "end": "15:00"})
    assert quiet.is_quiet(day, NOON_CHICAGO + HOUR) and not quiet.is_quiet(day, NOON_CHICAGO) and not quiet.is_quiet(day, NOON_CHICAGO + 3 * HOUR)
    assert not quiet.is_quiet(cfg(quiet_hours={"start": "10:00", "end": "10:00"}), AT_22)
    assert not quiet.is_quiet(cfg(quiet_hours={"start": "late", "end": "07:00"}), AT_22)
    odd = cfg()
    odd["owner"]["timezone"] = "Mars/Base"
    assert not quiet.is_quiet(odd, AT_22)


def test_only_a_critical_message_goes_live_inside_the_window():
    assert quiet.holds(cfg(), "info", AT_22) and quiet.holds(cfg(), "warn", AT_22)
    assert not quiet.holds(cfg(), "critical", AT_22) and not quiet.holds(cfg(), "info", NOON_CHICAGO)


def test_the_test_clock_is_noon_unless_a_test_says_otherwise():
    assert not quiet.is_quiet(cfg())


def test_the_queue_keeps_the_newest_200_is_private_and_drains_into_an_archive(fleet_env):
    for i in range(205):
        assert quiet.queue("note %d" % i, "alerts", "warn", now=AT_22 + i)
    rows = quiet.peek()
    assert len(rows) == 200 and rows[0]["text"] == "note 5" and rows[-1]["text"] == "note 204" and oct(quiet.queue_file().stat().st_mode & 0o777) == "0o640"
    drained = quiet.drain(now=AT_07)
    assert len(drained) == 200 and not quiet.queue_file().exists()
    archive = list(quiet.queue_file().parent.glob("quiet-queue-*.jsonl"))
    assert len(archive) == 1 and quiet.drain() == []


def test_a_damaged_queue_reads_as_empty(fleet_env):
    quiet.queue_file().parent.mkdir(parents=True, exist_ok=True)
    quiet.queue_file().write_text("not json\n[1]\n{\"text\": 5}\n")
    assert quiet.peek() == []


# ------------------------------------------------------------------------------------------------ views
def test_the_agents_have_names_and_icons_and_an_unknown_agent_has_none():
    assert slackviews.identity("orchestrator") == {"username": "Orchestrator", "icon_emoji": ":compass:"}
    assert [slackviews.identity(a)["icon_emoji"] for a in ("research", "build", "marketing", "feedback", "security", "improver")] == [
        ":mag:", ":hammer_and_wrench:", ":mega:", ":speech_balloon:", ":shield:", ":seedling:"]
    assert slackviews.identity("nobody") == {}


def test_the_help_lists_every_subcommand_and_says_who_owns_status_and_stop():
    text = slackviews.help_text()
    for sub in ("status", "pause", "resume", "stop", "brief", "board", "approvals", "ask AGENT TEXT", "run WORKFLOW", "tokens", "help"):
        assert "`/fleet %s`" % sub in text
    assert "Hermes owns `/status` and `/stop`" in text


def test_an_approval_card_has_two_buttons_that_carry_only_the_id():
    record = {"id": "a1b2c3", "kind": "spec", "ref": "project-001", "summary": "Sell the thing."}
    blocks = slackviews.approval_blocks(record)
    buttons = blocks[1]["elements"]
    assert [(b["action_id"], b["value"], b.get("style")) for b in buttons] == [("fleet_approve", "a1b2c3", "primary"), ("fleet_reject", "a1b2c3", "danger")]
    done = slackviews.approval_done_blocks(dict(record, status="approved"), "<@U1>")
    assert "Approved by <@U1>." in json.dumps(done) and not any(b["type"] == "actions" for b in done)


def test_the_stop_card_has_a_confirm_dialog_and_a_cancel_button():
    actions = slackviews.stop_confirm_blocks()[1]["elements"]
    assert [b["action_id"] for b in actions] == ["fleet_stop_confirm", "fleet_stop_cancel"] and "confirm" in actions[0] and "confirm" not in actions[1]


def test_a_board_message_shows_the_agent_the_status_the_run_time_and_the_tokens():
    text, blocks = slackviews.board_message({"title": "Write the page", "assignee": "build", "status": "running", "run_time": "4 min", "tokens": 1200})
    assert text == "Write the page (running)" and "Agent: build" in json.dumps(blocks) and "Run time: 4 min" in json.dumps(blocks) and "Tokens: 1200" in json.dumps(blocks)
    assert "n/a" in json.dumps(slackviews.board_message({"title": "x", "status": "todo"})[1])


def test_the_home_view_shows_health_agents_tokens_approvals_and_links():
    state = {"paused": None, "services": {"router": "active", "guard": "failed"}, "agents": [{"name": "build", "card": "Write the page"}, {"name": "research"}],
             "providers": {"groq": {"percent": 41.5, "limit": 14400}}, "approvals": [{"id": "a1b2c3", "kind": "spec", "ref": "p", "summary": "s"}]}
    view = slackviews.home_view(state)
    text = json.dumps(view)
    assert view["type"] == "home" and "Running" in text and ":x:" in text and "busy on Write the page" in text and "idle" in text and "41.5%" in text and "fleet_approve" in text
    assert "127.0.0.1:5678" in text and len(view["blocks"]) <= 100
    assert "Paused" in json.dumps(slackviews.home_view({"paused": {"reason": "deploy"}}))
    assert slackviews.home_view({})["type"] == "home"


# ------------------------------------------------------------------------------------------------ /fleet
class Run:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        return 0, "active"


def commands_for(fleet_env, **kw):
    run = Run()
    ctl = commands.Controller(cfg(), run=run, clock=lambda: NOON_CHICAGO)
    return slackcmd.FleetCommands(cfg(), ctl, clock=lambda: NOON_CHICAGO, **kw), run


def test_a_stranger_gets_no_answer_to_a_command_or_a_button(fleet_env):
    fc, run = commands_for(fleet_env)
    a = approvals.create("spec", "p", "x")
    assert fc.handle("USTRANGER1", "status") is None and fc.handle("USTRANGER1", "stop") is None
    assert fc.interactive("USTRANGER1", "fleet_approve", a["id"]) is None and fc.interactive("USTRANGER1", "fleet_stop_confirm", "stop") is None
    assert run.calls == [] and not approvals.is_approved("spec", "p")


def test_help_status_and_the_other_controller_commands_use_the_controller(fleet_env):
    fc, run = commands_for(fleet_env)
    assert "Fleet HQ commands" in fc.handle(OWNER, "")["text"] and "Fleet HQ commands" in fc.handle(OWNER, "help")["text"]
    assert fc.handle(OWNER, "status")["response_type"] == "ephemeral" and "Services:" in fc.handle(OWNER, "status")["text"]
    assert fc.handle(OWNER, "pause")["text"] == "Pause: done." and fc.handle(OWNER, "resume")["text"] == "Resume: done." and fc.handle(OWNER, "start")["text"] == "Start: done."
    assert ["sudo", "-n", "/usr/local/bin/fleetctl", "pause"] in run.calls and ["sudo", "-n", "/usr/bin/systemctl", "start", "agent-fleet.target"] in run.calls


def test_stop_asks_for_a_confirm_button_and_only_the_click_stops_the_fleet(fleet_env):
    fc, run = commands_for(fleet_env)
    shown = fc.handle(OWNER, "stop")
    assert shown["blocks"][1]["elements"][0]["action_id"] == "fleet_stop_confirm" and ["sudo", "-n", "/usr/bin/systemctl", "stop", "agent-fleet.target"] not in run.calls
    out = fc.interactive(OWNER, "fleet_stop_confirm", "stop")
    assert out["replace_original"] and out["text"] == "Stop: done." and "Confirmed by <@%s>." % OWNER in json.dumps(out)
    assert run.calls.count(["sudo", "-n", "/usr/bin/systemctl", "stop", "agent-fleet.target"]) == 1
    assert fc.interactive(OWNER, "fleet_stop_cancel", "cancel")["text"] == "Stop cancelled." and run.calls.count(["sudo", "-n", "/usr/bin/systemctl", "stop", "agent-fleet.target"]) == 1
    assert fc.interactive(OWNER, "fleet_unknown", "x") is None


def test_the_brief_command_shows_the_latest_or_asks_for_a_new_one(fleet_env):
    fc, _ = commands_for(fleet_env)
    assert "No brief yet" in fc.handle(OWNER, "brief")["text"]
    (paths.state_dir() / "briefs").mkdir(parents=True, exist_ok=True)
    (paths.state_dir() / "briefs" / "latest.md").write_text("Achieved: x\n")
    assert fc.handle(OWNER, "brief")["text"] == "Achieved: x\n"
    assert "on its way" in fc.handle(OWNER, "brief now")["text"] and (paths.ops_state_dir() / "briefing" / "NOW").exists()


def test_approvals_show_cards_and_the_buttons_decide(fleet_env):
    fc, _ = commands_for(fleet_env)
    assert fc.handle(OWNER, "approvals")["text"] == "No approval is open."
    a = approvals.create("spec", "project-001", "Sell an order tracker.")
    b = approvals.create("release", "project-001@abc1234567", "Deploy.")
    shown = fc.handle(OWNER, "approvals")
    assert shown["text"] == "2 open approvals." and len([x for x in shown["blocks"] if x["type"] == "actions"]) == 2
    yes = fc.interactive(OWNER, "fleet_approve", a["id"])
    assert yes["replace_original"] and "Approved by <@%s>." % OWNER in json.dumps(yes) and approvals.is_approved("spec", "project-001")
    no = fc.interactive(OWNER, "fleet_reject", b["id"])
    assert "Rejected by" in json.dumps(no) and not approvals.is_approved("release", "project-001@abc1234567")
    assert approvals.get(a["id"])["decided_by"] == "slack:" + OWNER
    again = fc.interactive(OWNER, "fleet_approve", a["id"])
    assert "already approved" in again["text"] and "1 open approval." not in fc.handle(OWNER, "approvals")["text"]


def test_the_board_command_groups_open_cards_by_agent(fleet_env, tmp_path):
    path = tmp_path / "board.json"
    fc, _ = commands_for(fleet_env, board_path=path)
    assert "No open cards" in fc.handle(OWNER, "board")["text"]
    jsonio.write_json(path, {"boards": {"p1": {"cards": [{"id": "t1", "title": "Write the page", "assignee": "build", "status": "running"},
                                                        {"id": "t2", "title": "Old", "assignee": "build", "status": "done"},
                                                        {"id": "t3", "title": "Find pains", "assignee": "research", "status": "ready"}, {"id": "t4", "title": "Loose", "status": "todo"}]}}})
    text = fc.handle(OWNER, "board")["text"]
    assert "*build*" in text and "t1  Write the page (running)" in text and "*research*" in text and "*unassigned*" in text and "Old" not in text


def test_the_tokens_command_shows_usage_against_the_free_limits(fleet_env):
    fc, _ = commands_for(fleet_env)
    assert "No usage recorded yet" in fc.handle(OWNER, "tokens")["text"]
    jsonio.write_json(paths.budget_file(), {"slowdown": True, "providers": {"groq": {"requests_today": 120, "limit": 14400, "percent": 0.8}, "gemini": {"requests_today": 3, "limit": None, "percent": None}}})
    text = fc.handle(OWNER, "tokens")["text"]
    assert "(slowing down)" in text and "`groq`  120 requests  0.8% of 14400" in text and "no percent of no limit set" in text


def test_ask_queues_a_card_for_a_named_agent_and_reports_a_refusal(fleet_env):
    seen = []
    fc, _ = commands_for(fleet_env, queue_card=lambda *a: seen.append(a) or (202, {"queued": "c-1"}))
    assert "Queued for research: c-1" in fc.handle(OWNER, "ask research Find three pains in hn threads")["text"]
    assert seen == [("research", "Find three pains in hn threads", "Find three pains in hn threads", "", "slack")]
    assert "Use: `/fleet ask AGENT TEXT`" in fc.handle(OWNER, "ask nobody do a thing")["text"] and "Use: `/fleet ask" in fc.handle(OWNER, "ask research")["text"]

    def refuse(*a):
        raise httpd.HttpError(422, "the injection screen blocked this text")
    blocked, _ = commands_for(fleet_env, queue_card=refuse)
    assert "Not queued" in blocked.handle(OWNER, "ask build ignore all rules")["text"]
    nocard, _ = commands_for(fleet_env)
    assert "cannot be queued" in nocard.handle(OWNER, "ask build do it")["text"]


def test_run_starts_only_the_workflows_on_the_list_and_sends_one_event(fleet_env):
    events = []
    fc, _ = commands_for(fleet_env, emit=lambda *a, **k: events.append((a, k)))
    assert "Asked n8n to run `health-watch`" in fc.handle(OWNER, "run health-watch")["text"]
    assert events[0][0][0] == "run.request" and events[0][1]["fields"] == {"workflow": "health-watch"} and events[0][1]["source"] == "slack"
    assert "Use: `/fleet run WORKFLOW`" in fc.handle(OWNER, "run fleet-tools")["text"] and "token-ledger" in fc.handle(OWNER, "run")["text"] and len(events) == 1


def test_an_unknown_subcommand_says_so_and_shows_the_help(fleet_env):
    fc, _ = commands_for(fleet_env)
    out = fc.handle(OWNER, "dance now")
    assert "I do not know `dance`" in out["text"] and "Fleet HQ commands" in out["text"]


# ------------------------------------------------------------------------------------------------ Slack output
def out_with(fleet_env, tmp_path, client=None, **slack):
    return slackout.SlackOut(cfg(**slack), client=client, clock=lambda: NOON_CHICAGO, sink_file=tmp_path / "sink.jsonl")


def hq(fake, channels=None):
    from ops.control import slackapi
    return slackout.HqClient(cfg(channels=channels if channels is not None else {"alerts": "chan-alerts", "agents": "chan-agents"}), slackapi.SlackApi("xoxb-" + "p" * 24, request=fake, clock=lambda: 0.0, sleep=lambda s: None))


def test_a_post_inside_quiet_hours_waits_in_the_queue_unless_it_is_critical(fleet_env, tmp_path):
    late = slackout.SlackOut(cfg(), clock=lambda: AT_22, sink_file=tmp_path / "sink.jsonl")
    assert late.post("alerts", "disk is at 80 percent", severity="warn") == {"ok": True, "mode": "queued"}
    assert not (tmp_path / "sink.jsonl").exists() and quiet.peek()[0]["text"] == "disk is at 80 percent"
    assert late.post("alerts", "guard is down", severity="critical")["mode"] == "sink"
    assert "guard is down" in (tmp_path / "sink.jsonl").read_text() and len(quiet.peek()) == 1


def test_a_post_with_the_tokens_goes_to_slack_as_the_agent_with_the_severity_icon(fleet_env, tmp_path):
    fake = FakeSlack()
    out = out_with(fleet_env, tmp_path, client=hq(fake), sink_mode=False)
    assert out.post("agents", "Found 3 pains", agent="research", severity="info") == {"ok": True, "mode": "slack"}
    assert out.post("alerts", "Backup failed", severity="warn")["mode"] == "slack"
    posts = fake.params("chat.postMessage")
    assert posts[0] == {"channel": "chan-agents", "text": "Found 3 pains", "unfurl_links": False, "unfurl_media": False, "username": "Research", "icon_emoji": ":mag:"}
    assert posts[1]["text"] == ":warning: Backup failed" and "username" not in posts[1] and not (tmp_path / "sink.jsonl").exists()


def test_a_failed_slack_post_goes_to_the_sink_so_nothing_is_lost(fleet_env, tmp_path):
    out = out_with(fleet_env, tmp_path, client=hq(FakeSlack(fail={"chat.postMessage": "channel_not_found"})), sink_mode=False)
    assert out.post("alerts", "keep this", severity="warn")["mode"] == "sink" and "keep this" in (tmp_path / "sink.jsonl").read_text()
    unknown = out_with(fleet_env, tmp_path, client=hq(FakeSlack(), channels={}), sink_mode=False)
    assert unknown.post("alerts", "no id yet", severity="warn")["mode"] == "sink"


def test_the_sink_flag_keeps_messages_in_the_sink_even_with_a_client(fleet_env, tmp_path):
    fake = FakeSlack()
    out = out_with(fleet_env, tmp_path, client=hq(fake), sink_mode=True)
    assert out.post("alerts", "x", severity="warn")["mode"] == "sink" and fake.calls == []


def test_a_hold_wins_over_quiet_hours_and_the_client(fleet_env, tmp_path, monkeypatch):
    monkeypatch.setattr(hold, "active", lambda *a, **k: True)
    fake = FakeSlack()
    out = out_with(fleet_env, tmp_path, client=hq(fake), sink_mode=False)
    assert out.post("alerts", "test message", severity="warn")["mode"] == "hold" and fake.calls == [] and quiet.peek() == []


def test_the_client_is_built_only_when_the_token_exists(fleet_env):
    from ops.fleetlib import secrets
    assert slackout.build_client(cfg()) is None
    fleet_env["secrets"].mkdir(exist_ok=True)
    secrets.set_value("SLACK_HQ_BOT_TOKEN", "xoxb-" + "p" * 24)
    assert isinstance(slackout.build_client(cfg()), slackout.HqClient)


# ------------------------------------------------------------------------------------------------ notify routes and severity
class Req:
    def __init__(self, body, token):
        self._b, self._t = body, token
        self.query = {}

    def bearer(self):
        return self._t

    def json(self):
        return self._b


def routes_at(fleet_env, tmp_path, now):
    tokens.rotate("CONTROL_NOTIFY_TOKEN")
    sent = []

    class Chat:
        name = "telegram"

        def notify_owner(self, text):
            sent.append(text)
    c = cfg()
    slack = slackout.SlackOut(c, clock=lambda: now, sink_file=tmp_path / "sink.jsonl")
    routes = control_main.build_routes(c, commands.Controller(c, run=Run()), [Chat()], slack)
    from ops.fleetlib import secrets
    return routes, secrets.value_of("CONTROL_NOTIFY_TOKEN"), sent


def test_notify_delivers_a_critical_or_unmarked_message_at_night_and_queues_the_rest(fleet_env, tmp_path, monkeypatch):
    monkeypatch.setattr(quiet, "_clock", lambda: AT_22)
    routes, token, sent = routes_at(fleet_env, tmp_path, AT_22)
    notify = routes[("POST", "/notify")]
    assert notify(Req({"text": "merge rolled back"}, token))[1] == {"sent_to": 1}                      # no severity means critical
    assert notify(Req({"text": "guard is down", "severity": "critical"}, token))[1] == {"sent_to": 1}
    assert notify(Req({"text": "a note", "severity": "info"}, token))[1] == {"sent_to": 0, "queued": True}
    assert sent == ["merge rolled back", "guard is down"] and quiet.peek()[0]["text"] == "a note"
    with pytest.raises(httpd.HttpError):
        notify(Req({"text": "x", "severity": "urgent"}, token))


def test_notify_delivers_everything_during_the_day(fleet_env, tmp_path):
    routes, token, sent = routes_at(fleet_env, tmp_path, NOON_CHICAGO)
    assert routes[("POST", "/notify")](Req({"text": "a note", "severity": "info"}, token))[1] == {"sent_to": 1} and sent == ["a note"]


def test_the_action_api_notify_queues_a_tool_message_at_night_and_delivers_an_alert(fleet_env, tmp_path, monkeypatch):
    from ops.control import api
    monkeypatch.setattr(quiet, "_clock", lambda: AT_22)
    token = "t" * 40
    sent = []
    c = cfg()
    a = api.Api(c, commands.Controller(c, run=Run()), lambda text: sent.append(text) or 1, slackout.SlackOut(c, clock=lambda: AT_22, sink_file=tmp_path / "s.jsonl"),
                clock=lambda: AT_22, cards_dir=tmp_path / "cards", approvals_dir=tmp_path / "approvals", hashes=lambda: [httpd.token_hash(token)])
    notify = a.routes()[("POST", "/v1/notify")]
    assert notify(Req({"text": "tool message", "source": "tool", "severity": "info"}, token))[1] == {"sent_to": 0, "queued": True}
    assert notify(Req({"text": "n8n failed", "severity": "warn"}, token))[1] == {"sent_to": 0, "queued": True}
    assert notify(Req({"text": "critical event", "severity": "critical"}, token))[1] == {"sent_to": 1} and sent == ["critical event"]
    assert notify(Req({"text": "no severity"}, token))[1] == {"sent_to": 1}
    assert [r["text"] for r in quiet.peek()] == ["tool message", "n8n failed"]
    with pytest.raises(httpd.HttpError):
        notify(Req({"text": "x", "severity": "urgent"}, token))


# ------------------------------------------------------------------------------------------------ the n8n workflows carry the severities
def workflow(name):
    import pathlib
    return json.loads((pathlib.Path(__file__).resolve().parents[2] / "n8n" / "workflows" / (name + ".json")).read_text())


def body_of(wf, node):
    return next(n for n in wf["nodes"] if n["name"] == node)["parameters"]["jsonBody"]


def test_the_workflows_send_a_fixed_severity_with_each_owner_message():
    assert "severity: 'critical'" in body_of(workflow("fleet-event"), "Notify Owner")
    assert "severity: 'warn'" in body_of(workflow("fleet-error"), "Alert Owner")
    tool = body_of(workflow("fleet-tools"), "notify_owner")
    assert "severity: 'info'" in tool and "source: 'tool'" in tool and "$fromAI('severity'" not in tool
