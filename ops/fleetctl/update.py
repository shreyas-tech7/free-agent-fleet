"""fleetctl update: bring new code into the live system through the merger gates.

The owner runs it after `git pull` in the clone that the system was installed from, or with --source.
The code goes through the same gates as an agent change (tests, scans, Rule of Two, skill size, eval score).
Gate G3 does not apply, because the owner is allowed to change the locked core. If every gate passes,
the merger deploys it, restarts the affected services, runs the health check, and rolls back on failure.
After a deploy the installer steps from 30 on run again, so new units, pins, and profiles take effect.
"""
import fcntl
import os
import re
import subprocess
import pathlib

from ops.fleetctl import system
from ops.fleetlib import paths

REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,60}$")
GATES = ("G1", "G2", "G3", "G4", "G5", "G6")


class UpdateError(Exception):
    pass


def live_origin(live, runner=system.run):
    rc, out = runner(["git", "-C", str(live), "config", "--get", "remote.origin.url"], timeout=20)
    return out.strip() if rc == 0 and out.strip() else None


def fetch_candidate(merger, source, ref):
    from ops.merger import gitops
    staging = pathlib.Path(merger.staging)
    staging.parent.mkdir(parents=True, exist_ok=True)
    if not (staging / "HEAD").exists():
        subprocess.run(["git", "init", "--bare", "--quiet", str(staging)], check=True, env=gitops.SAFE_ENV, capture_output=True)
    last = None
    for refspec in ("+refs/heads/%s:refs/owner/update" % ref, "+refs/tags/%s:refs/owner/update" % ref):
        try:
            gitops.fetch_into(staging, source, refspec, trust_source=True)
            return gitops.rev_parse(staging, "refs/owner/update")
        except gitops.GitError as exc:
            last = exc
    raise UpdateError("cannot fetch %s from %s: %s" % (ref, source, last))


def run_update(console, source=None, ref="main", merger=None, runner=system.run, run_installer=True):
    from ops.merger import runner as mrunner
    if not REF_RE.match(ref):
        raise UpdateError("the ref name is not valid")
    merger = merger or mrunner.Merger(ignore_pause=True)
    live = pathlib.Path(merger.live)
    source = source or live_origin(live, runner)
    if not source:
        raise UpdateError("the live copy has no origin. Pass --source PATH_OR_URL.")
    merger.state_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(merger.state_dir, 0o700)
    except OSError:
        pass
    with open(pathlib.Path(merger.state_dir) / "lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise UpdateError("the merger is busy. Try again in a minute.")
        sha = fetch_candidate(merger, source, ref)
        console.say("Candidate %s from %s (%s)." % (sha[:10], source if "://" in source or "@" in source else "the local clone", ref))
        verdict = merger.evaluate("owner/update", sha, origin="owner")
        if verdict["merge"] is None:
            if "no new commits" in (verdict.get("reason") or ""):
                console.say("Already up to date.")
                return 0
            console.say("Cannot update: %s" % verdict.get("reason"))
            return 1
        for g in GATES:
            console.say("  %s %s" % (g, verdict["gates"].get(g, "?")))
        if not merger.passed(verdict):
            for g in GATES:
                if verdict["gates"].get(g) == "fail":
                    detail = verdict["details"].get(g)
                    console.say("  %s detail: %s" % (g, str(detail)[:300]))
            console.say("The update did not pass the gates. Nothing changed.")
            return 1
        outcome = merger.deploy(verdict)
        merger.changelog(verdict, "update merged" if outcome["result"] == "merged" else "update rolled back")
    if outcome["result"] != "merged":
        console.say("The health check failed after the deploy. The merger rolled the change back.")
        for line in outcome.get("health", [])[:5]:
            console.say("  " + str(line))
        return 1
    console.say("Updated %s to %s." % (outcome["old"][:10], outcome["new"][:10]))
    if run_installer:
        rc, out = runner(["bash", str(live / "install.sh"), "--from", "30"], timeout=1800)
        console.say("Installer steps from 30: %s" % ("done" if rc == 0 else "failed. Run: sudo bash %s/install.sh --from 30. %s" % (live, out[-200:])))
        return 0 if rc == 0 else 1
    return 0


def cmd_update(args, console, runner=system.run):
    try:
        return run_update(console, source=args.source, ref=args.ref, runner=runner, run_installer=not args.no_install)
    except UpdateError as exc:
        console.say("Update stopped: %s" % exc)
        return 1
