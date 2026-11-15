"""JSON line logging with secret redaction. Output goes to stderr and so to the journal."""
import datetime
import json
import sys

from . import redact


class Logger:
    def __init__(self, service, stream=None):
        self.service = service
        self.stream = stream

    def _emit(self, level, event, fields):
        record = {
            "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": level,
            "service": self.service,
            "event": event,
        }
        record.update(fields)
        line = json.dumps(redact.redact_obj(record), default=str, sort_keys=True)
        print(line, file=self.stream or sys.stderr, flush=True)

    def info(self, event, **fields):
        self._emit("info", event, fields)

    def warn(self, event, **fields):
        self._emit("warn", event, fields)

    def error(self, event, **fields):
        self._emit("error", event, fields)


def get(service, stream=None):
    return Logger(service, stream)
