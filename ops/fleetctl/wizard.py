"""Guided steps for the human parts: `fleetctl secret wizard --group G` and `fleetctl setup`.

Every step can be skipped with an empty answer. Run it again later. Secrets are typed hidden.
Nothing here creates an account. The owner makes each account on the provider site.
"""
import re
import zoneinfo
from types import SimpleNamespace

from ops.fleetctl import cfgcmd, checks, secretcmd, system
from ops.fleetlib import config, secrets

GROUP_ORDER = ["control", "slack", "router", "deployer", "publisher", "briefing", "backup"]

PLATFORMS = [
    # name, config keys to ask for, secrets
    ("bluesky", [("handle", "Bluesky handle, for example yourbrand.bsky.social", r"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$")], ["BLUESKY_APP_PASSWORD"]),
    ("mastodon", [("instance_url", "Mastodon server address, for example https://mastodon.example", r"^https://[A-Za-z0-9.-]+(:[0-9]+)?$")], ["MASTODON_ACCESS_TOKEN"]),
    ("discord", [("channel_id", "Channel ID in the fleet's own Discord server", r"^[0-9]{15,25}$")], ["DISCORD_PUBLISHER_BOT_TOKEN"]),
    ("telegram", [("channel_id", "Channel name, for example @yourchannel", r"^(@[A-Za-z0-9_]{5,}|-?[0-9]{5,})$")], ["TELEGRAM_PUBLISHER_BOT_TOKEN"]),
    ("devto", [], ["DEVTO_API_KEY"]),
    ("github", [("repo", "GitHub repository, for example yourorg/yourproduct", r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")], ["GITHUB_PUBLISHER_TOKEN"]),
]


def secret_step(console, name, runner=system.run, again=False):
    """Ask for one secret. Returns True when the secret is set after the step."""
    entry = secrets.get_secret(name)
    if secrets.is_set(name) and not again:
        console.say("%s is already set. Skipped. Use fleetctl secret set %s to replace it." % (name, name))
        return True
    console.say("")
    console.say("%s: %s" % (name, entry["purpose"]))
    console.say("  Make or find it here: %s" % entry["get_it"])
    for _try in range(3):
        value = console.ask_secret("  Paste the value, or press Enter to skip: ")
        if not value:
            console.say("  Skipped.")
            return False
        ok, why = secrets.check_format(name, value)
        if ok:
            break
        console.say("  Not accepted: %s." % why)
    else:
        console.say("  Skipped after three tries.")
        return False
    secrets.set_value(name, value)
    console.say("  Saved (mode 600).")
    for note in secretcmd.after_set(entry, runner):
        console.say("  " + note)
    return True


def ask_valid(console, prompt, pattern, default=None):
    for _try in range(3):
        answer = console.ask(prompt, default)
        if not answer:
            return ""
        if re.match(pattern, answer):
            return answer
        console.say("  That does not look right. Try again, or press Enter to skip.")
    return ""


def set_config(console, key, value, runner=system.run):
    """Write one config key through the same check as `fleetctl config set`."""
    text = value if isinstance(value, str) else str(value).replace("'", "")
    try:
        cfgcmd.set_checked(key, text)
    except config.ConfigError as exc:
        console.say("  Could not set %s: %s" % (key, exc))
        return False
    for note in cfgcmd.apply_change(key, runner):
        console.say("  " + note)
    return True


def add_owner_id(console, platform, runner):
    ids = list(config.get(config.load(), "owner.%s_user_ids" % platform, []) or [])
    if platform == "telegram":
        console.say("  Your numeric Telegram user ID. Send a message to the bot @userinfobot to see it.")
    else:
        console.say("  Your numeric Discord user ID. Turn on Developer Mode, right-click your name, choose Copy User ID.")
    value = ask_valid(console, "  Your %s user ID" % platform, r"^[0-9]{5,20}$")
    if not value:
        return False
    if int(value) not in ids:
        ids.append(int(value))
    return set_config(console, "owner.%s_user_ids" % platform, "[%s]" % ", ".join(str(i) for i in ids), runner)


# ----------------------------------------------------------------------------- groups
def wizard_control(console, runner, again):
    console.say("Control bot. It is your kill switch from the phone. It obeys only your user ID.")
    for platform, secret in (("telegram", "CONTROL_TELEGRAM_BOT_TOKEN"), ("discord", "CONTROL_DISCORD_BOT_TOKEN")):
        if not console.confirm("Use %s for the control bot?" % platform.capitalize(), default=(platform == "telegram")):
            continue
        if not add_owner_id(console, platform, runner):
            console.say("  Skipped %s. The bot needs your ID." % platform)
            continue
        if secret_step(console, secret, runner, again):
            set_config(console, "control.%s.enabled" % platform, "true", runner)
    console.say("Test it: send /status to your bot.")


def wizard_slack(console, runner, again):
    from ops.fleetctl import slackctl
    console.say("Slack. It is the main place where you talk to the fleet. You make two apps in your own workspace: Fleet HQ and Hermes.")
    console.say("  Open https://api.slack.com/apps and choose Create New App, then From a manifest.")
    console.say("  Paste slack/fleet-hq.manifest.json for Fleet HQ and slack/hermes.manifest.json for Hermes. The steps are in docs/SLACK.md.")
    if not console.confirm("Do you have the two apps and their tokens now?", default=False):
        console.say("Skipped. Slack stays WAITING_FOR_KEYS and every message waits in the sink file. Run this step again later.")
        return
    console.say("  Your Slack member ID. Open your profile in Slack, choose the three dots, then Copy member ID.")
    member = ask_valid(console, "  Your Slack member ID", r"^[UW][A-Z0-9]{8,12}$")
    if not member:
        console.say("  Skipped Slack. Fleet HQ needs your member ID, because it answers only you.")
        return
    ids = [str(i) for i in (config.get(config.load(), "owner.slack_user_ids", []) or [])]
    if member not in ids:
        ids.append(member)
    set_config(console, "owner.slack_user_ids", "[%s]" % ", ".join(ids), runner)
    for entry in secrets.all_secrets():
        if entry["group"] == "slack":
            secret_step(console, entry["name"], runner, again)
    if not (secrets.is_set("SLACK_HQ_BOT_TOKEN") and secrets.is_set("SLACK_HQ_APP_TOKEN")):
        console.say("Fleet HQ needs both of its tokens. Run this step again when you have them.")
        return
    if console.confirm("Make the channels now?", default=True):
        code = slackctl.cmd_bootstrap(SimpleNamespace(again=False), console, runner)
        if code == 0 and console.confirm("Send fleet messages to Slack now, and not to the sink file?", default=True):
            set_config(console, "slack.sink_mode", "false", runner)


def wizard_router(console, runner, again):
    from ops.router import apply as ra
    console.say("Model providers. The fleet needs at least one free key. Never add a card or a paid plan (rule R1).")
    for entry in secrets.all_secrets():
        if entry["group"] == "router":
            secret_step(console, entry["name"], runner, again)
    combos = ra.load_combos()
    approved = list(config.get(config.load(), "router.approved_providers", []) or [])
    for pid, spec in combos["providers"].items():
        if pid in approved or not all(secrets.is_set(n) for n in spec["secrets"]):
            continue
        console.say("")
        console.say("Provider %s has its key. Read its data terms: %s" % (pid, spec["terms"]))
        console.say("Approve only if the plan does not train on your prompts (rule 10).")
        if console.confirm("Approve %s? You read the terms." % pid):
            secretcmd.cmd_router_approve(SimpleNamespace(provider=pid, yes=True), console, runner)
    if config.get(config.load(), "router.approved_providers", []):
        secretcmd.cmd_router_apply(SimpleNamespace(), console, runner)


def wizard_deployer(console, runner, again):
    console.say("Deployer. It publishes tested products to the Cloudflare free plan only.")
    console.say("Make a token that can edit Workers and D1 on the free plan. Do not add a payment method.")
    for entry in secrets.all_secrets():
        if entry["group"] == "deployer":
            secret_step(console, entry["name"], runner, again)
    if all(secrets.is_set(e["name"]) for e in secrets.all_secrets() if e["group"] == "deployer"):
        if config.get(config.load(), "deployer.mode") != "live" and console.confirm("Switch the Deployer from dry-run to live now?"):
            set_config(console, "deployer.mode", "live", runner)


def wizard_publisher(console, runner, again):
    console.say("Publisher. It is the only part that holds social tokens. Posts stay in the outbox until you switch it to live.")
    console.say("X and Reddit stay off until their API access exists.")
    for name, asks, names in PLATFORMS:
        if not console.confirm("Turn on %s?" % name):
            continue
        done = True
        for key, prompt, pattern in asks:
            value = ask_valid(console, "  " + prompt, pattern)
            if not value:
                done = False
                break
            done = set_config(console, "publisher.platforms.%s.%s" % (name, key), value, runner) and done
        if not done:
            console.say("  Skipped %s." % name)
            continue
        if all(secret_step(console, n, runner, again) for n in names):
            set_config(console, "publisher.platforms.%s.enabled" % name, "true", runner)
    if config.get(config.load(), "publisher.mode") != "live" and console.confirm("Switch the Publisher from dry-run to live now?"):
        set_config(console, "publisher.mode", "live", runner)


def wizard_briefing(console, runner, again):
    console.say("Daily brief. It goes to Telegram and Discord through the control bot. Email is optional.")
    if not console.confirm("Send the brief by email too?"):
        return
    owner = ask_valid(console, "Your email address", r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    host = ask_valid(console, "Mail server (SMTP host)", r"^[A-Za-z0-9.-]+$")
    port = ask_valid(console, "Mail server port", r"^[0-9]{2,5}$", "587")
    user = console.ask("Mail login name (often your email address)", owner)
    if not (owner and host and port and user):
        console.say("Skipped email.")
        return
    set_config(console, "owner.email", owner, runner)
    set_config(console, "briefing.email.smtp_host", host, runner)
    set_config(console, "briefing.email.smtp_port", port, runner)
    set_config(console, "briefing.email.smtp_user", user, runner)
    set_config(console, "briefing.email.from_address", user, runner)
    if secret_step(console, "SMTP_PASSWORD", runner, again):
        set_config(console, "briefing.email.enabled", "true", runner)


def wizard_backup(console, runner, again):
    console.say("Backup push. The nightly backup can push the repository (it holds no secrets) to a private GitHub repository.")
    if not console.confirm("Set that up now?"):
        return
    remote = ask_valid(console, "Repository address, for example https://github.com/yourorg/agent-fleet.git", r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$")
    if remote and set_config(console, "backup.github_remote", remote, runner):
        secret_step(console, "BACKUP_GITHUB_TOKEN", runner, again)


WIZARDS = {"control": wizard_control, "slack": wizard_slack, "router": wizard_router, "deployer": wizard_deployer,
           "publisher": wizard_publisher, "briefing": wizard_briefing, "backup": wizard_backup}


def cmd_wizard(args, console, runner=system.run):
    group = args.group
    if group == "internal":
        console.say("The installer makes the internal keys. You never type them.")
        return 2
    if group not in WIZARDS:
        console.say("Unknown group. Choose one of: %s" % ", ".join(GROUP_ORDER))
        return 2
    if not console.interactive:
        console.say("The wizard needs a terminal.")
        return 2
    WIZARDS[group](console, runner, getattr(args, "again", False))
    console.say("")
    console.say("Done with group %s. Check the keys with: fleetctl secret check" % group)
    return 0


# ----------------------------------------------------------------------------- setup
def valid_zone(name):
    try:
        zoneinfo.ZoneInfo(name)
        return "/" in name or name == "UTC"
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return False


def owner_step(console, runner):
    console.say("Step 1. About you. The fleet uses this only for the daily brief. It stays on this machine.")
    cfg = config.load()
    name = console.ask("Your name", config.get(cfg, "owner.name", "") if config.get(cfg, "owner.name") != "Your Name" else "")
    if name:
        set_config(console, "owner.name", name, runner)
    for _try in range(3):
        zone = console.ask("Your time zone (IANA name)", config.get(cfg, "owner.timezone", "America/Chicago"))
        if valid_zone(zone):
            if zone != config.get(cfg, "owner.timezone"):
                set_config(console, "owner.timezone", zone, runner)
            break
        console.say("  Unknown time zone. Example: America/Chicago or Europe/Berlin.")


def n8n_step(console):
    """n8n needs no key from you. The installer made it. This step only tells you how to open it."""
    console.say("Step 9. n8n (the workflow engine). The installer made it and it needs no key from you.")
    console.say("  Check it with: fleetctl n8n status")
    console.say("  To open the editor, use an SSH tunnel to 127.0.0.1:5678, or run: sudo fleetctl n8n serve on (private, over Tailscale).")
    console.say("  To read the owner password in a terminal: sudo fleetctl secret show N8N_OWNER_PASSWORD")
    console.say("  Never share the n8n address on the public internet. The steps are in docs/N8N.md.")


def cmd_setup(args, console, runner=system.run):
    from core.killswitch import killswitch as kb
    if not console.interactive:
        console.say("Setup needs a terminal. Run it over SSH. For one group at a time use: fleetctl secret wizard --group NAME")
        return 2
    console.say("agent-fleet setup. Each step is optional. Press Enter to skip a step. Run this again at any time.")
    console.say("Rule R1: spend nothing. Add no payment method anywhere.")
    console.say("")
    owner_step(console, runner)
    steps = [("control", "Step 2. Control bot (your kill switch)"), ("slack", "Step 3. Slack (the main place to talk to the fleet)"),
             ("router", "Step 4. Model provider keys"), ("deployer", "Step 5. Cloudflare token"), ("publisher", "Step 6. Social channels"),
             ("briefing", "Step 7. Daily brief by email"), ("backup", "Step 8. Backup push")]
    for group, title in steps:
        console.say("")
        console.say(title)
        if console.confirm("Do this step now?", default=group in ("control", "router")):
            WIZARDS[group](console, runner, False)
    console.say("")
    n8n_step(console)
    console.say("")
    console.say("Step 10. Tailscale (remote access). Run this yourself: sudo tailscale up")
    console.say("It prints a login link. Open it in your browser. The fleet never logs in for you.")
    console.say("")
    console.say("Final checks")
    for entry in secrets.all_secrets():
        if secrets.is_set(entry["name"]) and not entry.get("auto"):
            state, detail = checks.check_secret(entry["name"], config.load())
            console.say("  %-32s %s %s" % (entry["name"], state, detail))
    ok, why = kb.ready_to_run()
    if ok and console.confirm("The fleet is paused until it has keys. Start it now?", default=True):
        try:
            kb.resume_fleet()
            console.say("The fleet runs.")
        except kb.NotReady as exc:
            console.say(str(exc))
    elif not ok:
        console.say("The fleet stays paused: %s." % why)
    console.say("Run: fleetctl doctor")
    return 0
