"""fleetctl config set|get|show. A change is checked before it is written, then applied."""
import copy
import json
import pathlib

import yaml

from ops.fleetctl import system
from ops.fleetlib import config, paths

# config key prefix -> units that must restart to read the change
RESTART_MAP = [
    ("publisher.", ["agent-fleet-publisher"]),
    ("deployer.", ["agent-fleet-deployer"]),
    ("guard.", ["agent-fleet-guard"]),
    ("reader.", ["agent-fleet-reader"]),
    ("control.", ["agent-fleet-control"]),
    ("owner.telegram_user_ids", ["agent-fleet-control"]),
    ("owner.discord_user_ids", ["agent-fleet-control"]),
    ("ports.", ["agent-fleet-guard", "agent-fleet-reader", "agent-fleet-publisher", "agent-fleet-deployer", "agent-fleet-control"]),
]
# keys that change a unit file. The units are rendered again from the templates.
RERENDER_KEYS = ("briefing.time", "owner.timezone", "capacity.tier", "ports.router", "ports.searxng")


def would_be(key, text, path=None):
    """The merged config as it would look after the change. Raises ConfigError when the change is not valid."""
    value = config.coerce(key, text)
    overrides = copy.deepcopy(config.load_overrides(path))
    node = overrides
    parts = key.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    merged = config._merge(config.defaults(), overrides)
    problems = config.validate(merged)
    if problems:
        raise config.ConfigError("; ".join(problems))
    return value, merged


def set_checked(key, text, path=None):
    """Check, then write. Returns the stored value."""
    would_be(key, text, path)
    return config.set_override(key, text, path)


def apply_change(key, runner=system.run):
    """Make a running system follow the change. Returns short notes. Only restarts units that run now."""
    from ops.fleetctl import secretcmd
    notes = []
    if key in RERENDER_KEYS:
        live = paths.live_repo()
        rc, out = runner(["bash", str(live / "install" / "60-units.sh")], timeout=300)
        notes.append("units rendered again" if rc == 0 else "could not render the units: %s" % out[-160:])
        if key == "owner.timezone":
            value = config.get(config.load(), "owner.timezone", "UTC")
            runner(["hermes", "config", "set", "timezone", value], user="fleet", timeout=60)
            notes.append("Hermes time zone set")
    if key == "limits.max_concurrent_workers":
        value = str(config.get(config.load(), key, 1))
        rc, _out = runner(["hermes", "config", "set", "kanban.max_in_progress", value], user="fleet", timeout=60)
        notes.append("Hermes kanban.max_in_progress set" if rc == 0 else "could not set kanban.max_in_progress")
    units = []
    for prefix, targets in RESTART_MAP:
        if key == prefix or (prefix.endswith(".") and key.startswith(prefix)):
            units += [u for u in targets if u not in units]
    if units:
        notes += secretcmd.restart_units(units, runner)
    if key.startswith("router.") and key != "router.base_url":
        notes.append("Run: fleetctl router apply")
    return notes


def cmd_set(args, console, runner=system.run):
    try:
        value, _merged = would_be(args.key, args.value)
    except config.ConfigError as exc:
        console.say("Not changed: %s" % exc)
        return 2
    try:
        config.set_override(args.key, args.value)
    except (config.ConfigError, OSError) as exc:
        console.say("Not changed: %s" % exc)
        return 1
    console.say("%s = %s" % (args.key, json.dumps(value)))
    for note in apply_change(args.key, runner):
        console.say(note)
    return 0


def cmd_get(args, console):
    cfg = config.load()
    value = config.get(cfg, args.key, None)
    if value is None:
        console.say("No such key: %s" % args.key)
        return 1
    console.say(json.dumps(value) if isinstance(value, (dict, list)) else str(value))
    return 0


def cmd_show(args, console):
    """Show the keys that differ from the defaults."""
    console.say(yaml.safe_dump(config.load_overrides(), sort_keys=False).rstrip() or "(no changes from the defaults)")
    return 0
