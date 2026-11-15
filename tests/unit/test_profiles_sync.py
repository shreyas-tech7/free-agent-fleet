import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

from tests.helpers import copy_tree

from ops.fleetlib import paths, secrets
from ops.profiles import sync
from tests.unit.fctl_helpers import Runner

ROOT = pathlib.Path(__file__).resolve().parents[2]


class FakeHermes:
    """Acts like the parts of the hermes CLI that the sync module calls. It edits files in a fake Hermes home."""

    def __init__(self, home, paused=False):
        self.home = pathlib.Path(home)
        self.calls = []
        self.fail = set()

    def profile_dir(self, name):
        return self.home / "profiles" / name

    def _cfg(self, name):
        path = self.profile_dir(name) / "config.yaml"
        return path, (yaml.safe_load(path.read_text()) if path.exists() else {}) or {}

    def __call__(self, argv, timeout=60, user=None, extra_env=None):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "git":
            return 0, ""
        assert argv[0] == "hermes"
        name = None
        if argv[1] == "-p":
            name, argv = argv[2], [argv[0]] + argv[3:]
        verb = argv[1:]
        key = " ".join(verb[:2])
        if key in self.fail:
            return 1, "boom"
        if verb[:2] == ["profile", "create"]:
            d = self.profile_dir(verb[2])
            d.mkdir(parents=True)
            (d / "config.yaml").write_text("model:\n  default: fleet-worker\n_config_version: 53\n")
            (d / "profile.yaml").write_text("description: x\n")
            (d / ".env").write_text("# empty\n")
            (d / "SOUL.md").write_text("default soul")
            (d / "hooks").mkdir()
            return 0, "created"
        if verb[:2] == ["config", "unset"]:
            path, cfg = self._cfg(name)
            node, parts = cfg, verb[2].split(".")
            for part in parts[:-1]:
                node = node.get(part) or {}
            node.pop(parts[-1], None)
            path.write_text(yaml.safe_dump(cfg))
            return 0, "ok"
        if verb[:2] == ["config", "set"]:
            path, cfg = self._cfg(name)
            args = verb[2:]
            force = "--force" in args
            args = [a for a in args if a != "--force"]
            node = cfg
            parts = args[0].split(".")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            value = args[1]
            try:
                value = yaml.safe_load(value)
            except yaml.YAMLError:
                pass
            if isinstance(node.get(parts[-1]), dict) and not force:      # the real tool refuses to replace a section
                return 1, "Cannot set %r to a scalar" % parts[-1]
            node[parts[-1]] = value
            path.write_text(yaml.safe_dump(cfg))
            return 0, "ok"
        if verb[:1] == ["tools"] and verb[1] in ("enable", "disable"):
            platform = verb[verb.index("--platform") + 1]
            names = verb[2:verb.index("--platform")]
            path, cfg = self._cfg(name)
            cur = set((cfg.setdefault("platform_toolsets", {}).get(platform)) or sync.KNOWN_BUILTIN[:12])
            cur = cur | set(names) if verb[1] == "enable" else cur - set(names)
            cfg["platform_toolsets"][platform] = sorted(cur)
            path.write_text(yaml.safe_dump(cfg))
            return 0, "ok"
        if verb[:2] == ["cron", "create"]:
            jobs = self.profile_dir(name) / "cron" / "jobs.json"
            jobs.parent.mkdir(exist_ok=True)
            data = json.loads(jobs.read_text()) if jobs.exists() else {"jobs": []}
            rest, positional, opts = verb[2:], [], {}
            while rest and not rest[0].startswith("--"):
                positional.append(rest.pop(0))
            while rest:
                key = rest.pop(0)
                opts[key] = True if key == "--no-agent" else rest.pop(0)
            data["jobs"].append({"id": "j%d" % len(data["jobs"]), "name": opts["--name"], "prompt": positional[1] if len(positional) > 1 else "",
                                 "schedule": {"expr": positional[0]}, "monitor_script": opts.get("--monitor-script"), "script": opts.get("--script"),
                                 "no_agent": bool(opts.get("--no-agent"))})
            jobs.write_text(json.dumps(data))
            return 0, "Created job"
        if verb[:2] == ["cron", "remove"]:
            jobs = self.profile_dir(name) / "cron" / "jobs.json"
            data = json.loads(jobs.read_text())
            data["jobs"] = [j for j in data["jobs"] if j["id"] != verb[2]]
            jobs.write_text(json.dumps(data))
            return 0, "removed"
        if verb[:2] == ["sessions", "list"]:
            marker = self.profile_dir(name) / "botchat"
            return 0, ("Title  Preview\nBot Chat   x\n" if marker.exists() else "Title  Preview\n")
        if verb[:1] == ["chat"]:
            (self.profile_dir(name) / "botchat").write_text("1")
            return 1, "Error: Hermes is paused"            # the real tool exits with 1 when the pause hook blocks the turn
        return 0, ""

    def ran(self, *words):
        return any(all(w in c for w in words) for c in self.calls)


@pytest.fixture
def world(fleet_env, tmp_path, monkeypatch):
    live = tmp_path / "live"
    copy_tree(ROOT / "agents", live / "agents")
    copy_tree(ROOT / "skills", live / "skills")
    (live / "skills" / "_enabled").mkdir()
    monkeypatch.setenv("FLEET_SKILLS_VIEW", str(tmp_path / "skills-view"))
    monkeypatch.setenv("FLEET_WORK_CLONE", str(tmp_path / "work"))
    monkeypatch.setenv("FLEET_LIVE_REPO", str(live))
    shutil.rmtree(fleet_env["hermes"] / "profiles")          # the fixture made empty profile folders. Start clean.
    (fleet_env["hermes"] / "profiles").mkdir()
    (fleet_env["hermes"] / ".env").write_text("")
    fake = FakeHermes(fleet_env["hermes"])
    return {"live": live, "fake": fake, "env": fleet_env, "view": tmp_path / "skills-view"}


def new_sync(world):
    return sync.Sync(live=world["live"], runner=world["fake"])


def test_the_first_run_makes_all_seven_profiles(world):
    report = new_sync(world).sync_all(botmode=False)
    assert report["ok"] and set(report["agents"]) == {"orchestrator", "research", "build", "marketing", "feedback", "security", "improver"}
    for name in report["agents"]:
        d = world["fake"].profile_dir(name)
        assert (d / "SOUL.md").read_text() == (world["live"] / "agents" / name / "SOUL.md").read_text()
    assert world["fake"].ran("profile", "create", "orchestrator", "--no-skills", "--no-alias")


def test_a_second_run_changes_nothing_and_calls_hermes_only_to_read(world):
    s = new_sync(world)
    s.sync_all(botmode=False)
    world["fake"].calls.clear()
    report = s.sync_all(botmode=False)
    assert report["ok"]
    writes = [c for c in world["fake"].calls if c[0] == "hermes" and any(w in c for w in ("create", "set", "enable", "disable", "remove"))]
    assert writes == [], writes


def test_config_follows_agent_yaml(world):
    new_sync(world).sync_all(botmode=False)
    cfg = yaml.safe_load((world["fake"].profile_dir("build") / "config.yaml").read_text())
    assert cfg["model"]["default"] == "fleet-coder" and cfg["agent"]["max_turns"] == 120
    assert "browser" in cfg["agent"]["disabled_toolsets"] and "web" in cfg["agent"]["disabled_toolsets"]
    assert cfg["terminal"]["cwd"] == "/workspace/agent-fleet"
    assert any(v.endswith(":/workspace/agent-fleet") for v in cfg["terminal"]["docker_volumes"])
    assert any(v.endswith(":/fleet-state/status:ro") for v in cfg["terminal"]["docker_volumes"])
    fleet = cfg["mcp_servers"]["fleet_tools"]
    assert fleet["args"][-1] == "ticket_list,ticket_update,deploy_commit,preview_start,preview_stop" and fleet["env"]["DEPLOYER_API_TOKEN"] == "${DEPLOYER_API_TOKEN}"
    assert "PUBLISHER_API_TOKEN" not in fleet["env"]
    assert cfg["mcp_servers"]["playwright"]["args"][:2] == ["--headless", "--isolated"]
    assert cfg["skills"]["external_dirs"] == [str(world["view"] / "build")]


def test_the_feedback_agent_has_no_workspace_at_all(world):
    new_sync(world).sync_all(botmode=False)
    cfg = yaml.safe_load((world["fake"].profile_dir("feedback") / "config.yaml").read_text())
    assert "cwd" not in cfg.get("terminal", {}) and not cfg.get("terminal", {}).get("docker_volumes")
    assert sorted(cfg["mcp_servers"]) == ["fleet_tools", "n8n_tools"]


def test_toolsets_are_set_for_the_cli_and_cron_platforms(world):
    new_sync(world).sync_all(botmode=False)
    for name, kanban in (("orchestrator", True), ("research", False)):
        cfg = yaml.safe_load((world["fake"].profile_dir(name) / "config.yaml").read_text())
        for platform in ("cli", "cron"):
            enabled = set(cfg["platform_toolsets"][platform])
            assert ("kanban" in enabled) == kanban
            assert "browser" not in enabled and "delegation" not in enabled


def test_each_profile_gets_only_its_skills_in_a_root_view(world):
    new_sync(world).sync_all(botmode=False)
    got = sorted(p.name for p in (world["view"] / "build").iterdir())
    assert got == sorted(yaml.safe_load((world["live"] / "agents" / "build" / "agent.yaml").read_text())["skills"])
    assert not (world["view"] / "build" / "copywriting").exists() and (world["view"] / "marketing" / "copywriting" / "SKILL.md").is_file()


def test_extra_skills_come_from_the_enabled_list_only_when_they_exist(world):
    (world["live"] / "skills" / "_enabled" / "research.txt").write_text("# extras\nschema\nnot-a-skill\n../evil\n")
    new_sync(world).sync_all(botmode=False)
    got = {p.name for p in (world["view"] / "research").iterdir()}
    assert "schema" in got and "not-a-skill" not in got and "evil" not in got
    (world["live"] / "skills" / "_enabled" / "research.txt").write_text("")
    new_sync(world).sync_all(botmode=False)
    assert "schema" not in {p.name for p in (world["view"] / "research").iterdir()}


def test_soul_changes_reach_the_profile(world):
    s = new_sync(world)
    s.sync_all(botmode=False)
    soul = world["live"] / "agents" / "research" / "SOUL.md"
    soul.write_text(soul.read_text() + "\nExtra line.\n")
    report = s.sync_all(botmode=False)
    assert report["agents"]["research"]["soul"] == "updated" and report["agents"]["marketing"]["soul"] == "same"
    assert "Extra line." in (world["fake"].profile_dir("research") / "SOUL.md").read_text()


def test_monitor_scripts_are_copied(world):
    new_sync(world).sync_all(botmode=False)
    assert (world["fake"].profile_dir("orchestrator") / "scripts" / "tick.py").is_file()
    assert (world["fake"].profile_dir("feedback") / "scripts" / "inbox.py").is_file()


def test_cron_jobs_follow_agent_yaml_and_are_replaced_when_they_change(world):
    s = new_sync(world)
    s.sync_all(botmode=False)
    jobs = json.loads((world["fake"].profile_dir("orchestrator") / "cron" / "jobs.json").read_text())["jobs"]
    assert {j["name"] for j in jobs} == {"tick", "briefing-input", "weekly-memo"}
    traces = json.loads((world["fake"].profile_dir("improver") / "cron" / "jobs.json").read_text())["jobs"]
    assert any(j["name"] == "weekly-traces" and j["script"] == "traces_job.py" and j["no_agent"] and j["prompt"] == "" for j in traces)
    assert next(j for j in jobs if j["name"] == "tick")["monitor_script"] == "tick.py"
    spec_path = world["live"] / "agents" / "orchestrator" / "agent.yaml"
    spec_path.write_text(spec_path.read_text().replace('"17 */4 * * *"', '"17 */6 * * *"'))
    s.sync_all(botmode=False)
    jobs = json.loads((world["fake"].profile_dir("orchestrator") / "cron" / "jobs.json").read_text())["jobs"]
    assert len(jobs) == 3 and next(j for j in jobs if j["name"] == "tick")["schedule"]["expr"] == "17 */6 * * *"


def test_a_job_that_is_not_in_agent_yaml_is_left_alone_and_reported(world):
    s = new_sync(world)
    s.sync_all(botmode=False)
    d = world["fake"].profile_dir("research") / "cron"
    data = json.loads((d / "jobs.json").read_text())
    data["jobs"].append({"id": "zz", "name": "mystery", "prompt": "x", "schedule": {"expr": "* * * * *"}})
    (d / "jobs.json").write_text(json.dumps(data))
    notes = s.sync_cron("research", s.agents()["research"])
    assert any("mystery" in n for n in notes) and not world["fake"].ran("cron", "remove", "zz")


def test_the_router_key_reaches_every_profile_and_local_tokens_are_made(world):
    secrets.set_value("ROUTER_KEY_HERMES", "k" * 40, enforce_format=False)
    new_sync(world).sync_all(botmode=False)
    for name in ("orchestrator", "build", "feedback"):
        assert secrets.read_values(world["fake"].profile_dir(name) / ".env").get("ROUTER_KEY_HERMES") == "k" * 40
    assert secrets.is_set("PUBLISHER_API_TOKEN") and secrets.is_set("PUBLISHER_REPLY_TOKEN") and secrets.is_set("DEPLOYER_API_TOKEN")
    # each token sits in the one profile that needs it
    assert "PUBLISHER_API_TOKEN" in secrets.read_values(world["fake"].profile_dir("marketing") / ".env")
    assert "PUBLISHER_API_TOKEN" not in secrets.read_values(world["fake"].profile_dir("build") / ".env")


def test_hooks_folder_is_locked(world):
    new_sync(world).sync_all(botmode=False)
    assert (os.stat(world["fake"].profile_dir("research") / "hooks").st_mode & 0o777) == 0o755


def test_bot_mode_marker_and_chat_session_only_while_paused(world):
    s = new_sync(world)
    s.sync_all(botmode=False)
    notes = s.sync_botmode("orchestrator", s.agents()["orchestrator"])
    assert "marked" in notes and any("deferred" in n for n in notes)
    meta = yaml.safe_load((world["fake"].profile_dir("orchestrator") / "profile.yaml").read_text())
    assert meta["ui_meta"] == {"hermes-bots": {}} and meta["description"] == "x"
    assert not world["fake"].ran("chat")
    (world["env"]["state"] / "PAUSED").write_text(json.dumps({"reason": "waiting_for_keys"}))
    notes = s.sync_botmode("orchestrator", s.agents()["orchestrator"])
    assert "Bot Chat created" in notes and world["fake"].ran("chat", "-c", "Bot Chat", "--create-if-missing")
    world["fake"].calls.clear()
    assert "Bot Chat exists" in s.sync_botmode("orchestrator", s.agents()["orchestrator"]) and not world["fake"].ran("chat")


def test_a_failing_step_is_reported_and_stops_that_agent_only(world):
    world["fake"].fail.add("tools disable")
    report = new_sync(world).sync_all(botmode=False)
    assert not report["ok"] and report["agents"]["research"]["toolsets"].startswith("FAILED")
    assert "cron" not in report["agents"]["research"]             # the agent stopped at the failed step


def test_the_merger_entry_point_never_raises(world, monkeypatch):
    monkeypatch.setattr(sync.Sync, "sync_all", lambda self, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert "sync failed" in sync.sync_all(world["live"])


def test_the_working_clone_is_fast_forwarded_only_from_main(world, tmp_path):
    (tmp_path / "work" / ".git").mkdir(parents=True)
    s = new_sync(world)
    s.run = Runner(rules=[(["symbolic-ref"], (0, "main\n")), (["merge"], (0, ""))])
    assert s.sync_work_clone() == "fast-forwarded"
    s.run = Runner(rules=[(["symbolic-ref"], (0, "agent/x/y\n"))])
    assert "left alone" in s.sync_work_clone()


def _git(repo, *args):
    done = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@localhost", "-C", str(repo), *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _real_runner(argv, timeout=60, user=None, extra_env=None):
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)  # nosemgrep: dangerous-subprocess-use-audit
    return done.returncode, done.stdout + done.stderr


@pytest.fixture
def clones(world, tmp_path):
    """A live repository and a working clone with real git. The runner runs commands as the current user."""
    live = tmp_path / "gitlive"
    live.mkdir()
    _git(live, "init", "-q", "-b", "main")
    (live / "a.txt").write_text("one\n")
    _git(live, "add", "-A")
    _git(live, "commit", "-q", "-m", "A")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(live), str(work)], check=True)
    s = sync.Sync(live=world["live"], runner=_real_runner)
    return {"live": live, "work": work, "sync": s}


def test_a_clone_that_is_behind_is_fast_forwarded(clones):
    (clones["live"] / "a.txt").write_text("two\n")
    _git(clones["live"], "commit", "-q", "-am", "B")
    assert clones["sync"].sync_work_clone() == "fast-forwarded"
    assert (clones["work"] / "a.txt").read_text() == "two\n"


def test_a_clone_that_took_a_change_before_a_rollback_is_repaired_and_nothing_is_lost(clones):
    live, work = clones["live"], clones["work"]
    old = _git(live, "rev-parse", "HEAD")
    (live / "a.txt").write_text("bad change\n")
    _git(live, "commit", "-q", "-am", "bad merge")
    bad = _git(live, "rev-parse", "HEAD")
    assert clones["sync"].sync_work_clone() == "fast-forwarded"          # the old merger synced before its health check
    _git(live, "reset", "-q", "--hard", old)                              # the rollback
    (live / "log.txt").write_text("changelog\n")
    _git(live, "add", "-A")
    _git(live, "commit", "-q", "-m", "changelog")
    note = clones["sync"].sync_work_clone()
    assert note.startswith("main reset to origin/main") and "rolled-back/%s" % bad[:12] in note
    assert _git(work, "rev-parse", "HEAD") == _git(live, "rev-parse", "HEAD")
    assert (work / "a.txt").read_text() == "one\n"
    assert _git(work, "rev-parse", "rolled-back/%s" % bad[:12]) == bad       # the old tip is kept
    assert clones["sync"].sync_work_clone() == "fast-forwarded"           # and the next sync is normal again


def test_edits_on_main_go_to_a_stash(clones):
    live, work = clones["live"], clones["work"]
    (live / "a.txt").write_text("two\n")
    _git(live, "commit", "-q", "-am", "B")
    (work / "a.txt").write_text("my local edit\n")
    note = clones["sync"].sync_work_clone()
    assert "edits kept in a stash" in note
    assert (work / "a.txt").read_text() == "two\n"
    assert len(_git(work, "stash", "list").splitlines()) == 1
    assert _git(work, "branch", "--list", "rolled-back/*") == ""            # no commit was lost, so no archive branch


def test_a_clone_on_a_branch_is_left_alone_even_when_it_diverged(clones):
    work = clones["work"]
    _git(work, "checkout", "-q", "-b", "agent/build/topic")
    (work / "b.txt").write_text("work\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "agent work")
    assert "left alone" in clones["sync"].sync_work_clone()
    assert (work / "b.txt").read_text() == "work\n"


def test_the_merger_entry_point_for_the_clone_never_raises(world, monkeypatch):
    monkeypatch.setattr(sync.Sync, "sync_work_clone", lambda self: (_ for _ in ()).throw(RuntimeError("x")))
    assert "sync failed" in sync.sync_work_clone(world["live"])
    monkeypatch.setattr(sync.Sync, "sync_work_clone", lambda self: "fast-forwarded")
    assert sync.sync_work_clone(world["live"]) == "work clone: fast-forwarded"


def test_sync_all_can_leave_the_clone_for_later(world, tmp_path):
    (tmp_path / "work" / ".git").mkdir(parents=True)
    s = new_sync(world)
    s.run = Runner(rules=[(["symbolic-ref"], (0, "main\n")), (["merge"], (0, ""))])
    s.agents = lambda: {}
    assert s.sync_all(botmode=False, work_clone=False)["work_clone"].startswith("left for after")
    assert s.sync_all(botmode=False)["work_clone"] == "fast-forwarded"


def test_wanted_config_by_tier(fleet_env):
    from ops.fleetlib import config
    cfg = config.load()
    spec = yaml.safe_load((ROOT / "agents" / "build" / "agent.yaml").read_text())
    assert sync.wanted_config(spec, cfg, "/v")["terminal.container_memory"] == 1536
    cfg["capacity"]["tier"] = "large"
    assert sync.wanted_config(spec, cfg, "/v")["terminal.container_memory"] == 5120


def test_a_section_that_exists_is_replaced_with_force(world):
    s = new_sync(world)
    s.sync_all(botmode=False)
    path = world["fake"].profile_dir("orchestrator") / "config.yaml"
    cfg = yaml.safe_load(path.read_text())
    cfg["mcp_servers"]["old_server"] = {"command": "evil"}
    path.write_text(yaml.safe_dump(cfg))
    report = s.sync_all(botmode=False)
    assert report["ok"] and world["fake"].ran("config", "set", "--force", "mcp_servers")
    assert sorted(yaml.safe_load(path.read_text())["mcp_servers"]) == ["fleet_tools", "n8n_tools"]



# ---- Prompt 3: each profile loads only its own tools from the fleet-tools MCP server in n8n ----
def test_each_profile_gets_the_n8n_server_with_only_its_own_tools(world):
    new_sync(world).sync_all(botmode=False)
    expected = {"orchestrator": ["notify_owner", "request_approval", "log_event"], "research": ["log_event", "fetch_feed"],
                "build": ["request_approval", "log_event"], "marketing": ["request_approval", "log_event"], "feedback": ["log_event"],
                "security": ["notify_owner", "request_approval", "log_event"], "improver": ["request_approval", "log_event"]}
    for name, tools_ in expected.items():
        server = yaml.safe_load((world["fake"].profile_dir(name) / "config.yaml").read_text())["mcp_servers"]["n8n_tools"]
        assert server["tools"]["include"] == tools_ and server["tools"]["prompts"] is False and server["tools"]["resources"] is False
        assert server["url"] == "http://127.0.0.1:5678/mcp/fleet-tools" and server["headers"] == {"Authorization": "Bearer ${N8N_TOOLS_TOKEN}"}


def test_the_n8n_server_url_follows_the_n8n_port(world):
    from ops.fleetlib import config
    spec = {"mcp_servers": {"n8n_tools": {"tools": ["log_event"]}}}
    cfg = dict(config.defaults())
    cfg["ports"] = dict(cfg["ports"], n8n=5999)
    assert sync.mcp_servers(spec, cfg)["n8n_tools"]["url"] == "http://127.0.0.1:5999/mcp/fleet-tools"


def test_the_bearer_token_is_in_every_profile_env_and_never_in_a_config_file(world):
    from ops.fleetlib import secrets
    s = new_sync(world)
    s.sync_all(botmode=False)
    token = secrets.value_of("N8N_TOOLS_TOKEN")
    assert token
    for name in ("orchestrator", "research", "build", "marketing", "feedback", "security", "improver"):
        assert secrets.read_values(world["fake"].profile_dir(name) / ".env").get("N8N_TOOLS_TOKEN") == token
        assert token not in (world["fake"].profile_dir(name) / "config.yaml").read_text()


def test_a_token_that_changed_reaches_the_profiles_on_the_next_sync(world):
    from ops.fleetlib import secrets
    s = new_sync(world)
    s.sync_all(botmode=False)
    secrets.set_value("N8N_TOOLS_TOKEN", "z" * 40)
    for name in ("orchestrator", "research"):                           # simulate a profile that missed the also_files write
        (world["fake"].profile_dir(name) / ".env").write_text("OTHER=1\n")
    report = s.sync_all(botmode=False)
    assert report["ok"]
    assert secrets.read_values(world["fake"].profile_dir("research") / ".env")["N8N_TOOLS_TOKEN"] == "z" * 40


# ---- D-058: token settings
TOKEN_EXPECTED = {("compression", "proactive_prune_tokens"): 48000, ("tool_output", "max_bytes"): 16000,
                  ("web", "cache_ttl_minutes"): 1440, ("auxiliary", "compression"): {"provider": "main", "model": "fleet-worker"}}


def test_every_profile_gets_the_token_settings(world):
    new_sync(world).sync_all(botmode=False)
    for name in ("orchestrator", "research", "build", "marketing", "feedback", "security", "improver"):
        cfg = yaml.safe_load((world["fake"].profile_dir(name) / "config.yaml").read_text())
        assert cfg["compression"]["proactive_prune_tokens"] == 48000, name
        assert cfg["tool_output"]["max_bytes"] == 16000 and "tool_budget" not in cfg, name
        assert cfg["web"]["cache_ttl_minutes"] == 1440, name
        assert cfg["auxiliary"]["compression"]["provider"] == "main" and cfg["auxiliary"]["compression"]["model"] == "fleet-worker", name


def test_the_token_settings_do_not_touch_the_managed_keys():
    forbidden = ("approvals", "security", "terminal", "model", "providers", "mcp_servers", "updates", "skills", "agent", "platform_toolsets", "web.search_backend",
                 "web.keyless_fallback")
    for key in sync.TOKEN_SETTINGS:
        assert not key.startswith(forbidden), key


def test_an_agent_yaml_may_change_a_token_key_and_nothing_else():
    spec = {"model": "fleet-worker", "max_turns": 10, "hermes_config": {"compression.proactive_prune_tokens": 24000, "web.extract_char_limit": 9000}}
    from ops.fleetlib import config
    want = sync.wanted_config(spec, config.defaults(), "/v")
    assert want["compression.proactive_prune_tokens"] == 24000 and want["web.extract_char_limit"] == 9000 and want["tool_output.max_bytes"] == 16000


@pytest.mark.parametrize("key", ["approvals.mode", "terminal.backend", "model.base_url", "security.redact_secrets", "mcp_servers", "providers.fleet-router.api",
                                 "web.search_backend", "web.keyless_fallback", "agent.disabled_toolsets", "updates.check", "skills.write_approval", "compressionx.threshold", ""])
def test_a_key_outside_the_token_families_is_refused(key):
    with pytest.raises(sync.SyncError, match="only token keys"):
        sync.token_settings({"hermes_config": {key: 1}})


def test_a_key_that_is_not_text_is_refused():
    with pytest.raises(sync.SyncError, match="only token keys"):
        sync.token_settings({"hermes_config": {7: 1}})


def test_the_real_agent_files_stay_inside_the_token_families():
    for path in sorted((ROOT / "agents").glob("*/agent.yaml")):
        spec = yaml.safe_load(path.read_text())
        sync.token_settings(spec)                       # raises when a key is outside the families


def test_a_retired_key_that_an_earlier_build_set_is_removed_from_each_profile(world):
    s = new_sync(world)
    s.sync_all(botmode=False)
    path = world["fake"].profile_dir("research") / "config.yaml"
    cfg = yaml.safe_load(path.read_text())
    cfg.setdefault("tool_budget", {})["mcp_result_size_chars"] = 16000          # what the first live run left behind
    path.write_text(yaml.safe_dump(cfg))
    world["fake"].calls.clear()
    report = new_sync(world).sync_all(botmode=False)
    assert report["ok"] and world["fake"].ran("config", "unset", "tool_budget.mcp_result_size_chars")
    assert "mcp_result_size_chars" not in (yaml.safe_load(path.read_text()).get("tool_budget") or {})
    world["fake"].calls.clear()
    new_sync(world).sync_all(botmode=False)
    assert not world["fake"].ran("config", "unset")                              # a second run does nothing


def test_the_retired_keys_are_never_also_wanted():
    assert not set(sync.RETIRED_KEYS) & set(sync.TOKEN_SETTINGS)
