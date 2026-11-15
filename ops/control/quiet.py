"""Quiet hours. See D-052.

From slack.quiet_hours.start to slack.quiet_hours.end in the owner's time zone, only critical messages go out live.
Everything else waits in a queue. The daily brief reads the queue and clears it, so nothing is lost.
"""
import datetime
import json
import pathlib
import time
import zoneinfo

from ops.fleetlib import config, log, paths

LOG = log.get("quiet")
SEVERITIES = ("info", "warn", "critical")
MAX_QUEUE = 200


def _clock():
    return time.time()


def queue_file():
    return paths.state_dir() / "outbox" / "quiet-queue.jsonl"


def _minutes(text):
    hour, minute = str(text).split(":")
    return int(hour) * 60 + int(minute)


def is_quiet(cfg, now=None):
    """True inside the quiet window. The window may cross midnight. The end time is not quiet."""
    quiet = config.get(cfg, "slack.quiet_hours", {}) or {}
    try:
        start, end = _minutes(quiet.get("start", "22:00")), _minutes(quiet.get("end", "07:00"))
        zone = zoneinfo.ZoneInfo(config.get(cfg, "owner.timezone", "America/Chicago"))
    except (ValueError, zoneinfo.ZoneInfoNotFoundError):
        return False
    moment = datetime.datetime.fromtimestamp(_clock() if now is None else now, zone)
    minute = moment.hour * 60 + moment.minute
    if start == end:
        return False
    return start <= minute < end if start < end else (minute >= start or minute < end)


def holds(cfg, severity, now=None):
    """True when a message of this severity must wait for the brief."""
    return severity != "critical" and is_quiet(cfg, now)


def queue(text, channel="hq", severity="info", now=None, path=None):
    """Add one message to the queue. The queue keeps the newest 200."""
    path = pathlib.Path(path or queue_file())
    now = int(_clock() if now is None else now)
    row = json.dumps({"ts": now, "channel": str(channel)[:20], "severity": severity, "text": str(text)[:1000]}, sort_keys=True)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = path.read_text().splitlines() if path.exists() else []
        path.write_text("\n".join((lines + [row])[-MAX_QUEUE:]) + "\n")
        path.chmod(0o640)
    except OSError as exc:
        LOG.warn("quiet_queue_failed", error=type(exc).__name__)
        return False
    return True


def peek(path=None):
    path = pathlib.Path(path or queue_file())
    try:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("text"), str)]


def drain(path=None, now=None):
    """Return the queued messages and archive the file. Nothing is deleted."""
    path = pathlib.Path(path or queue_file())
    rows = peek(path)
    if rows:
        stamp = datetime.datetime.fromtimestamp(_clock() if now is None else now, datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        try:
            path.rename(path.with_name("quiet-queue-%s.jsonl" % stamp))
        except OSError as exc:
            LOG.warn("quiet_archive_failed", error=type(exc).__name__)
    return rows
