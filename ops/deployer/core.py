"""The Deployer pipeline.

It deploys a commit only when all of these hold (rule R1 and the merger gates):
  1 the fleet is not paused
  2 the project name is a valid slug and the commit is a full 40-character hash
  3 the commit is in the live repository, is merged on main, and the merger recorded a pass for it
  4 the plan is free: the config says free, only Workers and D1 are used, the module is small,
    no other binding is asked for, and the account has no paid subscription
  5 the product follows the layout: projects/<slug>/app/worker.js, optional migrations/*.sql, deploy.json
Dry-run mode does steps 1 to 5 and writes the plan. It does not call Cloudflare.
"""
import json
import pathlib
import re
import subprocess
import time

from ops.fleetlib import jsonio, log
from ops.deployer.cloudflare import CloudflareError

LOG = log.get("deployer")
SLUG = re.compile(r"^[a-z0-9][a-z0-9\-]{0,40}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
MAX_MODULE_BYTES = 1_000_000
MAX_MIGRATION_BYTES = 200_000
ALLOWED_KEYS = {"compatibility_date", "d1"}


class GitRepo:
    """Read files from the live repository. Never checks out anything."""

    def __init__(self, path):
        self.path = str(path)

    def _git(self, *args):
        proc = subprocess.run(["git", "-C", self.path] + list(args), capture_output=True, timeout=60)  # nosemgrep: dangerous-subprocess-use-audit
        return proc.returncode, proc.stdout

    def on_main(self, commit):
        rc, _ = self._git("merge-base", "--is-ancestor", commit, "HEAD")
        return rc == 0

    def read(self, commit, path):
        rc, out = self._git("show", "%s:%s" % (commit, path))
        return out if rc == 0 else None

    def list(self, commit, folder):
        rc, out = self._git("ls-tree", "--name-only", commit, folder.rstrip("/") + "/")
        return sorted(l.decode() for l in out.splitlines()) if rc == 0 else []


def plan_is_free(subscriptions):
    """True when no subscription of the account costs money."""
    for sub in subscriptions or []:
        price = sub.get("price")
        if price not in (None, 0, 0.0, "0", "0.0") or (sub.get("rate_plan") or {}).get("is_contract"):
            return False
    return True


class Deployer:
    def __init__(self, cfg, cloudflare, repo, tested, state_dir, paused=lambda: None, clock=time.time, approved=None):
        self.approved = approved          # callable(kind, ref) -> bool. True when the owner approved. None means never. See D-050.
        self.cfg = cfg
        self.cf = cloudflare
        self.repo = repo
        self.tested = tested              # callable(commit) -> bool. True when the merger recorded a pass.
        self.state = pathlib.Path(state_dir)
        self.paused = paused
        self.clock = clock

    @property
    def mode(self):
        return (self.cfg.get("deployer") or {}).get("mode", "dry-run")

    def _blocked(self, project, commit, reason, **extra):
        self._log(project, commit, "blocked", reason)
        return dict({"status": "blocked", "reason": reason}, **extra)

    def _log(self, project, commit, status, reason="", url=""):
        record = {"ts": int(self.clock()), "project": project, "commit": commit, "status": status, "reason": reason, "url": url}
        try:
            self.state.mkdir(parents=True, exist_ok=True)
            with open(self.state / "deploys.log", "a") as fh:
                fh.write(json.dumps(record, sort_keys=True) + "\n")
        except OSError:
            pass
        LOG.info("deploy", project=project, status=status, reason=reason)

    def _save_migrations(self, project, names):
        """Record the migrations that ran. The other fields of the project stay as they are."""
        state = jsonio.read_json(self.state / "deployments.json", {}) or {}
        entry = dict(state.get(project) or {})
        entry["migrations"] = sorted(set(names) | set(entry.get("migrations", [])))
        state[project] = entry
        jsonio.write_json(self.state / "deployments.json", state, mode=0o640)

    def check_free_rules(self, spec, module, migrations):
        """Return a reason string when the product breaks a $0 rule, else None."""
        dep = self.cfg.get("deployer") or {}
        if dep.get("plan") != "free":
            return "plan_not_free"
        allowed = set(dep.get("allowed_products") or [])
        if "workers" not in allowed:
            return "workers_not_allowed"
        if spec.get("d1") and "d1" not in allowed:
            return "d1_not_allowed"
        if set(spec) - ALLOWED_KEYS:
            return "unknown_deploy_key: " + ",".join(sorted(set(spec) - ALLOWED_KEYS))
        if len(module) > MAX_MODULE_BYTES:
            return "module_too_large"
        if any(len(m) > MAX_MIGRATION_BYTES for m in migrations.values()):
            return "migration_too_large"
        if migrations and not spec.get("d1"):
            return "migrations_without_d1"
        return None

    def deploy(self, req):
        project, commit = (req or {}).get("project", ""), (req or {}).get("commit", "")
        if self.paused():
            return self._blocked(project, commit, "paused")
        if not isinstance(project, str) or not SLUG.match(project) or not isinstance(commit, str) or not COMMIT.match(commit):
            return self._blocked(str(project)[:41], str(commit)[:40], "bad_request")
        if not self.repo.on_main(commit):
            return self._blocked(project, commit, "not_merged")
        if not self.tested(commit):
            return self._blocked(project, commit, "not_tested")
        base = "projects/%s/app" % project
        module = self.repo.read(commit, base + "/worker.js")
        if module is None:
            return self._blocked(project, commit, "no_worker_js")
        try:
            spec = json.loads((self.repo.read(commit, base + "/deploy.json") or b"{}").decode())
        except ValueError:
            return self._blocked(project, commit, "bad_deploy_json")
        if not isinstance(spec, dict):
            return self._blocked(project, commit, "bad_deploy_json")
        migrations = {}
        for path in self.repo.list(commit, base + "/migrations"):
            if path.endswith(".sql"):
                migrations[path.rsplit("/", 1)[-1]] = self.repo.read(commit, path) or b""
        reason = self.check_free_rules(spec, module, migrations)
        if reason:
            return self._blocked(project, commit, reason)
        script = "fleet-" + project
        ref = "%s@%s" % (project, commit[:10])
        approved = bool(self.approved and self.approved("release", ref))
        plan = {"project": project, "commit": commit, "script": script, "module_bytes": len(module),
                "d1": bool(spec.get("d1")), "migrations": sorted(migrations), "release_approval": "approved" if approved else "missing"}
        if self.mode == "live" and not approved:
            return self._blocked(project, commit, "no_release_approval", ref=ref)      # the owner approves each release. See D-050.
        if self.mode != "live":
            jsonio.write_json(self.state / "plans" / ("%s-%s.json" % (project, commit[:10])), plan, mode=0o640)
            self._log(project, commit, "dry_run")
            return {"status": "dry_run", "plan": plan}
        try:
            if not self.cf.verify_token():
                return self._blocked(project, commit, "token_invalid")
            if not plan_is_free(self.cf.subscriptions()):
                return self._blocked(project, commit, "account_has_paid_subscription")
            d1_id = None
            if spec.get("d1"):
                d1_id = self.cf.d1_ensure("fleet-" + project)
                known = list((jsonio.read_json(self.state / "deployments.json", {}) or {}).get(project, {}).get("migrations", []))
                for name in sorted(migrations):
                    if name not in known:
                        self.cf.d1_query(d1_id, migrations[name].decode())
                        known.append(name)
                        self._save_migrations(project, known)      # at once. A later failure must not run it again.
            self.cf.upload_worker(script, module, spec.get("compatibility_date", "2025-09-01"), d1_id)
            url = "https://%s.%s.workers.dev" % (script, self.cf.subdomain())
        except CloudflareError as exc:
            return self._blocked(project, commit, "cloudflare_error", detail=str(exc))
        state = jsonio.read_json(self.state / "deployments.json", {}) or {}
        state[project] = {"commit": commit, "time": int(self.clock()), "url": url,
                          "migrations": sorted(set(migrations) | set(state.get(project, {}).get("migrations", [])))}
        jsonio.write_json(self.state / "deployments.json", state, mode=0o640)
        self._log(project, commit, "deployed", url=url)
        return {"status": "deployed", "url": url}
