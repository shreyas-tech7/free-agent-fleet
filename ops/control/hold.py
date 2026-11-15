"""A short hold on owner messages. The selftest uses it, so that a test never wakes the owner. See D-043.

While the hold file names a time in the future, the control bot sends no chat message and posts nothing to Slack.
It writes each message to a sink file instead, so the test can read what the bot would have sent.

The hold ends by itself. The time is capped at 15 minutes after the last change of the file,
so a wrong or forged value cannot mute the owner for long. Only root and fleetops can write the file.
"""
import json
import math
import os
import pathlib
import time

from ops.fleetlib import log, paths

LOG = log.get("hold")
MAX_SECONDS = 900
MAX_SINK_BYTES = 1_000_000


def hold_file():
    return paths.ops_state_dir() / "control" / "hold-until"


def sink_file():
    return paths.state_dir() / "outbox" / "owner-hold.jsonl"


def seconds_left(now=None, path=None):
    """How many seconds the hold still lasts. 0 means that there is no hold."""
    path = pathlib.Path(path or hold_file())
    now = time.time() if now is None else now
    try:
        until = float(path.read_text().strip())
        changed = path.stat().st_mtime
    except (OSError, ValueError):
        return 0
    if not math.isfinite(until):               # "nan" and "inf" parse as numbers. Neither is a time.
        return 0
    until = min(until, changed + MAX_SECONDS)
    return max(0, int(until - now))


def active(now=None, path=None):
    return seconds_left(now, path) > 0


def start(seconds=120, now=None, path=None):
    """Hold owner messages for `seconds`, at most 15 minutes. Returns the seconds that were set."""
    path = pathlib.Path(path or hold_file())
    now = time.time() if now is None else now
    seconds = max(1, min(int(seconds), MAX_SECONDS))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("%.0f\n" % (now + seconds))
    path.chmod(0o644)
    return seconds


def stop(path=None):
    try:
        pathlib.Path(path or hold_file()).unlink()
    except FileNotFoundError:
        pass


def record(kind, text, sink=None, now=None):
    """Write one held message to the sink file. A failure to write never stops the bot."""
    sink = pathlib.Path(sink or sink_file())
    now = time.time() if now is None else now
    line = json.dumps({"ts": int(now), "kind": kind, "text": str(text)[:3000]}, sort_keys=True) + "\n"
    try:
        sink.parent.mkdir(parents=True, exist_ok=True)
        if sink.exists() and sink.stat().st_size > MAX_SINK_BYTES:
            os.replace(str(sink), str(sink) + ".1")
        with open(sink, "a") as fh:
            fh.write(line)
        sink.chmod(0o640)
    except OSError as exc:
        LOG.warn("hold_sink_failed", error=type(exc).__name__)
    LOG.info("held", kind=kind, chars=len(str(text)))
