"""fleetctl slack. See D-052 and docs/SLACK.md.

  bootstrap   make the channels and set Slack up for the fleet. Safe to run twice
  add-agent   make the manifest of one more agent app in slack/agents/
  status      which tokens are set, which channels are known, and the quiet hours
"""
import pathlib

from ops.control import slackapi, slackboot, slackviews
from ops.fleetlib import config, paths, secrets

ORCHESTRATOR_ENV = "/home/fleet/.hermes/profiles/orchestrator/.env"
TOKENS = ("SLACK_HQ_BOT_TOKEN", "SLACK_HQ_APP_TOKEN", "SLACK_HERMES_BOT_TOKEN", "SLACK_HERMES_APP_TOKEN")


def _git_page(remote):
    remote = (remote or "").strip()
    if remote.startswith("git@github.com:"):
        remote = "https://github.com/" + remote.split(":", 1)[1]
    if remote.startswith("https://github.com/"):
        return remote[:-4] if remote.endswith(".git") else remote
    return ""


def bookmarks(cfg):
    links = [("n8n", "http://127.0.0.1:%d" % config.port(cfg, "n8n")), ("Hermes dashboard", "http://127.0.0.1:9119")]
    page = _git_page(config.get(cfg, "backup.github_remote", ""))
    if page:
        links.append(("GitHub repository", page))
    return links


def cmd_bootstrap(args, console, runner=None, request=None):
    cfg = config.load()
    token = secrets.value_of("SLACK_HQ_BOT_TOKEN")
    if not token:
        console.say("Slack is WAITING_FOR_KEYS. Make the Fleet HQ app from slack/fleet-hq.manifest.json, then run: fleetctl secret set SLACK_HQ_BOT_TOKEN")
        console.say("The steps are in docs/SLACK.md.")
        return 1
    owners = config.get(cfg, "owner.slack_user_ids", []) or []
    api = slackapi.SlackApi(token, request=request)

    def hermes_user():
        hermes_token = secrets.value_of("SLACK_HERMES_BOT_TOKEN")
        return slackapi.SlackApi(hermes_token, request=request).call("auth.test").get("user_id") if hermes_token else None

    def home_view():
        from ops.control import commands, slackbot
        bot = slackbot.SlackBot(cfg, commands.Controller(cfg), None, api)
        return slackviews.home_view(bot.home_state())

    try:
        results = slackboot.bootstrap(
            cfg, api, str(owners[0]) if owners else "",
            save_channel=lambda name, cid: config.set_override("slack.channels.%s" % name, cid),
            write_env=lambda key, value: secrets.set_env_line(ORCHESTRATOR_ENV, key, value, "fleet"),
            hermes_user=hermes_user, links=bookmarks(cfg), home_view=home_view, again=args.again)
    except slackboot.BootstrapError as exc:
        console.say("Not done: %s" % exc)
        return 1
    for name, ok, detail in results:
        console.say("%-4s %-34s %s" % ("OK" if ok else "FAIL", name, detail))
    bad = [r for r in results if not r[1]]
    console.say("Result: %d of %d steps passed." % (len(results) - len(bad), len(results)))
    if not bad:
        console.say("Next: restart the gateway so that Hermes reads its Slack settings, then set slack.sink_mode to false: fleetctl config set slack.sink_mode false")
    return 1 if bad else 0


def clone_slack_folder():
    """The `slack` folder of the repository where the command runs. It is never the locked live copy. See D-065."""
    here = pathlib.Path.cwd()
    for folder in (here, *here.parents):
        if (folder / "slack" / "hermes.manifest.json").is_file():
            return folder / "slack"
    return None


def cmd_add_agent(args, console, runner=None):
    folder = clone_slack_folder()
    if folder is None:
        console.say("Not made: run this command inside your clone of the repository. It holds the folder slack/ with hermes.manifest.json.")
        return 1
    if str(folder.resolve()).startswith(str(paths.live_repo().resolve())):
        console.say("Not made: that is the locked live copy. Run the command in your own clone and commit the file there.")
        return 1
    try:
        path = slackboot.write_agent_manifest(args.name, folder)
    except (slackboot.BootstrapError, OSError, ValueError) as exc:
        console.say("Not made: %s" % exc)
        return 1
    console.say("Wrote %s. Do not install the extra apps until you need them. The free plan allows 10 apps, and 3 are in use." % path)
    return 0


def cmd_status(args, console, runner=None):
    cfg = config.load()
    for name in TOKENS:
        console.say("%-26s %s" % (name, "set" if secrets.value_of(name) else "WAITING_FOR_KEYS"))
    channels = config.get(cfg, "slack.channels", {}) or {}
    console.say("Channels known: %s" % (", ".join(sorted(channels)) or "none yet. Run: fleetctl slack bootstrap"))
    quiet = config.get(cfg, "slack.quiet_hours", {}) or {}
    console.say("Sink mode: %s. Quiet hours: %s to %s." % ("on" if config.get(cfg, "slack.sink_mode", True) else "off", quiet.get("start", "?"), quiet.get("end", "?")))
    return 0


def dispatch(args, console, runner=None):
    table = {"bootstrap": cmd_bootstrap, "add-agent": cmd_add_agent, "status": cmd_status}
    fn = table.get(args.slack_command)
    if not fn:
        console.say("Choose one of: %s" % ", ".join(sorted(table)))
        return 2
    return fn(args, console, runner)
