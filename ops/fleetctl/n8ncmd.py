"""fleetctl n8n ... Prompt 3, Phase 1. n8n does the work that needs no model.

  status      what runs, the version, the workflows, the last backup, the event spool
  prepare     folders, secrets, the owner hash, the compose .env, the pinned image
  bootstrap   the API key, the credentials, and the workflows. Safe to run twice.
  smoke       the checks of Phase 1. With --restart it also restarts n8n and checks the workflows again.
  backup      a backup now: workflows, encrypted credentials, and a safe database copy. The newest 7 stay.
  restore-test  start a throwaway n8n from the newest backup, check it, and remove it
  upgrade VERSION  move to a newer 2.x release with a backup, smoke checks, and a rollback
  check       read the n8n releases and advisories. With --migration, read the Migration Report.
  serve on|off  owner access over Tailscale (never funnel)
  mcp-env     write the env file of the n8n-mcp helper for Claude Code
  selftest    live checks of the MCP tools, the event webhook, the error workflow, the unsent-alert fallback, and the injection screen
"""
import datetime
import json

from ops.fleetctl import system
from ops.fleetlib import config, jsonio, paths, secrets


def cmd_status(args, console, runner=system.run):
    from ops.n8n import bootstrap, prepare
    from ops.n8n import client as nclient
    cfg = config.load()
    unit = system.unit_state("agent-fleet-n8n", runner)
    c = nclient.Client("http://127.0.0.1:%d" % config.port(cfg, "n8n"))
    pins = prepare.read_pins()
    lines = ["n8n: unit %s, %s" % (unit, "healthy" if c.healthy() else "not answering"), "pinned: %s" % pins["N8N_VERSION"]]
    key = secrets.value_of("N8N_API_KEY")
    if key and c.healthy():
        try:
            flows = c.workflows(key)
            lines.append("workflows: %d of %d published" % (sum(1 for w in flows if w.get("active")), len(flows)))
        except nclient.N8nError as exc:
            lines.append("workflows: %s" % nclient.secret_free(exc))
    else:
        lines.append("workflows: no API key yet. Run: fleetctl n8n bootstrap")
    status = jsonio.read_json(paths.ops_state_dir() / "events" / "status.json", {}) or {}
    lines.append("events: %s waiting, %s sent, %s dropped" % (status.get("depth", "?"), status.get("sent", "?"), status.get("dropped", "?")))
    backups = sorted((prepare.run_dir() / "backups").glob("n8n-*")) if (prepare.run_dir() / "backups").exists() else []
    lines.append("backup: %s" % (backups[-1].name if backups else "none yet"))
    for line in lines:
        console.say(line)
    return 0 if unit == "active" else 1


def cmd_prepare(args, console, runner=system.run):
    from ops.n8n import prepare
    try:
        report = prepare.prepare(runner=runner)
    except (prepare.PrepareError, secrets.SecretError) as exc:
        console.say("Prepare failed: %s" % exc)
        return 1
    console.say("Prepared n8n. Image %s. Owner hash %s. Secrets made: %s." % (report["image"], report["owner_hash"], ", ".join(report["secrets"]) or "none"))
    if report.get("repaired"):
        # The control bot reads its secret file through a bind mount. It must restart to see a new hash. See D-053.
        from ops.fleetctl import secretcmd
        console.say("Repaired the hash of: %s. The token itself did not change." % ", ".join(report["repaired"]))
        for note in secretcmd.restart_units(["agent-fleet-control"], runner):
            console.say(note)
    for w in report["warnings"]:
        console.say("Warning: %s" % w)
    return 0


def cmd_bootstrap(args, console, runner=system.run):
    from ops.n8n import bootstrap
    from ops.n8n import client as nclient
    try:
        report = bootstrap.bootstrap()
    except (nclient.N8nError, secrets.SecretError) as exc:
        console.say("Bootstrap failed: %s" % nclient.secret_free(exc))
        return 1
    console.say("API key: %s." % report["api_key"])
    for name, state in sorted(report["credentials"].items()):
        console.say("  credential %s: %s" % (name, state))
    for name, state in sorted(report["workflows"].items()):
        console.say("  workflow %s: %s" % (name, state))
    console.say("Published: %s." % (", ".join(report["published"]) or "nothing new"))
    return 0


def in_brief_window(now=None):
    """06:40 to 07:10 in the owner's zone. The brief must not meet a restart."""
    import zoneinfo
    cfg = config.load()
    zone = zoneinfo.ZoneInfo(config.get(cfg, "owner.timezone", "America/Chicago"))
    t = (now or datetime.datetime.now(zone)).astimezone(zone)
    return 6 * 60 + 40 <= t.hour * 60 + t.minute <= 7 * 60 + 10


def cmd_smoke(args, console, runner=system.run):
    from ops.n8n import smoke
    if args.restart and in_brief_window():
        console.say("Refused. A restart between 06:40 and 07:10 could meet the daily brief. Try again after 07:10.")
        return 2
    results = smoke.run_all(runner=runner, restart=args.restart)
    for name, ok, detail in results:
        console.say("%-4s %-38s %s" % ("OK" if ok else "FAIL", name, detail))
    bad = [r for r in results if not r[1]]
    console.say("Result: %d of %d checks passed." % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


def cmd_backup(args, console, runner=system.run):
    from ops.n8n import backup
    try:
        out = backup.run_backup(runner)
    except backup.BackupError as exc:
        console.say("Backup failed: %s" % exc)
        return 1
    console.say("Backup %s: %d workflows and %d credentials (encrypted). Removed: %s." % (out["backup"], out["workflows"], out["credentials"], ", ".join(out["removed"]) or "none"))
    return 0


def cmd_restore_test(args, console, runner=system.run):
    from ops.n8n import backup
    try:
        out = backup.restore_test(runner)
    except backup.BackupError as exc:
        console.say("Restore test failed: %s" % exc)
        return 1
    console.say("Restored %s into a throwaway n8n: %d of %d workflows (%d published), %d credentials, decrypt %s." % (
        out["backup"], out["workflows"], out["expected_workflows"], out["active"], out["credentials"], "ok" if out["decrypts"] else "FAILED"))
    console.say("The throwaway container and its folder are gone.")
    return 0 if out["ok"] else 1


def cmd_upgrade(args, console, runner=system.run):
    from ops.n8n import upgrade
    try:
        out = upgrade.run_upgrade(args.version, allow_young=args.allow_young, runner=runner, brief_window=in_brief_window)
    except upgrade.UpgradeError as exc:
        console.say("Not upgraded: %s" % exc)
        return 1
    if out["status"] == "upgraded":
        console.say("n8n %s now runs (was %s). Backup %s." % (out["to"], out["from"], out["backup"]))
        console.say("Next: put N8N_VERSION=%s and N8N_IMAGE=%s in vendor/pins.env through the merger." % (out["to"], out["image"]))
        return 0
    console.say("The upgrade failed at: %s. Status: %s. The old version is back." % (", ".join(out["failed"]), out["status"]))
    return 1


def cmd_check(args, console, runner=system.run):
    from ops.n8n import prepare, watch
    from ops.n8n import client as nclient
    env = prepare.read_env_file(prepare.run_dir() / ".env")
    if args.migration:
        cfg = config.load()
        out = watch.check_migration(nclient.Client("http://127.0.0.1:%d" % config.port(cfg, "n8n")), env.get("N8N_OWNER_EMAIL", ""), secrets.value_of("N8N_OWNER_PASSWORD"))
    else:
        out = watch.check_releases(env.get("N8N_VERSION", ""))
    console.say(json.dumps(out, sort_keys=True))
    return 0 if out.get("status") == "ok" else 1


def cmd_serve(args, console, runner=system.run):
    from ops.n8n import serve
    try:
        if args.mode == "on":
            if in_brief_window():
                console.say("Refused. A restart between 06:40 and 07:10 could meet the daily brief. Try again after 07:10.")
                return 2
            url = serve.serve_on(runner)
            runner(["systemctl", "restart", "agent-fleet-n8n.service"], timeout=240)
            console.say("n8n is on your tailnet only: %s" % url)
            console.say("Never use tailscale funnel. It would open n8n to the internet.")
        else:
            serve.serve_off(runner)
            runner(["systemctl", "restart", "agent-fleet-n8n.service"], timeout=240)
            console.say("The tailnet address is off. Use an SSH tunnel: ssh -L 5678:127.0.0.1:5678 USER@MACHINE")
    except serve.ServeError as exc:
        console.say("Not changed: %s" % exc)
        return 1
    return 0


def cmd_mcp_env(args, console, runner=system.run):
    from ops.n8n import mcpenv
    try:
        path = mcpenv.write()
    except mcpenv.McpEnvError as exc:
        console.say("Not written: %s" % exc)
        return 1
    console.say("Wrote %s (mode 600). It holds the n8n API key. Never paste it into a chat." % path)
    return 0


def cmd_selftest(args, console, runner=system.run):
    from ops.n8n import selftest
    parts = {"all": ("live", "clone"), "live": ("live",), "clone": ("clone",)}[args.part]
    console.say("Owner messages are held while this test runs, so it cannot wake you. The hold ends when the test ends.")
    results = selftest.Selftest(runner=runner, hermes=not args.no_hermes).run(parts)
    for name, status, detail in results:
        console.say("%-4s %-42s %s" % (status, name, detail))
    bad = [r for r in results if r[1] == selftest.FAIL]
    warn = [r for r in results if r[1] == selftest.WARN]
    console.say("Result: %d of %d checks passed%s." % (len(results) - len(bad) - len(warn), len(results), (", %d warnings" % len(warn)) if warn else ""))
    return 1 if bad else 0


def dispatch(args, console, runner=system.run):
    table = {"status": cmd_status, "prepare": cmd_prepare, "bootstrap": cmd_bootstrap, "smoke": cmd_smoke, "backup": cmd_backup,
             "restore-test": cmd_restore_test, "upgrade": cmd_upgrade, "check": cmd_check, "serve": cmd_serve, "mcp-env": cmd_mcp_env,
             "selftest": cmd_selftest}
    fn = table.get(args.n8n_command)
    if not fn:
        console.say("Choose one of: %s" % ", ".join(sorted(table)))
        return 2
    return fn(args, console, runner)
