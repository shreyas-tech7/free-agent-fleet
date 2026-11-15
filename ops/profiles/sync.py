"""Make the Hermes profiles match agents/*/agent.yaml and agents/*/SOUL.md of the live repository.

Root runs this module. It writes only inside the Hermes home of the user fleet and in the root-owned skills view.
It runs no agent code. Every step is idempotent. It changes only what differs, so a run with no change is quick.

Per agent it does these steps:
  profile    create the profile (no bundled skills, no command alias) when it is missing
  soul       copy SOUL.md from the live repository
  skills     copy the skills that the agent may use into /opt/agent-fleet/skills-view/<agent> (root-owned)
  scripts    copy the monitor scripts into the profile
  config     model, turn limit, disabled toolsets, skills folder, sandbox mounts, MCP servers, time zone
  toolsets   the toolsets of the cli and cron platforms
  secrets    the router key and the local tokens in the profile .env
  hooks      make the hooks folder of the profile root-owned, so no agent can add a hook
  cron       the scheduled jobs of agent.yaml
  botmode    mark the install as Bot Mode managed and make the canonical Bot Chat session (only while paused)
Then it fast-forwards the working clone of the agents.
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import yaml

from ops.fleetctl import system
from ops.fleetlib import autosecrets, config, jsonio, log, paths, pause, secrets

LOG = log.get("profiles")
PLATFORMS = ("cli", "cron")
KNOWN_BUILTIN = ["browser", "clarify", "code_execution", "computer_use", "connections", "context_engine", "cronjob", "delegation",
                 "file", "image_gen", "kanban", "memory", "session_search", "skills", "stt", "terminal", "todo", "tts", "video",
                 "video_gen", "vision", "web", "x_search"]
WORK = "/workspace/agent-fleet"
RTK = "/opt/agent-fleet/bin/rtk"
STATE_MOUNTS = {"status": ("status", "ro"), "tickets": ("tickets", "ro"), "requests": ("requests", "rw")}
MEMORY_MB = {"small": 1536, "medium": 3072, "large": 5120}
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,50}$")


class SyncError(Exception):
    pass


def deep_get(node, dotted, default=None):
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


# ------------------------------------------------------------------ what each profile should hold
def wanted_skills(live, name, spec):
    """The skills of agent.yaml plus the extra names in skills/_enabled/<agent>.txt that exist in skills/."""
    names = list(spec.get("skills") or [])
    extra = pathlib.Path(live) / "skills" / "_enabled" / ("%s.txt" % name)
    try:
        for line in extra.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if SKILL_NAME.match(line) and (pathlib.Path(live) / "skills" / line / "SKILL.md").is_file() and line not in names:
                names.append(line)
    except OSError:
        pass
    return names


def mcp_servers(spec, cfg=None):
    out = {}
    for sname, s in (spec.get("mcp_servers") or {}).items():
        if sname == "fleet_tools":
            env = {"PYTHONDONTWRITEBYTECODE": "1"}
            for token in s.get("env_from_profile") or []:
                env[token] = "${%s}" % token
            out["fleet_tools"] = {"command": "/usr/bin/python3", "args": ["-m", "ops.mcp.server", "--tools", ",".join(s.get("tools") or [])],
                                  "env": env, "cwd": str(paths.live_repo()), "timeout": 300, "connect_timeout": 60}
        elif sname == "n8n_tools":
            # The fleet-tools MCP server in n8n. Hermes loads only the tools in this list. The token is in the profile .env.
            out["n8n_tools"] = {"url": "http://127.0.0.1:%d/mcp/fleet-tools" % config.port(cfg or config.defaults(), "n8n"),
                                "headers": {"Authorization": "Bearer ${N8N_TOOLS_TOKEN}"},
                                "tools": {"include": list(s.get("tools") or []), "prompts": False, "resources": False},
                                "timeout": 60, "connect_timeout": 15}
        elif sname == "playwright":
            out["playwright"] = {"command": "/opt/agent-fleet/mcp/playwright/node_modules/.bin/playwright-mcp",
                                 "args": ["--headless", "--isolated", "--allowed-origins", "http://127.0.0.1:*;http://localhost:*", "--blocked-origins", "*"],
                                 "idle_timeout_seconds": 600, "max_lifetime_seconds": 43200, "lazy": True, "timeout": 120}
    return out


def volumes(spec):
    out = []
    ws = spec.get("workspace") or {}
    if ws.get("repo") == "rw":
        out.append("%s:%s" % (paths.work_clone(), WORK))
        out.append("%s:/usr/local/bin/rtk:ro" % RTK)                  # a static binary that shortens long command output. See docs/DECISIONS.md.
    for key in ws.get("state") or []:
        sub, mode = STATE_MOUNTS[key]
        out.append("%s/%s:/fleet-state/%s%s" % (paths.state_dir(), sub, sub, ":ro" if mode == "ro" else ""))
    return out


# Token settings. Each one cuts what the model reads again at every turn. The numbers come from the real sessions of the research agent
# and are in docs/TOKEN_REPORT.md. See D-058.
TOKEN_SETTINGS = {
    "compression.proactive_prune_tokens": 48000,       # a prune of old tool results that calls no model. The Hermes docs say to try 48000
    "tool_output.max_bytes": 16000,                    # a terminal result keeps its first 40 percent and last 60 percent past this size
    "web.cache_ttl_minutes": 1440,                     # the extract cache on disk keeps a page for 24 hours. Prompt 3 asks for it. Hermes allows 1440 at most
    "auxiliary.compression.provider": "main",          # the same router and key as the main model
    "auxiliary.compression.model": "fleet-worker",     # the summary of an old conversation is a job for the worker model. Prompt 3 asks for it. Its window equals the smart model
}
# An agent.yaml may set more keys under hermes_config. Only these families. A key outside them is refused, so no file can reach the security keys.
TOKEN_KEY_PREFIXES = ("compression.", "auxiliary.compression.", "tool_output.", "tool_budget.", "web.cache_", "web.extract_char_limit")


# Keys that an earlier build set and that no build wants now. The sync removes them from each profile. See D-058.
RETIRED_KEYS = ("tool_budget.mcp_result_size_chars",)


def token_settings(spec):
    want = dict(TOKEN_SETTINGS)
    for key, value in (spec.get("hermes_config") or {}).items():
        if not isinstance(key, str) or not key.startswith(TOKEN_KEY_PREFIXES):
            raise SyncError("hermes_config may set only token keys. %r is not one of them" % (key,))
        want[key] = value
    return want


def wanted_config(spec, cfg, view_dir):
    tier = config.get(cfg, "capacity.tier", "small")
    want = {
        "model.default": spec["model"],
        "agent.max_turns": int(spec["max_turns"]),
        "agent.disabled_toolsets": list(spec.get("disabled_toolsets") or []),
        "skills.external_dirs": [str(view_dir)],
        "skills.creation_nudge_interval": 0,
        "timezone": config.get(cfg, "owner.timezone", "UTC"),
        "terminal.container_memory": MEMORY_MB.get(tier, 1536),
        "terminal.container_cpu": 1,
        "terminal.docker_volumes": volumes(spec),
        "mcp_servers": mcp_servers(spec, cfg),
    }
    want.update(token_settings(spec))
    if (spec.get("workspace") or {}).get("repo") == "rw":
        want["terminal.cwd"] = WORK
    return want


# ------------------------------------------------------------------ the sync
class Sync:
    def __init__(self, live=None, cfg=None, runner=system.run, now=None):
        self.live = pathlib.Path(live or paths.live_repo())
        self.cfg = cfg or config.load()
        self.run = runner
        self.profiles = paths.hermes_home() / "profiles"
        self.is_root = os.geteuid() == 0

    # --- helpers
    def hermes(self, profile, args, timeout=120):
        argv = ["hermes"] + (["-p", profile] if profile else []) + list(args)
        return self.run(argv, user="fleet", timeout=timeout)

    def _own(self, path, mode, owner="fleet"):
        os.chmod(path, mode)
        if self.is_root:
            shutil.chown(path, owner, owner)

    def _write_if_changed(self, path, data, mode=0o644, owner="fleet"):
        path = pathlib.Path(path)
        try:
            if path.read_bytes() == data:
                return False
        except OSError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        jsonio.atomic_write(path, data, mode=mode, owner=owner if self.is_root else None, group=owner if self.is_root else None)
        return True

    def agents(self):
        out = {}
        for d in sorted((self.live / "agents").glob("*")):
            if (d / "agent.yaml").is_file() and (d / "SOUL.md").is_file():
                out[d.name] = yaml.safe_load((d / "agent.yaml").read_text()) or {}
        return out

    # --- steps
    def ensure_profile(self, name, spec):
        if (self.profiles / name).is_dir():
            return "present"
        rc, out = self.hermes(None, ["profile", "create", name, "--no-skills", "--no-alias", "--description", str(spec.get("description", ""))[:300]])
        if rc != 0 or not (self.profiles / name).is_dir():
            raise SyncError("could not create the profile %s: %s" % (name, out[-200:]))
        return "created"

    def sync_soul(self, name):
        data = (self.live / "agents" / name / "SOUL.md").read_bytes()
        return "updated" if self._write_if_changed(self.profiles / name / "SOUL.md", data) else "same"

    def sync_scripts(self, name):
        src = self.live / "agents" / name / "monitor"
        changed = 0
        if src.is_dir():
            dest = self.profiles / name / "scripts"
            dest.mkdir(parents=True, exist_ok=True)
            if self.is_root:
                shutil.chown(dest, "fleet", "fleet")
            for f in sorted(src.glob("*")):
                if f.is_file():
                    changed += self._write_if_changed(dest / f.name, f.read_bytes(), mode=0o644)
        return "%d changed" % changed

    def sync_skills(self, name, spec):
        """Copy the allowed skills to the root-owned view. A copy is used because it is plain files. No link needs to resolve."""
        view = paths.skills_view_dir() / name
        wanted = wanted_skills(self.live, name, spec)
        view.mkdir(parents=True, exist_ok=True)
        changed = 0
        for stale in sorted(view.iterdir()):
            if stale.name not in wanted and stale.is_dir():
                shutil.rmtree(stale)
                changed += 1
        for skill in wanted:
            src = self.live / "skills" / skill
            if not (src / "SKILL.md").is_file():
                continue
            dest = view / skill
            if _tree_hash(src) != _tree_hash(dest):
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                changed += 1
        if self.is_root:
            subprocess.run(["chown", "-R", "root:root", str(view)], check=False, timeout=60)
        subprocess.run(["chmod", "-R", "u=rwX,go=rX", str(view)], check=False, timeout=60)
        return "%d changed, %d skills" % (changed, len(wanted))

    def current_config(self, name):
        try:
            return yaml.safe_load((self.profiles / name / "config.yaml").read_text()) or {}
        except (OSError, yaml.YAMLError):
            return {}

    def sync_config(self, name, spec):
        want = wanted_config(spec, self.cfg, paths.skills_view_dir() / name)
        cur = self.current_config(name)
        changed = []
        for key, value in want.items():
            have = deep_get(cur, key, None)
            if have == value or (have is None and value in ([], {}, "")):
                continue
            text = value if isinstance(value, str) else json.dumps(value)
            # Hermes refuses to replace a whole section unless --force is given. A mapping is a section.
            force = ["--force"] if isinstance(value, dict) else []
            rc, out = self.hermes(name, ["config", "set"] + force + [key, text])
            if rc != 0:
                raise SyncError("could not set %s on %s: %s" % (key, name, out[-160:]))
            changed.append(key)
        for key in RETIRED_KEYS:
            if deep_get(cur, key, None) is not None:
                rc, out = self.hermes(name, ["config", "unset", key])
                if rc != 0:
                    raise SyncError("could not unset %s on %s: %s" % (key, name, out[-160:]))
                changed.append("-" + key)
        return changed

    def sync_toolsets(self, name, spec):
        cur = self.current_config(name)
        notes = []
        want = set(spec.get("toolsets") or [])
        for platform in PLATFORMS:
            have = deep_get(cur, "platform_toolsets.%s" % platform)
            have = set(have) if isinstance(have, list) else None
            known = set(deep_get(cur, "known_builtin_toolsets.%s" % platform) or KNOWN_BUILTIN)
            if have is not None and have == want:
                continue
            to_off = sorted((known - want) if have is None else (have - want))
            to_on = sorted(want if have is None else (want - have))
            if to_off:
                rc, out = self.hermes(name, ["tools", "disable"] + to_off + ["--platform", platform])
                if rc != 0:
                    raise SyncError("could not disable toolsets on %s/%s: %s" % (name, platform, out[-160:]))
            if to_on:
                rc, out = self.hermes(name, ["tools", "enable"] + to_on + ["--platform", platform])
                if rc != 0:
                    raise SyncError("could not enable toolsets on %s/%s: %s" % (name, platform, out[-160:]))
            notes.append("%s: +%d -%d" % (platform, len(to_on), len(to_off)))
        return notes

    def sync_secrets(self, name):
        """The router key and the local tokens. Auto secrets are made once. A new profile gets the router key and the n8n tools token."""
        made = autosecrets.ensure()
        key = secrets.value_of("ROUTER_KEY_HERMES")
        if key:
            env = secrets.read_values(self.profiles / name / ".env")
            if env.get("ROUTER_KEY_HERMES") != key:
                secrets.set_value("ROUTER_KEY_HERMES", key, enforce_format=False)
                made.append("ROUTER_KEY_HERMES(profile)")
        tools_token = secrets.value_of("N8N_TOOLS_TOKEN")
        if tools_token and secrets.read_values(self.profiles / name / ".env").get("N8N_TOOLS_TOKEN") != tools_token:
            secrets.set_value("N8N_TOOLS_TOKEN", tools_token)
            made.append("N8N_TOOLS_TOKEN(profile)")
        return made

    def lock_hooks(self, name):
        hooks = self.profiles / name / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        if self.is_root:
            shutil.chown(hooks, "root", "root")
        os.chmod(hooks, 0o755)
        return "root 755"

    def sync_cron(self, name, spec):
        try:
            data = json.loads((self.profiles / name / "cron" / "jobs.json").read_text())
            existing = {j["name"]: j for j in data.get("jobs", []) if isinstance(j, dict) and j.get("name")}
        except (OSError, ValueError):
            existing = {}
        notes, wanted_names = [], set()
        for job in spec.get("schedule") or []:
            wanted_names.add(job["name"])
            cur = existing.get(job["name"])
            expr = ((cur or {}).get("schedule") or {}).get("expr")
            prompt = job.get("prompt") or ""
            if cur and expr == job["cron"] and (cur.get("prompt") or "") == prompt and (cur.get("monitor_script") or None) == (job.get("monitor") or None) \
                    and (cur.get("script") or None) == (job.get("script") or None):
                continue
            if cur:
                self.hermes(name, ["cron", "remove", str(cur["id"])])
            argv = ["cron", "create", job["cron"]] + ([prompt] if prompt else []) + ["--name", job["name"], "--deliver", "local", "--catch-up", "off"]
            if job.get("monitor"):
                argv += ["--monitor-script", job["monitor"]]
            if job.get("script"):
                argv += ["--script", job["script"], "--no-agent"]
            rc, out = self.hermes(name, argv)
            if rc != 0:
                raise SyncError("could not create the job %s on %s: %s" % (job["name"], name, out[-160:]))
            notes.append("%s %s" % ("replaced" if cur else "created", job["name"]))
        for stale in sorted(set(existing) - wanted_names):
            notes.append("unknown job %s left alone (agents cannot create jobs)" % stale)
        return notes

    def sync_botmode(self, name, spec):
        """Mark the install as Bot Mode managed (a profile.yaml block). Make the canonical Bot Chat while the fleet is paused."""
        path = self.profiles / name / "profile.yaml"
        try:
            meta = yaml.safe_load(path.read_text()) or {}
        except (OSError, yaml.YAMLError):
            meta = {}
        notes = []
        ui = meta.get("ui_meta") if isinstance(meta.get("ui_meta"), dict) else {}
        if "hermes-bots" not in ui:
            ui["hermes-bots"] = {}
            meta["ui_meta"] = ui
            self._write_if_changed(path, yaml.safe_dump(meta, sort_keys=False).encode())
            notes.append("marked")
        rc, out = self.hermes(name, ["sessions", "list"], timeout=60)
        if rc == 0 and re.search(r"(?m)^\s*Bot Chat\s", _strip_ansi(out)):
            return notes + ["Bot Chat exists"]
        if pause.paused() is None:
            return notes + ["Bot Chat deferred: the fleet runs, so a first message would call a model. Run the sync while paused."]
        # The pause hook blocks the first message, so Hermes exits with a non-zero code. The session exists anyway. Check the list.
        _rc, out = self.hermes(name, ["chat", "-c", "Bot Chat", "--create-if-missing", "-q", "Canonical chat of the %s agent." % name, "--oneshot", "-Q"], timeout=180)
        rc2, listing = self.hermes(name, ["sessions", "list"], timeout=60)
        if rc2 == 0 and re.search(r"(?m)^\s*Bot Chat\s", _strip_ansi(listing)):
            return notes + ["Bot Chat created"]
        return notes + ["Bot Chat failed: %s" % _strip_ansi(out)[-120:]]

    def sync_work_clone(self):
        work = paths.work_clone()
        if not (work / ".git").exists():
            return "no working clone"
        rc, branch = self.run(["git", "-C", str(work), "symbolic-ref", "--short", "HEAD"], user="fleet", timeout=30)
        if rc != 0 or branch.strip() != "main":
            return "left alone (the main checkout is on %s)" % (branch.strip() or "no branch")
        self.run(["git", "-C", str(work), "fetch", "--quiet", "origin"], user="fleet", timeout=120)
        rc, out = self.run(["git", "-C", str(work), "merge", "--quiet", "--ff-only", "origin/main"], user="fleet", timeout=60)
        if rc == 0:
            return "fast-forwarded"
        return self.repair_work_clone(work, out)

    def _work_git(self, work, *args, timeout=60):
        return self.run(["git", "-C", str(work), *args], user="fleet", timeout=timeout)

    def repair_work_clone(self, work, reason=""):
        """Main of the working clone cannot fast-forward to origin/main. Agents never commit on main.
        So main left origin/main for one of two reasons: the merger rolled a change back after the clone had taken it,
        or someone edited the main checkout. Nothing is deleted. The old tip stays on a branch named rolled-back/<sha>.
        Edits to tracked files go to a stash. Then main follows origin/main again."""
        rc_a, head = self._work_git(work, "rev-parse", "HEAD")
        rc_b, target = self._work_git(work, "rev-parse", "--verify", "origin/main")
        head, target = head.strip(), target.strip()
        if rc_a != 0 or rc_b != 0 or not head or not target:
            return "not fast-forwarded: %s" % (reason or "cannot read the commits")[-120:]
        notes = []
        rc, dirty = self._work_git(work, "status", "--porcelain", "--untracked-files=no")
        if rc == 0 and dirty.strip():
            rc, out = self._work_git(work, "-c", "user.name=fleet", "-c", "user.email=fleet@localhost", "stash", "push", "--quiet", "-m",
                                     "edits found on main by the sync")
            if rc != 0:
                return "not fast-forwarded: the edits on main could not be saved (%s)" % out[-80:]
            notes.append("edits kept in a stash")
        rc, _ = self._work_git(work, "merge-base", "--is-ancestor", head, target)
        if rc != 0:                                                  # the old tip is not part of origin/main: keep it
            archive = "rolled-back/%s" % head[:12]
            rc, out = self._work_git(work, "branch", archive, head)
            if rc != 0 and "already exists" not in out:
                return "not fast-forwarded: the old tip could not be kept (%s)" % out[-80:]
            notes.append("old tip kept as %s" % archive)
        rc, out = self._work_git(work, "reset", "--hard", "--quiet", target)
        if rc != 0:
            return "not fast-forwarded: %s" % out[-120:]
        return "main reset to origin/main (%s)" % ", ".join(notes or ["no change was lost"])

    # --- all steps for one agent, and for all agents
    def sync_agent(self, name, spec, botmode=True):
        report = {}
        for step, fn in (("profile", lambda: self.ensure_profile(name, spec)), ("soul", lambda: self.sync_soul(name)),
                         ("scripts", lambda: self.sync_scripts(name)), ("skills", lambda: self.sync_skills(name, spec)),
                         ("config", lambda: self.sync_config(name, spec)), ("toolsets", lambda: self.sync_toolsets(name, spec)),
                         ("secrets", lambda: self.sync_secrets(name)), ("hooks", lambda: self.lock_hooks(name)),
                         ("cron", lambda: self.sync_cron(name, spec))) + ((("botmode", lambda: self.sync_botmode(name, spec)),) if botmode else ()):
            try:
                report[step] = fn()
            except SyncError as exc:
                report[step] = "FAILED: %s" % exc
                break
        return report

    def sync_all(self, only=None, botmode=True, work_clone=True):
        report = {"agents": {}}
        for name, spec in self.agents().items():
            if only and name != only:
                continue
            report["agents"][name] = self.sync_agent(name, spec, botmode)
        report["work_clone"] = self.sync_work_clone() if work_clone else "left for after the health check"
        report["ok"] = not any(isinstance(v, str) and v.startswith("FAILED") for a in report["agents"].values() for v in a.values())
        return report


def _strip_ansi(text):
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text or "")


def _tree_hash(path):
    import hashlib
    path = pathlib.Path(path)
    h = hashlib.sha256()
    if not path.is_dir():
        return None
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(path)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def sync_all(live=None, work_clone=True):
    """Entry point for the merger. Returns a short text. It never raises.
    The merger passes work_clone=False before the health check. The agents get the new main only after it is healthy."""
    try:
        report = Sync(live).sync_all(botmode=False, work_clone=work_clone)
    except Exception as exc:                          # the merger must not fail because of a profile problem
        LOG.error("profile_sync_failed", error=type(exc).__name__)
        return "profiles: sync failed (%s)" % type(exc).__name__
    LOG.info("profile_sync", ok=report["ok"])
    return "profiles: %s" % ("ok" if report["ok"] else "some steps failed. Run: sudo python3 -m ops.profiles.sync")


def sync_work_clone(live=None):
    """Entry point for the merger. Moves the agents' working clone to the live main. It never raises."""
    try:
        note = Sync(live).sync_work_clone()
    except Exception as exc:
        LOG.error("work_clone_sync_failed", error=type(exc).__name__)
        return "work clone: sync failed (%s)" % type(exc).__name__
    LOG.info("work_clone_sync", result=note)
    return "work clone: %s" % note


def main(argv=None):
    ap = argparse.ArgumentParser(description="Make the Hermes profiles match agents/*.")
    ap.add_argument("--only", help="sync one agent")
    ap.add_argument("--no-botmode", action="store_true", help="skip the Bot Chat step")
    args = ap.parse_args(argv)
    if os.geteuid() != 0 and os.environ.get("FLEET_TESTING") != "1":
        print("Run this as root: sudo python3 -m ops.profiles.sync", file=sys.stderr)
        return 2
    report = Sync().sync_all(args.only, botmode=not args.no_botmode)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
