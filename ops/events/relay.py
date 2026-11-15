"""The event relay. It sends the event files of the spool to the n8n webhook fleet-event.

Runs as fleetops in its own unit. It is the only part that holds FLEET_EVENT_TOKEN.
- A 2xx answer deletes the file.
- A transport error, a 5xx, a 404 (the workflow is not published yet), or a 401 or 403 keeps the file and tries again later.
  The wait grows from 5 seconds to 5 minutes.
- A 400 answer or a file that does not parse moves to the bad folder. It never blocks the others.
- A file older than events.max_age_hours is dropped and counted. The owner reads the count in the status file.
- The spool holds 500 files at most. The oldest info events go first.
"""
import json
import os
import pathlib
import time

from ops.fleetlib import config, events, httpc, jsonio, log, paths, secrets

LOG = log.get("event-relay")
MAX_PER_RUN = 50
MAX_SPOOL = 500
HEADER = "X-Fleet-Token"


class Relay:
    def __init__(self, url, token, spool=None, bad=None, max_age_hours=24, transport=httpc.request, now=time.time, status_path=None,
                 extra_spools=()):
        self.url, self.token = url, token
        self.spool = pathlib.Path(spool or events.spool_dir())
        self.spools = [self.spool] + [pathlib.Path(p) for p in extra_spools]
        self.bad = pathlib.Path(bad or events.bad_dir())
        self.max_age = float(max_age_hours) * 3600
        self.transport, self.now = transport, now
        self.status_path = pathlib.Path(status_path or (self.spool.parent / "status.json"))
        self.wait = {}                       # file name -> (tries, next try time)
        self.totals = {"sent": 0, "dropped": 0, "bad": 0, "last_ok": 0}

    def _files(self):
        found = []
        for folder in self.spools:
            try:
                found += [p for p in folder.glob("*.json") if p.is_file()]
            except OSError:
                continue
        return sorted(found, key=lambda p: p.name)

    def _quarantine(self, path):
        self.bad.mkdir(parents=True, exist_ok=True)
        try:
            path.rename(self.bad / path.name)
        except OSError:
            path.unlink(missing_ok=True)
        self.totals["bad"] += 1

    def _trim(self, files):
        """Keep the spool small. The oldest info events go first."""
        if len(files) <= MAX_SPOOL:
            return files
        extra = len(files) - MAX_SPOOL
        keep = []
        for path in files:
            if extra > 0:
                try:
                    if json.loads(path.read_text()).get("severity") == "info":
                        path.unlink(missing_ok=True)
                        self.totals["dropped"] += 1
                        extra -= 1
                        continue
                except (OSError, ValueError):
                    pass
            keep.append(path)
        return keep

    def run_once(self):
        now = self.now()
        counts = {"sent": 0, "kept": 0, "dropped": 0, "bad": 0}
        files = self._trim(self._files())
        for path in files[:MAX_PER_RUN]:
            tries, next_try = self.wait.get(path.name, (0, 0))
            if next_try > now:
                counts["kept"] += 1
                continue
            try:
                event = json.loads(path.read_text())
                events.build(event["type"], event["text"], event["severity"], event.get("source", ""), event.get("title", ""),
                             event.get("fields"), now=event["ts"], event_id=event["id"])
            except (OSError, ValueError, KeyError, events.EventError):
                self._quarantine(path)
                counts["bad"] += 1
                continue
            if now - event["ts"] > self.max_age:
                path.unlink(missing_ok=True)
                self.totals["dropped"] += 1
                counts["dropped"] += 1
                LOG.warn("event_dropped", type=event["type"], reason="too old")
                continue
            try:
                resp = self.transport("POST", self.url, headers={HEADER: self.token}, json_body=event, timeout=10)
            except httpc.TransportError:
                resp = None
            if resp is not None and 200 <= resp.status < 300:
                path.unlink(missing_ok=True)
                self.wait.pop(path.name, None)
                self.totals["sent"] += 1
                self.totals["last_ok"] = int(now)
                counts["sent"] += 1
            elif resp is not None and resp.status == 400:
                self._quarantine(path)
                counts["bad"] += 1
            else:
                tries += 1
                self.wait[path.name] = (tries, now + min(300, 5 * 2 ** min(tries - 1, 6)))
                counts["kept"] += 1
        self.wait = {k: v for k, v in self.wait.items() if any((d / k).exists() for d in self.spools)}
        self._write_status(len(self._files()))
        return counts

    def _write_status(self, depth):
        files = self._files()
        oldest = 0
        if files:
            try:
                oldest = max(0, int(self.now() - json.loads(files[0].read_text()).get("ts", self.now())))
            except (OSError, ValueError):
                oldest = 0
        status = dict(self.totals, depth=depth, oldest_seconds=oldest, updated=int(self.now()))
        try:
            owner = "fleetops" if os.geteuid() == 0 else None
            jsonio.write_json(self.status_path, status, mode=0o644, owner=owner, group=owner)
        except OSError:
            pass


def main():
    cfg = config.load()
    url = "http://127.0.0.1:%d%s" % (config.port(cfg, "n8n"), config.get(cfg, "events.webhook_path", "/webhook/fleet-event"))
    token = secrets.value_of("FLEET_EVENT_TOKEN")
    on = bool(config.get(cfg, "events.relay", True)) and bool(config.get(cfg, "n8n.enabled", True))
    relay = Relay(url, token, max_age_hours=config.get(cfg, "events.max_age_hours", 24), extra_spools=[events.fleet_spool_dir()])
    LOG.info("started", relay=on, has_token=bool(token))
    while True:
        if on and token:
            try:
                relay.run_once()
            except Exception as exc:        # one bad run must not stop the relay
                LOG.error("run_failed", error=type(exc).__name__)
        time.sleep(2)


if __name__ == "__main__":
    main()
