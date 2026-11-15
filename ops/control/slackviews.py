"""Block Kit views for Fleet HQ. Pure functions. No network. See D-052."""

AGENTS = (("orchestrator", ":compass:", "Orchestrator"), ("research", ":mag:", "Research"), ("build", ":hammer_and_wrench:", "Build"),
          ("marketing", ":mega:", "Marketing"), ("feedback", ":speech_balloon:", "Feedback"), ("security", ":shield:", "Security"),
          ("improver", ":seedling:", "Improver"))
SEVERITY_ICON = {"info": ":information_source:", "warn": ":warning:", "critical": ":rotating_light:"}
COMMANDS = (("status", "What runs, the pause state, keys, budget, and the active project"),
            ("pause", "Stop new work. Running work finishes"), ("resume", "Start work again"),
            ("stop", "Stop every service. It asks you to confirm"), ("start", "Start every service"),
            ("brief", "Show the latest daily brief. Add `now` to make a new one"), ("board", "The open cards, grouped by agent"),
            ("approvals", "Open approvals, with buttons"), ("ask AGENT TEXT", "Queue a card for one agent"),
            ("run WORKFLOW", "Run one named n8n workflow"), ("tokens", "Today's use against the free limits"), ("help", "This list"))
HOME_LINKS = (("n8n editor", "http://127.0.0.1:5678"), ("Hermes dashboard", "http://127.0.0.1:9119"))


def identity(agent):
    """The name and icon that a message of one agent carries. Nothing for an unknown agent."""
    for key, icon, name in AGENTS:
        if key == agent:
            return {"username": name, "icon_emoji": icon}
    return {}


def section(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}


def context(text):
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:2900]}]}


def button(label, action_id, value, style=None, confirm=None):
    out = {"type": "button", "text": {"type": "plain_text", "text": label}, "action_id": action_id, "value": value}
    if style:
        out["style"] = style
    if confirm:
        out["confirm"] = {"title": {"type": "plain_text", "text": confirm[0]}, "text": {"type": "mrkdwn", "text": confirm[1]},
                          "confirm": {"type": "plain_text", "text": confirm[2]}, "deny": {"type": "plain_text", "text": "Cancel"}}
    return out


def help_text():
    return "*Fleet HQ commands*\n" + "\n".join("`/fleet %s`  %s" % (name, what) for name, what in COMMANDS) + "\nHermes owns `/status` and `/stop`. Use `/fleet stop` for the kill switch."


def approval_blocks(record):
    """An approval card. The buttons carry only the approval ID."""
    return [section("*Approval %s*  `%s`  %s\n%s" % (record["id"], record["kind"], record["ref"], record["summary"])),
            {"type": "actions", "block_id": "approval_" + record["id"], "elements": [
                button("Approve", "fleet_approve", record["id"], "primary"), button("Reject", "fleet_reject", record["id"], "danger")]}]


def approval_done_blocks(record, who):
    word = "Approved" if record["status"] == "approved" else "Rejected"
    return [section("*Approval %s*  `%s`  %s\n%s" % (record["id"], record["kind"], record["ref"], record["summary"])),
            context("%s by %s." % (word, who))]


def stop_confirm_blocks():
    return [section("*Stop every fleet service?*\nThe control bot and this app stay up. Start again with `/fleet start`."),
            {"type": "actions", "block_id": "stop_confirm", "elements": [
                button("Stop everything", "fleet_stop_confirm", "stop", "danger", ("Stop everything", "Every fleet service stops now.", "Stop")),
                button("Cancel", "fleet_stop_cancel", "cancel")]}]


def board_message(card):
    """(text, blocks) of the one message that mirrors a card. The bot edits it when the card changes."""
    icon = {"done": ":white_check_mark:", "running": ":hourglass_flowing_sand:", "blocked": ":no_entry:", "review": ":eyes:", "ready": ":arrow_forward:"}.get(card.get("status"), ":white_circle:")
    head = "%s *%s*" % (icon, card.get("title", "")[:120])
    facts = "Agent: %s  |  Status: %s  |  Run time: %s  |  Tokens: %s" % (card.get("assignee") or "none", card.get("status", "?"), card.get("run_time", "n/a"), card.get("tokens", "n/a"))
    return "%s (%s)" % (card.get("title", "")[:120], card.get("status", "?")), [section(head), context(facts)]


def home_view(state):
    """App Home. `state` holds services, paused, agents, providers, approvals, and links."""
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": "Fleet HQ"}}]
    paused = state.get("paused")
    blocks.append(section(("*:double_vertical_bar: Paused* (%s)" % paused.get("reason", "owner")) if paused else "*:large_green_circle: Running*"))
    services = state.get("services") or {}
    blocks.append(section("*Services*\n" + ("\n".join("%s `%s`" % (":white_check_mark:" if v == "active" else ":x:", k) for k, v in sorted(services.items())) or "No data yet.")))
    agents = state.get("agents") or []
    blocks.append(section("*Agents*\n" + ("\n".join("%s %s: %s" % (identity(a["name"]).get("icon_emoji", ":white_circle:"), a["name"], ("busy on " + a["card"]) if a.get("card") else "idle") for a in agents) or "No agent data yet.")))
    providers = state.get("providers") or {}
    lines = ["`%s` %s%% of %s" % (name, p.get("percent") if p.get("percent") is not None else "?", p.get("limit") or "?") for name, p in sorted(providers.items())]
    blocks.append(section("*Tokens today against the free limits*\n" + ("\n".join(lines) or "No usage yet.")))
    waiting = state.get("approvals") or []
    blocks.append(section("*Open approvals*" + ("" if waiting else "\nNone.")))
    for record in waiting[:5]:
        blocks += approval_blocks(record)
    links = state.get("links") or HOME_LINKS
    blocks.append(context("  ".join("<%s|%s>" % (url, name) for name, url in links)))
    return {"type": "home", "blocks": blocks[:100]}
