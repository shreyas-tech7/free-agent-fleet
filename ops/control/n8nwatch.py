"""The control bot watches n8n, and the workflow health-watch watches the rest. Each one watches the other.

The control bot runs in its own unit outside agent-fleet.target, so it still runs after /stop.
Every minute it asks n8n for /healthz. After 3 misses in a row it tells the owner once. It tells the owner again when n8n is back.
It also reads the heartbeat file that health-watch writes every 5 minutes. A healthy n8n with a heartbeat older than
20 minutes means that the workflow stopped. That is one alert too. See D-040.
"""
import json
import os
import pathlib
import time

from ops.control import unsent
from ops.fleetlib import config, httpc, log, paths

LOG = log.get("n8n-watch")
MISSES_BEFORE_ALERT = 3
HEARTBEAT_STALE_SECONDS = 20 * 60


def heartbeat_file():
    return paths.ops_state_dir() / "n8n" / "files" / "heartbeat" / "health-watch.json"


def expected_workflows():
    """True once bootstrap has published the workflows. Before that, a missing heartbeat is normal."""
    state = paths.ops_state_dir() / "n8n" / "bootstrap-state.json"
    try:
        return bool(json.loads(state.read_text()).get("workflows"))
    except (OSError, ValueError):
        return False


class N8nWatch:
    def __init__(self, cfg, notify, get=None, clock=time.time, heartbeat=None):
        self.cfg, self.notify, self.clock = cfg, notify, clock
        self.url = "http://127.0.0.1:%d/healthz" % config.port(cfg, "n8n")
        self.get = get or (lambda url: httpc.get(url, timeout=5))
        self.heartbeat = pathlib.Path(heartbeat or heartbeat_file())
        self.misses = 0
        self.down_alerted = False
        self.stale_alerted = False
        self.healthy_since = None

    def healthy(self):
        try:
            resp = self.get(self.url)
            return resp.status == 200 and resp.json().get("status") == "ok"
        except (httpc.TransportError, ValueError, AttributeError):
            return False

    def check_once(self):
        """One round. Returns the messages that were sent."""
        sent = []
        now = self.clock()
        if not config.get(self.cfg, "n8n.enabled", True):
            return sent
        if self.healthy():
            self.misses = 0
            self.healthy_since = self.healthy_since or now
            if self.down_alerted:
                self.down_alerted = False
                sent.append("n8n is back.")
            if expected_workflows() and now - self.healthy_since > HEARTBEAT_STALE_SECONDS:
                try:
                    age = now - self.heartbeat.stat().st_mtime
                except OSError:
                    age = None
                if age is None or age > HEARTBEAT_STALE_SECONDS:
                    if not self.stale_alerted:
                        self.stale_alerted = True
                        sent.append("n8n runs, but the health-watch workflow has not run for %s. Run: fleetctl n8n smoke" % (
                            "%d minutes" % (age // 60) if age is not None else "a long time"))
                else:
                    self.stale_alerted = False
        else:
            self.misses += 1
            self.healthy_since = None
            if self.misses >= MISSES_BEFORE_ALERT and not self.down_alerted:
                self.down_alerted = True
                sent.append("n8n is down. It failed %d checks in a row. Run: fleetctl logs n8n" % self.misses)
        for text in sent:
            try:
                self.notify(text)
            except Exception as exc:                  # a broken channel must not stop the watch
                LOG.warn("notify_failed", error=type(exc).__name__)
        return sent

    def loop(self, stop, interval=60):
        while not stop.is_set():
            try:
                self.check_once()
            except Exception as exc:
                LOG.error("watch_failed", error=type(exc).__name__)
            try:
                unsent.deliver(self.notify)            # alerts that n8n could not send. See D-041.
            except Exception as exc:
                LOG.error("unsent_failed", error=type(exc).__name__)
            stop.wait(interval)
