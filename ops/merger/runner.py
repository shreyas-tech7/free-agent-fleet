"""The merger. It runs as root on a 15-minute timer. It is the only path for agent changes into the live system.

For each new agent branch it runs gates G1 to G6. If all pass, it merges, deploys to the live copy,
restarts the affected services, and runs a health check. If the health check fails, it rolls back
and files a ticket. It writes each result to docs/CHANGELOG.md.

Agents learn only pass or fail for each gate. They never see a log or a test name.
"""
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import time

from ops.fleetlib import config, jsonio, log, paths, pause
from ops.merger import container, evals, gitops, health, policy

LOG = log.get("merger")
MARKER_START = "<!-- MERGER-LOG:START -->"
MARKER_END = "<!-- MERGER-LOG:END -->"
GATES = ["G1", "G2", "G3", "G4", "G5", "G6"]


def _now_text(now):
    return datetime.datetime.fromtimestamp(now, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class Merger:
    def __init__(self, cfg=None, runner=subprocess.run, now=time.time, run_gates=None, restart=None, sync_profiles=None,
                 check_health=None, make_bundle=None, ignore_pause=False, force_health_failure=False, image_id=None, sync_work_clone=None, unit_exists=None):
        self.cfg = cfg or config.load()
        self.runner = runner
        self.now = now
        self.live = paths.live_repo()
        self.state_dir = paths.merger_dir()
        self.staging = self.state_dir / "staging.git"
        self.runs = paths.merger_runs_dir()
        self.run_gates = run_gates or self._container_gates
        self.restart = restart or self._systemd_restart
        # On the real machine only: leave out a unit that the installer has not made yet. Tests pass their own function or none.
        self.unit_exists = unit_exists or (health.unit_installed if os.geteuid() == 0 and pathlib.Path("/run/systemd/system").is_dir() else None)
        self.sync_profiles = sync_profiles or self._sync_profiles
        self.sync_work_clone = sync_work_clone or self._sync_work_clone
        self.check_health = check_health or (lambda restarted: health.check(self.live, restarted, self.cfg, force_failure=force_health_failure))
        self.make_bundle = make_bundle or gitops.make_bundle_as_fleet
        self.ignore_pause = ignore_pause
        self.image_id = image_id

    # ------------------------------------------------------------ state
    def _state(self):
        return jsonio.read_json(self.state_dir / "state.json", {"seen": {}}) or {"seen": {}}

    def _save_state(self, state):
        jsonio.write_json(self.state_dir / "state.json", state, mode=0o600)

    def _write_status(self, state):
        """The view that agents may read. Pass or fail for each gate. No logs, no test names."""
        out = {"updated": int(self.now()), "branches": {}}
        for branch, rec in sorted(state["seen"].items())[-60:]:
            out["branches"][branch] = {"commit": rec["sha"][:10], "result": rec["result"], "gates": rec.get("gates", {})}
        owner = "root" if os.geteuid() == 0 else None
        jsonio.write_json(paths.state_dir() / "status" / "merger.json", out, mode=0o644, owner=owner, group="fleet" if owner else None)

    def _private_log(self, record):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.state_dir / "merges.log", "a") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        os.chmod(self.state_dir / "merges.log", 0o600)

    # ------------------------------------------------------------ default hooks
    def _container_gates(self, commit, entries, changed):
        image = self.image_id or container.pinned_image(self.runner)
        osv_dir = paths.merger_runs_dir().parent / "osv-cache"
        LOG.info("osv_refresh", result=str(container.refresh_osv(osv_dir, self.runner))[:200])
        run_dir = self.runs / ("run-%s" % commit[:10])
        container.cleanup(run_dir)
        try:
            container.prepare_run(run_dir, lambda dest: gitops.export_tree(self.staging, commit, dest), paths.hidden_tests_dir(),
                                  self.live / "ops" / "merger" / "gate_entry.py", {"changed": changed, "gates": ["G1", "G2", "G4"]})
            return container.run_gates(run_dir, image, "/opt/agent-fleet/vendor/semgrep-rules", osv_dir, runner=self.runner)
        finally:
            container.cleanup(run_dir)

    def _systemd_restart(self, units):
        results = {}
        for unit in units:
            proc = self.runner(["systemctl", "restart", unit], capture_output=True, text=True, timeout=180)
            results[unit] = proc.returncode == 0
        return results

    def _sync_profiles(self):
        try:
            from ops.profiles import sync
        except ImportError:
            return "profiles: not installed yet"
        return sync.sync_all(self.live, work_clone=False)          # the agents' clone waits for the health check

    def _sync_work_clone(self):
        try:
            from ops.profiles import sync
        except ImportError:
            return "work clone: not installed yet"
        return sync.sync_work_clone(self.live)

    # ------------------------------------------------------------ one branch
    def evaluate(self, branch, sha, origin="agent"):
        """Run every gate for one branch. Returns a dict with the verdict. Nothing is deployed here."""
        live_head = gitops.rev_parse(self.live, "HEAD")
        gitops.fetch_into(self.staging, self.live, "+HEAD:refs/live/main")
        merge, reason = gitops.merge_commit(self.staging, live_head, sha, "merge %s" % branch)
        verdict = {"branch": branch, "sha": sha, "base": live_head, "gates": {}, "reason": reason, "merge": merge, "changed": [], "details": {}}
        if merge is None:
            verdict["gates"] = {g: "fail" for g in GATES}
            return verdict
        entries = gitops.changed_entries(self.staging, live_head, merge)
        verdict["changed"] = [e["path"] for e in entries]
        protected, writable = policy.load_policy(self.live)
        if origin == "owner":
            g3 = []                                    # an update from the owner may change protected paths
        else:
            g3 = policy.gate_g3(entries, protected, writable)
        g5 = policy.gate_g5(self.staging, merge, entries)
        g6 = evals.gate_g6(entries, lambda p: gitops.read_blob(self.staging, live_head, p), lambda p: gitops.read_blob(self.staging, merge, p))
        verdict["gates"].update({"G3": "fail" if g3 else "pass", "G5": "fail" if g5 else "pass", "G6": "fail" if g6 else "pass"})
        verdict["details"].update({"G3": g3, "G5": g5, "G6": g6})
        if g3:
            # Do not run agent code when the change touches a forbidden path.
            verdict["gates"].update({"G1": "skipped", "G2": "skipped", "G4": "skipped"})
            return verdict
        if not entries:
            verdict["gates"].update({"G1": "skipped", "G2": "skipped", "G4": "skipped"})
            verdict["reason"] = "the branch changes nothing"
            return verdict
        try:
            result = self.run_gates(merge, entries, verdict["changed"])
        except (container.GateError, OSError, subprocess.SubprocessError) as exc:
            result = {g: {"ok": False, "detail": "gate run failed: %s" % exc} for g in ("G1", "G2", "G4")}
        for g in ("G1", "G2", "G4"):
            ok = bool(result.get(g, {}).get("ok"))
            verdict["gates"][g] = "pass" if ok else "fail"
            verdict["details"][g] = result.get(g, {}).get("detail", "no result")
        return verdict

    def passed(self, verdict):
        return all(verdict["gates"].get(g) == "pass" for g in GATES)

    # ------------------------------------------------------------ deploy and rollback
    def deploy(self, verdict):
        """Fast-forward the live copy, sync profiles, restart, check health. Roll back on failure."""
        old = gitops.rev_parse(self.live, "HEAD")
        merge = verdict["merge"]
        parked = self._park_local_changes()
        gitops.fetch_into(self.live, self.staging, merge)
        gitops.git(self.live, "merge", "--ff-only", "--quiet", merge, identity=True)
        self._fix_permissions()
        notes = {"profiles": self.sync_profiles()}
        if parked:
            notes["parked"] = parked
        units = health.units_to_restart(verdict["changed"], exists=self.unit_exists)
        notes["restart"] = self.restart(units) if units else {}
        ok, details = self.check_health(units)
        if ok:
            self._record_pass(verdict)
            notes["work_clone"] = self.sync_work_clone()                 # the agents see the new main only now
            return {"result": "merged", "old": old, "new": merge, "notes": notes}
        # roll back
        gitops.git(self.live, "reset", "--hard", "--quiet", old)
        self._fix_permissions()
        notes["rollback_profiles"] = self.sync_profiles()
        notes["work_clone"] = self.sync_work_clone()                     # repairs a clone that took the change too early
        notes["rollback_restart"] = self.restart(units) if units else {}
        ticket = self._file_ticket(verdict, details)
        return {"result": "rolled_back", "old": old, "new": merge, "notes": notes, "health": details, "ticket": ticket}

    def _park_local_changes(self):
        """Save and reset any change to a tracked file in the live copy. See D-049.

        Installer steps add rows to vendor/LOCK.md of the live copy. A fast-forward cannot overwrite a changed file,
        so the deploy used to stop with a GitError. The change is saved as a patch first, so nothing is lost.
        Merge the saved rows into the source with: python3 scripts/lock.py merge-from PATH
        """
        status = gitops.git(self.live, "status", "--porcelain", "--untracked-files=no")
        if not status.strip():
            return None
        patch = gitops.git(self.live, "diff", "--binary", "HEAD")
        folder = paths.ops_state_dir() / "merger" / "parked"
        folder.mkdir(parents=True, exist_ok=True)
        saved = folder / ("live-changes-%d.patch" % int(self.now()))
        saved.write_text(patch)
        os.chmod(saved, 0o600)
        gitops.git(self.live, "checkout", "--quiet", "HEAD", "--", ".")
        files = sorted({line[3:].strip() for line in status.splitlines() if line.strip()})
        LOG.warn("live_changes_parked", files=files[:10], patch=str(saved))
        return {"files": files[:10], "patch": str(saved)}

    def _record_pass(self, verdict):
        """Tell the Deployer that these commits passed every gate and are on main. It deploys nothing else."""
        path = paths.ops_state_dir() / "merger" / "results.json"
        data = jsonio.read_json(path, {}) or {}
        now = int(self.now())
        for commit in {verdict["merge"], verdict["sha"]}:
            data[commit] = {"status": "pass", "branch": verdict["branch"], "at": now}
        if len(data) > 500:
            data = dict(sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-500:])
        owner = "fleetops" if os.geteuid() == 0 else None
        jsonio.write_json(path, data, mode=0o640, owner=owner, group=owner)

    def _fix_permissions(self):
        if os.geteuid() != 0:
            return
        subprocess.run(["chown", "-R", "root:root", str(self.live)], check=False, timeout=120)
        subprocess.run(["chmod", "-R", "go-w", str(self.live)], check=False, timeout=120)

    def _file_ticket(self, verdict, details):
        """A ticket for the orchestrator. Same schema as the tickets that the feedback agent writes."""
        tdir = paths.state_dir() / "tickets"
        tdir.mkdir(parents=True, exist_ok=True)
        stamp = int(self.now())
        digest = hashlib.sha256((verdict["branch"] + verdict["sha"]).encode()).hexdigest()[:10]
        ticket = {"id": "t-%d-%s" % (stamp, digest), "category": "deploy", "severity": "high", "status": "open", "created": stamp,
                  "summary": ("The merger rolled back branch %s because the health check failed after the deploy." % verdict["branch"])[:280],
                  "evidence_link": "merger/%s" % verdict["sha"][:10], "user_hash": "system"}
        owner = "root" if os.geteuid() == 0 else None
        jsonio.write_json(tdir / (ticket["id"] + ".json"), ticket, mode=0o664, owner=owner, group="fleet" if owner else None)
        return ticket["id"]

    # ------------------------------------------------------------ changelog
    def changelog(self, verdict, outcome):
        """Add one line to docs/CHANGELOG.md in the live copy and commit it."""
        path = self.live / "docs" / "CHANGELOG.md"
        try:
            text = path.read_text()
        except OSError:
            return False
        line = "- %s: %s `%s` (%s)" % (_now_text(self.now()), outcome, verdict["branch"], verdict["sha"][:10])
        gates = ", ".join("%s %s" % (g, verdict["gates"].get(g, "?")) for g in GATES)
        line += ". Gates: %s." % gates
        if MARKER_START not in text:
            return False
        path.write_text(text.replace(MARKER_START, MARKER_START + "\n" + line, 1))
        gitops.git(self.live, "add", "docs/CHANGELOG.md")
        gitops.git(self.live, "commit", "--quiet", "-m", "changelog: %s %s" % (outcome, verdict["branch"]), identity=True, check=False)
        self._fix_permissions()
        return True

    # ------------------------------------------------------------ main loop
    def run_once(self):
        report = {"processed": [], "skipped": None}
        if not self.ignore_pause and pause.paused() is not None:
            report["skipped"] = "paused"
            return report
        self.state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.state_dir, 0o700)
        with open(self.state_dir / "lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                report["skipped"] = "another merger run is active"
                return report
            report = self._locked_run(report)
            report["work_clone"] = self._keep_work_clone_current()
            return report

    def _keep_work_clone_current(self):
        """Make the agents' clone follow the live main at the end of every run.

        The live main moves without a deploy: the changelog line after each verdict, the pivot service, an owner update.
        Before this, the clone moved only after a good deploy. The orchestrator then built its branches on an old main
        and every state branch conflicted with the file that the pivot service had added. See D-032.
        The sync leaves a clone alone when an agent has a branch checked out."""
        try:
            return self.sync_work_clone()
        except Exception as exc:        # the run must not fail because of the sync
            LOG.warn("work clone sync failed", error=type(exc).__name__)
            return "work clone sync failed: %s" % type(exc).__name__

    def prepare_outgoing(self, directory, chown=shutil.chown):
        """The user fleet writes the bundle here, so the folder must belong to fleet. Root treats the file as untrusted data."""
        if os.geteuid() == 0:
            chown(str(directory), "fleet", "fleet")
            os.chmod(directory, 0o750)

    def _locked_run(self, report):
        outgoing = paths.work_clone().parent / "outgoing"
        outgoing.mkdir(parents=True, exist_ok=True)
        self.prepare_outgoing(outgoing)
        bundle = outgoing / "agents.bundle"
        try:
            bundle.unlink()
        except FileNotFoundError:
            pass
        if not self.make_bundle(paths.work_clone(), bundle):
            report["skipped"] = "no agent branches"
            return report
        work = self.state_dir / "incoming"
        heads = gitops.import_bundle(self.staging, bundle, work)
        try:
            bundle.unlink()
        except FileNotFoundError:
            pass
        state = self._state()
        for branch, sha in sorted(heads.items()):
            if state["seen"].get(branch, {}).get("sha") == sha:
                continue
            verdict = self.evaluate(branch, sha)
            if self.passed(verdict):
                outcome = self.deploy(verdict)
                result = outcome["result"]
                verdict["outcome"] = {k: v for k, v in outcome.items() if k != "notes"}
                verdict["notes"] = outcome.get("notes")
            else:
                result = "rejected"
            rec = {"sha": sha, "result": {"merged": "merged", "rolled_back": "rolled_back", "rejected": "rejected"}[result],
                   "gates": verdict["gates"], "at": int(self.now())}
            state["seen"][branch] = rec
            self._private_log(dict(verdict, result=result, at=int(self.now())))
            self.changelog(verdict, {"merged": "merged", "rolled_back": "rolled back", "rejected": "rejected"}[result])
            report["processed"].append({"branch": branch, "result": result, "gates": verdict["gates"]})
            LOG.info("branch", branch=branch, result=result, gates=verdict["gates"])
        self._save_state(state)
        self._write_status(state)
        return report
