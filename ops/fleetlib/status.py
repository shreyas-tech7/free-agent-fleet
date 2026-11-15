"""Status values that `fleetctl doctor` and the services report."""
from dataclasses import dataclass

OK = "OK"
WAITING = "WAITING_FOR_KEYS"
DEGRADED = "DEGRADED"
FAIL = "FAIL"
SKIPPED = "SKIPPED"            # the clean-machine test in a container skips what needs systemd or Docker. See D-051.

# Doctor exits 0 when every component is OK, waiting for keys, or skipped.
GOOD = (OK, WAITING, SKIPPED)


@dataclass
class Component:
    name: str
    status: str
    detail: str = ""

    def line(self):
        text = "%-22s %s" % (self.name, self.status)
        return text + ("  " + self.detail if self.detail else "")
