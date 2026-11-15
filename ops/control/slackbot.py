"""The Slack adapter of the control bot. It listens in Socket Mode and answers /fleet, the buttons, and App Home. See D-052.

It runs as its own unit with its own venv, because Bolt is a third-party package. The kill-switch bot stays plain Python
with no packages. This process idles until both Fleet HQ tokens exist, and it asks for no restart when they arrive.
Only one process may use the app token. Slack splits the events of one app across all of its connections.
Only the owner's member ID gets an answer. A stranger gets an empty acknowledgement and nothing else.
"""
import threading
import time

from ops.control import api as control_api
from ops.control import approvals, board, commands, slackapi, slackcmd, slackout, slackviews
from ops.fleetlib import config, jsonio, log, paths, secrets

LOG = log.get("slack-bot")
ACTIONS = ("fleet_approve", "fleet_reject", "fleet_stop_confirm", "fleet_stop_cancel")
RETRY_SECONDS = 30
IDLE_SECONDS = 60


class SlackBot:
    def __init__(self, cfg, controller, fleet_commands, api, clock=time.time):
        self.cfg, self.controller, self.fc, self.api, self.clock = cfg, controller, fleet_commands, api, clock

    # ---- handlers. Bolt calls them with these arguments. Tests call them directly.
    def on_command(self, ack, command, respond):
        ack()                                                       # Slack wants an answer within 3 seconds
        reply = self.fc.handle(command.get("user_id", ""), command.get("text", ""))
        if reply is None:
            LOG.warn("ignored_command")                             # not the owner. Never answer.
            return
        respond(**reply)

    def on_action(self, ack, body, action, respond):
        ack()
        user = (body.get("user") or {}).get("id", "")
        out = self.fc.interactive(user, action.get("action_id", ""), action.get("value", ""))
        if out is None:
            LOG.warn("ignored_action")
            return
        respond(**out)

    def on_home_opened(self, event):
        user = event.get("user", "")
        if not self.fc.is_owner(user):
            return
        try:
            self.api.publish_home(user, slackviews.home_view(self.home_state()))
        except slackapi.SlackApiError as exc:
            LOG.warn("home_failed", error=exc.error)

    def home_state(self):
        """What App Home shows: service health, each agent, tokens against the free limits, open approvals, and links."""
        status = self.controller.status_data()
        snap = jsonio.read_json(slackcmd.board_file(), {}) or {}
        running = {}
        for board_ in (snap.get("boards") or {}).values():
            for card in board_.get("cards") or []:
                if card.get("status") == "running" and card.get("assignee"):
                    running.setdefault(card["assignee"], card.get("title", ""))
        budget = jsonio.read_json(paths.budget_file(), {}) or {}
        return {"paused": status["paused"], "services": status["services"],
                "agents": [{"name": name, "card": running.get(name)} for name, _icon, _label in slackviews.AGENTS],
                "providers": budget.get("providers") or {}, "approvals": approvals.pending(now=self.clock())}


def register(app, bot):
    """Put the handlers on a Bolt app. It works with the real App and with a fake one that has the same decorators."""
    app.command("/fleet")(bot.on_command)
    for action_id in ACTIONS:
        app.action(action_id)(bot.on_action)
    app.event("app_home_opened")(bot.on_home_opened)
    return app


def build_app(token):
    """A Bolt app for Socket Mode. The import is here, so the other code never needs the package."""
    from slack_bolt import App
    return App(token=token, request_verification_enabled=False, ssl_check_enabled=False, token_verification_enabled=False)


def start_handler(app, app_token):
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    handler = SocketModeHandler(app, app_token)
    handler.start()                                                 # blocks until the connection ends
    return handler


def make_bot(cfg, bot_token):
    controller = commands.Controller(cfg)
    queue = control_api.Api(cfg, controller, lambda text: 0, slackout.SlackOut(cfg)).queue_card
    api = slackapi.SlackApi(bot_token)
    return SlackBot(cfg, controller, slackcmd.FleetCommands(cfg, controller, queue_card=queue), api), api


def main(stop=None, build=build_app, start=start_handler, sleep=None):
    cfg = config.load()
    stop = stop or threading.Event()
    wait = sleep or stop.wait
    mirror_started = False
    while not stop.is_set():
        bot_token, app_token = secrets.value_of("SLACK_HQ_BOT_TOKEN"), secrets.value_of("SLACK_HQ_APP_TOKEN")
        if not (bot_token and app_token):
            LOG.info("idle", reason="the Fleet HQ tokens are not set")
            wait(IDLE_SECONDS)
            continue
        try:
            bot, api = make_bot(cfg, bot_token)
            if not mirror_started:
                threading.Thread(target=board.BoardMirror(cfg, api).loop, args=(stop,), daemon=True).start()
                mirror_started = True
            app = register(build(bot_token), bot)
            LOG.info("started", mode="socket")
            start(app, app_token)
        except Exception as exc:                                    # a lost connection or a bad token. Try again.
            LOG.error("connection_failed", error=type(exc).__name__)
        wait(RETRY_SECONDS)


if __name__ == "__main__":
    main()
