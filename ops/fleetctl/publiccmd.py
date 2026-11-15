"""fleetctl public. The public copy of the repository. See D-051.

  terms           build the deny list (~/.config/agent-fleet/private-terms.txt). It needs root, because it reads the live config
  scan PATH       run the scans on any folder
  export          build the public copy, scan it, write docs/PUBLIC_SCRUB_REPORT.md, and make the public repository
"""
import os
import pathlib
import pwd
import subprocess

import yaml

from ops.fleetlib import config, owner, paths


def _owner_home():
    user = os.environ.get("SUDO_USER")
    return pathlib.Path(pwd.getpwnam(user).pw_dir) if user and user != "root" else pathlib.Path.home()


def _repo(args):
    if getattr(args, "repo", None):
        return pathlib.Path(args.repo)
    here = pathlib.Path.cwd()
    return here if (here / "public" / "manifest.txt").is_file() else _owner_home() / "agent-fleet"


def cmd_terms(args, console, runner=subprocess.run):
    from ops.public import terms
    if os.geteuid() != 0 and os.environ.get("FLEET_TESTING") != "1":
        from ops.fleetctl import cli
        return cli.elevate(["public", "terms"] + (["--repo", args.repo] if getattr(args, "repo", None) else []))
    repo = _repo(args)
    try:
        overrides = config.load_overrides()
        owner_data = owner.read(paths.owner_config())
    except (config.ConfigError, owner.OwnerError):
        overrides, owner_data = {}, {}
    home = _owner_home()
    found = terms.collect(repo, home, runner, overrides, owner_data, extra_file=home / ".config" / "agent-fleet" / "private-terms-extra.txt")
    path = terms.terms_path(home)
    count = terms.write(found, path)
    if os.geteuid() == 0:
        info = pwd.getpwnam(os.environ.get("SUDO_USER") or "root")
        os.chown(str(path), info.pw_uid, info.pw_gid)
        os.chown(str(path.parent), info.pw_uid, info.pw_gid)
    console.say("Wrote %d terms to %s (mode 600). Add more by hand in private-terms-extra.txt in the same folder. Terms are never printed." % (count, path))
    return 0


def _show(console, results):
    from ops.public import scans
    total = 0
    for name, rows in results.items():
        total += len(rows)
        console.say("%-9s %s" % (name, "pass" if not rows else "FAIL, %d findings" % len(rows)))
        for rel, line, rule in rows[:15]:
            console.say("   %s%s  %s" % (rel, ":%d" % line if line else "", rule))
        if len(rows) > 15:
            console.say("   ... %d more" % (len(rows) - 15))
    return total


def cmd_scan(args, console, runner=subprocess.run):
    from ops.public import export, scans
    target = pathlib.Path(args.path)
    terms_file = pathlib.Path(args.terms) if args.terms else _owner_home() / ".config" / "agent-fleet" / "private-terms.txt"
    allow = _repo(args) / "public" / "scan-allow.txt"
    try:
        results, count = export.run_scans(target, terms_file, allow, runner, with_osv=not args.no_osv)
    except (scans.ScanError, export.ExportError) as exc:
        console.say("Scan stopped: %s" % exc)
        return 1
    console.say("Scanned %s with %d deny-list terms." % (target, count))
    return 1 if _show(console, results) else 0


def cmd_export(args, console, runner=subprocess.run):
    from ops.public import export, scans
    repo = _repo(args)
    public_repo = pathlib.Path(args.public_repo) if args.public_repo else _owner_home() / "free-agent-fleet"
    terms_file = pathlib.Path(args.terms) if args.terms else _owner_home() / ".config" / "agent-fleet" / "private-terms.txt"
    report = pathlib.Path(args.report) if args.report else repo / "docs" / "PUBLIC_SCRUB_REPORT.md"
    try:
        result = export.export(repo, public_repo, terms_file, report, runner=runner, with_osv=not args.no_osv, allow_dirty=args.allow_dirty,
                               write_public=not args.dry_run)
    except (export.ExportError, scans.ScanError) as exc:
        console.say("Export stopped: %s" % exc)
        return 1
    console.say("Private commit %s. %d files in the export. Report: %s" % (result["commit"][:10], result["files"], report))
    if not result["ok"]:
        for rule, count in result["findings"].items():
            console.say("  FAIL %s x%d" % (rule, count))
        for problem in result["template_problems"]:
            console.say("  FAIL %s" % problem)
        console.say("The export FAILED. Nothing was written to the public repository. Read the report for the files and rules.")
        return 1
    if args.dry_run:
        console.say("All scans passed. Dry run: the public repository was not written.")
        return 0
    pub = result["public"]
    console.say("All scans passed. Public repository %s: branch %s, %s." % (public_repo, pub["branch"], "committed" if pub["committed"] else "no change"))
    return 0


def dispatch(args, console, runner=None):
    """The shared fleetctl runner has another shape. These commands call git, gh, gitleaks, and osv-scanner through subprocess.run."""
    table = {"terms": cmd_terms, "scan": cmd_scan, "export": cmd_export}
    fn = table.get(args.public_command)
    if not fn:
        console.say("Choose one of: %s" % ", ".join(sorted(table)))
        return 2
    return fn(args, console, subprocess.run)
