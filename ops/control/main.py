"""The control bot. Its own unit. It stays up when `systemctl stop agent-fleet.target` runs.

It polls Telegram and Discord for the owner's commands. It also has a local notify endpoint on
127.0.0.1 that the briefing and the merger use to reach the owner. Without tokens it idles.
"""
import threading
import time

from ops.control import api, channels, commands, hold, n8nwatch, quiet, slackout
from ops.fleetlib import config, httpd, log, secrets, tokens

LOG = log.get("control")


def build_channels(cfg):
    out = []
    owner_tg = config.get(cfg, "owner.telegram_user_ids", []) or []
    owner_dc = config.get(cfg, "owner.discord_user_ids", []) or []
    if config.get(cfg, "control.telegram.enabled", False):
        out.append(channels.Telegram(secrets.value_of("CONTROL_TELEGRAM_BOT_TOKEN"), owner_ids=owner_tg))
    if config.get(cfg, "control.discord.enabled", False):
        out.append(channels.Discord(secrets.value_of("CONTROL_DISCORD_BOT_TOKEN"), owner_ids=owner_dc,
                                    channel_id=config.get(cfg, "control.discord.channel_id", "")))
    return [c for c in out if c.ready()]


def build_slack(cfg):
    """The Slack sender of the bot. Without a client, every message goes to the sink file. See D-068."""
    return slackout.SlackOut(cfg, client=slackout.build_client(cfg))


def notify_all(chans, text):
    if hold.active():                      # a selftest holds owner messages. See D-043.
        hold.record("chat", text)
        return 0
    sent = 0
    for ch in chans:
        try:
            ch.notify_owner(text)
            sent += 1
        except channels.ChannelError as exc:
            LOG.warn("notify_failed", channel=ch.name, error=str(exc))
    return sent


def serve_channel(ch, controller, stop):
    while not stop.is_set():
        try:
            for user_id, target, text in ch.poll():
                reply = controller.handle(ch.name, user_id, text)
                if reply is None:
                    LOG.warn("ignored", channel=ch.name)          # not the owner. Never answer.
                    continue
                ch.send(target, reply)
        except channels.ChannelError as exc:
            LOG.warn("poll_failed", channel=ch.name, error=str(exc))
            stop.wait(15)
        if ch.name == "discord":
            stop.wait(float(config.get(controller.cfg, "control.poll_seconds", 3)))


def build_routes(cfg, controller, chans, slack):
    """The HTTP routes of the control bot: /notify for the briefing and the merger, /health, and the fleet action API for n8n."""
    def notify(req):
        if not httpd.verify_token(req.bearer(), tokens.hashes("CONTROL_NOTIFY_TOKEN")):
            raise httpd.HttpError(401, "a valid bearer token is required")
        body = req.json() or {}
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            raise httpd.HttpError(400, "the body must be {\"text\": \"...\"}")
        severity = body.get("severity", "critical")
        if severity not in quiet.SEVERITIES:
            raise httpd.HttpError(400, "severity must be info, warn, or critical")
        if quiet.holds(cfg, severity):                      # quiet hours. A message that is not critical waits for the brief.
            quiet.queue(text[:1000], body.get("slack_channel") or "hq", severity)
            return 200, {"sent_to": 0, "queued": True}
        out = {"sent_to": notify_all(chans, text[:8000]) if body.get("chat", True) is not False else 0}
        channel = body.get("slack_channel")
        if channel:
            # Slack is the fourth channel of the brief. The sink takes the message until the Slack tokens exist.
            try:
                out["slack"] = slack.post(channel, text[:slackout.MAX_TEXT])["mode"]
            except slackout.SlackError:
                out["slack"] = "refused"
        return 200, out

    routes = {("POST", "/notify"): notify,
              ("GET", "/health"): lambda r: (200, {"status": "OK", "channels": [c.name for c in chans], "hold": True})}
    # The fleet action API for n8n. Every route needs FLEET_ACTION_TOKEN. See ops/control/api.py.
    routes.update(api.Api(cfg, controller, lambda text: notify_all(chans, text), slack).routes())
    return routes


def main():
    cfg = config.load()
    controller = commands.Controller(cfg)
    chans = build_channels(cfg)
    slack = build_slack(cfg)
    stop = threading.Event()
    for ch in chans:
        threading.Thread(target=serve_channel, args=(ch, controller, stop), daemon=True).start()
    # The bot watches n8n. The workflow health-watch watches the rest of the fleet. See ops/control/n8nwatch.py.
    watcher = n8nwatch.N8nWatch(cfg, lambda text: notify_all(chans, text))
    threading.Thread(target=watcher.loop, args=(stop, float(config.get(cfg, "n8n.watch_seconds", 60))), daemon=True).start()

    routes = build_routes(cfg, controller, chans, slack)
    server = httpd.JsonServer("127.0.0.1", config.port(cfg, "control"), routes, max_body=50_000, log=LOG)
    LOG.info("started", channels=[c.name for c in chans], idle=not chans)
    server.serve_forever()


if __name__ == "__main__":
    main()
