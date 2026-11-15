"""The weekly portfolio log. See D-048."""
import json
import time

import pytest

from ops.briefing import collect, weekly
from ops.briefing import main as briefing
from ops.fleetlib import config, housestyle, httpc, paths

FACTS = {"week_ending": "2026-11-15", "merged": 4, "rolled_back": 1, "deploys_dry_run": 3, "deploys_live": 0, "posts_dry_run": 5, "posts_sent": 0,
         "projects": {"project-001": {"stage": "validate"}}, "tickets_open": 2, "paused": False}


def facts(**kw):
    return dict(FACTS, **kw)


def model(entry):
    return lambda prompt: json.dumps({"entry": entry})


GOOD = "The fleet merged four changes and rolled one back. It held five posts in dry-run and deployed nothing. Project-001 reached the validate stage."


def test_a_good_model_entry_is_used():
    text, source = weekly.entry_text(facts(), ask_model=model(GOOD))
    assert source == "model" and text == GOOD


def test_dashes_and_semicolons_in_the_model_entry_are_fixed():
    text, source = weekly.entry_text(facts(), ask_model=model("The fleet merged four changes — one rolled back; nothing deployed to the public."))
    assert source == "model" and text == "The fleet merged four changes, one rolled back. Nothing deployed to the public."


@pytest.mark.parametrize("ask", [
    model("A seamless week of pivotal merges across the whole fleet this week."),
    model("word " * 130),
    model("short"),
    lambda p: "not json",
    lambda p: None,
    lambda p: json.dumps({"entry": 5}),
    lambda p: json.dumps({"entry": GOOD, "extra": 1}),
])
def test_a_bad_or_banned_model_entry_means_plain_code_writes_it(ask):
    text, source = weekly.entry_text(facts(), ask_model=ask)
    assert source == "plain" and text == weekly.plain_entry(facts())


def test_a_model_that_raises_means_plain_code():
    def boom(prompt):
        raise RuntimeError("router down")
    assert weekly.entry_text(facts(), ask_model=boom)[1] == "plain" and weekly.entry_text(facts())[1] == "plain"


@pytest.mark.parametrize("f", [facts(), facts(projects={}), facts(paused=True), facts(deploys_live=2, posts_sent=3), facts(merged=0, rolled_back=0, tickets_open=0)])
def test_the_plain_entry_follows_the_house_style_and_the_word_cap(f):
    text = weekly.plain_entry(f)
    assert housestyle.check(text) == [] and len(text.split()) <= weekly.MAX_WORDS


def test_the_plain_entry_says_what_happened_without_inventing_anything():
    text = weekly.plain_entry(facts())
    assert "merged 4 changes and rolled back 1" in text and "3 deploys in dry-run" in text and "project-001 at validate" in text and "Open tickets: 2." in text


def test_the_model_prompt_holds_the_facts_and_the_house_rules_and_no_personal_data():
    prompt = weekly.model_prompt(facts())
    assert '"merged": 4' in prompt and "No dashes, no semicolons" in prompt and "seamless" in prompt and "JSON only" in prompt


def test_append_writes_a_header_once_and_the_same_week_only_once(tmp_path):
    path = tmp_path / "portfolio" / "PORTFOLIO_LOG.md"
    assert weekly.append("2026-11-15", "First entry.", path) is True
    assert weekly.append("2026-11-15", "Changed.", path) is False
    assert weekly.append("2026-11-22", "Second entry.", path) is True
    text = path.read_text()
    assert text.startswith("# Portfolio log") and text.count("## Week ending") == 2 and "Changed." not in text
    assert text.index("2026-11-15") < text.index("2026-11-22") and oct(path.stat().st_mode & 0o777) == "0o644"


def test_week_facts_counts_only_the_last_seven_days(fleet_env, monkeypatch):
    now = 1_800_000_000
    ops = paths.ops_state_dir()
    for sub, name, rows in (("merger", "merges.log", [("merged", 1), ("merged", 6), ("merged", 9), ("rolled_back", 2)]),
                            ("deployer", "deploys.log", [("dry_run", 1), ("dry_run", 3), ("deployed", 20)]),
                            ("publisher", "posts.log", [("dry_run", 2), ("sent", 8), ("blocked", 1)])):
        (ops / sub).mkdir(parents=True, exist_ok=True)
        (ops / sub / name).write_text("\n".join(json.dumps({"ts": now - d * 86400 + 60, "status": s}) for s, d in rows) + "\n")
    monkeypatch.setattr(collect, "collect", lambda cfg, n=None: {"date": "2026-11-15", "projects": {"p1": {"stage": "build"}}, "tickets_open": 3, "paused": True})
    f = weekly.week_facts(config.defaults(), now=now)
    assert (f["merged"], f["rolled_back"], f["deploys_dry_run"], f["deploys_live"], f["posts_dry_run"], f["posts_sent"]) == (2, 1, 2, 0, 1, 0)
    assert f["projects"] == {"p1": {"stage": "build"}} and f["tickets_open"] == 3 and f["paused"] is True and f["week_ending"] == "2026-11-15"


def test_main_writes_the_log_posts_to_slack_only_and_sends_one_event_and_a_second_run_does_nothing(fleet_env, monkeypatch, capsys):
    posted, emitted = [], []
    monkeypatch.setattr(weekly, "week_facts", lambda cfg, now=None: facts())
    monkeypatch.setattr(briefing, "ask_router", lambda cfg, prompt: None)
    monkeypatch.setattr(briefing, "send_chat", lambda cfg, text, slack_channel="brief", chat=True: posted.append((text, slack_channel, chat)) or "sent to 0, slack sink")
    monkeypatch.setattr(weekly.events, "emit", lambda *a, **k: emitted.append((a, k)))
    assert weekly.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source"] == "plain" and out["wrote"] is True and out["slack"].startswith("sent to")
    assert len(posted) == 1 and posted[0][1:] == ("portfolio", False) and "week ending 2026-11-15" in posted[0][0]
    assert emitted[0][0][0] == "portfolio.weekly" and "2026-11-15" in weekly.log_file().read_text()
    assert weekly.main([]) == 0
    assert json.loads(capsys.readouterr().out)["wrote"] is False and len(posted) == 1 and len(emitted) == 1


def test_send_chat_keeps_the_text_out_of_the_owner_chats_when_chat_is_false(fleet_env):
    from ops.fleetlib import secrets, tokens
    tokens.rotate("CONTROL_NOTIFY_TOKEN")
    seen = []

    def transport(method, url, headers, body, timeout, max_bytes):
        seen.append(json.loads(body))
        return httpc.Response(200, {}, b'{"sent_to": 0, "slack": "sink"}')
    httpc.set_transport(transport)
    try:
        assert briefing.send_chat(config.defaults(), "week", slack_channel="portfolio", chat=False) == "sent to 0, slack sink"
        briefing.send_chat(config.defaults(), "brief")
    finally:
        httpc.reset_transport()
    assert seen[0] == {"text": "week", "slack_channel": "portfolio", "chat": False}
    assert seen[1] == {"text": "brief", "slack_channel": "brief"}
