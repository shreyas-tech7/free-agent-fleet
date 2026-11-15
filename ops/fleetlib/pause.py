"""Read the pause flag. Every service checks it before it starts a task."""
import json

from . import paths


class Paused(Exception):
    pass


def paused():
    """Return the flag content as a dict when the fleet is paused, else None."""
    flag = paths.paused_flag()
    try:
        text = flag.read_text()
    except FileNotFoundError:
        return None
    except OSError:
        return {"reason": "unreadable flag", "since": ""}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"reason": str(data)}
    except ValueError:
        return {"reason": text.strip()[:200] or "paused", "since": ""}


def require_running():
    info = paused()
    if info is not None:
        raise Paused(info.get("reason", "paused"))
