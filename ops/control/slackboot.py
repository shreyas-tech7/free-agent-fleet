"""Set up Slack for the fleet in one run, and make manifests for more agents. See D-052.

`fleetctl slack bootstrap` makes the nine channels, sets each topic, saves each channel ID in the live config, writes the
Hermes Slack settings, pins a help note in #hq, adds bookmarks, invites you and the Hermes bot, publishes App Home, and posts
one test message in each channel. It is safe to run twice. A second run changes nothing that exists. It never posts a second
help note, a second bookmark, or a second test message, unless you ask with --again.
"""
import copy
import json
import pathlib

from ops.control import slackapi, slackviews
from ops.fleetlib import config, jsonio, paths, secrets

CHANNELS = (("hq", "Talk to the fleet. Pinned: how /fleet stop differs from Hermes /stop."),
            ("brief", "The daily brief, at the time that you set in config/owner.yaml."),
            ("approvals", "Approval cards. Approve or reject with the buttons."),
            ("alerts", "Alerts that need a look."),
            ("board", "One message for each Kanban card. Its log is in the thread."),
            ("agents", "Short notes from the agents."),
            ("n8n", "Workflow, backup, and update news from n8n."),
            ("github", "Commits and pull requests."),
            ("portfolio", "What the fleet shipped each week."))
HELP_NOTE = ("*How to talk to the fleet*\n"
             "Use `/fleet` for the fleet. `/fleet status`, `/fleet pause`, `/fleet resume`, `/fleet brief`, `/fleet board`, `/fleet approvals`, `/fleet tokens`.\n"
             "`/fleet stop` is the kill switch. It stops every fleet service after you press a confirm button. Start again with `/fleet start`.\n"
             "Hermes has its own `/stop`. It stops the current run of the orchestrator agent, and nothing else. Mention @Hermes in a channel to talk to the orchestrator.")
AGENT_NAME_RE = r"^[a-z][a-z0-9-]{1,19}$"
STATE_NAME = "slack-bootstrap.json"


class BootstrapError(Exception):
    pass


def state_file():
    return paths.ops_state_dir() / "control" / STATE_NAME


def _step(results, name, ok, detail):
    results.append((name, ok, detail))
    return ok


def bootstrap(cfg, api, owner_id, save_channel, write_env, hermes_user=None, links=(), home_view=None, again=False, state_path=None):
    """Run every step. `save_channel(name, id)` writes the live config. `write_env(key, value)` writes the Hermes profile settings.

    Returns [(step, ok, detail)]. A failed step does not stop the others.
    """
    results, ids = [], {}
    state_path = pathlib.Path(state_path or state_file())
    state = {} if again else (jsonio.read_json(state_path, {}) or {})
    if not owner_id:
        raise BootstrapError("your Slack member ID is not set. Run: fleetctl config set owner.slack_user_ids '[\"YOUR_MEMBER_ID\"]'")
    for name, topic in CHANNELS:
        try:
            ids[name] = api.create_channel(name)
            api.set_topic(ids[name], topic)
            api.join(ids[name])
            api.invite(ids[name], [owner_id])
            save_channel(name, ids[name])
            _step(results, "channel #" + name, True, ids[name])
        except slackapi.SlackApiError as exc:
            _step(results, "channel #" + name, False, exc.error)
    hq = ids.get("hq")
    try:
        write_env("SLACK_HOME_CHANNEL", hq or "")
        write_env("SLACK_HOME_CHANNEL_NAME", "hq")
        write_env("SLACK_ALLOWED_USERS", owner_id)
        write_env("SLACK_ALLOW_BOTS", "none")
        _step(results, "Hermes settings", bool(hq), "home channel, allowed user, bots none. Restart the gateway to apply" if hq else "no #hq channel")
    except (OSError, secrets.SecretError) as exc:
        _step(results, "Hermes settings", False, type(exc).__name__)
    if hq and hermes_user:
        try:
            who = hermes_user()
            if who:
                api.invite(hq, [who])
            _step(results, "invite the Hermes bot to #hq", True, "done" if who else "skipped. The Hermes bot token is not set yet. Run this again after you set it")
        except slackapi.SlackApiError as exc:
            _step(results, "invite the Hermes bot to #hq", False, exc.error)
    if hq and not state.get("help_ts"):
        try:
            ts = api.post(hq, HELP_NOTE)["ts"]
            api.pin(hq, ts)
            state["help_ts"] = ts
            _step(results, "pin the help note in #hq", True, "pinned")
        except slackapi.SlackApiError as exc:
            _step(results, "pin the help note in #hq", False, exc.error)
    elif hq:
        _step(results, "pin the help note in #hq", True, "already pinned")
    if hq and links:
        added = list(state.get("bookmarks") or [])
        for title, url in links:
            if title in added:
                continue
            try:
                api.bookmark(hq, title, url)
                added.append(title)
            except slackapi.SlackApiError as exc:
                _step(results, "bookmark " + title, False, exc.error)
        state["bookmarks"] = added
        _step(results, "bookmarks in #hq", len(added) == len(links), ", ".join(added) or "none")
    if home_view is not None:
        try:
            api.publish_home(owner_id, home_view())
            _step(results, "App Home", True, "published")
        except slackapi.SlackApiError as exc:
            _step(results, "App Home", False, exc.error)
    tested = set(state.get("tested") or [])
    for name, _topic in CHANNELS:
        if name in ids and name not in tested:
            try:
                api.post(ids[name], "Fleet HQ is connected to #%s. This is a test message." % name, **slackviews.identity("orchestrator"))
                tested.add(name)
            except slackapi.SlackApiError as exc:
                _step(results, "test message in #" + name, False, exc.error)
    state["tested"] = sorted(tested)
    _step(results, "test messages", len(tested) == len(CHANNELS), "%d of %d channels" % (len(tested), len(CHANNELS)))
    try:
        jsonio.write_json(state_path, state, mode=0o600)
    except OSError:
        pass
    return results


# ------------------------------------------------------------------------------------------------ manifests for more agents
FORBIDDEN_FEATURES = ("assistant_view", "agent_view")
FORBIDDEN_SCOPES = ("assistant:write",)
FORBIDDEN_EVENTS_PREFIX = ("assistant_thread",)


# Slash commands that change the approval posture. The fleet pins smart approvals in the managed config. A command in Slack must not reach for it. See D-057.
POSTURE_COMMANDS = ("/yolo", "/approvals")


def manifest_problems(manifest):
    """What a manifest must not hold. The free plan has no agent view and no assistant view. No command may change the approval posture."""
    out = []
    for command in (manifest.get("features") or {}).get("slash_commands") or []:
        if command.get("command") in POSTURE_COMMANDS:
            out.append("it has the command " + command["command"])
    for key in FORBIDDEN_FEATURES:
        if key in (manifest.get("features") or {}):
            out.append("it has the feature " + key)
    for scope in (manifest.get("oauth_config", {}).get("scopes", {}).get("bot") or []):
        if scope in FORBIDDEN_SCOPES:
            out.append("it has the scope " + scope)
    for event in (manifest.get("settings", {}).get("event_subscriptions", {}).get("bot_events") or []):
        if event.startswith(FORBIDDEN_EVENTS_PREFIX):
            out.append("it has the event " + event)
    if not (manifest.get("settings") or {}).get("socket_mode_enabled"):
        out.append("Socket Mode is off")
    return out


def agent_manifest(base, agent):
    """The manifest of one more agent app. It has its own name and no slash commands, because the Hermes app owns them."""
    import re
    if not re.match(AGENT_NAME_RE, agent):
        raise BootstrapError("the agent name must be lowercase letters, digits, and hyphens")
    if agent not in [a[0] for a in slackviews.AGENTS]:
        raise BootstrapError("the agent must be one of: %s" % ", ".join(a[0] for a in slackviews.AGENTS))
    out = copy.deepcopy(base)
    label = "Hermes " + agent.capitalize()
    out["display_information"]["name"] = label
    out["display_information"]["description"] = "The %s agent of the fleet." % agent
    out["features"]["bot_user"]["display_name"] = label
    out["features"].pop("slash_commands", None)
    problems = manifest_problems(out)
    if problems:
        raise BootstrapError("the manifest is not allowed: " + "; ".join(problems))
    return out


def write_agent_manifest(agent, slack_dir):
    slack_dir = pathlib.Path(slack_dir)
    base = json.loads((slack_dir / "hermes.manifest.json").read_text())
    manifest = agent_manifest(base, agent)
    target = slack_dir / "agents" / ("%s.manifest.json" % agent)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=2) + "\n")
    return target
