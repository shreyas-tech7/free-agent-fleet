"""Commands of the control bot. The bot answers only the owner's user IDs. It is the kill switch."""
import json
import pathlib
import subprocess
import time

from ops.control import approvals
from ops.fleetlib import config, jsonio, paths, pause

UNITS = ["agent-fleet-router", "agent-fleet-guard", "agent-fleet-searxng", "agent-fleet-reader", "agent-fleet-publisher",
         "agent-fleet-deployer", "agent-fleet-gateway", "agent-fleet-n8n"]

HELP = ("Commands:\n/status  what is running\n/pause  stop new work. Running work finishes.\n/resume  start work again\n"
        "/stop  stop every fleet service\n/start  start every fleet service\n/brief  send the latest daily brief\n"
        "/brief now  make a new brief now\n/approvals  list the open approvals\n/approve ID  approve one item\n/reject ID  reject one item")

SUDO = {
    "pause": ["sudo", "-n", "/usr/local/bin/fleetctl", "pause"],
    "resume": ["sudo", "-n", "/usr/local/bin/fleetctl", "resume"],
    "stop": ["sudo", "-n", "/usr/bin/systemctl", "stop", "agent-fleet.target"],
    "start": ["sudo", "-n", "/usr/bin/systemctl", "start", "agent-fleet.target"],
}


def run_command(argv, timeout=120):
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)  # nosemgrep: dangerous-subprocess-use-audit
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, type(exc).__name__


class Controller:
    def __init__(self, cfg, run=run_command, clock=time.time):
        self.cfg = cfg
        self.run = run
        self.clock = clock

    def owner_ids(self, platform):
        return {str(i) for i in (config.get(self.cfg, "owner.%s_user_ids" % platform, []) or [])}

    def status_data(self):
        """The state of the fleet as data. `status` writes the same facts as text. The action API returns this dict."""
        info = pause.paused()
        services = {}
        for unit in UNITS:
            _rc, out = self.run(["systemctl", "is-active", unit + ".service"])
            services[unit.replace("agent-fleet-", "")] = out.splitlines()[0] if out else "unknown"
        budget = jsonio.read_json(paths.budget_file(), {}) or {}
        try:
            outbox = len(list((paths.state_dir() / "outbox").glob("*.json")))
        except OSError:
            outbox = 0
        return {"paused": ({"reason": info.get("reason", "?"), "since": info.get("since", "")} if info else None),
                "services": services,
                "publisher": config.get(self.cfg, "publisher.mode", "?"), "deployer": config.get(self.cfg, "deployer.mode", "?"),
                "budget_slowdown": bool(budget.get("slowdown")), "budget_hot": list(budget.get("hot", [])), "outbox": outbox}

    def status(self):
        d = self.status_data()
        lines = ["PAUSED: %s (%s)" % (d["paused"]["reason"], d["paused"]["since"]) if d["paused"] else "Running (not paused)"]
        lines.append("Services: " + ", ".join("%s %s" % (k, v) for k, v in d["services"].items()))
        lines.append("Publisher: %s. Deployer: %s." % (d["publisher"], d["deployer"]))
        if d["budget_slowdown"]:
            lines.append("Budget: slowing down (%s)." % ", ".join(d["budget_hot"]))
        lines.append("Outbox: %d dry-run posts." % d["outbox"])
        return "\n".join(lines)

    def brief_now(self):
        """Ask the briefing unit for a new brief now. True when the request file was written."""
        trigger = paths.ops_state_dir() / "briefing" / "NOW"
        try:
            trigger.parent.mkdir(parents=True, exist_ok=True)
            trigger.write_text(str(int(self.clock())))
        except OSError:
            return False
        return True

    def approvals_text(self):
        waiting = approvals.pending(now=self.clock())
        if not waiting:
            return "No approval is open."
        return "\n".join(["Open approvals:"] + ["%s  %s %s: %s" % (r["id"], r["kind"], r["ref"], r["summary"][:100]) for r in waiting[:10]])

    def decide(self, platform, user_id, approve, approval_id):
        """The owner decides one approval. See D-039 and D-050."""
        if not approval_id:
            return "Use: /%s ID. Run /approvals to see the IDs." % ("approve" if approve else "reject")
        try:
            record = approvals.decide(approval_id.lower(), approve, "%s:%s" % (platform, user_id), now=self.clock())
        except approvals.ApprovalError as exc:
            return "Not done: %s." % exc
        return "%s %s (%s %s)." % ("Approved" if approve else "Rejected", record["id"], record["kind"], record["ref"])

    def handle(self, platform, user_id, text):
        """Return the reply text, or None when the sender is not the owner."""
        if str(user_id) not in self.owner_ids(platform):
            return None
        parts = (text or "").strip().split()
        if not parts or not parts[0].startswith("/"):
            return HELP
        cmd = parts[0].lower().split("@")[0]
        if cmd == "/help":
            return HELP
        if cmd == "/status":
            return self.status()
        if cmd in ("/pause", "/resume", "/stop", "/start"):
            name = cmd[1:]
            rc, out = self.run(SUDO[name])
            return ("%s: done." % name.capitalize()) if rc == 0 else "%s failed. %s" % (name.capitalize(), out[-200:])
        if cmd == "/approvals":
            return self.approvals_text()
        if cmd in ("/approve", "/reject"):
            return self.decide(platform, user_id, cmd == "/approve", parts[1] if len(parts) == 2 else "")
        if cmd == "/brief":
            if len(parts) > 1 and parts[1].lower() == "now":
                return "A new brief is on its way." if self.brief_now() else "Could not ask for a brief."
            latest = paths.state_dir() / "briefs" / "latest.md"
            try:
                return latest.read_text()
            except OSError:
                return "No brief yet. The next one is at %s." % config.get(self.cfg, "briefing.time", "07:00")
        return HELP
