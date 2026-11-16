"""The fleet action API of the control bot. n8n and the event relay call it on 127.0.0.1.

Every route needs the bearer token FLEET_ACTION_TOKEN. The bot holds only its SHA-256 hash.
The API can do only this:
  read   the fleet status, the budget, and a small public part of the config
  send   a message to the owner, and a message to a Slack channel (the sink takes it until Slack is on)
  act    pause, resume, and ask for a new brief
  queue  a Kanban card for one named agent. The text goes through the injection screen first.
  ask    the owner for an approval, and read its state
It has no shell, no file read, and no way to change the config. n8n gets nothing more. See D-038.
"""
import json
import pathlib
import re
import secrets as pysecrets
import time

from ops.control import approvals, commands, quiet, slackout, slackviews
from ops.fleetlib import config, httpc, httpd, jsonio, log, paths, redact, tokens

LOG = log.get("control-api")
AGENTS = slackout.AGENTS
BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
MAX_TITLE, MAX_BODY, MAX_NOTIFY = 120, 2000, 1000


class RateLimit:
    """At most `limit` hits for one key in `window` seconds."""

    def __init__(self, limit, window, clock=time.time):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits = {}

    def allow(self, key):
        now = self.clock()
        recent = [t for t in self.hits.get(key, []) if now - t < self.window]
        ok = len(recent) < self.limit
        if ok:
            recent.append(now)
        self.hits[key] = recent
        return ok


def make_guard(cfg):
    """scan(text) -> the answer of the injection screen, or None when it cannot be reached."""
    url = "http://127.0.0.1:%d/scan" % config.port(cfg, "guard")

    def scan(text):
        try:
            resp = httpc.post(url, json_body={"text": text}, timeout=60)
            data = resp.json()
            return data if resp.status == 200 and "label" in data else None
        except (httpc.TransportError, ValueError):
            return None
    return scan


def _string(body, field, limit, required=True):
    value = body.get(field, "")
    if not isinstance(value, str):
        raise httpd.HttpError(400, "%s must be a string" % field)
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value).strip()
    if required and not value:
        raise httpd.HttpError(400, "%s is required" % field)
    if len(value) > limit:
        raise httpd.HttpError(400, "%s is longer than %d characters" % (field, limit))
    return value


def _object(req):
    body = req.json()
    if not isinstance(body, dict):
        raise httpd.HttpError(400, "the body must be a JSON object")
    return body


class Api:
    def __init__(self, cfg, controller, notify, slack, guard=None, clock=time.time, cards_dir=None, approvals_dir=None, hashes=None):
        self.cfg, self.controller, self.notify, self.slack = cfg, controller, notify, slack
        self.guard = guard or make_guard(cfg)
        self.clock = clock
        self.cards_dir = pathlib.Path(cards_dir or (paths.state_dir() / "cards-in"))      # in no agent sandbox
        self.approvals_dir = approvals_dir
        self.hashes = hashes or (lambda: tokens.hashes("FLEET_ACTION_TOKEN"))
        self.limits = {"tool_notify": RateLimit(6, 3600, clock), "cards": RateLimit(20, 3600, clock), "approvals": RateLimit(20, 3600, clock)}

    def _auth(self, req):
        if not httpd.verify_token(req.bearer(), self.hashes()):
            raise httpd.HttpError(401, "a valid bearer token is required")

    def routes(self):
        def guarded(fn):
            def handler(req):
                self._auth(req)
                return fn(req)
            return handler
        return {
            ("GET", "/v1/status"): guarded(self.status),
            ("GET", "/v1/budget"): guarded(self.budget),
            ("GET", "/v1/config/public"): guarded(self.public_config),
            ("POST", "/v1/notify"): guarded(self.notify_owner),
            ("POST", "/v1/slack/post"): guarded(self.slack_post),
            ("POST", "/v1/actions/pause"): guarded(lambda req: self.act(req, "pause")),
            ("POST", "/v1/actions/resume"): guarded(lambda req: self.act(req, "resume")),
            ("POST", "/v1/actions/brief"): guarded(self.brief),
            ("POST", "/v1/actions/card"): guarded(self.card),
            ("POST", "/v1/approvals"): guarded(self.approval_create),
            ("GET", "/v1/approvals"): guarded(self.approval_read),
        }

    # ------------------------------------------------------------ read
    def status(self, req):
        data = self.controller.status_data()
        data["approvals_pending"] = len(approvals.pending(now=self.clock(), base=self.approvals_dir))
        data["events"] = jsonio.read_json(paths.ops_state_dir() / "events" / "status.json", {}) or {}
        return 200, data

    def budget(self, req):
        return 200, jsonio.read_json(paths.budget_file(), {}) or {}

    def public_config(self, req):
        return 200, {"slack_sink": bool(config.get(self.cfg, "slack.sink_mode", True)),
                     "quiet_hours": config.get(self.cfg, "slack.quiet_hours", {}),
                     "channels": list(slackout.CHANNELS), "agents": list(AGENTS)}

    # ------------------------------------------------------------ send
    def notify_owner(self, req):
        body = _object(req)
        text = _string(body, "text", MAX_NOTIFY)
        # An agent can call this through the fleet-tools MCP server. A looping agent must not flood the owner.
        if body.get("source") == "tool" and not self.limits["tool_notify"].allow("tool"):
            raise httpd.HttpError(429, "too many messages from tools in one hour")
        severity = body.get("severity", "critical")
        if severity not in quiet.SEVERITIES:
            raise httpd.HttpError(400, "severity must be info, warn, or critical")
        if quiet.holds(self.cfg, severity, self.clock()):   # quiet hours. A message that is not critical waits for the brief.
            quiet.queue(text, "hq", severity, now=self.clock())
            return 200, {"sent_to": 0, "queued": True}
        return 200, {"sent_to": self.notify(text)}

    def slack_post(self, req):
        body = _object(req)
        try:
            return 200, self.slack.post(_string(body, "channel", 20), _string(body, "text", slackout.MAX_TEXT),
                                        _string(body, "agent", 20, required=False), _string(body, "severity", 10, required=False) or "info")
        except slackout.SlackError as exc:
            raise httpd.HttpError(400, str(exc)) from None

    # ------------------------------------------------------------ act
    def act(self, req, name):
        rc, out = self.controller.run(commands.SUDO[name])
        LOG.info("action", action=name, ok=rc == 0)
        if rc != 0:
            raise httpd.HttpError(502, "%s failed" % name)
        return 200, {"ok": True, "action": name}

    def brief(self, req):
        if not self.controller.brief_now():
            raise httpd.HttpError(502, "could not ask for a brief")
        return 200, {"ok": True, "action": "brief"}

    # ------------------------------------------------------------ queue a card
    def card(self, req):
        body = _object(req)
        agent = _string(body, "agent", 20)
        if agent not in AGENTS:
            raise httpd.HttpError(400, "the agent must be one of: %s" % ", ".join(AGENTS))
        title = _string(body, "title", MAX_TITLE)
        text = _string(body, "body", MAX_BODY, required=False)
        board = _string(body, "board", 41, required=False)
        return self.queue_card(agent, title, text, board, source="n8n")

    def queue_card(self, agent, title, text="", board="", source="n8n"):
        """Queue one card for an agent. The text goes through the injection screen first. n8n and the Slack command ask use it."""
        if agent not in AGENTS:
            raise httpd.HttpError(400, "the agent must be one of: %s" % ", ".join(AGENTS))
        if board and not BOARD_RE.match(board):
            raise httpd.HttpError(400, "the board name is not valid")
        if not self.limits["cards"].allow("cards"):
            raise httpd.HttpError(429, "too many cards in one hour")
        scan = self.guard(title + "\n" + text)
        if scan is None:
            raise httpd.HttpError(503, "the injection screen cannot be reached. Nothing was queued")
        if scan.get("label") != "SAFE":
            LOG.warn("card_blocked", agent=agent, label=scan.get("label"))
            raise httpd.HttpError(422, "the injection screen blocked this text")
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        card_id = "c-%d-%s" % (int(self.clock() * 1000), pysecrets.token_hex(3))
        record = {"v": 1, "id": card_id, "agent": agent, "title": title, "body": text, "board": board, "ts": int(self.clock()), "source": source}
        jsonio.write_json(self.cards_dir / (card_id + ".json"), record, mode=0o660)
        return 202, {"queued": card_id}

    # ------------------------------------------------------------ approvals
    def approval_create(self, req):
        body = _object(req)
        if not self.limits["approvals"].allow("approvals"):
            raise httpd.HttpError(429, "too many approval requests in one hour")
        try:
            record = approvals.create(_string(body, "kind", 20), _string(body, "ref", 80), _string(body, "summary", approvals.MAX_SUMMARY),
                                      _string(body, "requester", 32, required=False), now=self.clock(), base=self.approvals_dir)
        except approvals.ApprovalError as exc:
            raise httpd.HttpError(400, str(exc)) from None
        self.notify("Approval %s needed (%s): %s\nReply /approve %s or /reject %s" % (record["id"], record["kind"], record["summary"], record["id"], record["id"]))
        try:
            card = dict(record, summary=redact.redact_text(record["summary"]))      # the buttons carry only the approval ID
            self.slack.post("approvals", "Approval %s (%s): %s" % (record["id"], record["kind"], record["summary"]), severity="info",
                            blocks=slackviews.approval_blocks(card))
        except slackout.SlackError:
            pass
        return 202, {"id": record["id"], "status": record["status"]}

    def approval_read(self, req):
        wanted = (req.query.get("id") or [""])[0]
        try:
            if wanted:
                return 200, approvals.get(wanted, now=self.clock(), base=self.approvals_dir)
        except approvals.ApprovalError as exc:
            raise httpd.HttpError(404, str(exc)) from None
        return 200, {"pending": approvals.pending(now=self.clock(), base=self.approvals_dir)}
