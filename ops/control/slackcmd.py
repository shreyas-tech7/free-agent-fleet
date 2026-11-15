"""The /fleet command and the buttons of Fleet HQ. Pure logic. The Bolt adapter in slackbot.py calls it. See D-052.

Only the owner's Slack member IDs (owner.slack_user_ids) get an answer. Everyone else gets nothing.
/fleet stop does not stop anything by itself. It shows a confirm button, and the click does the work.
"""
import time

from ops.control import approvals, slackviews
from ops.fleetlib import config, events, jsonio, paths

AGENT_NAMES = [a[0] for a in slackviews.AGENTS]
DEFAULT_RUNNABLE = ["health-watch", "token-ledger"]
MAX_ASK = 1500


def board_file():
    return paths.state_dir() / "board" / "board.json"


def reply(text, blocks=None, in_channel=False):
    out = {"response_type": "in_channel" if in_channel else "ephemeral", "text": text}
    if blocks:
        out["blocks"] = blocks
    return out


class FleetCommands:
    def __init__(self, cfg, controller, queue_card=None, emit=events.emit, clock=time.time, board_path=None):
        self.cfg, self.controller, self.queue_card, self.emit, self.clock = cfg, controller, queue_card, emit, clock
        self.board_path = board_path

    def is_owner(self, user_id):
        return str(user_id) in self.controller.owner_ids("slack")

    # ---- /fleet SUBCOMMAND
    def handle(self, user_id, text):
        if not self.is_owner(user_id):
            return None
        parts = (text or "").strip().split(None, 2)
        sub = parts[0].lower() if parts else "help"
        rest = parts[1:]
        if sub == "help":
            return reply(slackviews.help_text())
        if sub in ("status", "pause", "resume", "start"):
            return reply(self.controller.handle("slack", user_id, "/" + sub))
        if sub == "stop":
            return reply("This stops every fleet service.", slackviews.stop_confirm_blocks())
        if sub == "brief":
            return reply(self.controller.handle("slack", user_id, "/brief now" if rest[:1] == ["now"] else "/brief"))
        if sub == "board":
            return reply(self.board_text())
        if sub == "approvals":
            return self.approvals_reply()
        if sub == "tokens":
            return reply(self.tokens_text())
        if sub == "ask":
            return self.ask(rest)
        if sub == "run":
            return self.run(rest)
        return reply("I do not know `%s`.\n%s" % (sub[:30], slackviews.help_text()))

    # ---- buttons
    def interactive(self, user_id, action_id, value):
        """A click on a button. Returns a message that replaces the old one, or None for a stranger."""
        if not self.is_owner(user_id):
            return None
        who = "<@%s>" % user_id
        if action_id in ("fleet_approve", "fleet_reject"):
            try:
                record = approvals.decide(str(value).lower(), action_id == "fleet_approve", "slack:%s" % user_id, now=self.clock())
            except approvals.ApprovalError as exc:
                return {"replace_original": True, "text": "Not done: %s." % exc, "blocks": [slackviews.section("Not done: %s." % exc)]}
            return {"replace_original": True, "text": "%s %s" % (record["status"].capitalize(), record["id"]), "blocks": slackviews.approval_done_blocks(record, who)}
        if action_id == "fleet_stop_confirm":
            text = self.controller.handle("slack", user_id, "/stop")
            return {"replace_original": True, "text": text, "blocks": [slackviews.section(text), slackviews.context("Confirmed by %s." % who)]}
        if action_id == "fleet_stop_cancel":
            return {"replace_original": True, "text": "Stop cancelled.", "blocks": [slackviews.section("Stop cancelled.")]}
        return None

    # ---- the parts
    def approvals_reply(self):
        waiting = approvals.pending(now=self.clock())
        if not waiting:
            return reply("No approval is open.")
        blocks = []
        for record in waiting[:8]:
            blocks += slackviews.approval_blocks(record)
        return reply("%d open approval%s." % (len(waiting), "" if len(waiting) == 1 else "s"), blocks)

    def snapshot(self):
        return jsonio.read_json(self.board_path or board_file(), {}) or {}

    def board_text(self):
        boards = self.snapshot().get("boards") or {}
        cards = [dict(c, board=slug) for slug, b in boards.items() for c in (b.get("cards") or []) if c.get("status") not in ("done", "archived")]
        if not cards:
            return "No open cards. The board mirror has not run yet, or every card is done."
        grouped = {}
        for card in cards:
            grouped.setdefault(card.get("assignee") or "unassigned", []).append(card)
        lines = []
        for agent in sorted(grouped):
            lines.append("*%s*" % agent)
            lines += ["  %s  %s (%s)" % (c.get("id", "?"), c.get("title", "")[:80], c.get("status", "?")) for c in grouped[agent][:8]]
        return "\n".join(lines)

    def tokens_text(self):
        budget = jsonio.read_json(paths.budget_file(), {}) or {}
        providers = budget.get("providers") or {}
        if not providers:
            return "No usage recorded yet. The quota tracker runs every 15 minutes."
        lines = ["*Tokens today against the free limits*" + ("  (slowing down)" if budget.get("slowdown") else "")]
        for name, p in sorted(providers.items()):
            pct = p.get("percent")
            lines.append("`%s`  %s requests  %s of %s" % (name, p.get("requests_today", "?"), ("%s%%" % pct) if pct is not None else "no percent", p.get("limit") or "no limit set"))
        return "\n".join(lines)

    def ask(self, rest):
        if len(rest) < 2 or rest[0].lower() not in AGENT_NAMES:
            return reply("Use: `/fleet ask AGENT TEXT`. The agents are: %s." % ", ".join(AGENT_NAMES))
        if self.queue_card is None:
            return reply("Cards cannot be queued now.")
        text = rest[1].strip()[:MAX_ASK]
        try:
            card = self.queue_card(rest[0].lower(), text[:100], text, "", "slack")
        except Exception as exc:                      # the action API refuses with an HttpError. Its message is safe to show.
            return reply("Not queued: %s." % str(getattr(exc, "message", None) or exc)[:120])
        return reply("Queued for %s: %s" % (rest[0].lower(), (card[1] if isinstance(card, tuple) else card).get("queued", "ok")))

    def runnable(self):
        return list(config.get(self.cfg, "slack.runnable_workflows", DEFAULT_RUNNABLE) or DEFAULT_RUNNABLE)

    def run(self, rest):
        names = self.runnable()
        if not rest or rest[0] not in names:
            return reply("Use: `/fleet run WORKFLOW`. The workflows are: %s." % ", ".join(names))
        self.emit("run.request", "Run the workflow %s now. Asked in Slack." % rest[0], source="slack", fields={"workflow": rest[0]})
        return reply("Asked n8n to run `%s`. The result appears in #n8n." % rest[0])
