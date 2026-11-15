"""Load, read, and change the live fleet configuration.

The defaults come from config/fleet.example.yaml. The live file at
/etc/agent-fleet/fleet.yaml holds only the keys that the owner changed.
"""
import copy
import os
import pathlib

import yaml

from . import jsonio, paths


class ConfigError(ValueError):
    pass


# Dictionary sections where any key is allowed.
FREE_FORM_PREFIXES = ("router.models", "quota.daily_free_limits", "slack.channels")


def _merge(base, over):
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def defaults():
    return yaml.safe_load(paths.example_config().read_text())


def load_overrides(path=None):
    path = pathlib.Path(path or paths.config_file())
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except FileNotFoundError:
        return {}
    except PermissionError as exc:
        raise ConfigError("cannot read %s. Run this command with sudo." % path) from exc
    if not isinstance(data, dict):
        raise ConfigError("%s must hold a mapping" % path)
    return data


def owner_overlay(path=None):
    """The private owner file as config keys. An empty dictionary when the file is missing or unreadable."""
    from . import owner
    return owner.overlay(path)


def load(path=None):
    """Defaults, then the private owner file, then the live file. The live file wins."""
    return _merge(_merge(defaults(), owner_overlay()), load_overrides(path))


def get(cfg, dotted, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _default_for(dotted):
    """Return (known, default_value) for a dotted key."""
    node = defaults()
    for prefix in FREE_FORM_PREFIXES:
        if dotted == prefix or dotted.startswith(prefix + "."):
            return True, None
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def coerce(dotted, text):
    """Turn the text from the command line into a value of the right type."""
    known, default = _default_for(dotted)
    if not known:
        raise ConfigError("unknown config key: %s" % dotted)
    if isinstance(default, bool):
        low = str(text).strip().lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0"):
            return False
        raise ConfigError("%s needs true or false" % dotted)
    if isinstance(default, int):
        try:
            return int(text)
        except ValueError as exc:
            raise ConfigError("%s needs a whole number" % dotted) from exc
    if isinstance(default, float):
        try:
            return float(text)
        except ValueError as exc:
            raise ConfigError("%s needs a number" % dotted) from exc
    if isinstance(default, str):
        return str(text)
    value = yaml.safe_load(text)
    if isinstance(default, list) and not isinstance(value, list):
        raise ConfigError("%s needs a list. Example: [1, 2]" % dotted)
    if isinstance(default, dict) and not isinstance(value, dict):
        raise ConfigError("%s needs a mapping" % dotted)
    return value


def set_override(dotted, text, path=None):
    """Write one key to the live file. Returns the stored value."""
    value = coerce(dotted, text)
    path = pathlib.Path(path or paths.config_file())
    data = load_overrides(path)
    node = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value
    owner = group = None
    if os.geteuid() == 0:
        owner, group = "root", "fleetops"
    jsonio.atomic_write(path, yaml.safe_dump(data, sort_keys=False), mode=0o640, owner=owner, group=group)
    return value


def validate(cfg):
    """Return a list of problems. An empty list means the config is usable."""
    problems = []
    tz = get(cfg, "owner.timezone", "")
    if not tz or "/" not in tz and tz != "UTC":
        problems.append("owner.timezone must be an IANA name such as America/Chicago")
    if get(cfg, "capacity.tier") not in ("small", "medium", "large"):
        problems.append("capacity.tier must be small, medium, or large")
    if get(cfg, "publisher.mode") not in ("dry-run", "live"):
        problems.append("publisher.mode must be dry-run or live")
    if get(cfg, "deployer.mode") not in ("dry-run", "live"):
        problems.append("deployer.mode must be dry-run or live")
    if get(cfg, "deployer.plan") != "free":
        problems.append("deployer.plan must be free (rule R1)")
    if get(cfg, "router.kind") not in ("omniroute", "litellm"):
        problems.append("router.kind must be omniroute or litellm")
    for name, port in (get(cfg, "ports", {}) or {}).items():
        if not isinstance(port, int) or not 1024 <= port <= 65535:
            problems.append("ports.%s must be a number from 1024 to 65535" % name)
    return problems


def port(cfg, name):
    return int(get(cfg, "ports.%s" % name))


def owner_now(cfg, now=None):
    """The current time in the owner's time zone (owner.timezone). Falls back to UTC for a bad zone name."""
    import datetime
    import zoneinfo
    stamp = datetime.datetime.fromtimestamp(now, datetime.timezone.utc) if now is not None else datetime.datetime.now(datetime.timezone.utc)
    try:
        return stamp.astimezone(zoneinfo.ZoneInfo(get(cfg, "owner.timezone", "UTC") or "UTC"))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        return stamp
