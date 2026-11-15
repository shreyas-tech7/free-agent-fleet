"""Fleet events. A service writes an event file into a spool folder. The event relay sends it to n8n.

Why a spool. n8n restarts and upgrades. An event must not get lost. And no service needs the webhook token.
Only the relay holds FLEET_EVENT_TOKEN. See D-038.

An event is one JSON file:
  {"v": 1, "id", "ts", "source", "type", "severity", "title", "text", "fields"}
type is dotted lower case, for example backup.ok or alert.quota. severity is info, warn, or critical.
Text, title, and fields are cut to a small size and pass the secret filter before they are written.
`emit` never raises. A service must not fail because an event could not be written.
"""
import json
import os
import pathlib
import pwd
import re
import time
import uuid

from . import jsonio, log, paths, redact

LOG = log.get("events")
SEVERITIES = ("info", "warn", "critical")
TYPE_RE = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+){0,3}$")
KEY_RE = re.compile(r"^[a-z0-9_]{1,32}$")
SOURCE_RE = re.compile(r"^[a-z0-9_.-]{0,32}$")
MAX_TEXT, MAX_TITLE, MAX_FIELDS, MAX_VALUE = 1000, 120, 10, 200
MAX_FILE_BYTES = 8000


class EventError(ValueError):
    pass


def spool_dir():
    return paths.ops_state_dir() / "events" / "spool"


def bad_dir():
    return paths.ops_state_dir() / "events" / "bad"


def fleet_spool_dir():
    """The spool of the user fleet (the agents' side). The relay reads it too. fleetops is in its group."""
    return paths.state_dir() / "events" / "spool"


def default_spool():
    try:
        name = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        name = ""
    return fleet_spool_dir() if name == "fleet" else spool_dir()


def _clip(value, limit):
    value = redact.redact_text(str(value))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    return value[:limit]


def build(type_, text, severity="info", source="", title="", fields=None, now=None, event_id=None):
    """A valid event dict, or EventError."""
    if not isinstance(type_, str) or not TYPE_RE.match(type_):
        raise EventError("the event type must look like backup.ok")
    if severity not in SEVERITIES:
        raise EventError("severity must be info, warn, or critical")
    if not isinstance(source, str) or not SOURCE_RE.match(source):
        raise EventError("the source name is not valid")
    clean = {}
    for key, value in (fields or {}).items():
        if len(clean) >= MAX_FIELDS:
            break
        if not KEY_RE.match(str(key)):
            raise EventError("a field name must be lower case letters, digits, and underscores")
        if isinstance(value, bool) or isinstance(value, (int, float)):
            clean[str(key)] = value
        else:
            clean[str(key)] = _clip(value, MAX_VALUE)
    event = {"v": 1, "id": event_id or uuid.uuid4().hex[:16], "ts": int(now if now is not None else time.time()),
             "source": source, "type": type_, "severity": severity, "title": _clip(title, MAX_TITLE),
             "text": _clip(text, MAX_TEXT), "fields": clean}
    if len(json.dumps(event)) > MAX_FILE_BYTES:
        raise EventError("the event is too large")
    return event


def emit(type_, text, severity="info", source="", title="", fields=None, spool=None, now=None):
    """Write one event to the spool. Returns the event id, or None when it could not be written."""
    try:
        event = build(type_, text, severity, source, title, fields, now)
        folder = pathlib.Path(spool or default_spool())
        folder.mkdir(parents=True, exist_ok=True)
        name = "%013d-%s.json" % (int(event["ts"] * 1000), event["id"])
        root = os.geteuid() == 0
        jsonio.atomic_write(folder / name, json.dumps(event, sort_keys=True), mode=0o660,
                            owner="fleetops" if root else None, group="fleetops" if root else None)
        return event["id"]
    except (EventError, OSError) as exc:
        LOG.warn("event_not_written", type=str(type_)[:40], error=type(exc).__name__)
        return None
