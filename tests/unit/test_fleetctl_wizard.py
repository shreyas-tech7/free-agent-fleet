from types import SimpleNamespace

import pytest

from ops.fleetctl import secretcmd, wizard
from ops.fleetlib import config, secrets
from tests.unit.fctl_helpers import Runner, Script

TOKEN = "123456789:" + "A" * 35
GROQ = "gsk_" + "A1b2C3d4E5" * 3


def ns(**kw):
    return SimpleNamespace(**kw)


def test_the_control_wizard_saves_id_flag_and_token(fleet_env):
    # confirm telegram yes, id, confirm discord no
    s = Script(inputs=["y", "555123456", "n"], hidden=[TOKEN])
    r = Runner(rules=[(["is-active"], (0, "active"))])
    assert wizard.cmd_wizard(ns(group="control", again=False), s.con, r) == 0
    cfg = config.load()
    assert config.get(cfg, "owner.telegram_user_ids") == [555123456]
    assert config.get(cfg, "control.telegram.enabled") is True and config.get(cfg, "control.discord.enabled") is False
    assert secrets.value_of("CONTROL_TELEGRAM_BOT_TOKEN") == TOKEN and TOKEN not in s.text
    assert r.ran("restart", "agent-fleet-control.service")


def test_a_wrong_id_is_asked_again_then_skipped(fleet_env):
    s = Script(inputs=["y", "abc", "xyz", "nope", "n"])
    wizard.cmd_wizard(ns(group="control", again=False), s.con, Runner())
    assert config.get(config.load(), "owner.telegram_user_ids") == []
    assert "Skipped telegram" in s.text and not secrets.is_set("CONTROL_TELEGRAM_BOT_TOKEN")


def test_a_wrong_token_is_refused_up_to_three_times(fleet_env):
    s = Script(inputs=["y", "555123456", "n"], hidden=["bad1", "bad2", "bad3"])
    wizard.cmd_wizard(ns(group="control", again=False), s.con, Runner())
    assert not secrets.is_set("CONTROL_TELEGRAM_BOT_TOKEN") and "Skipped after three tries" in s.text
    assert config.get(config.load(), "control.telegram.enabled") is False
    assert "bad1" not in s.text


def test_an_empty_answer_skips_a_secret(fleet_env):
    s = Script(hidden=[""])
    assert wizard.secret_step(s.con, "GROQ_API_KEY", Runner()) is False and "Skipped." in s.text


def test_a_secret_that_is_set_is_not_asked_again_unless_requested(fleet_env):
    secrets.set_value("GROQ_API_KEY", GROQ)
    s = Script()
    assert wizard.secret_step(s.con, "GROQ_API_KEY", Runner()) is True and "already set" in s.text
    s2 = Script(hidden=["gsk_" + "Z9y8X7w6V5" * 3])
    wizard.secret_step(s2.con, "GROQ_API_KEY", Runner(), again=True)
    assert secrets.value_of("GROQ_API_KEY").startswith("gsk_Z9y8")


def test_the_router_wizard_approves_only_after_the_owner_says_yes(fleet_env, monkeypatch):
    applied = []
    monkeypatch.setattr(secretcmd, "cmd_router_apply", lambda *a, **k: applied.append(1) or 0)
    # skip every key except Groq (first), then approve groq
    s = Script(inputs=["y"], hidden=[GROQ, "", "", "", ""])
    r = Runner()
    wizard.cmd_wizard(ns(group="router", again=False), s.con, r)
    assert config.get(config.load(), "router.approved_providers") == ["groq"] and applied
    assert "groq.com" in s.text and GROQ not in s.text


def test_the_router_wizard_does_not_approve_on_no(fleet_env, monkeypatch):
    monkeypatch.setattr(secretcmd, "cmd_router_apply", lambda *a, **k: 0)
    s = Script(inputs=["n"], hidden=[GROQ, "", "", "", ""])
    wizard.cmd_wizard(ns(group="router", again=False), s.con, Runner())
    assert config.get(config.load(), "router.approved_providers") == []


def test_the_publisher_wizard_enables_a_platform_with_its_settings(fleet_env):
    # bluesky: yes, handle; mastodon..github: no; live: no
    s = Script(inputs=["y", "shop.bsky.social", "n", "n", "n", "n", "n", "n"], hidden=["abcd-efgh-ijkl-mnop"])
    wizard.cmd_wizard(ns(group="publisher", again=False), s.con, Runner())
    cfg = config.load()
    assert config.get(cfg, "publisher.platforms.bluesky.enabled") is True
    assert config.get(cfg, "publisher.platforms.bluesky.handle") == "shop.bsky.social"
    assert config.get(cfg, "publisher.mode") == "dry-run"                    # still dry-run until the owner says so
    assert config.get(cfg, "publisher.platforms.x.enabled") is False


def test_the_publisher_wizard_can_switch_to_live_only_when_asked(fleet_env):
    s = Script(inputs=["n"] * 6 + ["y"])
    wizard.cmd_wizard(ns(group="publisher", again=False), s.con, Runner())
    assert config.get(config.load(), "publisher.mode") == "live"


def test_the_deployer_wizard_asks_before_live(fleet_env):
    s = Script(inputs=["n"], hidden=["c" * 40, "a" * 32])
    wizard.cmd_wizard(ns(group="deployer", again=False), s.con, Runner())
    assert secrets.is_set("CLOUDFLARE_API_TOKEN") and config.get(config.load(), "deployer.mode") == "dry-run"


def test_the_briefing_wizard_sets_mail(fleet_env):
    s = Script(inputs=["y", "me@example.com", "smtp.example.com", "587", "me@example.com"], hidden=["mail-password-123"])
    wizard.cmd_wizard(ns(group="briefing", again=False), s.con, Runner())
    cfg = config.load()
    assert config.get(cfg, "briefing.email.smtp_host") == "smtp.example.com" and config.get(cfg, "briefing.email.enabled") is True
    assert config.get(cfg, "owner.email") == "me@example.com"


def test_the_backup_wizard_wants_an_https_remote(fleet_env):
    s = Script(inputs=["y", "git@github.com:me/x.git", "bad", "also bad"])
    wizard.cmd_wizard(ns(group="backup", again=False), s.con, Runner())
    assert config.get(config.load(), "backup.github_remote") == ""
    s2 = Script(inputs=["y", "https://github.com/me/agent-fleet.git"], hidden=["ghp_" + "a" * 30])
    wizard.cmd_wizard(ns(group="backup", again=False), s2.con, Runner())
    assert config.get(config.load(), "backup.github_remote") == "https://github.com/me/agent-fleet.git" and secrets.is_set("BACKUP_GITHUB_TOKEN")


def test_the_wizard_refuses_unknown_and_internal_groups_and_non_terminals(fleet_env):
    assert wizard.cmd_wizard(ns(group="internal", again=False), Script().con, Runner()) == 2
    assert wizard.cmd_wizard(ns(group="nope", again=False), Script().con, Runner()) == 2
    s = Script(interactive=False)
    assert wizard.cmd_wizard(ns(group="control", again=False), s.con, Runner()) == 2 and "terminal" in s.text


def test_time_zone_check():
    assert wizard.valid_zone("America/Chicago") and wizard.valid_zone("UTC")
    assert not wizard.valid_zone("Mars/Base") and not wizard.valid_zone("Chicago") and not wizard.valid_zone("")


def test_setup_runs_every_step_and_resumes_only_when_ready(fleet_env, monkeypatch):
    from core.killswitch import killswitch as kb
    calls = []
    monkeypatch.setattr(kb, "resume_fleet", lambda **k: calls.append("resume") or {"flag": "removed"})
    monkeypatch.setattr(kb, "ready_to_run", lambda: (False, "no router provider key exists yet"))
    # name, zone, then "do this step" for each of the seven groups: no
    s = Script(inputs=["Ada", "Europe/Berlin"] + ["n"] * 7)
    assert wizard.cmd_setup(ns(), s.con, Runner()) == 0
    assert config.get(config.load(), "owner.name") == "Ada" and config.get(config.load(), "owner.timezone") == "Europe/Berlin"
    assert "stays paused" in s.text and calls == [] and "sudo tailscale up" in s.text
    assert "Step 3. Slack" in s.text and "Step 9. n8n" in s.text and "fleetctl n8n status" in s.text
    monkeypatch.setattr(kb, "ready_to_run", lambda: (True, "a provider key exists"))
    s2 = Script(inputs=["", "", "n", "n", "n", "n", "n", "n", "n", "y"])
    assert wizard.cmd_setup(ns(), s2.con, Runner()) == 0 and calls == ["resume"]


def test_setup_needs_a_terminal(fleet_env):
    s = Script(interactive=False)
    assert wizard.cmd_setup(ns(), s.con, Runner()) == 2 and "terminal" in s.text


# ---- the Slack step. See D-052.
HQ_BOT, HQ_APP = "xoxb-" + "1" * 12 + "-" + "A" * 24, "xapp-1-" + "B" * 30
HERMES_BOT, HERMES_APP = "xoxb-" + "2" * 12 + "-" + "C" * 24, "xapp-1-" + "D" * 30


def test_the_slack_step_is_skipped_when_the_apps_do_not_exist_yet(fleet_env):
    s = Script(inputs=["n"])
    assert wizard.cmd_wizard(ns(group="slack", again=False), s.con, Runner()) == 0
    assert "WAITING_FOR_KEYS" in s.text and not secrets.is_set("SLACK_HQ_BOT_TOKEN")
    assert config.get(config.load(), "owner.slack_user_ids") == []


def test_the_slack_step_refuses_a_wrong_member_id_and_asks_for_none_of_the_tokens(fleet_env):
    s = Script(inputs=["y", "abc", "12345", "u01lower"])
    wizard.cmd_wizard(ns(group="slack", again=False), s.con, Runner())
    assert "Skipped Slack" in s.text and config.get(config.load(), "owner.slack_user_ids") == []
    assert not secrets.is_set("SLACK_HQ_BOT_TOKEN")


def test_the_slack_step_saves_the_id_the_four_tokens_and_makes_the_channels(fleet_env, monkeypatch):
    from ops.fleetctl import slackctl
    made = []
    monkeypatch.setattr(slackctl, "cmd_bootstrap", lambda args, console, runner=None, request=None: made.append(args.again) or 0)
    # confirm apps yes, member id, then confirm channels yes and confirm live messages yes
    s = Script(inputs=["y", "U1AB12CD3EF", "y", "y"], hidden=[HQ_BOT, HQ_APP, HERMES_BOT, HERMES_APP])
    r = Runner(rules=[(["is-active"], (0, "active"))])
    assert wizard.cmd_wizard(ns(group="slack", again=False), s.con, r) == 0
    cfg = config.load()
    assert config.get(cfg, "owner.slack_user_ids") == ["U1AB12CD3EF"]
    assert secrets.value_of("SLACK_HQ_BOT_TOKEN") == HQ_BOT and secrets.value_of("SLACK_HERMES_APP_TOKEN") == HERMES_APP
    assert made == [False] and config.get(cfg, "slack.sink_mode") is False
    for token in (HQ_BOT, HQ_APP, HERMES_BOT, HERMES_APP):
        assert token not in s.text
    assert r.ran("restart", "agent-fleet-slack.service")                 # the adapter reads its tokens at start
    assert "fleetctl pause" in s.text and "agent-fleet-gateway" in s.text    # the note for the Hermes tokens


def test_the_slack_step_keeps_the_sink_when_the_channels_cannot_be_made(fleet_env, monkeypatch):
    from ops.fleetctl import slackctl
    monkeypatch.setattr(slackctl, "cmd_bootstrap", lambda *a, **k: 1)
    s = Script(inputs=["y", "U1AB12CD3EF", "y"], hidden=[HQ_BOT, HQ_APP, "", ""])
    wizard.cmd_wizard(ns(group="slack", again=False), s.con, Runner())
    assert config.get(config.load(), "slack.sink_mode") is True


def test_the_slack_step_stops_when_a_fleet_hq_token_is_missing(fleet_env, monkeypatch):
    from ops.fleetctl import slackctl
    monkeypatch.setattr(slackctl, "cmd_bootstrap", lambda *a, **k: pytest.fail("no bootstrap without both tokens"))
    s = Script(inputs=["y", "U1AB12CD3EF"], hidden=[HQ_BOT, "", "", ""])
    wizard.cmd_wizard(ns(group="slack", again=False), s.con, Runner())
    assert "needs both of its tokens" in s.text


def test_the_member_id_is_added_once_and_a_second_run_keeps_the_first_id(fleet_env, monkeypatch):
    from ops.fleetctl import slackctl
    monkeypatch.setattr(slackctl, "cmd_bootstrap", lambda *a, **k: 0)
    for member in ("U1AB12CD3EF", "U1AB12CD3EF", "W1ZZ99YY88X"):
        s = Script(inputs=["y", member], hidden=[""] * 4)
        wizard.cmd_wizard(ns(group="slack", again=False), s.con, Runner())
    assert config.get(config.load(), "owner.slack_user_ids") == ["U1AB12CD3EF", "W1ZZ99YY88X"]


def test_the_n8n_step_only_explains_and_never_asks_for_a_key(fleet_env):
    s = Script()
    wizard.n8n_step(s.con)
    assert "needs no key" in s.text and "docs/N8N.md" in s.text and "public internet" in s.text
