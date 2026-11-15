"""Alerts that n8n could not send. See D-041.

The workflow fleet-error sends each alert to the control bot. When the bot is down, or it refuses the token, the alert
would vanish. So the workflow leaves one small JSON file for each failed alert in the folder n8n/files/unsent.
The control bot reads that folder every minute and sends the text to the owner.

A sent file moves to sent/. A file that is not a valid alert moves to bad/. No file is deleted.
"""
import json
import os
import pathlib
import time

from ops.fleetlib import log, paths

LOG = log.get("unsent")
MAX_PER_ROUND = 20
MAX_BYTES = 8192
MAX_TEXT = 600
_SEEN = set()           # files that this process sent and could not move. They never go out twice in one run.


def unsent_dir():
    return paths.ops_state_dir() / "n8n" / "files" / "unsent"


def _files(folder):
    try:
        found = [p for p in pathlib.Path(folder).iterdir() if p.name.startswith("alert-") and p.suffix == ".json" and p.is_file() and not p.is_symlink()]
    except OSError:
        return []
    return sorted(found)


def waiting(folder=None, now=None):
    """Return (count, age in seconds of the oldest file). The doctor uses it."""
    now = time.time() if now is None else now
    oldest = 0
    files = _files(folder or unsent_dir())
    for path in files:
        try:
            oldest = max(oldest, int(now - path.stat().st_mtime))
        except OSError:
            continue
    return len(files), oldest


def _text_of(path):
    """The text of one file, or None when the file is not a valid alert."""
    try:
        if path.stat().st_size > MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict) or not isinstance(data.get("text"), str) or not data["text"].strip():
        return None
    return " ".join(data["text"].split())[:MAX_TEXT]


def _move(path, sub):
    target = path.parent / sub
    target.mkdir(mode=0o700, exist_ok=True)
    os.replace(str(path), str(target / path.name))


def deliver(notify, folder=None, limit=MAX_PER_ROUND):
    """Send each waiting alert through `notify`. Return the number sent.

    `notify` returns the number of chats that got the text. 0 means that nobody got it, so the file stays.
    None means unknown, and that counts as sent. The round stops at the first send that fails.
    """
    sent = 0
    for path in _files(folder or unsent_dir())[:limit]:
        key = (str(path), path.stat().st_mtime_ns)
        if key in _SEEN:
            continue
        text = _text_of(path)
        if text is None:
            _move(path, "bad")
            LOG.warn("unsent_bad_file", file=path.name)
            continue
        try:
            result = notify("Late alert. " + text)
        except Exception as exc:                  # the channel is down. The next round tries again.
            LOG.warn("unsent_notify_failed", error=type(exc).__name__)
            break
        if result is not None and not result:
            break
        _SEEN.add(key)
        try:
            _move(path, "sent")
        except OSError as exc:
            LOG.error("unsent_move_failed", file=path.name, error=type(exc).__name__)
            break
        sent += 1
    return sent
