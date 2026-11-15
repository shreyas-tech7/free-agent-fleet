"""The daily briefing. A systemd timer starts it at 07:00 in the owner's time zone.

It collects facts, writes the brief, saves it, and sends it by email, Telegram, and Discord.
Telegram and Discord go through the control bot, so only that bot holds those tokens.
If no model is available, it sends the plain-code brief.
"""
import datetime
import json
import pathlib
import smtplib
import ssl
import sys
from email.message import EmailMessage

from ops.briefing import collect, note, render
from ops.control import quiet
from ops.fleetlib import config, events, httpc, jsonio, log, paths, secrets

LOG = log.get("briefing")


def ask_router(cfg, prompt):
    key = secrets.value_of("ROUTER_KEY_BRIEFING")
    if not key or config.get(cfg, "router.base_url", "") == "":
        return None
    resp = httpc.post(config.get(cfg, "router.base_url").rstrip("/") + "/chat/completions", timeout=90,
                      headers={"Authorization": "Bearer " + key},
                      json_body={"model": "fleet-worker", "messages": [{"role": "user", "content": prompt}], "temperature": 0.2})
    if resp.status != 200:
        return None
    return resp.json()["choices"][0]["message"]["content"]


def save(text, date):
    out = paths.state_dir() / "briefs"
    out.mkdir(parents=True, exist_ok=True)
    for name in (date + ".md", "latest.md"):
        jsonio.atomic_write(out / name, text + "\n", mode=0o644)


def send_email(cfg, text, smtp=smtplib):
    e = config.get(cfg, "briefing.email", {}) or {}
    if not e.get("enabled") or not e.get("smtp_host"):
        return "off"
    password = secrets.value_of("SMTP_PASSWORD")
    if not password:
        return "no password"
    msg = EmailMessage()
    msg["Subject"] = "agent-fleet daily brief"
    msg["From"] = e.get("from_address") or e.get("smtp_user")
    msg["To"] = config.get(cfg, "owner.email")
    msg.set_content(text)
    try:
        with smtp.SMTP(e["smtp_host"], int(e.get("smtp_port", 587)), timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(e.get("smtp_user") or msg["From"], password)
            s.send_message(msg)
        return "sent"
    except (OSError, smtplib.SMTPException) as exc:
        return "failed: " + type(exc).__name__


def send_chat(cfg, text, slack_channel="brief", chat=True):
    """Send a text through the control bot. With chat=False the text goes to Slack only. The weekly log uses that."""
    token = secrets.value_of("CONTROL_NOTIFY_TOKEN")
    if not token:
        return "no token"
    body = {"text": text, "slack_channel": slack_channel}
    if not chat:
        body["chat"] = False
    try:
        resp = httpc.post("http://127.0.0.1:%d/notify" % config.port(cfg, "control"), json_body=body,
                          headers={"Authorization": "Bearer " + token}, timeout=30)
        if resp.status != 200:
            return "failed: status %d" % resp.status
        body = resp.json()
        return "sent to %s, slack %s" % (body.get("sent_to", 0), body.get("slack", "off"))
    except (httpc.TransportError, ValueError):
        return "failed: control bot not reachable"


def main(argv=None):
    cfg = config.load()
    trigger = paths.ops_state_dir() / "briefing" / "NOW"
    try:
        trigger.unlink()
    except FileNotFoundError:
        pass
    data = collect.collect(cfg)
    notes = quiet.drain()                       # messages that waited in quiet hours go into this brief. The file is archived, not deleted.
    data["overnight"] = [{"channel": n["channel"], "severity": n["severity"], "text": n["text"][:140]} for n in notes[-10:]]
    data["overnight_count"] = len(notes)
    data["note"] = note.read()
    text, source = render.brief(data, ask_model=lambda p: ask_router(cfg, p), max_words=int(config.get(cfg, "briefing.max_words", 150)))
    save(text, data["date"])
    result = {"source": source, "words": render.words(text), "email": send_email(cfg, text), "chat": send_chat(cfg, text)}
    LOG.info("brief", **result)
    if data["note"] and delivered(result):
        note.clear()                              # the note goes out once
    report_event(result, data["date"])
    print(json.dumps(result))
    return 0


def delivered(result):
    """The brief reached somebody when the chat call worked or the email went out."""
    return result["chat"].startswith("sent to") or result["email"].startswith("sent")


def report_event(result, date, emit=events.emit):
    """Tell n8n that the brief went out, or that it did not. The brief itself never fails because of this."""
    if delivered(result):
        emit("brief.sent", "The brief for %s went out (%d words, %s)." % (date, result["words"], result["source"]), source="briefing",
             fields={"words": result["words"], "source": result["source"]})
    else:
        emit("brief.failed", "The brief for %s did not reach you. Email: %s. Chat: %s." % (date, result["email"], result["chat"]),
             severity="warn", source="briefing")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
