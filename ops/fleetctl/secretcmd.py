"""fleetctl secret ..., fleetctl router ...

Rules:
- A secret value is read from a hidden prompt or from standard input. It is never an argument.
- No message, log line, or exception holds a value.
- Each secret goes to the one file that config/secrets.yaml names, with mode 600.
"""
import sys

from ops.fleetctl import checks, system
from ops.fleetlib import config, secrets, tokens

# After a change, these units must restart to read the new value.
RESTART_AFTER = {
    "publisher": ["agent-fleet-publisher"],
    "deployer": ["agent-fleet-deployer"],
    "control": ["agent-fleet-control"],
    "slack": ["agent-fleet-slack"],      # the Hermes tokens are read by the gateway. after_set tells the owner how to restart it
    "briefing": [],
    "backup": [],
    "router": [],            # the router apply step pushes provider keys through the router API
}
# Auto secrets that must never rotate.
NEVER_ROTATE = ("API_KEY_SECRET", "STORAGE_ENCRYPTION_KEY", "N8N_ENCRYPTION_KEY")


def restart_units(units, runner=system.run):
    """Restart only units that run now. A unit that the owner stopped stays stopped."""
    notes = []
    for unit in units:
        if system.unit_state(unit, runner) == "active":
            rc, out = runner(["systemctl", "restart", unit + ".service"], timeout=240)
            notes.append("%s restarted" % unit if rc == 0 else "%s did not restart: %s" % (unit, out[-120:]))
        else:
            notes.append("%s is not running (not started)" % unit)
    return notes


def _service_group(entry):
    return entry["group"]


def cmd_list(args, console, runner=system.run):
    rows = secrets.status_rows(getattr(args, "group", None))
    if not rows:
        console.say("No secrets in that group. Groups: %s" % ", ".join(secrets.registry()["groups"]))
        return 2
    console.say("%-32s %-10s %-10s %s" % ("NAME", "GROUP", "SERVICE", "STATE"))
    for r in rows:
        state = {True: "set", False: "not set", None: "unreadable (use sudo)"}[r["set"]]
        if r["auto"] and r["set"]:
            state = "set (made by the installer)"
        console.say("%-32s %-10s %-10s %s" % (r["name"], r["group"], r["service"], state))
    return 0


def read_value(name, console, from_stdin):
    if from_stdin:
        line = sys.stdin.readline()
        return line.rstrip("\r\n")
    return console.ask_secret("Value for %s (the typing is hidden): " % name)


def cmd_set(args, console, runner=system.run):
    try:
        entry = secrets.get_secret(args.name)
    except secrets.SecretError as exc:
        console.say(str(exc))
        return 2
    if entry.get("auto"):
        console.say("%s is made by the installer. To make a new value, run: fleetctl secret rotate %s" % (args.name, args.name))
        return 2
    if not args.stdin and not console.interactive:
        console.say("This needs a terminal, so the value stays hidden. To pipe a value, use --stdin.")
        return 2
    if args.stdin and console.interactive:
        # On a terminal, a line read from standard input is echoed by the terminal. Refuse it.
        console.say("Not saved: with --stdin on a terminal the typing would show. Run it without --stdin to type the value hidden.")
        return 2
    value = read_value(args.name, console, args.stdin)
    ok, why = secrets.check_format(args.name, value)
    if not ok:
        console.say("Not saved: %s." % why)
        console.say("Where to get it: %s" % entry["get_it"])
        return 2
    try:
        path = secrets.set_value(args.name, value)
    except secrets.SecretError as exc:
        console.say("Not saved: %s" % exc)
        return 1
    console.say("Saved %s to %s (owner %s, mode 600)." % (args.name, path, entry["owner"]))
    for note in after_set(entry, runner):
        console.say(note)
    return 0


def cmd_show(args, console, runner=system.run):
    """Show one secret value on the terminal. It works only in an interactive terminal.

    Input and output must both be a terminal. A pipe, a file, a log, and an agent shell are refused.
    The value is hidden again when the owner presses Enter. Run it with sudo for files that the
    owner cannot read: sudo fleetctl secret show NAME
    """
    try:
        secrets.get_secret(args.name)
    except secrets.SecretError as exc:
        console.say(str(exc))
        return 2
    if not (console.interactive and console.output_is_tty):
        console.say("Refused. This command works only in an interactive terminal. "
                    "The value never goes to a pipe, a file, or a log.")
        return 2
    path = secrets.secret_path(args.name)
    try:
        text = path.read_text()
    except FileNotFoundError:
        console.say("%s is not set." % args.name)
        return 1
    except PermissionError:
        console.say("This user cannot read the file. Run: sudo fleetctl secret show %s" % args.name)
        return 1
    value = secrets.parse_env(text).get(args.name, "")
    if not value:
        console.say("%s is not set." % args.name)
        return 1
    header = "%s (shown on this terminal only):" % args.name
    console.say(header)
    console.say(value)
    console.ask("Press Enter to hide it")
    cols = console.columns()
    lines = -(-len(header) // cols) + -(-len(value) // cols) + 1
    console.erase_lines(lines)
    return 0


def after_set(entry, runner=system.run):
    group = entry["group"]
    notes = []
    if group == "router":
        notes.append("Next: read the data terms (%s), then run: fleetctl router approve <provider>" % entry.get("terms", "see the provider site"))
    if entry["name"].startswith("SLACK_HERMES_"):
        notes.append("Hermes reads this at start. When both Hermes tokens are set, run: fleetctl pause, then wait until no worker runs, "
                     "then: sudo systemctl restart agent-fleet-gateway, then: fleetctl resume")
    notes += restart_units(RESTART_AFTER.get(group, []), runner)
    return notes


def cmd_check(args, console, runner=system.run, http=None):
    cfg = config.load()
    names = args.names or [e["name"] for e in secrets.all_secrets()]
    bad = 0
    shown = 0
    for name in names:
        try:
            state, detail = checks.check_secret(name, cfg, **({"http": http} if http else {}))
        except secrets.SecretError as exc:
            console.say(str(exc))
            return 2
        if state == "not set" and not args.names:
            continue                                   # a full run lists only keys that exist
        shown += 1
        console.say("%-32s %-12s %s" % (name, state, detail))
        if state in ("failed", "unreachable"):
            bad += 1
    if shown == 0:
        console.say("No key is set yet. Nothing to check.")
    return 1 if bad else 0


def cmd_rotate(args, console, runner=system.run):
    try:
        entry = secrets.get_secret(args.name)
    except secrets.SecretError as exc:
        console.say(str(exc))
        return 2
    name = args.name
    if not entry.get("auto"):
        console.say("%s is a key from a provider. Make a new one on their site (%s), then run: fleetctl secret set %s" % (name, entry["revoke"], name))
        return 2
    if name in NEVER_ROTATE:
        console.say("Do not rotate %s. %s" % (name, entry["revoke"]))
        return 2
    if name in N8N_SECRETS:
        return rotate_n8n(name, entry, console, runner)
    notes = []
    if entry["auto"] == "router_key":
        secrets.unset_value(name)
        console.say("Old value removed. Asking the router for a new restricted key.")
        rc = cmd_router_apply(args, console, runner)
        if rc != 0:
            return rc
        units = ["agent-fleet-gateway"] if entry["service"] == "hermes" else []
    elif name in tokens.DERIVED:
        tokens.rotate(name)
        units = {"PUBLISHER_API_TOKEN": ["agent-fleet-publisher", "agent-fleet-gateway"],
                 "PUBLISHER_REPLY_TOKEN": ["agent-fleet-publisher", "agent-fleet-gateway"],
                 "DEPLOYER_API_TOKEN": ["agent-fleet-deployer", "agent-fleet-gateway"],
                 "CONTROL_NOTIFY_TOKEN": ["agent-fleet-control", "agent-fleet-briefing"]}.get(name, [])
    else:
        from ops.fleetlib import autosecrets
        secrets.set_value(name, autosecrets.generate(entry))
        units = {"JWT_SECRET": ["agent-fleet-router"], "INITIAL_PASSWORD": ["agent-fleet-router"],
                 "SEARXNG_SECRET": ["agent-fleet-searxng"]}.get(name, [])
    console.say("Rotated %s." % name)
    notes += restart_units([u for u in units if not u.endswith("briefing")], runner)
    for n in notes:
        console.say(n)
    return 0


# n8n holds a credential with the same value as each of these tokens. The units restart so that they read the new value.
# The control bot holds the hash of the action token in a bind-mounted file. It sees a new hash only after a restart. See D-053.
N8N_TOKENS = {"FLEET_EVENT_TOKEN": ["agent-fleet-events"], "FLEET_ACTION_TOKEN": ["agent-fleet-control"], "N8N_TOOLS_TOKEN": ["agent-fleet-gateway"]}
N8N_OWNER_SECRETS = ("N8N_OWNER_PASSWORD", "N8N_INSTANCE_OWNER_PASSWORD_HASH")
N8N_SECRETS = tuple(N8N_TOKENS) + N8N_OWNER_SECRETS + ("N8N_API_KEY",)


def rotate_n8n(name, entry, console, runner=system.run):
    """Rotate one of the n8n secrets. The generic path would break n8n here: a hash and an API key are not random tokens."""
    from ops.fleetctl import n8ncmd
    from ops.fleetlib import autosecrets
    from ops.n8n import bootstrap, prepare
    from ops.n8n import client as nclient
    units = list(N8N_TOKENS.get(name, []))
    if name in N8N_OWNER_SECRETS:
        units = ["agent-fleet-n8n"]                    # n8n reads the owner hash from its environment at start
    if units and n8ncmd.in_brief_window():
        console.say("Refused. A restart between 06:40 and 07:10 could meet the daily brief. Try again after 07:10.")
        return 2
    sync = name in N8N_TOKENS or name == "N8N_API_KEY"
    try:
        if name in N8N_OWNER_SECRETS:
            secrets.set_value("N8N_OWNER_PASSWORD", autosecrets.generate(secrets.get_secret("N8N_OWNER_PASSWORD")))
            image = prepare.read_env_file(prepare.run_dir() / ".env").get("N8N_IMAGE", "")
            prepare.ensure_owner_hash(image, runner)
            console.say("Made a new owner password and its hash. Read it with: sudo fleetctl secret show N8N_OWNER_PASSWORD")
        elif name == "N8N_API_KEY":
            secrets.unset_value(name)
            console.say("Old key removed. Making a new one.")
        elif name in tokens.DERIVED:
            tokens.rotate(name)
        else:
            secrets.set_value(name, autosecrets.generate(entry))
    except (prepare.PrepareError, secrets.SecretError) as exc:
        console.say("Not rotated: %s" % exc)
        return 1
    console.say("Rotated %s." % name)
    for note in restart_units(units, runner):
        console.say(note)
    if sync:
        try:
            report = bootstrap.bootstrap()
            console.say("n8n now holds the new value (API key %s, credentials %s)." % (report["api_key"], ", ".join(
                "%s %s" % kv for kv in sorted(report["credentials"].items()))))
        except (nclient.N8nError, secrets.SecretError) as exc:
            console.say("The n8n credential was not updated: %s. Run: sudo fleetctl n8n bootstrap" % nclient.secret_free(exc))
            return 1
    return 0


def cmd_router_apply(args, console, runner=system.run, apply_main=None):
    """Push provider keys, policy settings, combos, and client keys into the router.

    It runs as root, because it writes the local client key into the files of the user fleet.
    """
    import contextlib
    import io
    from ops.router import apply as ra
    apply_main = apply_main or ra.main
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            apply_main()
    except ra.RouterError as exc:
        console.say("The router apply step failed: %s" % exc)
        return 1
    except (OSError, secrets.SecretError) as exc:
        console.say("The router apply step failed: %s" % type(exc).__name__)
        return 1
    console.say("Router settings applied.")
    for line in _summary(buf.getvalue()):
        console.say(line)
    return 0


def _summary(out):
    import json
    try:
        data = json.loads(out)
    except ValueError:
        return []
    lines = []
    for section in ("providers", "combos", "keys"):
        for k, v in sorted((data.get(section) or {}).items()):
            lines.append("  %s %s: %s" % (section[:-1], k, v))
    for w in data.get("warnings", []):
        lines.append("  warning: %s" % w)
    return lines


def cmd_router_show(args, console, runner=system.run, router=None):
    """Show what the router holds. Read only. It prints names and model ids, never a key."""
    from ops.router import apply as ra
    cfg = config.load()
    if router is None:
        router = ra.Router("http://127.0.0.1:%d" % config.port(cfg, "router"), secrets.value_of("INITIAL_PASSWORD"))
    try:
        router.login()
    except ra.RouterError as exc:
        console.say("Cannot read the router: %s" % exc)
        return 1
    status, data, _ = router.req("GET", "/api/providers")
    conns = (data or {}).get("connections", []) if status == 200 else []
    console.say("Connected providers: %s" % (", ".join(sorted({str(c.get("provider")) for c in conns})) or "none"))
    status, data, _ = router.req("GET", "/api/combos")
    for combo in sorted((data or {}).get("combos", []) if status == 200 else [], key=lambda c: str(c.get("name"))):
        models = [m.get("model") if isinstance(m, dict) else m for m in combo.get("models", [])]
        console.say("combo %s (%d):" % (combo.get("name"), len(models)))
        for m in models:
            console.say("    %s" % m)
    if getattr(args, "offered", False):
        for conn in sorted(conns, key=lambda c: str(c.get("provider"))):
            st, models, _r = router.req("GET", "/api/providers/%s/models" % conn.get("id"))
            ids = sorted(ra._offered(models)) if st == 200 else []
            if conn.get("provider") == "openrouter":
                ids = [i for i in ids if i.endswith(":free")]
            console.say("offered by %s (%d):" % (conn.get("provider"), len(ids)))
            for i in ids:
                console.say("    %s" % i)
    return 0


def cmd_router_approve(args, console, runner=system.run):
    from ops.router import apply as ra
    combos = ra.load_combos()
    provider = args.provider
    spec = combos["providers"].get(provider)
    if not spec:
        console.say("Unknown provider. Choose one of: %s" % ", ".join(sorted(combos["providers"])))
        return 2
    cfg = config.load()
    approved = list(config.get(cfg, "router.approved_providers", []) or [])
    if provider in approved:
        console.say("%s is already approved." % provider)
        return 0
    console.say("Provider: %s" % provider)
    console.say("Read the data terms first: %s" % spec["terms"])
    console.say("Approve only a plan that does not train on your prompts. Rule 10 of core/RULES.md says: do not send customer data")
    console.say("to a provider that may train on prompts. Approval is your statement that you read the terms.")
    if not args.yes:
        if not console.interactive:
            console.say("Not approved. Run this in a terminal, or add --yes after you read the terms.")
            return 2
        if console.ask("Type the word approve to continue") != "approve":
            console.say("Not approved.")
            return 1
    config.set_override("router.approved_providers", "[%s]" % ", ".join(approved + [provider]))
    console.say("Approved %s." % provider)
    return cmd_router_apply(args, console, runner)
