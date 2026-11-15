"""The private owner file. See D-046.

The file lives in the private repository as config/owner.yaml. `fleetctl owner apply` copies it to /etc/agent-fleet/owner.yaml.
It holds personal settings: the name, the time zone, the brief time, the quiet hours, and the chat channels.
The public export never includes it. templates/owner.example.yaml shows the shape.

config.load() layers it between the defaults and the live file. The live file wins.
"""
import re
import zoneinfo

import yaml

from . import paths

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CHANNELS = ("slack", "telegram", "discord", "email")
BRIEF_CHANNELS = ("slack", "telegram", "discord", "email")
BACKUPS = ("telegram", "discord")


class OwnerError(ValueError):
    pass


def read(path=None):
    path = path or paths.owner_config()
    try:
        data = yaml.safe_load(open(path).read()) or {}
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        raise OwnerError("cannot read the owner file: %s" % type(exc).__name__) from None
    if not isinstance(data, dict):
        raise OwnerError("the owner file must hold a mapping")
    return data


def validate(data):
    """Return the problems in an owner file, as short sentences. No value of the file is repeated."""
    errors = []
    unknown = sorted(set(data) - {"owner", "brief", "quiet_hours", "channels"})
    if unknown:
        errors.append("unknown sections: %s" % ", ".join(unknown))
    owner = data.get("owner") or {}
    if not str(owner.get("name", "")).strip():
        errors.append("owner.name is empty")
    if owner.get("email") and not EMAIL_RE.match(str(owner["email"])):
        errors.append("owner.email does not look like an address")
    try:
        zoneinfo.ZoneInfo(str(owner.get("timezone", "")))
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        errors.append("owner.timezone is not a time zone name")
    brief = data.get("brief") or {}
    if not TIME_RE.match(str(brief.get("time", ""))):
        errors.append("brief.time must look like 07:00")
    words = brief.get("max_words", 150)
    if not isinstance(words, int) or isinstance(words, bool) or not 30 <= words <= 300:
        errors.append("brief.max_words must be a whole number from 30 to 300")
    for channel in brief.get("channels") or []:
        if channel not in BRIEF_CHANNELS:
            errors.append("brief.channels holds an unknown channel")
    quiet = data.get("quiet_hours") or {}
    for key in ("start", "end"):
        if not TIME_RE.match(str(quiet.get(key, ""))):
            errors.append("quiet_hours.%s must look like 22:00" % key)
    channels = data.get("channels") or {}
    if channels.get("main") not in CHANNELS:
        errors.append("channels.main must be one of: %s" % ", ".join(CHANNELS))
    for backup in channels.get("backups") or []:
        if backup not in BACKUPS or backup == channels.get("main"):
            errors.append("channels.backups must hold telegram or discord, and not the main channel")
    return errors


def to_config(data):
    """The owner file as config keys."""
    out = {}
    owner = data.get("owner") or {}
    if owner:
        out["owner"] = {k: owner[k] for k in ("name", "email", "timezone") if owner.get(k)}
    brief = data.get("brief") or {}
    briefing = {}
    if brief.get("time"):
        briefing["time"] = brief["time"]
    if "max_words" in brief:
        briefing["max_words"] = brief["max_words"]
    if "channels" in brief:
        chosen = set(brief["channels"] or [])
        briefing["telegram"] = "telegram" in chosen
        briefing["discord"] = "discord" in chosen
        briefing["email"] = {"enabled": "email" in chosen}
    if briefing:
        out["briefing"] = briefing
    quiet = data.get("quiet_hours") or {}
    if quiet:
        out["slack"] = {"quiet_hours": {"start": quiet.get("start"), "end": quiet.get("end")}}
    channels = data.get("channels") or {}
    if channels:
        out["channels"] = {k: channels[k] for k in ("main", "backups") if k in channels}
    return out


def overlay(path=None):
    """The owner file as a config overlay. A broken file gives an empty overlay, and `fleetctl owner check` names the problem."""
    try:
        data = read(path)
    except OwnerError:
        return {}
    if not data or validate(data):
        return {}
    return to_config(data)
