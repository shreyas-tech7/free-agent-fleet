"""fleetctl command line. Plain argparse. No secret ever goes through an argument."""
import argparse
import os
import pathlib
import sys

from ops.fleetctl import system, ui

VERSION = "0.1.0"
INSTALLED = "/usr/local/bin/fleetctl"

# Commands that run without root.
NO_ROOT = {"test", "version", "public", "catalog", None}


def build_parser():
    p = argparse.ArgumentParser(prog="fleetctl", description="Control tool of agent-fleet. Run `fleetctl COMMAND --help` for details.")
    p.add_argument("--version", action="store_true", help="print the version")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    s = sub.add_parser("status", help="show what runs, the pause state, keys, budget, and the active project")
    s.add_argument("--json", action="store_true")

    s = sub.add_parser("doctor", help="check every component. Exit 0 when each is OK or WAITING_FOR_KEYS")
    s.add_argument("--json", action="store_true")
    s.add_argument("--only", help="run only checks whose name contains this text")

    s = sub.add_parser("test", help="run the policy, unit, and injection tests")
    s.add_argument("suite", nargs="*", choices=["policy", "unit", "injection", "gate"], help="run only these suites. gate collects all tests in one session, like the merger")
    s.add_argument("-k", help="only tests that match this expression")
    s.add_argument("-x", action="store_true", help="stop at the first failure")

    sub.add_parser("start", help="start agent-fleet.target")
    sub.add_parser("stop", help="STOP: stop agent-fleet.target. The control bot keeps running")
    s = sub.add_parser("pause", help="PAUSE: no new work. Running work ends at its next step")
    s.add_argument("--reason", default="owner")
    s.add_argument("--by", default="fleetctl")
    s = sub.add_parser("resume", help="undo PAUSE")
    s.add_argument("--force", action="store_true", help="resume even when the fleet waits for keys")

    s = sub.add_parser("brief", help="show the latest daily brief")
    s.add_argument("--now", action="store_true", help="make a new brief first")
    s.add_argument("--note", metavar="TEXT", help="a note of 20 words or fewer that the next brief shows first. It goes out once")

    sec = sub.add_parser("secret", help="set, list, check, and rotate keys")
    ssub = sec.add_subparsers(dest="secret_command", metavar="ACTION")
    s = ssub.add_parser("set", help="save one key. The value is typed hidden. It is never echoed")
    s.add_argument("name")
    s.add_argument("--stdin", action="store_true", help="read the value from standard input (one line)")
    s = ssub.add_parser("show", help="show one value on an interactive terminal only. Refused in a pipe or a script")
    s.add_argument("name")
    s = ssub.add_parser("list", help="list names and whether each is set. Never shows a value")
    s.add_argument("--group")
    s = ssub.add_parser("check", help="one free test call for each key that is set")
    s.add_argument("names", nargs="*")
    s = ssub.add_parser("wizard", help="guided steps for one group of keys")
    s.add_argument("--group", required=True)
    s.add_argument("--again", action="store_true", help="ask again for keys that are already set")
    s = ssub.add_parser("rotate", help="make a new value for a key that the installer made")
    s.add_argument("name")

    cfg = sub.add_parser("config", help="read or change the live configuration")
    csub = cfg.add_subparsers(dest="config_command", metavar="ACTION")
    s = csub.add_parser("set", help="change one key. Example: fleetctl config set publisher.mode live")
    s.add_argument("key")
    s.add_argument("value")
    s = csub.add_parser("get", help="show one key")
    s.add_argument("key")
    csub.add_parser("show", help="show the keys that differ from the defaults")

    n8 = sub.add_parser("n8n", help="n8n, the workflow engine for the work that needs no model")
    nsub = n8.add_subparsers(dest="n8n_command", metavar="ACTION")
    nsub.add_parser("status", help="what runs, the version, the workflows, the last backup")
    nsub.add_parser("prepare", help="folders, secrets, the owner hash, the compose .env, the pinned image")
    nsub.add_parser("bootstrap", help="the API key, the credentials, and the workflows. Safe to run twice")
    s = nsub.add_parser("smoke", help="the Phase 1 checks")
    s.add_argument("--restart", action="store_true", help="also restart n8n and check that every workflow stays published")
    nsub.add_parser("backup", help="a backup now: workflows, encrypted credentials, a safe database copy")
    nsub.add_parser("restore-test", help="start a throwaway n8n from the newest backup, check it, and remove it")
    s = nsub.add_parser("upgrade", help="move to a newer 2.x release with a backup, smoke checks, and a rollback")
    s.add_argument("version", help="for example 2.47.6. Never next, beta, or 3.x")
    s.add_argument("--allow-young", action="store_true", help="allow a release that is under 3 days old. Use it only for a critical advisory")
    s = nsub.add_parser("check", help="read the n8n releases and advisories. It never upgrades")
    s.add_argument("--migration", action="store_true", help="read the n8n Migration Report instead")
    s = nsub.add_parser("serve", help="owner access to the editor over Tailscale (never funnel)")
    s.add_argument("mode", choices=["on", "off"])
    nsub.add_parser("mcp-env", help="write the env file of the n8n-mcp helper for Claude Code")
    s = nsub.add_parser("selftest", help="live checks of the links between n8n and the fleet. Owner messages are held while it runs")
    s.add_argument("--part", choices=["all", "live", "clone"], default="all", help="run only the checks on the live n8n, or only the checks on a throwaway copy")
    s.add_argument("--no-hermes", action="store_true", help="skip the check of the Hermes profiles")

    ow = sub.add_parser("owner", help="the private owner file: name, time zone, brief time, quiet hours, channels")
    osub = ow.add_subparsers(dest="owner_command", metavar="ACTION")
    s = osub.add_parser("check", help="validate the owner file. It never prints a value")
    s.add_argument("--file", help="check this file and not the live one")
    s = osub.add_parser("apply", help="copy config/owner.yaml of the repository to the live place after it validates")
    s.add_argument("--file", help="apply this file and not config/owner.yaml")

    pb = sub.add_parser("public", help="the public copy of the repository: deny list, scans, and export")
    pbsub = pb.add_subparsers(dest="public_command", metavar="ACTION")
    s = pbsub.add_parser("terms", help="build the deny list of private terms (mode 600, outside both repositories). It needs sudo")
    s.add_argument("--repo", help="the private repository. Default: here, or agent-fleet in your home")
    s = pbsub.add_parser("scan", help="run the deny list, pattern, gitleaks, and OSV scans on a folder")
    s.add_argument("path")
    s.add_argument("--terms")
    s.add_argument("--repo")
    s.add_argument("--no-osv", action="store_true")
    s = pbsub.add_parser("export", help="build the public copy, scan it, and write it to the public repository. Any finding fails it")
    s.add_argument("--repo", help="the private repository. Default: here, or agent-fleet in your home")
    s.add_argument("--public-repo", help="Default: free-agent-fleet in your home")
    s.add_argument("--terms")
    s.add_argument("--report", help="Default: docs/PUBLIC_SCRUB_REPORT.md in the private repository")
    s.add_argument("--no-osv", action="store_true")
    s.add_argument("--allow-dirty", action="store_true", help="export a repository that has uncommitted changes")
    s.add_argument("--dry-run", action="store_true", help="build, scan, and report. Write nothing to the public repository")

    sl = sub.add_parser("slack", help="Slack: set up the channels and the apps, and make manifests for more agents")
    slsub = sl.add_subparsers(dest="slack_command", metavar="ACTION")
    s = slsub.add_parser("bootstrap", help="make the channels, topics, pinned help, bookmarks, and test messages. Safe to run twice")
    s.add_argument("--again", action="store_true", help="post the help note, the bookmarks, and the test messages again")
    s = slsub.add_parser("add-agent", help="make the Slack app manifest of one more agent in slack/agents/")
    s.add_argument("name")
    slsub.add_parser("status", help="which tokens are set, which channels are known, and the quiet hours")

    pf = sub.add_parser("portfolio", help="the weekly portfolio log")
    pfsub = pf.add_subparsers(dest="portfolio_command", metavar="ACTION")
    s = pfsub.add_parser("sync", help="append new weeks to docs/PORTFOLIO_LOG.md of the private repository")
    s.add_argument("--repo", help="the repository folder. Default: agent-fleet in the home of the user who ran sudo")

    rt = sub.add_parser("router", help="model router steps")
    rsub = rt.add_subparsers(dest="router_command", metavar="ACTION")
    rsub.add_parser("apply", help="push provider keys, combos, and client keys into the router")
    s = rsub.add_parser("show", help="show the providers and the combos that the router holds. Never shows a key")
    s.add_argument("--offered", action="store_true", help="also list the model names that each connected provider offers")
    s = rsub.add_parser("approve", help="approve a provider after you read its data terms")
    s.add_argument("provider")
    s.add_argument("--yes", action="store_true", help="skip the question. Use it only after you read the terms")

    s = sub.add_parser("pivot", help="archive the active project and start a new one at Discover")
    s.add_argument("slug", nargs="?", help="name of the new project. Default: the next free project-NNN")
    s.add_argument("--reason", help="why the project pivots")
    s.add_argument("--lessons", help="file with the lessons")
    s.add_argument("--archive-only", action="store_true", help="archive the active project and start none")
    s.add_argument("--from-request", action="store_true", help="handle the request file of the orchestrator")

    pr = sub.add_parser("project", help="project commands")
    psub = pr.add_subparsers(dest="project_command", metavar="ACTION")
    s = psub.add_parser("status", help="stage, gate numbers, and missed gates")
    s.add_argument("slug", nargs="?")

    s = sub.add_parser("logs", help="show the log of one service")
    s.add_argument("service")
    s.add_argument("-n", "--lines", type=int, default=100)
    s.add_argument("-f", "--follow", action="store_true")

    sub.add_parser("backup", help="make a backup now (it holds no secrets)")
    s = sub.add_parser("update", help="bring new code in through the merger gates")
    s.add_argument("--source", help="path or URL of the repository. Default: where the system was installed from")
    s.add_argument("--ref", default="main")
    s.add_argument("--no-install", action="store_true", help="do not run the installer steps after the deploy")
    sub.add_parser("revoke-checklist", help="REVOKE: print every key and grant with its revoke page")
    ct = sub.add_parser("catalog", help="the tool catalog: each outside tool with its rating. It needs no root")
    ctsub = ct.add_subparsers(dest="catalog_command", metavar="ACTION")
    s = ctsub.add_parser("list", help="list the entries")
    s.add_argument("--rating", choices=["adopt", "trial", "watch", "reject"])
    s = ctsub.add_parser("show", help="show one entry by its name or its source address")
    s.add_argument("name")
    ctsub.add_parser("check", help="validate config/catalog.yaml")
    sub.add_parser("setup", help="guided steps for the human parts (keys, bots, mail)")
    return p


def trusted_launcher():
    """The path that sudo may run: a file that only root can change."""
    for candidate in (INSTALLED, os.path.realpath(sys.argv[0])):
        try:
            st = os.stat(candidate)
        except OSError:
            continue
        if st.st_uid == 0 and not (st.st_mode & 0o022):
            return candidate
    return None


def elevate(argv):
    """Run this command again as root through sudo. Never returns when it works."""
    target = trusted_launcher()
    if not target:
        print("fleetctl needs root, and no root-owned copy of fleetctl exists yet. Run: sudo ./fleetctl %s" % " ".join(argv), file=sys.stderr)
        return 1
    cmd = ["sudo"] + ([] if sys.stdin.isatty() else ["-n"]) + [target] + list(argv)
    os.execvp("sudo", cmd)
    return 1


def dispatch(args, console, runner=system.run):
    from ops.fleetctl import basic, cfgcmd, doctor, projects, secretcmd, testcmd, update, wizard
    c = args.command
    if c == "status":
        return basic.cmd_status(args, console, runner)
    if c == "doctor":
        return doctor.cmd_doctor(args, console, runner)
    if c == "test":
        return testcmd.cmd_test(args, console)
    if c == "start":
        return basic.cmd_start(args, console, runner)
    if c == "stop":
        return basic.cmd_stop(args, console, runner)
    if c == "pause":
        return basic.cmd_pause(args, console, runner)
    if c == "resume":
        return basic.cmd_resume(args, console, runner)
    if c == "brief":
        return basic.cmd_brief(args, console, runner)
    if c == "backup":
        return basic.cmd_backup(args, console, runner)
    if c == "logs":
        return basic.cmd_logs(args, console, runner)
    if c == "revoke-checklist":
        return basic.cmd_revoke(args, console)
    if c == "update":
        return update.cmd_update(args, console, runner)
    if c == "setup":
        return wizard.cmd_setup(args, console, runner)
    if c == "pivot":
        return projects.cmd_pivot(args, console, runner)
    if c == "project":
        if args.project_command == "status":
            return projects.cmd_status(args, console, runner)
    if c == "secret":
        table = {"set": secretcmd.cmd_set, "show": secretcmd.cmd_show, "list": secretcmd.cmd_list,
                 "check": secretcmd.cmd_check, "rotate": secretcmd.cmd_rotate, "wizard": wizard.cmd_wizard}
        fn = table.get(args.secret_command)
        if fn:
            return fn(args, console, runner)
    if c == "config":
        if args.config_command == "set":
            return cfgcmd.cmd_set(args, console, runner)
        if args.config_command == "get":
            return cfgcmd.cmd_get(args, console)
        if args.config_command == "show":
            return cfgcmd.cmd_show(args, console)
    if c == "n8n":
        from ops.fleetctl import n8ncmd
        return n8ncmd.dispatch(args, console, runner)
    if c == "owner":
        from ops.fleetctl import ownercmd
        return ownercmd.dispatch(args, console, runner)
    if c == "public":
        from ops.fleetctl import publiccmd
        return publiccmd.dispatch(args, console, runner)
    if c == "slack":
        from ops.fleetctl import slackctl
        return slackctl.dispatch(args, console, runner)
    if c == "portfolio":
        from ops.fleetctl import portfoliocmd
        return portfoliocmd.dispatch(args, console, runner)
    if c == "catalog":
        from ops.fleetctl import catalogcmd
        return catalogcmd.dispatch(args, console, runner)
    if c == "router":
        if args.router_command == "apply":
            return secretcmd.cmd_router_apply(args, console, runner)
        if args.router_command == "show":
            return secretcmd.cmd_router_show(args, console, runner)
        if args.router_command == "approve":
            return secretcmd.cmd_router_approve(args, console, runner)
    return None


def main(argv=None, console=None, runner=system.run):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print("fleetctl %s" % VERSION)
        return 0
    if args.command is None:
        parser.print_help()
        return 0
    no_root = args.command in NO_ROOT or (args.command == "slack" and getattr(args, "slack_command", None) == "add-agent")   # add-agent only writes files in your clone
    if not no_root and not system.is_root() and os.environ.get("FLEET_TESTING") != "1":
        return elevate(argv)
    console = console or ui.Console()
    os.umask(0o022)
    try:
        code = dispatch(args, console, runner)
    except KeyboardInterrupt:
        console.say("")
        console.say("Stopped.")
        return 130
    except BrokenPipeError:
        # The reader went away, as in `fleetctl logs gateway | head`. That is not an error. Point stdout at /dev/null,
        # so the flush at exit does not print a second trace.
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
        return 0
    except Exception as exc:                          # a clear one-line error. A trace could hold a value, so it needs FLEETCTL_DEBUG=1
        from ops.fleetctl import projects, update as upd
        from ops.fleetlib import config, secrets
        if isinstance(exc, (config.ConfigError, projects.ProjectError, upd.UpdateError, secrets.SecretError)):
            console.say("Error: %s" % exc)          # these messages are written by us and never hold a secret value
        else:
            console.say("Error: %s. Run with FLEETCTL_DEBUG=1 for the trace." % type(exc).__name__)
            if os.environ.get("FLEETCTL_DEBUG") == "1":
                raise
        return 1
    if code is None:
        console.say("Missing or unknown action. Run: fleetctl %s --help" % args.command)
        return 2
    return code
