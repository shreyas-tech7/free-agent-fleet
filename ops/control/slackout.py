"""Messages to Slack. Until the Slack tokens exist, every message goes to the sink.

The sink is a file, /var/lib/agent-fleet/outbox/slack-sink.jsonl, and a log line. One config flag, slack.sink_mode,
turns it off. With the flag off and no Slack client, the sink still takes the message, so nothing is lost.
With the Slack tokens, a message goes out through the Slack Web API. If that fails, the message goes to the sink.
Inside quiet hours, only critical messages go out live. The rest waits in a queue for the brief. See D-038 and D-052.
"""
import json
import pathlib
import time

from ops.control import hold, quiet, slackapi, slackviews
from ops.fleetlib import config, log, paths, redact, secrets

LOG = log.get("slack-out")
CHANNELS = ("hq", "brief", "approvals", "alerts", "board", "agents", "n8n", "github", "portfolio")
AGENTS = ("orchestrator", "research", "build", "marketing", "feedback", "security", "improver")
SEVERITIES = ("info", "warn", "critical")
MAX_TEXT = 3000
MAX_SINK_BYTES = 5_000_000


class SlackError(ValueError):
    pass


def validate(channel, text, agent="", severity="info"):
    if channel not in CHANNELS:
        raise SlackError("the channel must be one of: %s" % ", ".join(CHANNELS))
    if agent and agent not in AGENTS:
        raise SlackError("the agent must be one of: %s" % ", ".join(AGENTS))
    if severity not in SEVERITIES:
        raise SlackError("severity must be info, warn, or critical")
    if not isinstance(text, str) or not text.strip():
        raise SlackError("the text must be a non-empty string")
    return channel, redact.redact_text(text.strip())[:MAX_TEXT], agent, severity


class HqClient:
    """Posts through the Slack Web API with the Fleet HQ bot token. Channel names map to IDs in slack.channels."""

    def __init__(self, cfg, api):
        self.cfg, self.api = cfg, api

    def post(self, channel, text, agent="", severity="info", blocks=None):
        channel_id = config.get(self.cfg, "slack.channels.%s" % channel, "")
        if not channel_id:
            raise SlackError("the channel %s has no ID yet. Run: fleetctl slack bootstrap" % channel)
        icon = slackviews.SEVERITY_ICON.get(severity, "") if severity != "info" else ""
        who = slackviews.identity(agent)
        self.api.post(channel_id, ("%s %s" % (icon, text)).strip(), blocks=blocks, username=who.get("username"), icon_emoji=who.get("icon_emoji"))


def build_client(cfg, request=None):
    """The HQ client, or None when the token does not exist yet."""
    token = secrets.value_of("SLACK_HQ_BOT_TOKEN")
    return HqClient(cfg, slackapi.SlackApi(token, request=request)) if token else None


class SlackOut:
    def __init__(self, cfg, client=None, clock=None, sink_file=None):
        self.cfg = cfg
        self.client = client
        self.clock = clock or quiet._clock
        self.sink_file = pathlib.Path(sink_file or (paths.state_dir() / "outbox" / "slack-sink.jsonl"))

    def sink_mode(self):
        return bool(config.get(self.cfg, "slack.sink_mode", True)) or self.client is None

    def post(self, channel, text, agent="", severity="info", blocks=None):
        """`blocks` is for a card with buttons. The text stays as the notification and as the sink line. See D-068."""
        channel, text, agent, severity = validate(channel, text, agent, severity)
        if hold.active():                      # a selftest holds owner messages. See D-043.
            hold.record("slack:" + channel, text, now=self.clock())
            return {"ok": True, "mode": "hold"}
        if quiet.holds(self.cfg, severity, self.clock()):       # quiet hours. The brief reads the queue.
            quiet.queue(text, channel, severity, now=self.clock())
            return {"ok": True, "mode": "queued"}
        if self.sink_mode():
            self._sink(channel, text, agent, severity)
            return {"ok": True, "mode": "sink"}
        try:
            if blocks:
                self.client.post(channel, text, agent, severity, blocks=blocks)
            else:
                self.client.post(channel, text, agent, severity)
        except (slackapi.SlackApiError, SlackError) as exc:     # a failed post must not lose the message
            LOG.warn("slack_post_failed", error=str(getattr(exc, "error", exc))[:60])
            self._sink(channel, text, agent, severity)
            return {"ok": True, "mode": "sink"}
        return {"ok": True, "mode": "slack"}

    def _sink(self, channel, text, agent, severity):
        record = {"ts": int(self.clock()), "channel": channel, "agent": agent, "severity": severity, "text": text}
        try:
            self.sink_file.parent.mkdir(parents=True, exist_ok=True)
            if self.sink_file.exists() and self.sink_file.stat().st_size > MAX_SINK_BYTES:
                self.sink_file.replace(self.sink_file.with_suffix(".jsonl.1"))
            with open(self.sink_file, "a") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
            self.sink_file.chmod(0o640)
        except OSError as exc:
            LOG.warn("sink_write_failed", error=type(exc).__name__)
        LOG.info("slack_sink", channel=channel, agent=agent, severity=severity, chars=len(text))
